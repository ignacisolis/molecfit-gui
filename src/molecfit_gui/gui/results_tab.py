"""Pestaña Resultados: espectro corregido, transmisión, parámetros y zonas de exclusión."""
from __future__ import annotations

import os

import numpy as np
from astropy.io import fits
from matplotlib.widgets import SpanSelector
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from ..core.backends import EsorexBackend, StandaloneBackend
from ..core.excludes import merge_ranges, read_ranges, write_ranges
from ..core.pipeline import OutputLayout, auto_exclude, plot_result
from .common import AppState, PlotCanvas, Signals


class ResultsTab(QWidget):
    def __init__(self, state: AppState, sig: Signals, log, rerun):
        super().__init__()
        self.state, self.sig, self.log, self.rerun = state, sig, log, rerun
        self.spans = []

        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self.show_current)
        self.panels = QSpinBox()
        self.panels.setRange(1, 8)
        self.panels.setValue(4)
        self.panels.valueChanged.connect(self.show_current)
        b_scan = QPushButton("Buscar resultados en la salida")
        b_scan.setToolTip("Carga resultados de ejecuciones anteriores (carpeta molecfit/)")
        b_scan.clicked.connect(self.scan_existing)
        b_open = QPushButton("Abrir carpeta")
        b_open.clicked.connect(self.open_folder)
        top = QHBoxLayout()
        top.addWidget(QLabel("Orden:"))
        top.addWidget(self.combo, 1)
        top.addWidget(QLabel("Paneles:"))
        top.addWidget(self.panels)
        top.addWidget(b_scan)
        top.addWidget(b_open)

        self.plot = PlotCanvas(figsize=(10, 8))

        # ---- parámetros ajustados ----
        self.res_label = QLabel("")
        self.res_label.setWordWrap(True)
        self.params = QTableWidget(0, 3)
        self.params.setHorizontalHeaderLabels(["Parámetro", "Valor", "Incert."])
        self.params.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        pbox = QGroupBox("Parámetros del mejor ajuste")
        pl = QVBoxLayout(pbox)
        pl.addWidget(self.res_label)
        pl.addWidget(self.params)

        # ---- exclusiones ----
        self.ex_list = QListWidget()
        self.select_mode = QCheckBox("Seleccionar zonas con el ratón")
        self.select_mode.setToolTip("Arrastra sobre el gráfico para marcar una zona a excluir del ajuste")
        self.select_mode.toggled.connect(self._toggle_spans)
        self.sigma = QDoubleSpinBox()
        self.sigma.setRange(0.1, 20)
        self.sigma.setValue(2.0)
        self.sigma.setToolTip("Umbral de generar_excludes: |flujo − media| > factor · media")
        b_auto = QPushButton("Detectar aberraciones")
        b_auto.clicked.connect(self.auto_detect)
        b_del = QPushButton("Quitar")
        b_del.clicked.connect(self.delete_range)
        b_clear = QPushButton("Limpiar")
        b_clear.clicked.connect(self.clear_ranges)
        self.b_rerun = QPushButton("↻ Re-ejecutar este orden")
        self.b_rerun.clicked.connect(lambda: self.current_src() and self.rerun(self.current_src()))
        ebox = QGroupBox("Zonas de exclusión (µm)")
        el = QVBoxLayout(ebox)
        el.addWidget(self.ex_list)
        el.addWidget(self.select_mode)
        r = QHBoxLayout()
        r.addWidget(QLabel("factor:"))
        r.addWidget(self.sigma)
        r.addWidget(b_auto)
        el.addLayout(r)
        r2 = QHBoxLayout()
        r2.addWidget(b_del)
        r2.addWidget(b_clear)
        el.addLayout(r2)
        el.addWidget(self.b_rerun)

        side = QSplitter(Qt.Orientation.Vertical)
        side.addWidget(pbox)
        side.addWidget(ebox)
        side.setMinimumWidth(320)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.plot)
        split.addWidget(side)
        split.setSizes([900, 340])

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)

        sig.result_ready.connect(self._on_result)

    # ------------------------------------------------------------
    def current_src(self) -> str | None:
        return self.combo.currentData()

    def _refresh_combo(self, select: str | None = None):
        cur = select or self.current_src()
        self.combo.blockSignals(True)
        self.combo.clear()
        for src in sorted(self.state.results, key=lambda s: os.path.basename(s)):
            r = self.state.results[src]
            mark = "✔" if getattr(r, "ok", False) else "✖"
            self.combo.addItem(f"{mark} {os.path.basename(src)}", src)
        if cur:
            i = self.combo.findData(cur)
            if i >= 0:
                self.combo.setCurrentIndex(i)
        self.combo.blockSignals(False)
        self.show_current()

    def _on_result(self, src):
        self._refresh_combo(select=src)

    def layout_out(self) -> OutputLayout:
        return OutputLayout(self.state.output_dir)

    def show_current(self):
        src = self.current_src()
        fig = self.plot.figure
        fig.clear()
        self.params.setRowCount(0)
        self.res_label.setText("")
        if not src:
            self.plot.draw()
            return
        res = self.state.results[src]
        if not getattr(res, "ok", False) or res.wave_um is None:
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5, f"Error:\n{getattr(res, 'message', res)}", ha="center", va="center",
                    wrap=True, transform=ax.transAxes)
            ax.set_axis_off()
            self.plot.draw()
            self._load_ranges(src)
            return
        plot_result(fig, res, os.path.basename(src), self.panels.value())
        self._load_ranges(src)
        self._draw_ranges()
        self._toggle_spans(self.select_mode.isChecked())
        self.plot.draw()
        self._show_params(src, res)

    def _show_params(self, src, res):
        items = [(k, v) for k, v in res.best_fit.items() if not k.endswith("_err")]
        self.params.setRowCount(len(items))
        for i, (k, v) in enumerate(items):
            err = res.best_fit.get(k + "_err", "")
            self.params.setItem(i, 0, QTableWidgetItem(k))
            self.params.setItem(i, 1, QTableWidgetItem(f"{v:.6g}" if isinstance(v, float) else str(v)))
            self.params.setItem(i, 2, QTableWidgetItem(f"{err:.3g}" if isinstance(err, float) and err >= 0 else ""))
        lines = []
        prep = self.layout_out().prepared_path(src)
        if os.path.exists(prep):
            h = fits.getheader(prep)
            cdelt = h.get("CD1_1", h.get("CDELT1"))
            R = res.resolution(cdelt, self.state.config.wlg_to_micron)
            if R:
                lines.append(f"<b>R ≈ {R:,.0f}</b>  (λ_ref / (FWHM_gauss·CDELT), ec. 2.4 de la tesis)")
        if res.transmission is not None and res.flux_corr is not None:
            sat = np.mean(res.transmission < 0.1) * 100
            lines.append(f"Píxeles saturados (T&lt;0.1): {sat:.1f} %")
        self.res_label.setText("<br>".join(lines))

    # ---------------- exclusiones ----------------
    def _load_ranges(self, src):
        self.ex_list.clear()
        for a, b in read_ranges(self.layout_out().exclude_path(src)):
            self.ex_list.addItem(f"{a:.6f} – {b:.6f}")

    def _ranges(self):
        src = self.current_src()
        return read_ranges(self.layout_out().exclude_path(src)) if src else []

    def _save_ranges(self, ranges):
        src = self.current_src()
        if not src:
            return
        write_ranges(self.layout_out().exclude_path(src), merge_ranges(ranges))
        self.show_current()

    def _draw_ranges(self):
        for ax in self.plot.figure.axes:
            for a, b in self._ranges():
                ax.axvspan(a, b, color="orange", alpha=0.25, lw=0)

    def _toggle_spans(self, on):
        for s in self.spans:
            s.set_active(False)
        self.spans = []
        if not on:
            return
        for ax in self.plot.figure.axes:
            self.spans.append(SpanSelector(ax, self._on_span, "horizontal", useblit=True,
                                           props=dict(alpha=0.3, facecolor="orange"), interactive=False))

    def _on_span(self, a, b):
        if abs(b - a) < 1e-9:
            return
        self._save_ranges(self._ranges() + [(a, b)])
        self.log(f"Zona de exclusión añadida: {a:.6f} – {b:.6f} µm")

    def auto_detect(self):
        src = self.current_src()
        if not src:
            return
        rng = auto_exclude(self.state.results[src], self.sigma.value())
        if rng is None:
            self.log("No se detectaron aberraciones con ese umbral.")
            return
        self._save_ranges(self._ranges() + [rng])
        self.log(f"Aberración detectada: {rng[0]:.6f} – {rng[1]:.6f} µm")

    def delete_range(self):
        i = self.ex_list.currentRow()
        if i < 0:
            return
        r = self._ranges()
        del r[i]
        self._save_ranges(r)

    def clear_ranges(self):
        self._save_ranges([])

    # ---------------- utilidades ----------------
    def scan_existing(self):
        if not self.state.output_dir:
            return
        lay = self.layout_out()
        n = 0
        for src in self.state.files:
            wd = lay.workdir(src)
            prep = lay.prepared_path(src)
            if not (os.path.isdir(wd) and os.path.exists(prep)):
                continue
            col = StandaloneBackend.collect if any(f.endswith("_tac.fits") for f in os.listdir(wd)) \
                else EsorexBackend.collect
            try:
                res = col(wd, prep, self.state.config)
            except Exception as exc:  # noqa: BLE001
                self.log(f"No se pudo leer {wd}: {exc}")
                continue
            self.state.results[src] = res
            self.sig.result_ready.emit(src)
            n += 1
        self.log(f"Resultados cargados: {n}")

    def open_folder(self):
        if self.state.output_dir and os.path.isdir(self.state.output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.state.output_dir))
