"""Regiones de exclusión (port de ``generar_excludes``).

Formato de archivo .dat compatible con la tesis: una región por línea,
``lambda_min lambda_max`` en µm.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

Range = tuple[float, float]


def detect_aberrations(wave_um: np.ndarray, flux: np.ndarray, factor_sigma: float = 2.0,
                       pad_um: float = 0.0002) -> Range | None:
    """Misma regla que la tesis: píxeles con |flux - media| > factor * media.

    Devuelve un único rango que cubre todos los píxeles marcados (con margen),
    o ``None`` si no hay aberraciones.
    """
    wave_um = np.asarray(wave_um, float)
    flux = np.asarray(flux, float)
    good = np.isfinite(flux) & np.isfinite(wave_um)
    if not good.any():
        return None
    mean = np.mean(flux[good])
    mask = good & (np.abs(flux - mean) > factor_sigma * abs(mean))
    if not mask.any():
        return None
    lo = max(wave_um[mask].min() - pad_um, wave_um[good].min())
    hi = min(wave_um[mask].max() + pad_um, wave_um[good].max())
    return (float(lo), float(hi))


def merge_ranges(ranges: list[Range]) -> list[Range]:
    rs = sorted((min(a, b), max(a, b)) for a, b in ranges)
    out: list[Range] = []
    for a, b in rs:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def read_ranges(path: str | os.PathLike) -> list[Range]:
    out: list[Range] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", " ").split()
        if len(parts) >= 2:
            try:
                out.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return out


def write_ranges(path: str | os.PathLike, ranges: list[Range]) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for a, b in ranges:
            f.write(f"{a:.6f} {b:.6f}\n")
    return str(path)
