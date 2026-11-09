"""Pestaña Molecfit: backend, perfil de instrumento, parámetros y ejecución por lotes."""
from __future__ import annotations

import copy
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.backends import make_backend
from ..core.config import MolecfitConfig, Molecule, list_profiles, save_profile
from ..core.pipeline import process_file
from .common import AppState, FormBinder, Signals, start_worker

MOLECULES = ["H2O", "CO2", "CH4", "O2", "O3", "CO", "N2O", "N2", "NO", "NO2", "HNO3", "OCS", "NH3", "SO2"]

SPEC_INPUT = [
    ("wlg_to_micron", "Factor λ → µm", "float", {"tip": "1e-4 si el header está en Å; 1e-3 si está en nm"}),
    ("wavelength_frame", "Marco de λ", "choice", {"items": ["VAC", "AIR"]}),
    ("transmission", "Espectro de transmisión", "bool", None),
    ("default_error", "Error relativo por defecto", "float", None),
]
SPEC_FIT = [
    ("ftol", "FTOL (χ² relativo)", "float", None),
    ("xtol", "XTOL (parámetros)", "float", None),
    ("fit_continuum", "Ajustar continuo", "bool", None),
    ("continuum_n", "Grado polinomio continuo", "int", {"max": 10}),
    ("continuum_const", "Constante inicial continuo", "float", None),
    ("fit_wlc", "Ajustar solución de λ", "bool", None),
    ("wlc_n", "Grado Chebyshev λ", "int", {"max": 10}),
    ("wlc_const", "Desplazamiento inicial λ", "float", None),
]
SPEC_KERNEL = [
    ("fit_res_box", "Ajustar boxcar", "bool", None),
    ("res_box", "FWHM boxcar [rel. rendija]", "float", None),
    ("fit_res_gauss", "Ajustar gaussiana", "bool", None),
    ("res_gauss", "FWHM gaussiana [pix]", "float", None),
    ("fit_res_lorentz", "Ajustar lorentziana", "bool", None),
    ("res_lorentz", "FWHM lorentziana [pix]", "float", None),
    ("kernmode", "Perfil de Voigt (kernmode)", "bool", None),
    ("kernfac", "Tamaño kernel [FWHM]", "float", None),
    ("varkern", "Kernel crece con λ (varkern)", "bool", None),
]
SPEC_SITE = [
    ("elevation", "Altitud [m]", "float", None),
    ("longitude", "Longitud [°]", "float", None),
    ("latitude", "Latitud [°]", "float", None),
    ("slit_width", "Ancho de rendija [\"]", "float", None),
    ("pix_scale", "Escala de píxel [\"]", "float", None),
    ("reference_atmosphere", "Atmósfera de referencia", "choice",
     {"items": ["equ.fits", "tro.fits", "std.fits"], "editable": True}),
    ("gdas_profile", "Perfil GDAS", "choice", {"items": ["auto", "none", "null"], "editable": True}),
    ("pwv", "PWV [mm] (-1 = no escalar)", "float", None),
]
SPEC_KEYS = [
    ("obsdate_key", "Keyword fecha (MJD)", "str", None),
    ("utc_key", "Keyword UTC [s]", "str", None),
    ("telalt_key", "Keyword altitud telescopio", "str", None),
    ("telalt_value", "  valor si falta [°]", "float", None),
    ("rhum_key", "Keyword humedad", "str", None),
    ("rhum_value", "  valor si falta [%]", "float", None),
    ("pres_key", "Keyword presión", "str", None),
    ("pres_value", "  valor si falta [hPa]", "float", None),
    ("temp_key", "Keyword temperatura", "str", None),
    ("temp_value", "  valor si falta [°C]", "float", None),
    ("m1temp_key", "Keyword temp. M1", "str", None),
    ("m1temp_value", "  valor si falta [°C]", "float", None),
]


