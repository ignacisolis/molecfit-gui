"""Flujo completo por orden (equivalente a ``aplicar_todo`` / ``aplicar_todo_todos``).

1. preparar FITS: normalizar + MJD/UTC + WCS lineal  → ``<out>/prepared/``
2. Molecfit (backend elegido)                         → ``<out>/molecfit/<nombre>/``
3. espectro corregido como imagen FITS                → ``<out>/corrected/``
4. figura de diagnóstico de 4 paneles                 → ``<out>/figures/``
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .backends import MolecfitJob, MolecfitResult, make_backend
from .config import AppSettings, MolecfitConfig
from .excludes import Range, detect_aberrations, merge_ranges, read_ranges, write_ranges
from .fits_io import prepare_header_for_molecfit, read_spectrum, write_spectrum
from .normalize import fit_continuum
from .runner import LogFn


@dataclass
class OutputLayout:
    root: str

    def _d(self, name: str) -> str:
        p = os.path.join(self.root, name)
        os.makedirs(p, exist_ok=True)
        return p

    @property
    def prepared(self): return self._d("prepared")
    @property
    def molecfit(self): return self._d("molecfit")
    @property
    def corrected(self): return self._d("corrected")
    @property
    def figures(self): return self._d("figures")
    @property
    def excludes(self): return self._d("excludes")

    def stem(self, path: str) -> str:
        return os.path.splitext(os.path.basename(path))[0]

    def prepared_path(self, src: str) -> str:
        return os.path.join(self.prepared, self.stem(src) + "_norm.fits")

    def workdir(self, src: str) -> str:
        return os.path.join(self.molecfit, self.stem(src))

    def exclude_path(self, src: str) -> str:
        return os.path.join(self.excludes, self.stem(src) + "_exclude.dat")


def prepare_spectrum(src: str, cfg: MolecfitConfig, out_path: str, normalize: bool = True):
    spec = read_spectrum(src)
    header = prepare_header_for_molecfit(spec.header, spec.wave)
    if normalize:
        n = cfg.normalization
        res = fit_continuum(spec.wave, spec.flux, low_rej=n.low_rej, high_rej=n.high_rej,
                            niter=n.niter, n_knots=n.n_knots)
        flux = res.normalized_flux
        header["MFGNORM"] = (True, "normalizado con spline cubico natural")
        header["MFGNKNOT"] = (n.n_knots, "knots normalizacion")
        header["MFGNSTD"] = (res.residual_std, "std residuos continuo")
    else:
        flux, res = spec.flux, None
    flux = np.where(np.isfinite(flux), flux, np.nanmedian(flux))
    write_spectrum(out_path, flux, header)
    return out_path, res


def save_corrected(src_prepared: str, result: MolecfitResult, out_path: str,
                   wlg_to_micron: float) -> str | None:
    if result.flux_corr is None:
        return None
    spec = read_spectrum(src_prepared)
    flux = np.asarray(result.flux_corr, float)
    if len(flux) != len(spec.flux):
        # rejilla distinta (p.ej. _tac de 1.5.9 en µm) → interpolar a la rejilla original
        order = np.argsort(result.wave_um)
        flux = np.interp(spec.wave * wlg_to_micron, result.wave_um[order], flux[order])
    hdr = spec.header.copy()
    hdr["MFGTELL"] = (True, "corregido por absorcion telurica (molecfit)")
    for k in ("gaussfwhm", "lorentzfwhm"):
        if k in result.best_fit and isinstance(result.best_fit[k], float):
            hdr[f"MFG{k[:5].upper()}"] = (result.best_fit[k], f"{k} [pix] molecfit")
    return write_spectrum(out_path, flux, hdr)


def diagnostic_figure(result: MolecfitResult, title: str, path: str | None = None, n_panels: int = 4):
    """Figura como ``visualizar`` de la tesis: original, corregido y transmisión."""
    from matplotlib.figure import Figure  # API orientada a objetos: no toca el backend de la GUI

    fig = Figure(figsize=(15, 3 * n_panels), layout="constrained")
    plot_result(fig, result, title, n_panels)
    if path:
        fig.savefig(path, dpi=150)
    return fig


def plot_result(fig, result: MolecfitResult, title: str, n_panels: int = 4):
    fig.clear()
    w = result.wave_um
    edges = np.linspace(np.nanmin(w), np.nanmax(w), n_panels + 1)
    axes = fig.subplots(n_panels, 1)
    axes = np.atleast_1d(axes)
    for i, ax in enumerate(axes):
        m = (w >= edges[i]) & (w <= edges[i + 1])
        if result.flux_in is not None and len(result.flux_in) == len(w):
            ax.plot(w[m], result.flux_in[m], color="tab:blue", lw=0.8, label="sin corregir")
        if result.flux_corr is not None and len(result.flux_corr) == len(w):
            ax.plot(w[m], result.flux_corr[m], color="tab:red", lw=0.8, label="corregido")
        if result.transmission is not None and len(result.transmission) == len(w):
            ax.plot(w[m], result.transmission[m], color="tab:green", lw=0.8, label="transmisión")
        ax.set_xlim(edges[i], edges[i + 1])
        ax.grid(True, alpha=0.3)
        if i == 0:
            ax.legend(loc="lower left", fontsize="small", ncol=3)
    fig.supylabel("Flujo normalizado")
    fig.supxlabel("Longitud de onda (µm)")
    fig.suptitle(title)
    if fig.get_layout_engine() is None:
        fig.tight_layout()


def auto_exclude(result: MolecfitResult, factor_sigma: float = 2.0) -> Range | None:
    if result.flux_corr is None or result.wave_um is None:
        return None
    return detect_aberrations(result.wave_um, result.flux_corr, factor_sigma)


def process_file(src: str, cfg: MolecfitConfig, settings: AppSettings, out_root: str,
                 normalize: bool = True, log: LogFn | None = None,
                 cancel: threading.Event | None = None, make_figure: bool = True) -> MolecfitResult:
    log = log or print
    lay = OutputLayout(out_root)
    name = lay.stem(src)
    log(f"=== {name} ===")
    log("Preparando FITS (normalización, MJD/UTC, WCS)…")
    prepared, _ = prepare_spectrum(src, cfg, lay.prepared_path(src), normalize=normalize)
    exclude = merge_ranges(read_ranges(lay.exclude_path(src)))
    if exclude:
        log(f"Usando {len(exclude)} regiones de exclusión de {lay.exclude_path(src)}")
    job = MolecfitJob(prepared, lay.workdir(src), cfg, exclude, label=name)
    backend = make_backend(settings)
    result = backend.run(job, log=log, cancel=cancel)
    if not result.ok:
        log(f"ERROR: {result.message}")
        return result
    out = save_corrected(prepared, result, os.path.join(lay.corrected, name + "_tac.fits"),
                         cfg.wlg_to_micron)
    if out:
        result.files["corrected_image"] = out
        log(f"Espectro corregido: {out}")
    if make_figure:
        fig_path = os.path.join(lay.figures, name + ".png")
        diagnostic_figure(result, f"Espectro corregido — {name}", fig_path)
        result.files["figure"] = fig_path
    summary = {k: v for k, v in result.best_fit.items() if isinstance(v, (int, float))}
    Path(lay.workdir(src), "summary.json").write_text(json.dumps(
        {"source": src, "files": result.files, "best_fit": summary}, indent=2))
    return result


def add_exclusion(src: str, out_root: str, rng: Range) -> list[Range]:
    lay = OutputLayout(out_root)
    ranges = merge_ranges(read_ranges(lay.exclude_path(src)) + [rng])
    write_ranges(lay.exclude_path(src), ranges)
    return ranges
