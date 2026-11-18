"""Actual Qt preview/save/cancel/overwrite and unsaved-document export behavior."""

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from bruker_nmr.domain.document import ViewState
from bruker_nmr.export.snapshot import ViewSnapshot
from bruker_nmr.ui.pdf import PdfExportDialog
from tests.test_peaks_state import fixture
from tests.test_viewer import window


@pytest.fixture
def ui(qtbot):
    result = window(qtbot)
    result.document = fixture()
    result._render_document()
    result.confirm_discard = lambda: True
    return result


def dialog(ui, qtbot):
    result = PdfExportDialog(ui, ViewSnapshot(ui.document.current))
    qtbot.addWidget(result)
    result.show()
    qtbot.waitExposed(result)
    return result


def test_current_view_export_action_caption_save_and_return(ui, qtbot, monkeypatch, tmp_path):
    ui.document.appearance(trace_color="#006633", line_width=0.6)
    ui.commit_view(ViewState(6.8, 7.2, -0.1, 1.1))
    before = ui.document.current
    destination = tmp_path / "new-name"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), "PDF"))

    def respond():
        preview = ui.pdf_dialog
        assert preview is QApplication.activeModalWidget()
        assert preview.page.snapshot.state is before and not preview.caption.text()
        preview.caption.setText("Synthetic ЯМР δ −0.1")
        assert preview.save_pdf()
        assert (
            "Synthetic ЯМР δ −0.1"
            in PdfReader(destination.with_suffix(".pdf")).pages[0].extract_text()
        )
        preview.reject()
        assert preview.page is None and preview.preview.image.isNull()

    QTimer.singleShot(60, respond)
    ui.export_action.trigger()
    assert ui.document.current is before and ui.document.integration_modified
    assert ui.pdf_dialog is None and ui.isVisible()


def test_cancel_invalid_caption_and_source_change_keep_captured_state(
    ui, qtbot, monkeypatch, tmp_path
):
    preview = dialog(ui, qtbot)
    captured = preview.snapshot.state
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert not preview.save_pdf() and not preview.saved
    preview.caption.setText("M" * 240)
    preview.rebuild_preview()
    assert not preview.save_button.isEnabled() and preview.page is None
    assert "too long" in preview.note.text()
    preview.caption.setText("Captured synthetic state")
    preview.rebuild_preview()
    ui.document.appearance(trace_color="#800033")
    ui.document.navigate(ViewState(1, 2, -1, 2))
    assert preview.page.snapshot.state is captured
    assert preview.page.ax.get_xlim() == (captured.view.x_max, captured.view.x_min)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "captured.pdf"), "PDF")
    )
    assert preview.save_pdf()
    preview.reject()
    assert preview.snapshot is None


def test_existing_pdf_replacement_defaults_to_no_then_explicit_yes(
    ui, qtbot, monkeypatch, tmp_path
):
    preview = dialog(ui, qtbot)
    target = tmp_path / "original.pdf"
    target.write_bytes(b"keep original")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PDF"))
    prompts = []

    def refuse(*args):
        prompts.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", refuse)
    assert not preview.save_pdf() and target.read_bytes() == b"keep original"
    assert prompts[0][-1] == QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    assert preview.save_pdf() and len(PdfReader(target).pages) == 1
    preview.reject()


def test_write_error_retains_preview_and_spectrum(ui, qtbot, monkeypatch, tmp_path):
    ui.document.appearance(trace_color="#800033")
    before = ui.document.current
    preview = dialog(ui, qtbot)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(tmp_path / "missing" / "output.pdf"), "PDF"),
    )
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: errors.append(a[2]))
    assert not preview.save_pdf() and not preview.saved
    assert errors and "remains open" in errors[0]
    assert preview.page is not None and ui.document.current is before
    preview.reject()


def test_discard_export_saves_current_pdf_and_keeps_editable_document(
    ui, qtbot, monkeypatch, tmp_path
):
    ui.document.appearance(trace_color="#800033")
    before = ui.document.current
    target = tmp_path / "before-discard.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PDF"))

    def save_and_close():
        preview = ui.pdf_dialog
        assert preview.save_pdf()
        preview.reject()

    def choose_export():
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        button = next(b for b in box.buttons() if "PDF" in b.text())
        assert button.isEnabled()
        QTimer.singleShot(60, save_and_close)
        button.click()

    QTimer.singleShot(30, choose_export)
    from bruker_nmr.ui.main_window import ViewerWindow

    assert not ViewerWindow.confirm_discard(ui)
    assert target.is_file() and ui.document.current is before and ui.isVisible()


def test_loading_or_unapplied_processing_preview_blocks_export(ui):
    ui.open_baseline()
    assert not ui.export_pdf()
    assert ui.baseline_dialog.isVisible()
    ui.baseline_dialog.reject()
    ui._set_loading(True)
    assert not ui.export_button.isEnabled() and not ui.export_action.isEnabled()
    assert not ui.export_pdf()
    ui._set_loading(False)