class MolecfitTab(QWidget):
    def __init__(self, state: AppState, sig: Signals, log):
        super().__init__()
        self.state, self.sig, self.log = state, sig, log
        self.worker = None

        # ---------- backend ----------
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("EsoRex — Molecfit 4.4.2", "esorex")
        self.backend_combo.addItem("Standalone — Molecfit 1.5.9", "standalone")
        self.backend_combo.setCurrentIndex(1 if state.settings.backend == "standalone" else 0)
        self.backend_combo.currentIndexChanged.connect(self._backend_changed)
        b_check = QPushButton("Verificar instalación")
        b_check.clicked.connect(self.check_backend)
        self.backend_status = QLabel("")
        self.backend_status.setWordWrap(True)

        # ---------- perfiles ----------
        self.profile_combo = QComboBox()
        b_save = QPushButton("Guardar perfil…")
        b_import = QPushButton("Importar…")
        b_export = QPushButton("Exportar…")
        b_save.clicked.connect(self.save_profile)
        b_import.clicked.connect(self.import_profile)
        b_export.clicked.connect(self.export_profile)
        self.profile_combo.activated.connect(self._profile_selected)

        head = QGridLayout()
        head.addWidget(QLabel("Backend:"), 0, 0)
        head.addWidget(self.backend_combo, 0, 1)
        head.addWidget(b_check, 0, 2)
        head.addWidget(self.backend_status, 1, 0, 1, 6)
        head.addWidget(QLabel("Perfil:"), 2, 0)
        head.addWidget(self.profile_combo, 2, 1)
        head.addWidget(b_save, 2, 2)
        head.addWidget(b_import, 2, 3)
        head.addWidget(b_export, 2, 4)
        head.setColumnStretch(5, 1)

        # ---------- parámetros ----------
        self.form = FormBinder()
        self.mol_table = QTableWidget(0, 3)
        self.mol_table.setHorizontalHeaderLabels(["Molécula", "Ajustar", "Columna relativa"])
        self.mol_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        b_add_mol = QPushButton("Añadir")
        b_del_mol = QPushButton("Quitar")
        b_add_mol.clicked.connect(lambda: self._add_mol_row(Molecule("O3", True, 1.0)))
        b_del_mol.clicked.connect(lambda: self.mol_table.removeRow(self.mol_table.currentRow()))
        mol_box = QGroupBox("Moléculas")
        ml = QVBoxLayout(mol_box)
        ml.addWidget(self.mol_table)
        mb = QHBoxLayout()
        mb.addWidget(b_add_mol)
        mb.addWidget(b_del_mol)
        mb.addStretch()
        ml.addLayout(mb)

        self.include_edit = QLineEdit()
        self.include_edit.setPlaceholderText("vacío = todo el espectro;  ej: 1.105-1.112, 1.265-1.270")
        inc_box = QGroupBox("Rangos de ajuste (µm)")
        il = QVBoxLayout(inc_box)
        il.addWidget(self.include_edit)
        il.addWidget(QLabel("El manual recomienda rangos estrechos con líneas telúricas no saturadas."))

        self.extra_edit = QPlainTextEdit()
        self.extra_edit.setPlaceholderText("Parámetros adicionales, uno por línea: CLAVE=valor\n"
                                           "(se pasan tal cual al .rc o al .par)")
        self.extra_edit.setMaximumHeight(90)
        extra_box = QGroupBox("Avanzado")
        QVBoxLayout(extra_box).addWidget(self.extra_edit)

        grid = QGridLayout()
        grid.addWidget(mol_box, 0, 0)
        grid.addWidget(self.form.group("Datos de entrada", SPEC_INPUT), 1, 0)
        grid.addWidget(inc_box, 2, 0)
        grid.addWidget(self.form.group("Ajuste", SPEC_FIT), 0, 1)
        grid.addWidget(self.form.group("Kernel (LSF)", SPEC_KERNEL), 1, 1, 2, 1)
        grid.addWidget(self.form.group("Observatorio y atmósfera", SPEC_SITE), 0, 2)
        grid.addWidget(self.form.group("Keywords del header", SPEC_KEYS), 1, 2, 2, 1)
        grid.addWidget(extra_box, 3, 0, 1, 3)
        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        # ---------- ejecución ----------
        self.b_run_sel = QPushButton("▶ Ejecutar seleccionado")
        self.b_run_all = QPushButton("▶▶ Ejecutar marcados")
        self.b_cancel = QPushButton("■ Cancelar")
        self.b_cancel.setEnabled(False)
        self.b_run_sel.clicked.connect(lambda: self.run(selected_only=True))
        self.b_run_all.clicked.connect(lambda: self.run(selected_only=False))
        self.b_cancel.clicked.connect(self.cancel)
        self.progress = QProgressBar()
        self.progress_label = QLabel("")
        run = QHBoxLayout()
        run.addWidget(self.b_run_sel)
        run.addWidget(self.b_run_all)
        run.addWidget(self.b_cancel)
        run.addWidget(self.progress, 1)
        run.addWidget(self.progress_label)

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(scroll, 1)
        lay.addLayout(run)

        self._refresh_profiles(select=state.config.profile_name)
        self.load_config(state.config)

    # ---------------- config <-> widgets ----------------
    def _add_mol_row(self, m: Molecule):
        r = self.mol_table.rowCount()
        self.mol_table.insertRow(r)
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(MOLECULES)
        combo.setCurrentText(m.name)
        self.mol_table.setCellWidget(r, 0, combo)
        chk = QCheckBox()
        chk.setChecked(m.fit)
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(chk)
        self.mol_table.setCellWidget(r, 1, w)
        sp = QDoubleSpinBox()
        sp.setDecimals(3)
        sp.setRange(0, 100)
        sp.setSingleStep(0.01)
        sp.setValue(m.rel_col)
        self.mol_table.setCellWidget(r, 2, sp)

    def load_config(self, cfg: MolecfitConfig):
        self.form.load(cfg)
        self.mol_table.setRowCount(0)
        for m in cfg.molecules:
            self._add_mol_row(m)
        self.include_edit.setText(", ".join(f"{a:g}-{b:g}" for a, b in cfg.wave_include))
        self.extra_edit.setPlainText("\n".join(f"{k}={v}" for k, v in cfg.expert_extra.items()))

    def store_config(self) -> bool:
        cfg = self.state.config
        bad = self.form.store(cfg)
        mols = []
        for r in range(self.mol_table.rowCount()):
            name = self.mol_table.cellWidget(r, 0).currentText().strip()
            fit = self.mol_table.cellWidget(r, 1).findChild(QCheckBox).isChecked()
            rel = self.mol_table.cellWidget(r, 2).value()
            if name:
                mols.append(Molecule(name, fit, rel))
        cfg.molecules = mols
        try:
            cfg.wave_include = parse_ranges(self.include_edit.text())
        except ValueError:
            bad.append("rangos de ajuste")
        extra = {}
        for line in self.extra_edit.toPlainText().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                extra[k.strip()] = v.strip()
        cfg.expert_extra = extra
        if bad:
            QMessageBox.warning(self, "Parámetros", "Valores no válidos en: " + ", ".join(bad))
            return False
        if not mols:
            QMessageBox.warning(self, "Parámetros", "Debes incluir al menos una molécula.")
            return False
        return True

    # ---------------- perfiles ----------------
    def _refresh_profiles(self, select: str | None = None):
        self.profiles = list_profiles()
        self.profile_combo.clear()
        self.profile_combo.addItems(list(self.profiles))
        if select and select in self.profiles:
            self.profile_combo.setCurrentText(select)

    def _profile_selected(self, _i):
        name = self.profile_combo.currentText()
        cfg = copy.deepcopy(self.profiles[name])
        self.state.config = cfg
        self.load_config(cfg)
        self.sig.config_changed.emit()
        self.log(f"Perfil cargado: {name}")

    def save_profile(self):
        if not self.store_config():
            return
        name, ok = QInputDialog.getText(self, "Guardar perfil", "Nombre del perfil:",
                                        text=self.state.config.profile_name)
        if ok and name.strip():
            self.state.config.profile_name = name.strip()
            p = save_profile(self.state.config)
            self._refresh_profiles(select=name.strip())
            self.log(f"Perfil guardado en {p}")

    def import_profile(self):
        f, _ = QFileDialog.getOpenFileName(self, "Importar perfil", "", "Perfil JSON (*.json)")
        if f:
            cfg = MolecfitConfig.load(f)
            save_profile(cfg)
            self._refresh_profiles(select=cfg.profile_name)
            self._profile_selected(0)

    def export_profile(self):
        if not self.store_config():
            return
        f, _ = QFileDialog.getSaveFileName(self, "Exportar perfil", self.state.config.profile_name + ".json",
                                           "Perfil JSON (*.json)")
        if f:
            self.state.config.save(f)

    # ---------------- backend ----------------
    def _backend_changed(self, _i):
        self.state.settings.backend = self.backend_combo.currentData()
        self.state.settings.save()
        self.backend_status.setText("")

    def check_backend(self):
        ok, msg = make_backend(self.state.settings).check()
        color = "green" if ok else "#b00"
        self.backend_status.setText(f"<span style='color:{color}'>{'✔' if ok else '✖'} {msg}</span>")
        self.log(msg)
        return ok

    # ---------------- ejecución ----------------
    def run(self, selected_only: bool):
        if self.worker is not None:
            return
        if not self.store_config():
            return
        if not self.state.output_dir:
            QMessageBox.warning(self, "Molecfit", "Define una carpeta de salida en la pestaña Datos.")
            return
        if selected_only:
            files = [self.state.current] if self.state.current else []
        else:
            files = [f for f in self.state.files if f in self.state.checked]
        if not files:
            QMessageBox.information(self, "Molecfit", "No hay archivos para procesar.")
            return
        if not self.check_backend():
            QMessageBox.warning(self, "Molecfit", self.backend_status.text().split("</span>")[0].split("'>")[-1]
                                + "\n\nRevisa Configuración → Ejecutables.")
            return
        cfg = copy.deepcopy(self.state.config)
        settings = copy.deepcopy(self.state.settings)
        out, norm = self.state.output_dir, self.state.normalize

        def job(src, log, cancel):
            return process_file(src, cfg, settings, out, normalize=norm, log=log, cancel=cancel)

        self._set_running(True)
        self.progress.setRange(0, len(files))
        self.worker = start_worker(self, files, job, self.log, self._on_progress, self._on_item, self._on_finished)

    def rerun(self, src: str):
        """Re-ejecuta un solo archivo (usado desde Resultados tras añadir exclusiones)."""
        prev = self.state.current
        self.state.current = src
        self.run(selected_only=True)
        self.state.current = prev

    def cancel(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.log("Cancelando… (se detiene al terminar la salida del proceso actual)")

    def _set_running(self, on: bool):
        self.b_run_sel.setEnabled(not on)
        self.b_run_all.setEnabled(not on)
        self.b_cancel.setEnabled(on)

    def _on_progress(self, i, n, name):
        self.progress.setValue(i)
        self.progress_label.setText(f"{i}/{n}  {os.path.basename(name)}")

    def _on_item(self, src, res):
        if isinstance(res, Exception):
            from ..core.backends import MolecfitResult
            res = MolecfitResult(False, message=str(res))
        self.state.results[src] = res
        self.sig.result_ready.emit(src)

    def _on_finished(self):
        self.worker = None
        self._set_running(False)
        n_ok = sum(1 for r in self.state.results.values() if getattr(r, "ok", False))
        self.log(f"Proceso terminado. Correctos (acumulado): {n_ok}")


def parse_ranges(text: str) -> list[tuple[float, float]]:
    out = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        a, b = chunk.replace(" ", "").split("-", 1) if chunk.count("-") == 1 else chunk.split()
        out.append((float(a), float(b)))
    return out
