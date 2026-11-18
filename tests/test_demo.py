"""Independent analytic, PDF and UI checks for the synthetic packaging demo."""
import numpy as np
import pytest
from pypdf import PdfReader
from PySide6.QtCore import Qt, QTimer

from bruker_nmr.demo import make_demo
from bruker_nmr.export.demo_pdf import write_demo_pdf
from bruker_nmr.ui.main_window import ViewerWindow

IDENTITY = {"version": "0.1.1", "source_commit": "a" * 40}


def test_demo_independent_value_and_immutable():
    demo = make_demo()
    # Scalar truth expressed with a different Lorentzian algebra from the generator.
    k = 2700
    x = -1 + 13 * k / 8191
    truth = sum(h / (1 + ((x - c) / w)**2) for c, h, w in
                [(1.2, .65, .025), (3.6, 1, .035), (7.1, .8, .02), (7.3, .55, .02)])
    assert demo.amplitude[k] == pytest.approx(truth, rel=1e-12)
    assert demo.ppm[k] == pytest.approx(x)
    assert np.isfinite(demo.amplitude).all()
    assert demo.ppm.flags.writeable is False
    assert demo.amplitude.flags.writeable is False


@pytest.mark.parametrize("points", [0, 255, 65537, True, 8192.0])
def test_invalid_demo_size(points):
    with pytest.raises(ValueError):
        make_demo(points)


def test_pdf_structure_and_render(tmp_path):
    import pymupdf
    path = tmp_path / "synthetic.pdf"
    write_demo_pdf(path, make_demo(), IDENTITY)
    pdf = PdfReader(path)
    assert len(pdf.pages) == 1
    page = pdf.pages[0]
    assert float(page.mediabox.width) == pytest.approx(841.8898, abs=0.5)
    assert float(page.mediabox.height) == pytest.approx(595.2756, abs=0.5)
    assert "Chemical shift" in page.extract_text()
    assert "No experimental data" in page.extract_text()
    with pymupdf.open(path) as document:
        image = document[0].get_pixmap()
        pixels = np.frombuffer(image.samples, dtype=np.uint8)
        assert np.count_nonzero(pixels < 200) > 5000


def test_window_export_cancel_and_close(qtbot, monkeypatch, tmp_path):
    window = ViewerWindow(IDENTITY)
    qtbot.addWidget(window)
    window.show()
    assert window.plot.getPlotItem().getViewBox().state["xInverted"]
    monkeypatch.setattr("bruker_nmr.ui.main_window.QFileDialog.getSaveFileName",
                        lambda *a, **k: ("", ""))
    def cancel():
        try:
            assert not window.pdf_dialog.save_pdf()
        finally:
            window.pdf_dialog.reject()
    QTimer.singleShot(50,cancel)
    qtbot.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr("bruker_nmr.ui.main_window.QFileDialog.getSaveFileName",
                        lambda *a, **k: (str(tmp_path / "demo"), "PDF"))
    def save():
        try:
            assert window.pdf_dialog.save_pdf()
        finally:
            window.pdf_dialog.reject()
    QTimer.singleShot(50,save)
    qtbot.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
    assert (tmp_path / "demo.pdf").is_file()
    window.close()
    assert not window.isVisible()


def test_export_write_failure(qtbot, monkeypatch, tmp_path):
    window = ViewerWindow(IDENTITY)
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr("bruker_nmr.ui.main_window.QFileDialog.getSaveFileName",
                        lambda *a, **k: (str(tmp_path / "missing" / "demo.pdf"), "PDF"))
    monkeypatch.setattr("bruker_nmr.ui.main_window.QMessageBox.warning",
                        lambda *args: errors.append(args[2]))
    def fail():
        try:
            assert not window.pdf_dialog.save_pdf()
        finally:
            window.pdf_dialog.reject()
    QTimer.singleShot(50,fail)
    window.export_pdf()
    assert len(errors) == 1
