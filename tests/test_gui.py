"""Prueba de humo de la GUI (sin pantalla): carga datos, ejecuta con esorex falso, muestra resultados."""
import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import make_winered_like  # noqa: E402


def test_gui_end_to_end(tmp_path, fake_esorex, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"warning: {a[1:]}"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: pytest.fail(f"info: {a[1:]}"))
    import matplotlib
    from molecfit_gui.gui.main_window import MainWindow
    matplotlib.rcParams.update({"font.size": 8, "axes.titlesize": 9, "lines.linewidth": 0.8})

    app = QApplication.instance() or QApplication([])
    indir = tmp_path / "in"
    indir.mkdir()
    for o in (43, 44, 45):
        make_winered_like(indir / f"star9_m{o}_sum.fits", order=o, seed=o)

    w = MainWindow()
    w.state.settings.esorex_path = fake_esorex
    w.show()
    w.data_tab.out_edit.setText(str(tmp_path / "out"))
    w.data_tab.load_folder(str(indir))
    assert len(w.state.files) == 3
    assert w.state.current.endswith("m43_sum.fits")

    w.tabs.setCurrentWidget(w.norm_tab)
    w.norm_tab.knots.setValue(5)
    w.norm_tab.preview()
    assert "σ de residuos" in w.norm_tab.info.text()
    assert w.state.config.normalization.n_knots == 5

    w.tabs.setCurrentWidget(w.mf_tab)
    w.mf_tab.run(selected_only=False)
    t0 = time.time()
    while w.mf_tab.worker is not None and time.time() - t0 < 120:
        app.processEvents()
        time.sleep(0.02)
    assert w.mf_tab.worker is None
    assert len(w.state.results) == 3, w.log_view.toPlainText()
    assert all(r.ok for r in w.state.results.values()), w.log_view.toPlainText()

    w.tabs.setCurrentWidget(w.res_tab)
    app.processEvents()
    assert w.res_tab.combo.count() == 3
    assert "R ≈" in w.res_tab.res_label.text()
    w.res_tab.auto_detect()
    w.res_tab._on_span(1.2001, 1.2003)
    assert w.res_tab.ex_list.count() >= 1

    # rerun usa la exclusión
    src = w.res_tab.current_src()
    w.mf_tab.rerun(src)
    while w.mf_tab.worker is not None:
        app.processEvents()
        time.sleep(0.02)
    stem = os.path.splitext(os.path.basename(src))[0]
    rc = (tmp_path / "out" / "molecfit" / stem / "model.rc").read_text()
    assert "WAVE_EXCLUDE=1.200" in rc

    shots = os.environ.get("MFG_SCREENSHOTS")
    if shots:
        for tab in (w.data_tab, w.norm_tab, w.mf_tab, w.res_tab):
            w.tabs.setCurrentWidget(tab)
            app.processEvents()
            w.grab().save(os.path.join(shots, f"{type(tab).__name__}.png"))
    w.close()
