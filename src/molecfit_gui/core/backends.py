"""Backends de Molecfit.

- ``EsorexBackend``: Molecfit 4.4.2 (recetas molecfit_model → molecfit_calctrans
  → molecfit_correct vía EsoRex, archivos .rc + .sof).
- ``StandaloneBackend``: Molecfit 1.5.9 (binarios ``molecfit`` y ``calctrans``
  con un .par), el flujo de ``molecfit_pipeline_2.py``.

Ambos reciben un espectro ya preparado (normalizado, con MJD-OBS/UTC-SEC y
CD1_1) y devuelven un ``MolecfitResult`` con la misma estructura.
"""
from __future__ import annotations

import glob
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.io import fits

from .config import AppSettings, MolecfitConfig
from .excludes import Range, write_ranges
from .fits_io import read_spectrum
from .runner import CommandContext, LogFn, run_command


@dataclass
class MolecfitJob:
    input_fits: str                      # espectro preparado (imagen 1D)
    workdir: str                         # carpeta propia de este orden
    config: MolecfitConfig
    exclude: list[Range] = field(default_factory=list)   # µm
    label: str = ""


@dataclass
class MolecfitResult:
    ok: bool
    wave_um: np.ndarray | None = None
    flux_in: np.ndarray | None = None
    flux_corr: np.ndarray | None = None
    transmission: np.ndarray | None = None
    best_fit: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    message: str = ""

    def resolution(self, cdelt: float, wlg_to_micron: float) -> float | None:
        """R = λ_ref / (FWHM_pix · CDELT)   (ecs. 2.3-2.4 de la tesis)."""
        fwhm = self.best_fit.get("gaussfwhm")
        if not fwhm or self.wave_um is None or not cdelt:
            return None
        lam_ref = float(np.nanmedian(self.wave_um)) / wlg_to_micron
        return lam_ref / (float(fwhm) * cdelt)


def _keyword_or_none(header: fits.Header, key: str) -> str:
    """Si el keyword no existe en el header se usa NONE (→ valor fijo)."""
    if not key or key.upper() == "NONE":
        return "NONE"
    k = key[9:] if key.upper().startswith("HIERARCH ") else key
    return key if k in header else "NONE"


def _bool(v: bool) -> str:
    return "TRUE" if v else "FALSE"


def _csv(values) -> str:
    return ",".join(str(v) for v in values)


