"""Diálogo de ejecutables y entorno."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QWidget)

from ..core.config import AppSettings


def _browse(edit: QLineEdit, parent, directory=False, filt=""):
    def f():
        if directory:
            p = QFileDialog.getExistingDirectory(parent, "Seleccionar carpeta", edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(parent, "Seleccionar archivo", edit.text(), filt)
        if p:
            edit.setText(p)
    return f


def _row(edit, btn):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    h.addWidget(btn)
    return w


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ejecutables y entorno")
        self.settings = settings
        self.esorex = QLineEdit(settings.esorex_path)
        self.bindir = QLineEdit(settings.standalone_bin_dir)
        self.template = QLineEdit(settings.standalone_par_template)
        self.prefix = QLineEdit(settings.command_prefix)
        self.prefix.setPlaceholderText("vacío (Linux/WSL/macOS)  ·  'wsl -e' si la GUI corre en Windows")
        self.omp = QSpinBox()
        self.omp.setRange(0, 256)
        self.omp.setValue(settings.omp_threads)

        b1, b2, b3 = QPushButton("…"), QPushButton("…"), QPushButton("…")
        b1.clicked.connect(_browse(self.esorex, self))
        b2.clicked.connect(_browse(self.bindir, self, directory=True))
        b3.clicked.connect(_browse(self.template, self, filt="Parámetros (*.par)"))

        form = QFormLayout(self)
        form.addRow(QLabel("<b>Molecfit 4.4.2 (EsoRex)</b>"))
        form.addRow("esorex", _row(self.esorex, b1))
        form.addRow("Hilos OpenMP (0 = sin fijar)", self.omp)
        form.addRow(QLabel("<b>Molecfit 1.5.9 (standalone)</b>"))
        form.addRow("Carpeta bin/ (molecfit, calctrans)", _row(self.bindir, b2))
        form.addRow("Plantilla .par (opcional)", _row(self.template, b3))
        form.addRow(QLabel("<b>Entorno</b>"))
        form.addRow("Prefijo de comandos", self.prefix)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.resize(640, 0)

    def accept(self):
        s = self.settings
        s.esorex_path = self.esorex.text().strip() or "esorex"
        s.standalone_bin_dir = self.bindir.text().strip()
        s.standalone_par_template = self.template.text().strip()
        s.command_prefix = self.prefix.text().strip()
        s.omp_threads = self.omp.value()
        s.save()
        super().accept()
