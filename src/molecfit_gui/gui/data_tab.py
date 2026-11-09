"""Pestaña Datos: carpeta de entrada/salida, lista de órdenes y vista del espectro."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.fits_io import detect_order, list_fits, read_spectrum
from .common import AppState, PlotCanvas, Signals

COLS = ["", "Archivo", "Orden", "Píxeles", "λ mín", "λ máx", "Estado"]


class DataTab(QWidget):
    def __init__(self, state: AppState, sig: Signals):
        super().__init__()
        self.state, self.sig = state, sig

        self.in_edit = QLineEdit(state.settings.last_input_dir)
        self.out_edit = QLineEdit(state.settings.last_output_dir)
        b_in = QPushButton("Carpeta…")
        b_files = QPushButton("Archivos…")
        b_out = QPushButton("Carpeta…")
        b_reload = QPushButton("Recargar")
        b_in.clicked.connect(self.choose_input)
        b_files.clicked.connect(self.choose_files)
        b_out.clicked.connect(self.choose_output)
        b_reload.clicked.connect(lambda: self.load_folder(self.in_edit.text()))
        self.out_edit.editingFinished.connect(self._set_output)

        top = QVBoxLayout()
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Entrada:"))
        r1.addWidget(self.in_edit, 1)
        r1.addWidget(b_in)
        r1.addWidget(b_files)
        r1.addWidget(b_reload)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Salida:"))
        r2.addWidget(self.out_edit, 1)
        r2.addWidget(b_out)
        top.addLayout(r1)
        top.addLayout(r2)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (0, 2, 3, 4, 5, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.itemChanged.connect(self._on_check)

        b_all = QPushButton("Marcar todos")
        b_none = QPushButton("Desmarcar")
        b_all.clicked.connect(lambda: self._check_all(True))
        b_none.clicked.connect(lambda: self._check_all(False))
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.table)
        rb = QHBoxLayout()
        rb.addWidget(b_all)
        rb.addWidget(b_none)
        rb.addStretch()
        ll.addLayout(rb)

        self.plot = PlotCanvas()
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(left)
        split.addWidget(self.plot)
        split.setSizes([300, 400])

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)

        sig.result_ready.connect(self._mark_done)
        if state.settings.last_input_dir and os.path.isdir(state.settings.last_input_dir):
            self.load_folder(state.settings.last_input_dir)

    # ------------------------------------------------------------
    def choose_input(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta con espectros FITS", self.in_edit.text())
        if d:
            self.load_folder(d)

    def choose_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Espectros FITS", self.in_edit.text(),
                                             "FITS (*.fits *.fit *.fts)")
        if fs:
            self.in_edit.setText(os.path.dirname(fs[0]))
            self.set_files(sorted(fs, key=lambda p: int(detect_order(p) or 0)))

    def choose_output(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de salida", self.out_edit.text())
        if d:
            self.out_edit.setText(d)
            self._set_output()

    def _set_output(self):
        self.state.output_dir = self.out_edit.text().strip()
        self.state.settings.last_output_dir = self.state.output_dir
        self.state.settings.save()

    def load_folder(self, d: str):
        if not d or not os.path.isdir(d):
            return
        self.in_edit.setText(d)
        self.state.settings.last_input_dir = d
        if not self.out_edit.text().strip():
            self.out_edit.setText(os.path.join(d, "molecfit_gui_out"))
        self._set_output()
        self.set_files(list_fits(d))

    def set_files(self, files):
        self.state.files = list(files)
        self.state.checked = set(files)
        self.table.blockSignals(True)
        self.table.setRowCount(len(files))
        for r, f in enumerate(files):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(os.path.basename(f)))
            self.table.setItem(r, 2, QTableWidgetItem(f"m{detect_order(f)}" if detect_order(f) else "—"))
            try:
                s = read_spectrum(f)
                vals = [str(len(s.flux)), f"{np.nanmin(s.wave):.2f}", f"{np.nanmax(s.wave):.2f}", ""]
            except Exception as exc:  # noqa: BLE001
                vals = ["?", "?", "?", f"error: {exc}"]
            for c, v in enumerate(vals, 3):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.blockSignals(False)
        self.sig.files_changed.emit()
        if files:
            self.table.selectRow(0)

    def _check_all(self, on: bool):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _on_check(self, item):
        if item.column() != 0:
            return
        f = self.state.files[item.row()]
        if item.checkState() == Qt.CheckState.Checked:
            self.state.checked.add(f)
        else:
            self.state.checked.discard(f)

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        f = self.state.files[rows[0].row()]
        self.state.current = f
        self.sig.current_changed.emit(f)
        self._plot(f)

    def _plot(self, f):
        fig = self.plot.figure
        fig.clear()
        ax = fig.add_subplot(111)
        try:
            s = read_spectrum(f)
            ax.plot(s.wave, s.flux, lw=0.7, color="black")
            ax.set_xlabel("Longitud de onda (unidades del header)")
            ax.set_ylabel("Flujo")
            ax.set_title(os.path.basename(f))
        except Exception as exc:  # noqa: BLE001
            ax.text(0.5, 0.5, f"No se pudo leer:\n{exc}", ha="center", va="center", transform=ax.transAxes)
        self.plot.draw()

    def _mark_done(self, src: str):
        if src not in self.state.files:
            return
        r = self.state.files.index(src)
        res = self.state.results.get(src)
        txt = "✔ corregido" if getattr(res, "ok", False) else "✖ error"
        self.table.setItem(r, 6, QTableWidgetItem(txt))