# ======================================================================
# EsoRex / Molecfit 4.4.2
# ======================================================================
class EsorexBackend:
    name = "EsoRex (Molecfit 4.4.2)"
    RECIPES = ("molecfit_model", "molecfit_calctrans", "molecfit_correct")

    def __init__(self, settings: AppSettings):
        self.settings = settings
        env = {"OMP_NUM_THREADS": settings.omp_threads} if settings.omp_threads else None
        self.ctx = CommandContext(prefix=settings.command_prefix, env=env)

    def check(self, log: LogFn | None = None) -> tuple[bool, str]:
        exe = self.ctx.which(self.settings.esorex_path)
        if not exe:
            return False, f"No se encontró '{self.settings.esorex_path}'. Instala el kit 4.4.2 o ajusta la ruta."
        import subprocess
        try:
            out = subprocess.run(self.ctx.argv([self.settings.esorex_path, "--recipes"]),
                                 capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"esorex no responde: {exc}"
        missing = [r for r in self.RECIPES if r not in out]
        if missing:
            return False, f"esorex encontrado ({exe}) pero faltan recetas: {', '.join(missing)}"
        return True, f"esorex OK ({exe}); recetas molecfit disponibles"

    # ---------------- archivos de configuración ----------------
    def model_parameters(self, job: MolecfitJob, header: fits.Header,
                         full_range_um: tuple[float, float] | None = None) -> dict[str, str]:
        c = job.config
        # 4.4.2 exige al menos un rango (a diferencia de 1.5.9): sin rangos → todo el espectro
        ranges = list(c.wave_include) or ([full_range_um] if full_range_um else [])
        n_ranges = max(1, len(ranges))
        p: dict[str, str] = {
            # Entrada como tabla FITS (ext. 1, columnas lambda/flux): es el formato estándar
            # de 4.4.2 y evita depender del WCS del header (la imagen 1D falló con WINERED).
            "USE_ONLY_INPUT_PRIMARY_DATA": "FALSE",
            "USE_INPUT_KERNEL": "FALSE",
            "LIST_MOLEC": _csv(m.name for m in c.molecules),
            "FIT_MOLEC": _csv(int(m.fit) for m in c.molecules),
            "REL_COL": _csv(m.rel_col for m in c.molecules),
            "WAVE_INCLUDE": _csv(f"{v:.6f}" for r in ranges for v in r) if ranges else "NULL",
            "WAVE_EXCLUDE": _csv(f"{v:.6f}" for r in job.exclude for v in r) if job.exclude else "NULL",
            "PIXEL_EXCLUDE": "NULL",
            "SILENT_EXTERNAL_BINS": "TRUE",
            "TRANSMISSION": _bool(c.transmission),
            "COLUMN_LAMBDA": "lambda",
            "COLUMN_FLUX": "flux",
            "COLUMN_DFLUX": "NULL",
            "COLUMN_MASK": "NULL",
            "DEFAULT_ERROR": str(c.default_error),
            "WLG_TO_MICRON": repr(c.wlg_to_micron),
            "WAVELENGTH_FRAME": c.wavelength_frame.upper(),
            "FTOL": repr(c.ftol),
            "XTOL": repr(c.xtol),
            "FIT_TELESCOPE_BACKGROUND": "FALSE" if c.transmission else "TRUE",
            "FIT_CONTINUUM": _csv([int(c.fit_continuum)] * n_ranges),
            "CONTINUUM_N": _csv([c.continuum_n] * n_ranges),
            "CONTINUUM_CONST": str(c.continuum_const),
            "MAP_REGIONS_TO_CHIP": _csv([1] * n_ranges),
            "FIT_WLC": _csv([int(c.fit_wlc)] * n_ranges),
            "WLC_N": str(c.wlc_n),
            "WLC_CONST": str(c.wlc_const),
            "FIT_RES_BOX": _bool(c.fit_res_box),
            "RES_BOX": str(c.res_box),
            "FIT_RES_GAUSS": _bool(c.fit_res_gauss),
            "RES_GAUSS": str(c.res_gauss),
            "FIT_RES_LORENTZ": _bool(c.fit_res_lorentz),
            "RES_LORENTZ": str(c.res_lorentz),
            "KERNMODE": _bool(c.kernmode),
            "KERNFAC": str(c.kernfac),
            "VARKERN": _bool(c.varkern),
            "OBSERVING_DATE_KEYWORD": _keyword_or_none(header, c.obsdate_key),
            "OBSERVING_DATE_VALUE": str(header.get("MJD-OBS", -1.0)),
            "UTC_KEYWORD": _keyword_or_none(header, c.utc_key),
            "UTC_VALUE": str(header.get("UTC-SEC", -1.0)),
            "TELESCOPE_ANGLE_KEYWORD": _keyword_or_none(header, c.telalt_key),
            "TELESCOPE_ANGLE_VALUE": str(c.telalt_value),
            "RELATIVE_HUMIDITY_KEYWORD": _keyword_or_none(header, c.rhum_key),
            "RELATIVE_HUMIDITY_VALUE": str(c.rhum_value),
            "PRESSURE_KEYWORD": _keyword_or_none(header, c.pres_key),
            "PRESSURE_VALUE": str(c.pres_value),
            "TEMPERATURE_KEYWORD": _keyword_or_none(header, c.temp_key),
            "TEMPERATURE_VALUE": str(c.temp_value),
            "MIRROR_TEMPERATURE_KEYWORD": _keyword_or_none(header, c.m1temp_key),
            "MIRROR_TEMPERATURE_VALUE": str(c.m1temp_value),
            "ELEVATION_KEYWORD": "NONE",
            "ELEVATION_VALUE": str(c.elevation),
            "LONGITUDE_KEYWORD": "NONE",
            "LONGITUDE_VALUE": str(c.longitude),
            "LATITUDE_KEYWORD": "NONE",
            "LATITUDE_VALUE": str(c.latitude),
            "SLIT_WIDTH_KEYWORD": "NONE",
            "SLIT_WIDTH_VALUE": str(c.slit_width),
            "PIX_SCALE_KEYWORD": "NONE",
            "PIX_SCALE_VALUE": str(c.pix_scale),
            "REFERENCE_ATMOSPHERIC": c.reference_atmosphere,
            "GDAS_PROFILE": c.gdas_profile,
            "PWV": str(c.pwv),
        }
        p.update({str(k): str(v) for k, v in c.expert_extra.items()})
        return p

    @staticmethod
    def write_rc(path: str, params: dict[str, str]) -> str:
        with open(path, "w") as f:
            f.write("# Generado por molecfit_gui\n")
            for k, v in params.items():
                f.write(f"{k}={v}\n")
        return path

    def write_sof(self, path: str, entries: list[tuple[str, str]]) -> str:
        with open(path, "w") as f:
            for p, tag in entries:
                f.write(f"{self.ctx.path(p)} {tag}\n")
        return path

    def _esorex(self, recipe: str, rc: str, sof: str, wd: str, log, cancel) -> int:
        args = [self.settings.esorex_path,
                f"--output-dir={self.ctx.path(wd)}",
                f"--log-dir={self.ctx.path(wd)}",
                f"--log-file=esorex_{recipe}.log",
                f"--recipe-config={self.ctx.path(rc)}",
                recipe, self.ctx.path(sof)]
        return run_command(args, self.ctx, cwd=wd, log=log, cancel=cancel)

    # ---------------- ejecución ----------------
    def run(self, job: MolecfitJob, log: LogFn | None = None,
            cancel: threading.Event | None = None) -> MolecfitResult:
        log = log or (lambda s: None)
        wd = os.path.abspath(job.workdir)
        os.makedirs(wd, exist_ok=True)
        header = fits.getheader(job.input_fits)
        sci = image_to_table(job.input_fits, os.path.join(wd, "SCIENCE_TABLE.fits"))
        w_um = read_spectrum(job.input_fits).wave * job.config.wlg_to_micron
        full = (float(np.nanmin(w_um)), float(np.nanmax(w_um)))

        rc_m = self.write_rc(os.path.join(wd, "model.rc"), self.model_parameters(job, header, full))
        # Mapeos de extensiones (obligatorios en calctrans/correct 4.4.2, ver --man-page):
        # la tabla SCIENCE tiene primario vacío + ext. 1 → mapeo identidad "0,1".
        rc_c = self.write_rc(os.path.join(wd, "calctrans.rc"), {
            "USE_ONLY_INPUT_PRIMARY_DATA": "FALSE",
            "USE_INPUT_KERNEL": "FALSE",
            "MAPPING_ATMOSPHERIC": "0,1",
            "MAPPING_CONVOLVE": "0,1",
            "CHIP_EXTENSIONS": "FALSE",
        })
        rc_k = self.write_rc(os.path.join(wd, "correct.rc"), {
            "USE_ONLY_INPUT_PRIMARY_DATA": "FALSE",
            "MAPPING_CORRECT": "0,1",
            "CHIP_EXTENSIONS": "FALSE",
        })
        if job.exclude:
            write_ranges(os.path.join(wd, "exclude.dat"), job.exclude)

        f = lambda n: os.path.join(wd, n)  # noqa: E731
        # productos que debe dejar cada receta (esorex puede salir con código 0 sin crearlos)
        expected = {
            "molecfit_model": ["BEST_FIT_PARAMETERS.fits", "ATM_PARAMETERS.fits", "MODEL_MOLECULES.fits"],
            "molecfit_calctrans": ["TELLURIC_CORR.fits"],
            "molecfit_correct": [],
        }
        for name in sum(expected.values(), []):   # limpiar productos de corridas anteriores
            if os.path.exists(f(name)):
                os.remove(f(name))
        errors: list[str] = []

        def log_and_catch(line: str):
            if "[ ERROR ]" in line:
                errors.append(line.split("[ ERROR ]", 1)[1].strip())
            log(line)

        steps = [
            ("molecfit_model", rc_m, [(sci, "SCIENCE")]),
            ("molecfit_calctrans", rc_c, [(sci, "SCIENCE"), (f("ATM_PARAMETERS.fits"), "ATM_PARAMETERS"),
                                          (f("MODEL_MOLECULES.fits"), "MODEL_MOLECULES"),
                                          (f("BEST_FIT_PARAMETERS.fits"), "BEST_FIT_PARAMETERS")]),
            ("molecfit_correct", rc_k, [(sci, "SCIENCE"), (f("TELLURIC_CORR.fits"), "TELLURIC_CORR")]),
        ]
        for i, (recipe, rc, entries) in enumerate(steps, 1):
            log(f"[{i}/3] {recipe} — {job.label}")
            sof = self.write_sof(os.path.join(wd, f"{recipe}.sof"), entries)
            errors.clear()
            code = self._esorex(recipe, rc, sof, wd, log_and_catch, cancel)
            missing = [n for n in expected[recipe] if not os.path.exists(f(n))]
            if code != 0 or missing:
                why = "; ".join(e for e in errors if not e.startswith("esorex:"))[:500]
                return MolecfitResult(False, message=(
                    f"{recipe} falló (código {code}"
                    + (f", sin productos: {', '.join(missing)}" if missing else "") + ")"
                    + (f". Molecfit dice: {why}" if why else "")
                    + f". Log: {f('esorex_' + recipe + '.log')}"))
        return self.collect(wd, job.input_fits, job.config)

    @staticmethod
    def collect(wd: str, sci: str, cfg: MolecfitConfig) -> MolecfitResult:
        res = MolecfitResult(True)
        spec = read_spectrum(sci)
        res.wave_um = spec.wave * cfg.wlg_to_micron
        res.flux_in = spec.flux
        corr = sorted(glob.glob(os.path.join(wd, "SCIENCE_TELLURIC_CORR_*.fits")))
        if corr:
            res.files["corrected"] = corr[0]
            with fits.open(corr[0]) as h:
                d = h[0].data
                if d is not None and getattr(d, "ndim", 0) >= 1:
                    res.flux_corr = np.asarray(d, float).ravel()[: len(res.flux_in)]
            if res.flux_corr is None:
                tab = _first_table(corr[0])
                if tab is not None:
                    cols = {c.lower(): c for c in tab.columns.names}
                    if "flux" in cols:
                        res.flux_corr = np.asarray(tab[cols["flux"]], float)
        td = os.path.join(wd, "TELLURIC_DATA.fits")
        if os.path.exists(td):
            res.files["telluric_data"] = td
            tab = _first_table(td)
            if tab is not None:
                cols = {c.lower(): c for c in tab.columns.names}
                if "mtrans" in cols:
                    res.transmission = np.asarray(tab[cols["mtrans"]], float)
                if res.flux_corr is None and "cflux" in cols:
                    res.flux_corr = np.asarray(tab[cols["cflux"]], float)
        tc = os.path.join(wd, "TELLURIC_CORR.fits")
        if os.path.exists(tc):
            res.files["telluric_corr"] = tc
            if res.transmission is None:
                with fits.open(tc) as h:
                    for hdu in h:
                        if hdu.data is not None and getattr(hdu.data, "ndim", 0) == 1 and \
                                not isinstance(hdu, fits.BinTableHDU):
                            res.transmission = np.asarray(hdu.data, float)
                            break
                    else:
                        tab = _first_table(tc)
                        if tab is not None:
                            cols = {c.lower(): c for c in tab.columns.names}
                            for k in ("mtrans", "trans", "transmission"):
                                if k in cols:
                                    res.transmission = np.asarray(tab[cols[k]], float)
                                    break
        if res.flux_corr is None and res.transmission is not None and \
                len(res.transmission) == len(res.flux_in):
            with np.errstate(divide="ignore", invalid="ignore"):
                res.flux_corr = res.flux_in / res.transmission
        bfp = os.path.join(wd, "BEST_FIT_PARAMETERS.fits")
        if os.path.exists(bfp):
            res.files["best_fit"] = bfp
            res.best_fit = read_best_fit(bfp)
        if res.flux_corr is None:
            res.ok = False
            res.message = "Las recetas terminaron pero no se encontró el espectro corregido."
        return res


def image_to_table(image_path: str, out_path: str) -> str:
    """Imagen 1D → tabla FITS para Molecfit 4.4.2.

    Primario: header original (keywords de fecha/meteo) sin datos.
    Ext. 1: columnas ``lambda`` (unidades del header) y ``flux``; repite los keywords.
    """
    spec = read_spectrum(image_path)
    hdr = spec.header.copy()
    for k in ("NAXIS1", "NAXIS2", "BSCALE", "BZERO", "BUNIT"):
        hdr.remove(k, ignore_missing=True)
    hdr["NAXIS"] = 0
    prim = fits.PrimaryHDU(header=hdr)
    tab = fits.BinTableHDU.from_columns([
        fits.Column(name="lambda", format="D", array=np.asarray(spec.wave, float)),
        fits.Column(name="flux", format="D", array=np.asarray(spec.flux, float)),
    ])
    skip = {"SIMPLE", "BITPIX", "NAXIS", "EXTEND", "COMMENT", "HISTORY", "",
            "CRVAL1", "CRPIX1", "CDELT1", "CD1_1", "CTYPE1", "CUNIT1", "WCSDIM", "LTV1", "LTM1_1"}
    for card in spec.header.cards:
        if card.keyword not in skip and not card.keyword.startswith(("NAXIS", "TTYPE", "TFORM")) \
                and card.keyword not in tab.header:
            try:
                tab.header[card.keyword] = (card.value, card.comment)
            except (ValueError, KeyError):
                pass
    fits.HDUList([prim, tab]).writeto(out_path, overwrite=True)
    return out_path


def _first_table(path: str):
    with fits.open(path) as h:
        for hdu in h:
            if isinstance(hdu, fits.BinTableHDU) and hdu.data is not None:
                return hdu.data.copy()
    return None


def read_best_fit(path: str) -> dict:
    tab = _first_table(path)
    out: dict = {}
    if tab is None:
        return out
    cols = {c.lower(): c for c in tab.columns.names}
    pcol, vcol = cols.get("parameter"), cols.get("value")
    ucol = cols.get("uncertainty")
    if not pcol or not vcol:
        return out
    for row in tab:
        name = str(row[pcol]).strip()
        try:
            out[name] = float(row[vcol])
        except (TypeError, ValueError):
            out[name] = row[vcol]
        if ucol is not None:
            try:
                out[name + "_err"] = float(row[ucol])
            except (TypeError, ValueError):
                pass
    return out


# ======================================================================
# Standalone / Molecfit 1.5.9
# ======================================================================
class StandaloneBackend:
    name = "Standalone (Molecfit 1.5.9)"

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.ctx = CommandContext(prefix=settings.command_prefix)

    def _exe(self, name: str) -> str:
        d = self.settings.standalone_bin_dir
        if not d:
            return name
        return (d.rstrip("/\\") + "/" + name) if self.ctx.uses_wsl else os.path.join(d, name)

    def check(self, log: LogFn | None = None) -> tuple[bool, str]:
        missing = [n for n in ("molecfit", "calctrans") if not self.ctx.which(self._exe(n))]
        if missing:
            return False, f"No se encontraron: {', '.join(missing)} (carpeta: {self.settings.standalone_bin_dir or 'PATH'})"
        return True, "Binarios molecfit y calctrans encontrados"

    def par_parameters(self, job: MolecfitJob, header: fits.Header, out_name: str) -> dict[str, str]:
        c = job.config
        wd = self.ctx.path(os.path.abspath(job.workdir))
        incl = "none"
        if c.wave_include:
            incl = self.ctx.path(write_ranges(os.path.join(job.workdir, "include.dat"), c.wave_include))
        excl = "none"
        if job.exclude:
            excl = self.ctx.path(write_ranges(os.path.join(job.workdir, "exclude.dat"), job.exclude))
        key = lambda k: _keyword_or_none(header, k)  # noqa: E731
        return {
            "filename": self.ctx.path(os.path.abspath(job.input_fits)),
            "listname": "none",
            "trans": str(int(c.transmission)),
            "default_error": str(c.default_error),
            "wlgtomicron": repr(c.wlg_to_micron),
            "vac_air": c.wavelength_frame.lower(),
            "wrange_include": incl,
            "wrange_exclude": excl,
            "prange_exclude": "none",
            "output_dir": wd,
            "output_name": out_name,
            "ftol": repr(c.ftol),
            "xtol": repr(c.xtol),
            "list_molec": " ".join(m.name for m in c.molecules),
            "fit_molec": " ".join(str(int(m.fit)) for m in c.molecules),
            "relcol": " ".join(str(m.rel_col) for m in c.molecules),
            "flux_unit": "0",
            "fit_back": "0",
            "fit_cont": str(int(c.fit_continuum)),
            "cont_n": str(c.continuum_n),
            "cont_const": str(c.continuum_const),
            "fit_wlc": str(int(c.fit_wlc)),
            "wlc_n": str(c.wlc_n),
            "wlc_const": str(c.wlc_const),
            "fit_res_box": str(int(c.fit_res_box)),
            "relres_box": str(c.res_box),
            "kernmode": str(int(c.kernmode)),
            "fit_res_gauss": str(int(c.fit_res_gauss)),
            "res_gauss": str(c.res_gauss),
            "fit_res_lorentz": str(int(c.fit_res_lorentz)),
            "res_lorentz": str(c.res_lorentz),
            "kernfac": str(c.kernfac),
            "varkern": str(int(c.varkern)),
            "obsdate_key": key(c.obsdate_key),
            "obsdate": str(header.get("MJD-OBS", -1)),
            "utc_key": key(c.utc_key),
            "utc": str(header.get("UTC-SEC", -1)),
            "telalt_key": key(c.telalt_key),
            "telalt": str(c.telalt_value),
            "rhum_key": key(c.rhum_key),
            "rhum": str(c.rhum_value),
            "pres_key": key(c.pres_key),
            "pres": str(c.pres_value),
            "temp_key": key(c.temp_key),
            "temp": str(c.temp_value),
            "m1temp_key": key(c.m1temp_key),
            "m1temp": str(c.m1temp_value),
            "geoelev": str(c.elevation),
            "geoelev_key": "NONE",
            "longitude": str(c.longitude),
            "longitude_key": "NONE",
            "latitude": str(c.latitude),
            "latitude_key": "NONE",
            "slitw": str(c.slit_width),
            "slitw_key": "NONE",
            "pixsc": str(c.pix_scale),
            "pixsc_key": "NONE",
            "ref_atm": c.reference_atmosphere.replace(".fits", ".atm"),
            "gdas_prof": c.gdas_profile,
            "pwv": str(c.pwv),
        } | {str(k).lower(): str(v) for k, v in c.expert_extra.items()}

    @staticmethod
    def write_par(path: str, params: dict[str, str], template: str | None = None) -> str:
        """Igual que ``archivo_par_2``: si hay plantilla, se clona y se reemplazan
        las claves; las claves que no estén en la plantilla se agregan antes de ``end``."""
        pending = dict(params)
        lines_out: list[str] = []
        if template and os.path.exists(template):
            for line in Path(template).read_text().splitlines():
                s = line.strip()
                if s.lower() == "end":
                    continue
                if not s or s.startswith("#") or ":" not in s:
                    lines_out.append(line)
                    continue
                k = s.split(":", 1)[0].strip()
                if k in pending:
                    lines_out.append(f"{k}: {pending.pop(k)}")
                else:
                    lines_out.append(line)
        else:
            lines_out.append("# Generado por molecfit_gui (Molecfit 1.5.9)")
        for k, v in pending.items():
            lines_out.append(f"{k}: {v}")
        lines_out.append("")
        lines_out.append("end")
        Path(path).write_text("\n".join(lines_out) + "\n")
        return path

    def run(self, job: MolecfitJob, log: LogFn | None = None,
            cancel: threading.Event | None = None) -> MolecfitResult:
        log = log or (lambda s: None)
        wd = os.path.abspath(job.workdir)
        os.makedirs(wd, exist_ok=True)
        header = fits.getheader(job.input_fits)
        base = os.path.splitext(os.path.basename(job.input_fits))[0]
        par = os.path.join(wd, base + ".par")
        self.write_par(par, self.par_parameters(job, header, base),
                       self.settings.standalone_par_template or None)
        for i, exe in enumerate(("molecfit", "calctrans"), 1):
            log(f"[{i}/2] {exe} — {job.label}")
            code = run_command([self._exe(exe), self.ctx.path(par)], self.ctx, cwd=wd, log=log,
                               cancel=cancel, log_file=os.path.join(wd, f"{exe}.log"))
            if code != 0:
                return MolecfitResult(False, message=f"{exe} terminó con código {code}. Revisa {exe}.log")
        return self.collect(wd, job.input_fits, job.config)

    @staticmethod
    def collect(wd: str, sci: str, cfg: MolecfitConfig) -> MolecfitResult:
        tac = sorted(glob.glob(os.path.join(wd, "*_tac.fits")))
        if not tac:
            return MolecfitResult(False, message="calctrans no generó *_tac.fits")
        res = MolecfitResult(True, files={"tac": tac[0]})
        with fits.open(tac[0]) as h:
            tab = h[1].data
            cols = {c.lower(): c for c in tab.columns.names}
            res.wave_um = np.asarray(tab[cols["lambda"]], float)
            res.transmission = np.asarray(tab[cols["mtrans"]], float) if "mtrans" in cols else None
            res.flux_corr = np.asarray(tab[cols["cflux"]], float) if "cflux" in cols else None
            res.flux_in = np.asarray(tab[cols["flux"]], float) if "flux" in cols else read_spectrum(sci).flux
        fitres = sorted(glob.glob(os.path.join(wd, "*fit.res")))
        if fitres:
            res.files["fit_res"] = fitres[0]
            res.best_fit = parse_fit_res(fitres[0])
        return res


def parse_fit_res(path: str) -> dict:
    """Lectura aproximada del *fit.res de 1.5.9 (FWHM, χ², columnas relativas)."""
    out: dict = {}
    txt = Path(path).read_text(errors="replace")
    pats = {
        "gaussfwhm": r"FWHM of Gaussian[^:]*:\s*([-\d.eE+]+)",
        "lorentzfwhm": r"FWHM of Lorentzian[^:]*:\s*([-\d.eE+]+)",
        "boxfwhm": r"FWHM of boxcar[^:]*:\s*([-\d.eE+]+)",
        "reduced_chi2": r"[Rr]educed chi2[^:]*:\s*([-\d.eE+]+)",
    }
    for k, pat in pats.items():
        m = re.search(pat, txt)
        if m:
            out[k] = float(m.group(1))
    for m in re.finditer(r"rel_mol_col_(\w+)\s*:\s*([-\d.eE+]+)", txt):
        out[f"rel_mol_col_{m.group(1)}"] = float(m.group(2))
    return out


def make_backend(settings: AppSettings):
    return StandaloneBackend(settings) if settings.backend == "standalone" else EsorexBackend(settings)
