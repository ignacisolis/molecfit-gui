"""Normalización del continuo con spline cúbico natural y rechazo sigma iterativo.

Port de ``natural_cubic_spline.py`` (tesis Gran 1). La matemática es la misma:
una base de spline cúbico natural (knots equiespaciados en el interior del
rango) ajustada por mínimos cuadrados ordinarios con intercepto, repitiendo el
ajuste ``niter`` veces y descartando en cada iteración los píxeles cuyo residuo
cae por debajo de ``-low_rej*sigma`` o por encima de ``high_rej*sigma``.

Diferencias con el original:
- se usa ``numpy.linalg.lstsq`` en vez de ``sklearn.LinearRegression``
  (mismo estimador, sin dependencia de sklearn/pandas);
- se ignoran NaN/inf de entrada;
- si ambos umbrales son 0 no se rechaza nada (el original fallaba);
- la iteración se detiene si quedan demasiado pocos puntos.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


class NaturalCubicSpline:
    """Base de spline cúbico natural (lineal fuera del rango de los knots)."""

    def __init__(self, knots=None, min=None, max=None, n_knots=None):
        if knots is None:
            if not n_knots or n_knots < 2:
                raise ValueError("n_knots debe ser >= 2")
            knots = np.linspace(min, max, num=n_knots + 2)[1:-1]
        self.knots = np.asarray(knots, dtype=float)

    @property
    def n_knots(self) -> int:
        return len(self.knots)

    def transform(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=float).ravel()
        k = self.knots
        n = self.n_knots
        basis = np.zeros((x.shape[0], n - 1))
        basis[:, 0] = x

        def d(i):
            num = np.maximum(0.0, x - k[i]) ** 3 - np.maximum(0.0, x - k[n - 1]) ** 3
            return num / (k[n - 1] - k[i])

        d_last = d(n - 2)
        for i in range(n - 2):
            basis[:, i + 1] = d(i) - d_last
        return basis


class SplineModel:
    """Spline + regresión lineal con intercepto (equivalente al Pipeline de sklearn)."""

    def __init__(self, spline: NaturalCubicSpline):
        self.spline = spline
        self.coef_: np.ndarray | None = None
        self._x0 = 0.0
        self._xs = 1.0

    def _design(self, x):
        # Centrar/escalar x mejora el condicionamiento numérico sin cambiar el ajuste.
        xs = (np.asarray(x, float) - self._x0) / self._xs
        basis = self.spline_scaled.transform(xs)
        return np.column_stack([np.ones(len(xs)), basis])

    def fit(self, x, y):
        x = np.asarray(x, float)
        self._x0 = float(np.mean(x))
        self._xs = float(np.ptp(x)) or 1.0
        self.spline_scaled = NaturalCubicSpline(knots=(self.spline.knots - self._x0) / self._xs)
        A = self._design(x)
        self.coef_, *_ = np.linalg.lstsq(A, np.asarray(y, float), rcond=None)
        return self

    def predict(self, x):
        return self._design(x) @ self.coef_


def get_natural_cubic_spline_model(x, y, minval=None, maxval=None, n_knots=None, knots=None):
    """Misma firma que el original."""
    if knots is not None:
        spline = NaturalCubicSpline(knots=knots)
    else:
        spline = NaturalCubicSpline(min=minval, max=maxval, n_knots=n_knots)
    return SplineModel(spline).fit(x, y)


@dataclass
class ContinuumResult:
    normalized_flux: np.ndarray
    continuum: np.ndarray
    wave_excluded: np.ndarray
    flux_excluded: np.ndarray
    residual_std: float = field(default=np.nan)
    n_used: int = 0


def continuum(x, y, low_rej=1.8, high_re=0.0, niter=10, order=3, plots=False):
    """Interfaz idéntica a la función original.

    Devuelve ``(normalized_flux, y_fit, w_excl, f_excl)``. ``order`` es el
    número de knots (como en el código de la tesis).
    """
    res = fit_continuum(x, y, low_rej=low_rej, high_rej=high_re, niter=niter, n_knots=order)
    if plots:  # pragma: no cover - solo para uso interactivo
        import matplotlib.pyplot as plt

        fig, (a1, a2) = plt.subplots(2, 1, sharex=True)
        a1.plot(x, y, color="black")
        a1.plot(x, res.continuum, ls="--", color="red")
        a1.scatter(res.wave_excluded, res.flux_excluded, color="red", marker="x", s=20)
        a2.plot(x, res.normalized_flux, color="black")
        plt.show()
    return res.normalized_flux, res.continuum, res.wave_excluded, res.flux_excluded


def fit_continuum(wave, flux, low_rej=1.8, high_rej=0.0, niter=10, n_knots=3,
                  min_points: int | None = None) -> ContinuumResult:
    wave = np.asarray(wave, float)
    flux = np.asarray(flux, float)
    if wave.shape != flux.shape:
        raise ValueError("wave y flux deben tener la misma forma")
    if n_knots < 2:
        raise ValueError("El número de knots debe ser >= 2")
    good = np.isfinite(wave) & np.isfinite(flux)
    w_int, f_int = wave[good], flux[good]
    if min_points is None:
        min_points = max(3 * (n_knots + 1), 10)
    if len(w_int) < min_points:
        raise ValueError(f"Muy pocos puntos válidos ({len(w_int)}) para {n_knots} knots")

    w_excl, f_excl = [], []
    model = None
    for _ in range(max(1, int(niter))):
        model = get_natural_cubic_spline_model(
            w_int, f_int, minval=w_int.min(), maxval=w_int.max(), n_knots=n_knots)
        resid = f_int - model.predict(w_int)
        sig = np.std(resid)
        if low_rej > 0 and high_rej > 0:
            out = (resid >= high_rej * sig) | (resid <= -low_rej * sig)
        elif low_rej > 0:
            out = resid <= -low_rej * sig
        elif high_rej > 0:
            out = resid >= high_rej * sig
        else:
            out = np.zeros_like(resid, dtype=bool)
        if not out.any() or (len(w_int) - out.sum()) < min_points:
            break
        w_excl.append(w_int[out])
        f_excl.append(f_int[out])
        w_int, f_int = w_int[~out], f_int[~out]

    cont = np.full_like(flux, np.nan)
    cont[np.isfinite(wave)] = model.predict(wave[np.isfinite(wave)])
    with np.errstate(divide="ignore", invalid="ignore"):
        norm = flux / cont
    used_resid = f_int / model.predict(w_int) - 1.0
    return ContinuumResult(
        normalized_flux=norm,
        continuum=cont,
        wave_excluded=np.concatenate(w_excl) if w_excl else np.array([]),
        flux_excluded=np.concatenate(f_excl) if f_excl else np.array([]),
        residual_std=float(np.std(used_resid)),
        n_used=int(len(w_int)),
    )