def test_preview_zoom_changes_display_only_and_keeps_window_on_screen(ui, qtbot):
    preview = dialog(ui, qtbot)
    geometry = preview.page.geometry
    preview.zoom.setCurrentText("200%")
    qtbot.wait(30)
    assert preview.scroll.horizontalScrollBar().maximum() > 0
    assert preview.page.geometry is geometry
    available = preview.screen().availableGeometry()
    assert preview.frameGeometry().width() <= available.width()
    assert preview.frameGeometry().height() <= available.height()
    preview.zoom.setCurrentText("Fit page")
    qtbot.wait(30)
    assert preview.page.geometry is geometry and preview.scroll.widgetResizable()
    preview.reject()


def test_prefilled_header_can_be_erased_without_changing_source_metadata(
    ui, qtbot, monkeypatch, tmp_path
):
    from dataclasses import replace

    from bruker_nmr.domain.document import Document

    header = "Artificial editable header\nCDCl3 | 1H | 300 MHz | NS 64"
    ui.document = Document(replace(ui.document.current.data, print_header=header))
    ui._render_document()
    original = ui.document.current
    preview = dialog(ui, qtbot)
    assert preview.caption.text() == header
    assert "Artificial editable header" not in repr(preview.snapshot)
    preview.caption.clear()
    preview.rebuild_preview()
    assert preview.page.geometry.caption_lines == ()
    target = tmp_path / "erased.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PDF"))
    assert preview.save_pdf()
    assert "Artificial editable header" not in PdfReader(target).pages[0].extract_text()
    assert ui.document.current is original and original.data.print_header == header
    preview.reject()


def test_crowded_integrals_block_save_and_require_view_adjustment(ui, qtbot):
    from tests.test_print_layout import crowded_document

    ui.document = crowded_document(60)
    ui._render_document()
    preview = dialog(ui, qtbot)
    assert preview.page is None and not preview.save_button.isEnabled()
    assert "one readable row" in preview.note.text()
    assert "zoom" in preview.note.text()
    preview.reject()


def test_missing_bundled_font_does_not_abort_prefilled_header_dialog(
    ui, qtbot, monkeypatch, tmp_path
):
    from dataclasses import replace

    import bruker_nmr.export.current_pdf as renderer
    from bruker_nmr.domain.document import Document

    ui.document = Document(
        replace(ui.document.current.data, print_header="Artificial local header\n1H | 300 MHz")
    )
    ui._render_document()
    before = ui.document.current
    monkeypatch.setattr(renderer, "FONT_PATH", tmp_path / "missing-font.ttf")
    preview = dialog(ui, qtbot)
    assert preview.page is None and not preview.save_button.isEnabled()
    assert "complete application folder" in preview.note.text()
    assert "Artificial local header" not in preview.note.text()
    assert ui.document.current is before
    preview.reject()


def test_large_interface_font_keeps_save_accessible_with_scrollable_controls(ui, qtbot):
    from PySide6.QtGui import QFont

    font = QFont(ui.font())
    font.setPointSize(16)
    ui.setFont(font)
    preview = dialog(ui, qtbot)
    preview.resize(360, 160)
    qtbot.wait(20)
    available = preview.screen().availableGeometry()
    assert preview.frameGeometry().width() <= available.width()
    assert preview.frameGeometry().height() <= available.height()
    assert preview.save_button.isVisible()
    assert preview.controls_scroll.verticalScrollBar().maximum() > 0
    assert preview.page is not None
    preview.reject()


def test_grid_toggle_reaches_print_preview_and_vector_pdf(ui, qtbot, monkeypatch):
    import pymupdf

    captured = []

    def inspect_preview(preview):
        captured.append(preview.snapshot.show_grid)
        assert preview.page.ax.get_lines()[0].get_linewidth() == pytest.approx(.8)
        if preview.snapshot.show_grid:
            with pymupdf.open(stream=preview.page.pdf_bytes(), filetype='pdf') as pdf:
                dashes = [d for d in pdf[0].get_drawings() if d["dashes"] != "[] 0"
                          and abs((d.get("width") or 0) - .30) < 1e-3]
                assert any(d["rect"].width > 0 and d["rect"].height == 0 for d in dashes)
                assert any(d["rect"].height > 0 and d["rect"].width == 0 for d in dashes)
        else:
            assert not preview.page.ax.collections
        preview.reject()

    monkeypatch.setattr(PdfExportDialog, 'exec', inspect_preview)
    assert not ui.plot_grid.isVisible()
    assert ui.document.current.trace_color == "#1e0080"
    ui.grid_action.trigger()
    assert ui.plot_grid.isVisible()
    assert len(ui.plot_grid.getData()[0]) >= 4
    ui.export_action.trigger()
    ui.grid_action.trigger()
    assert not ui.plot_grid.isVisible()
    ui.export_action.trigger()
    assert captured == [True, False]
