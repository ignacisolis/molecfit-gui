"""Pestaña Normalización: parámetros del spline cúbico natural y vista previa."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core.fits_io import read_spectrum
from ..core.normalize import fit_continuum
from .common import AppState, PlotCanvas, Signals


class NormalizeTab(QWidget):
    def __init__(self, state: AppState, sig: Signals):
        super().__init__()
        self.state, self.sig = state, sig
        n = state.config.normalization

        self.enable = QCheckBox("Normalizar antes de Molecfit")
        self.enable.setChecked(state.normalize)
        self.enable.toggled.connect(lambda v: setattr(state, "normalize", v))

        self.low = QDoubleSpinBox()
        self.low.setRange(0, 20)
        self.low.setSingleStep(0.1)
        self.high = QDoubleSpinBox()
        self.high.setRange(0, 20)
        self.high.setSingleStep(0.1)
        self.niter = QSpinBox()
        self.niter.setRange(1, 200)
        self.knots = QSpinBox()
        self.knots.setRange(2, 500)
        self.low.setToolTip("Rechazo por debajo del ajuste, en unidades de σ (líneas de absorción)")
        self.high.setToolTip("Rechazo por encima del ajuste, en σ (0 = no rechazar; útil para rayos cósmicos)")
        self.knots.setToolTip("Número de knots del spline (controla la suavidad del continuo)")
        self._load(n)

        box = QGroupBox("Spline cúbico natural con rechazo σ iterativo")
        form = QFormLayout(box)
        form.addRow("low_reject [σ]", self.low)
        form.addRow("high_reject [σ]", self.high)
        form.addRow("Iteraciones", self.niter)
        form.addRow("Knots", self.knots)

        b_prev = QPushButton("Vista previa (archivo seleccionado)")
        b_prev.clicked.connect(self.preview)
        b_thesis = QPushButton("Valores de la tesis (15/15)")
        b_thesis.setToolTip("15 knots y 15 iteraciones, como en la Fig. 2.1 de la tesis")
        b_thesis.clicked.connect(lambda: (self.knots.setValue(15), self.niter.setValue(15)))
        b_pipe = QPushButton("Valores del pipeline (3/10)")
        b_pipe.setToolTip("low_rej=1.8, high_rej=0, 10 iteraciones, 3 knots (molecfit_pipeline_2.py)")
        b_pipe.clicked.connect(self._pipeline_defaults)
        self.info = QLabel("")

        side = QVBoxLayout()
        side.addWidget(self.enable)
        side.addWidget(box)
        side.addWidget(b_prev)
        side.addWidget(b_pipe)
        side.addWidget(b_thesis)
        side.addWidget(self.info)
        side.addStretch()
        sidew = QWidget()
        sidew.setLayout(side)
        sidew.setMaximumWidth(340)

        self.plot = PlotCanvas()
        lay = QHBoxLayout(self)
        lay.addWidget(sidew)
        lay.addWidget(self.plot, 1)

        for w in (self.low, self.high, self.niter, self.knots):
            (w.valueChanged).connect(self.store)
        sig.current_changed.connect(lambda _f: self.preview(quiet=True) if self.isVisible() else None)
        sig.config_changed.connect(lambda: self._load(self.state.config.normalization))

    def _load(self, n):
        for w, v in ((self.low, n.low_rej), (self.high, n.high_rej), (self.niter, n.niter), (self.knots, n.n_knots)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)

    def _pipeline_defaults(self):
        self.low.setValue(1.8)
        self.high.setValue(0.0)
        self.niter.setValue(10)
        self.knots.setValue(3)

    def store(self):
        n = self.state.config.normalization
        n.low_rej, n.high_rej = self.low.value(), self.high.value()
        n.niter, n.n_knots = self.niter.value(), self.knots.value()

    def showEvent(self, e):
        super().showEvent(e)
        self.preview(quiet=True)

    def preview(self, quiet=False):
        f = self.state.current
        if not f:
            if not quiet:
                QMessageBox.information(self, "Normalización", "Selecciona un archivo en la pestaña Datos.")
            return
        self.store()
        n = self.state.config.normalization
        fig = self.plot.figure
        fig.clear()
        a1, a2 = fig.subplots(2, 1, sharex=True)
        try:
            s = read_spectrum(f)
            r = fit_continuum(s.wave, s.flux, n.low_rej, n.high_rej, n.niter, n.n_knots)
        except Exception as exc:  # noqa: BLE001
            a1.text(0.5, 0.5, str(exc), ha="center", va="center", transform=a1.transAxes)
            self.plot.draw()
            return
        a1.plot(s.wave, s.flux, color="black", lw=0.7, label="observado")
        a1.plot(s.wave, r.continuum, "--", color="red", lw=1.2, label="continuo")
        if r.wave_excluded.size:
            a1.scatter(r.wave_excluded, r.flux_excluded, marker="x", s=8, color="red", alpha=0.5,
                       label=f"rechazados ({r.wave_excluded.size})")
        a1.legend(fontsize=8, loc="lower left")
        a1.set_ylabel("Flujo")
        a1.set_title(os.path.basename(f))
        a2.plot(s.wave, r.normalized_flux, color="black", lw=0.7)
        a2.axhline(1, color="red", lw=0.8)
        a2.set_ylabel("Flujo normalizado")
        a2.set_xlabel("Longitud de onda")
        a2.set_ylim(np.nanpercentile(r.normalized_flux, 0.5) - 0.05, np.nanpercentile(r.normalized_flux, 99.8) + 0.05)
        self.plot.draw()
        self.info.setText(f"σ de residuos del continuo: {r.residual_std:.4f}\n"
                          f"Puntos usados en el último ajuste: {r.n_used}/{len(s.flux)}")
