"""Piezas compartidas de la GUI: estado, lienzo matplotlib, worker en hilo, formularios."""
from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QLineEdit, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core.config import AppSettings, MolecfitConfig, builtin_profiles


@dataclass
class AppState:
    settings: AppSettings = field(default_factory=AppSettings.load)
    config: MolecfitConfig = field(default_factory=lambda: next(iter(builtin_profiles().values())))
    files: list[str] = field(default_factory=list)      # FITS de entrada
    checked: set[str] = field(default_factory=set)
    current: str | None = None                          # archivo seleccionado
    output_dir: str = ""
    normalize: bool = True
    results: dict = field(default_factory=dict)         # src -> MolecfitResult


class Signals(QObject):
    """Bus de eventos simple entre pestañas."""
    files_changed = Signal()
    current_changed = Signal(str)
    config_changed = Signal()
    result_ready = Signal(str)


class PlotCanvas(QWidget):
    def __init__(self, parent=None, figsize=(8, 4), toolbar=True):
        super().__init__(parent)
        self.figure = Figure(figsize=figsize, layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if toolbar:
            lay.addWidget(NavigationToolbar2QT(self.canvas, self))
        lay.addWidget(self.canvas)

    def draw(self):
        self.canvas.draw_idle()


class Worker(QObject):
    """Ejecuta ``fn(item, log, cancel)`` para cada item en un hilo aparte."""
    log = Signal(str)
    progress = Signal(int, int, str)
    item_done = Signal(str, object)
    finished = Signal()

    def __init__(self, items, fn):
        super().__init__()
        self.items = list(items)
        self.fn = fn
        self.cancel_event = threading.Event()

    def run(self):
        n = len(self.items)
        for i, it in enumerate(self.items, 1):
            if self.cancel_event.is_set():
                self.log.emit("Cancelado por el usuario.")
                break
            self.progress.emit(i - 1, n, str(it))
            try:
                res = self.fn(it, self.log.emit, self.cancel_event)
            except Exception as exc:  # noqa: BLE001 - se informa en el log
                self.log.emit(f"Error en {it}: {exc}\n{traceback.format_exc()}")
                res = exc
            self.item_done.emit(str(it), res)
        self.progress.emit(n, n, "")
        self.finished.emit()


def start_worker(owner, items, fn, on_log, on_progress, on_item, on_finished) -> Worker:
    thread = QThread(owner)
    worker = Worker(items, fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.log.connect(on_log)
    worker.progress.connect(on_progress)
    worker.item_done.connect(on_item)
    worker.finished.connect(on_finished)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    owner._worker_ref = (thread, worker)  # evitar recolección
    return worker


class FormBinder:
    """Crea campos a partir de una especificación y los sincroniza con un dataclass.

    spec: lista de (atributo, etiqueta, tipo, opciones) con tipo en
    {'float', 'int', 'bool', 'str', 'choice'}.
    """

    def __init__(self):
        self.widgets: dict[str, tuple[str, QWidget]] = {}

    def group(self, title: str, spec) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for attr, label, kind, opts in spec:
            w = self._make(kind, opts or {})
            if opts and opts.get("tip"):
                w.setToolTip(opts["tip"])
            self.widgets[attr] = (kind, w)
            form.addRow(label, w)
        return box

    @staticmethod
    def _make(kind, opts):
        if kind == "bool":
            return QCheckBox()
        if kind == "int":
            w = QSpinBox()
            w.setRange(opts.get("min", 0), opts.get("max", 1000))
            return w
        if kind == "choice":
            w = QComboBox()
            w.addItems(opts["items"])
            w.setEditable(opts.get("editable", False))
            return w
        w = QLineEdit()
        if kind == "float":
            v = QDoubleValidator()
            v.setNotation(QDoubleValidator.Notation.ScientificNotation)
            w.setValidator(v)
        return w

    def load(self, obj):
        for attr, (kind, w) in self.widgets.items():
            val = getattr(obj, attr)
            if kind == "bool":
                w.setChecked(bool(val))
            elif kind == "int":
                w.setValue(int(val))
            elif kind == "choice":
                i = w.findText(str(val))
                if i >= 0:
                    w.setCurrentIndex(i)
                else:
                    w.setEditText(str(val))
            else:
                w.setText(repr(val) if kind == "float" else str(val))

    def store(self, obj):
        errors = []
        for attr, (kind, w) in self.widgets.items():
            try:
                if kind == "bool":
                    val = w.isChecked()
                elif kind == "int":
                    val = w.value()
                elif kind == "choice":
                    val = w.currentText()
                elif kind == "float":
                    val = float(w.text().replace(",", "."))
                else:
                    val = w.text().strip()
                setattr(obj, attr, val)
            except ValueError:
                errors.append(attr)
        return errors


def hline():
    from PySide6.QtWidgets import QFrame
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    return f


ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight
