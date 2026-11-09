"""Ventana principal."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (QApplication, QDockWidget, QMainWindow, QMessageBox, QPlainTextEdit,
                               QTabWidget)

from .. import __version__
from .common import AppState, Signals
from .data_tab import DataTab
from .molecfit_tab import MolecfitTab
from .normalize_tab import NormalizeTab
from .results_tab import ResultsTab
from .settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Molecfit GUI {__version__}")
        self.resize(1400, 900)
        self.state = AppState()
        self.sig = Signals()

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(20000)
        self.log_view.setFont(QFont("Monospace", 9))
        dock = QDockWidget("Registro", self)
        dock.setWidget(self.log_view)
        dock.setObjectName("log")
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [160], Qt.Orientation.Vertical)

        self.tabs = QTabWidget()
        self.data_tab = DataTab(self.state, self.sig)
        self.norm_tab = NormalizeTab(self.state, self.sig)
        self.mf_tab = MolecfitTab(self.state, self.sig, self.log)
        self.res_tab = ResultsTab(self.state, self.sig, self.log, self.mf_tab.rerun)
        self.tabs.addTab(self.data_tab, "1 · Datos")
        self.tabs.addTab(self.norm_tab, "2 · Normalización")
        self.tabs.addTab(self.mf_tab, "3 · Molecfit")
        self.tabs.addTab(self.res_tab, "4 · Resultados")
        self.setCentralWidget(self.tabs)
        self.sig.result_ready.connect(lambda _s: self.statusBar().showMessage("Resultado listo", 4000))

        self._menus(dock)
        self.log(f"Molecfit GUI {__version__} — backend: {self.state.settings.backend}")

    def _menus(self, dock):
        m = self.menuBar().addMenu("&Archivo")
        a = QAction("Abrir carpeta de espectros…", self)
        a.triggered.connect(self.data_tab.choose_input)
        m.addAction(a)
        a = QAction("Salir", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        m = self.menuBar().addMenu("&Configuración")
        a = QAction("Ejecutables y entorno…", self)
        a.triggered.connect(self.edit_settings)
        m.addAction(a)
        m.addAction(dock.toggleViewAction())
        m = self.menuBar().addMenu("A&yuda")
        a = QAction("Acerca de", self)
        a.triggered.connect(self.about)
        m.addAction(a)

    def log(self, msg: str):
        self.log_view.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {msg}" if msg and not msg.startswith(" ") else msg)

    def edit_settings(self):
        if SettingsDialog(self.state.settings, self).exec():
            self.log("Ajustes guardados.")
            self.mf_tab.check_backend()

    def about(self):
        QMessageBox.about(self, "Molecfit GUI",
                          f"<b>Molecfit GUI {__version__}</b><br>"
                          "Interfaz para corrección telúrica con Molecfit (4.4.2 vía EsoRex o 1.5.9 standalone) "
                          "y normalización con spline cúbico natural.<br><br>"
                          "Si usas Molecfit en una publicación cita Smette et al. 2015 (A&A 576, A77) "
                          "y Kausch et al. 2015 (A&A 576, A78).")

    def closeEvent(self, e):
        if self.mf_tab.worker is not None:
            if QMessageBox.question(self, "Salir", "Hay un proceso en curso. ¿Cancelar y salir?") != \
                    QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.mf_tab.cancel()
        self.state.settings.save()
        super().closeEvent(e)


def main(argv=None) -> int:
    import matplotlib
    matplotlib.rcParams.update({"font.size": 8, "axes.titlesize": 9, "lines.linewidth": 0.8})
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Molecfit GUI")
    w = MainWindow()
    w.show()
    return app.exec()
