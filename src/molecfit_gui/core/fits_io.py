"""Lectura/escritura de espectros 1D (imagen FITS con WCS lineal, p.ej. WINERED).

Port de ``editar_fits_`` de ``molecfit_pipeline_2.py``, con dos cambios:
- no se sobrescriben DATE-OBS ni UT: se añaden ``MJD-OBS`` (días) y
  ``UTC-SEC`` (segundos desde medianoche) para Molecfit;
- se reescribe el WCS como CRPIX1=1, CRVAL1, CDELT1 y CD1_1 (Molecfit 4.4.2
  lee CRVAL1 + CD1_1 y avisa si falta CD1_1).
"""
from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.io import fits

ORDER_RE = re.compile(r"_m(\d+)_")


def detect_order(path: str | os.PathLike) -> str | None:
    """Orden de echelle a partir del nombre (``..._m43_...`` → ``'43'``)."""
    m = ORDER_RE.search(os.path.basename(str(path)))
    return m.group(1) if m else None


@dataclass
class Spectrum:
    wave: np.ndarray
    flux: np.ndarray
    header: fits.Header
    path: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def order(self) -> str | None:
        return detect_order(self.path) if self.path else None

    @property
    def name(self) -> str:
        return os.path.basename(self.path) if self.path else "espectro"


def wave_from_header(header: fits.Header, n_pix: int) -> np.ndarray:
    """Eje espectral desde el WCS lineal del eje 1 (como ``WCS.pixel_to_world``)."""
    crval = header.get("CRVAL1")
    if crval is None:
        raise KeyError("El header no tiene CRVAL1")
    step = header.get("CD1_1", header.get("CDELT1"))
    if step is None:
        raise KeyError("El header no tiene CDELT1 ni CD1_1")
    crpix = header.get("CRPIX1", 1.0)
    ctype = str(header.get("CTYPE1", "")).upper()
    pix = np.arange(n_pix, dtype=float) + 1.0  # FITS es 1-indexado
    wave = crval + (pix - crpix) * step
    if "LOG" in ctype:
        warnings.warn("CTYPE1 indica escala logarítmica; se asume log10(λ)", RuntimeWarning)
        wave = 10.0 ** wave
    return wave


def read_spectrum(path: str | os.PathLike) -> Spectrum:
    """Lee un espectro 1D. Soporta imagen primaria y tablas de Molecfit."""
    path = str(path)
    with fits.open(path) as hdul:
        prim = hdul[0]
        if prim.data is not None and prim.data.ndim >= 1:
            data = np.asarray(prim.data, dtype=float)
            if data.ndim > 1:
                data = data.reshape(-1, data.shape[-1])[0]
            wave = wave_from_header(prim.header, data.shape[-1])
            return Spectrum(wave, data, prim.header.copy(), path)
        for hdu in hdul[1:]:
            if isinstance(hdu, fits.BinTableHDU):
                cols = {c.lower(): c for c in hdu.columns.names}
                lam = next((cols[k] for k in ("lambda", "wave", "wavelength") if k in cols), None)
                flx = next((cols[k] for k in ("cflux", "flux") if k in cols), None)
                if lam and flx:
                    return Spectrum(np.asarray(hdu.data[lam], float), np.asarray(hdu.data[flx], float),
                                    prim.header.copy(), path)
    raise ValueError(f"No se encontró un espectro 1D en {path}")


def observation_time(header: fits.Header):
    """(MJD, segundos UTC) a partir de DATE-OBS (+ UT/UTC/TIME-OBS si hace falta)."""
    from astropy.time import Time

    date = str(header.get("DATE-OBS", "")).strip()
    if not date:
        raise KeyError("El header no tiene DATE-OBS")
    if "T" not in date:
        ut = next((header[k] for k in ("UT", "UTC", "TIME-OBS", "UT-STR") if k in header
                   and isinstance(header[k], str)), None)
        if ut is None:
            raise KeyError("DATE-OBS no incluye la hora y no hay UT/TIME-OBS")
        date = f"{date}T{ut.strip()}"
    t = Time(date, format="isot", scale="utc")
    dt = t.datetime
    secs = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6
    return float(t.mjd), float(secs)


def prepare_header_for_molecfit(header: fits.Header, wave: np.ndarray) -> fits.Header:
    hdr = header.copy()
    try:
        mjd, secs = observation_time(hdr)
        hdr["MJD-OBS"] = (mjd, "[d] MJD inicio (molecfit_gui)")
        hdr["UTC-SEC"] = (secs, "[s] UTC desde medianoche (molecfit_gui)")
    except (KeyError, ValueError) as exc:
        warnings.warn(f"No se pudo calcular MJD/UTC: {exc}", RuntimeWarning)
    step = float(np.median(np.diff(wave)))
    hdr["CRPIX1"] = 1.0
    hdr["CRVAL1"] = float(wave[0])
    hdr["CDELT1"] = step
    hdr["CD1_1"] = step
    return hdr


def write_spectrum(path: str | os.PathLike, flux: np.ndarray, header: fits.Header,
                   overwrite: bool = True) -> str:
    path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    hdr = header.copy()
    for k in ("NAXIS2", "NAXIS3"):
        hdr.remove(k, ignore_missing=True)
    fits.PrimaryHDU(np.asarray(flux, dtype=np.float64), header=hdr).writeto(path, overwrite=overwrite)
    return path


def list_fits(folder: str | os.PathLike) -> list[str]:
    folder = Path(folder)
    files = [p for p in folder.iterdir() if p.suffix.lower() in (".fits", ".fit", ".fts")]
    return sorted(map(str, files), key=lambda p: (detect_order(p) is None, int(detect_order(p) or 0), p))
