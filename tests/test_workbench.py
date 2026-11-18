"""Workbench interaction, ownership, full-grid pivot and responsive geometry."""

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolBar

from bruker_nmr.domain.document import ViewState
from bruker_nmr.phase_benchmark import analytic
from tests.test_phase import raw_document
from tests.test_viewer import window


@pytest.fixture
def ui(qtbot):
    w = window(qtbot)
    w.confirm_discard = lambda: True
    _, truth, _ = analytic(8192)
    w.document = raw_document(truth)
    w._render_document()
    return w


def test_single_toolbar_embedded_inspector_and_compact_canvas(ui, qtbot):
    assert len([t for t in ui.findChildren(QToolBar) if t.isVisible()]) == 1
    assert ui.main_scroll.height() / ui.height() >= 0.75
    from PySide6.QtWidgets import QPushButton

    assert not any(b.text() == "Close" and b.isVisible() for b in ui.findChildren(QPushButton))
    ui.integrals.show_table()
    qtbot.wait(20)
    assert not ui.integrals.isWindow() and ui.integrals.isVisible()
    assert ui.main_scroll.height() / ui.height() >= 0.60
    ui.peaks.show_table()
    assert ui.workbench.tabs.currentWidget() is ui.peaks
    assert not ui.peaks.isWindow()


def test_phase_navigation_pivot_compare_and_apply_are_transactional(ui, qtbot):
    ui.open_phase()
    p = ui.phase_dialog
    assert not p.isWindow() and QApplication.activeModalWidget() is None
    captured = ui.document.current
    p.controls[0].setValue(15)
    p.preview()
    assert ui.document.current is captured
    p.pivot_line.setValue(7)
    assert p.pivot_ppm.value() == pytest.approx(7, abs=1e-6)
    assert p.phase().pivot == pytest.approx((7 - p.pivot_origin) / p.pivot_span, abs=1e-8)
    target = ViewState(3, 8, -0.2, 2)
    ui.commit_view(target)
    p.compare.setChecked(True)
    assert np.array_equal(ui._phase_preview, p.base.real)
    p.compare.setChecked(False)
    assert not np.array_equal(ui._phase_preview, p.base.real)
    p.apply()
    assert ui.document.current.view == target and not ui.workbench.process.isVisible()
    assert ui._phase_preview is None
    ui.undo()
    assert ui.document.current.data is captured.data


def test_baseline_canvas_anchor_and_dock_close_cancel(ui, qtbot):
    ui.open_baseline()
    p = ui.baseline_dialog
    assert not p.isWindow()
    before = ui.document.current
    p.draw_anchor.setChecked(True)
    a = ui.view_box.mapFromView(__import__("pyqtgraph").Point(11, 0)).x()
    b = ui.view_box.mapFromView(__import__("pyqtgraph").Point(10.5, 0)).x()
    ui.view_box.anchor_created.emit(a, b)
    assert len(p.anchors) == 1 and ui.view_box.anchoring
    a = ui.view_box.mapFromView(__import__("pyqtgraph").Point(9, 0)).x()
    b = ui.view_box.mapFromView(__import__("pyqtgraph").Point(8.5, 0)).x()
    ui.view_box.anchor_created.emit(a, b)
    assert len(p.anchors) == 2 and ui.view_box.anchoring
    ui.workbench.escape()
    assert not ui.view_box.anchoring and p.isVisible()
    assert p.anchors[0].high == pytest.approx(11, abs=1e-6)
    ui.workbench.process.close()
    qtbot.wait(10)
    assert p.document is None and ui._baseline_preview is None
    assert ui.document.current is before


def test_edit_cancels_preview_and_switch_panels_no_stale_widget(ui):
    ui.open_phase()
    p = ui.phase_dialog
    p.controls[0].setValue(23)
    p.preview()
    ui.integrals.create_range(9, 10)
    assert not p.isVisible() and p.document is None
    assert not ui.document.current.data.phase_steps
    ui.open_baseline()
    ui.open_phase()
    ui.open_baseline()
    assert ui.phase_dialog is None or not ui.phase_dialog.isVisible()
    ui.baseline_dialog.reject()


def test_appearance_is_live_and_undo_syncs_the_controls(ui, qtbot):
    from PySide6.QtWidgets import QCheckBox

    ui.appearance_dialog()
    p = ui.workbench.appearance
    assert not p.isWindow() and QApplication.activeModalWidget() is None
    crosses = next(c for c in p.findChildren(QCheckBox) if "markers" in c.text())
    before = ui.document.current
    crosses.setChecked(not before.peaks.markers_visible)
    assert ui.document.current.peaks.markers_visible != before.peaks.markers_visible
    ui.undo()
    assert ui.document.current is before
    assert crosses.isChecked() == before.peaks.markers_visible
    assert ui.workbench.inspector.isVisible()
    ui.appearance_dialog()
    assert ui.workbench.appearance is p and ui.document.current is before


def test_delete_respects_numeric_focus_and_escape_cancels_preview(ui, qtbot):
    ui.document.add_peak(9.6, 0.1)
    ui._render_document()
    ui.peaks.show_table()
    ui.peaks.table.setFocus()
    qtbot.keyClick(ui.peaks.table, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: not ui.document.current.peaks.records)
    assert not ui.document.current.peaks.records
    ui.undo()
    ui.open_phase()
    ui.phase_dialog.controls[0].setFocus()
    ui.workbench.delete_selected()
    assert len(ui.document.current.peaks.records) == 1
    qtbot.keyClick(ui.phase_dialog.controls[0], Qt.Key.Key_Escape)
    assert not ui.workbench.process.isVisible()


def test_twenty_mixed_edits_navigation_undo_redo_with_inspector(ui):
    ui.peaks.show_table()
    source = ui.document.current.data
    for i in range(20):
        ui.document.appearance(
            line_width=0.5 + i * 0.02, trace_color="#164e80" if i % 2 else "#802c90"
        )
        last = ui.document.current
        ui.commit_view(ViewState(1 + i * 0.01, 10, -0.2, 2))
    assert ui.document.current.data is source
    for _ in range(20):
        ui.undo()
    for _ in range(20):
        ui.redo()
    assert ui.document.current is last
    ui.reset()
    assert ui.document.current.data is source


def test_double_click_integral_band_edits_selected_without_opening_table(ui, qtbot, monkeypatch):
    from PySide6.QtCore import QPoint

    ui.integrals.create_range(9, 10)
    qtbot.waitUntil(lambda: bool(ui.integral_band.hit_boxes))
    box, id = ui.integral_band.hit_boxes[0]
    selected = []
    monkeypatch.setattr(ui.integrals, "edit_dialog", lambda: selected.append(ui.integrals.selected))
    qtbot.mouseDClick(
        ui.integral_band,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(box.center().x()), round(box.center().y())),
    )
    assert selected == [id]
    assert not ui.workbench.inspector.isVisible()


def test_escape_restores_uncommitted_gesture_and_repeated_phase_open_keeps_preview(ui, qtbot):
    ui.document.add_peak(9.6, .1)
    ui.document.add_integral(10, 9)
    ui._render_document()
    assert ui.peaks.selected and ui.integrals.selected
    before = ui.document.current
    ui.view_box.setXRange(3, 4, padding=0)
    ui.view_box._wheel.start()
    ui._pointer_pressed = True
    ui.plot.setFocus()
    qtbot.keyClick(ui.plot.viewport(), Qt.Key.Key_Escape)
    assert ui.document.current is before
    assert ui.view_box.snapshot() == before.view
    assert not ui._pointer_pressed and not ui.view_box._wheel.isActive()
    assert ui.peaks.selected is None and ui.integrals.selected is None
    assert not ui.integrals.table.selectedItems()
    ui.open_phase()
    panel = ui.phase_dialog
    panel.controls[0].setValue(10)
    panel.preview()
    ui.open_phase()
    assert ui.phase_dialog is panel and panel.valid() and panel.isVisible()
    assert ui._phase_preview is not None
    panel.reject()


def test_collapsed_scientific_notes_keep_low_confidence_visible(ui, qtbot):
    ui.open_phase()
    panel = ui.phase_dialog
    panel.note.setText("LOW CONFIDENCE — synthetic proposal requires operator review.")
    panel.notes_button.setChecked(True)
    assert panel.note.isVisible() and not panel.note.brief.isVisible()
    panel.notes_button.setChecked(False)
    assert not panel.note.isVisible()
    assert panel.note.brief.isVisible() and "LOW CONFIDENCE" in panel.note.brief.text()
    panel.reject()


def test_right_docks_keep_all_columns_and_controls_inside(ui, qtbot):
    from PySide6.QtWidgets import QPushButton, QScrollArea, QWidget

    from bruker_nmr.processing.baseline import Anchor

    def check(panel, table):
        scroll = panel.findChild(QScrollArea)
        body = scroll.widget()
        assert body.width() == scroll.viewport().width()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert table.horizontalHeader().length() <= table.viewport().width()
        assert table.horizontalScrollBar().maximum() == 0
        for control in body.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if control.isVisible():
                assert 0 <= control.x() and control.x() + control.width() <= body.width()
                if isinstance(control, QPushButton):
                    assert control.width() >= control.minimumSizeHint().width(), control.text()

    ui.document.add_integral(10, 8)
    data = ui.document.current.data
    ui.document.add_peak(float(data.ppm[np.argmax(data.real)]), .3)
    ui._render_document()
    for editor in (ui.integrals, ui.peaks):
        editor.show_table()
        qtbot.wait(40)
        check(editor, editor.table)
    ui.open_baseline()
    panel = ui.baseline_dialog
    panel.anchors = [Anchor(11, 10)]
    panel.mode.setCurrentText("Constant")
    panel.preview()
    qtbot.wait(40)
    assert panel.areas.rowCount() == 1
    check(panel, panel.areas)
    check(panel, panel.table)
    panel.notes_button.setChecked(True)
    ui.resizeDocks([ui.workbench.process], [325], Qt.Orientation.Horizontal)
    qtbot.wait(40)
    check(panel, panel.areas)
    check(panel, panel.table)
    panel.reject()


def test_subpixel_peak_order_and_single_integral_row_when_docked(ui, qtbot):
    from dataclasses import replace

    from bruker_nmr.domain.integrals import Peak

    state = ui.document.current
    # Deliberately reverse IDs relative to ppm for two subpixel neighbours.
    peaks = (Peak('a', 7.369, 1, 0), Peak('z', 7.370, 1, 0))
    ui.document.apply_peaks(replace(state.peaks, records=peaks))
    ui.document.appearance(label_mode="all")
    for index in range(8):
        hi = 7.55 - .04 * index
        ui.document.add_integral(hi, hi - .032)
    first = ui.document.current.integrals.regions[0]
    ui.document.assign_protons(first.id, 3)
    ui._render_document()
    for docked in (False, True):
        if docked:
            ui.integrals.show_table()
        qtbot.wait(55)
        band = ui.peak_band
        assert 0 < band.pixel(peaks[0].ppm) - band.pixel(peaks[1].ppm) < 1
        assert [p.label.id for p in band.placements] == ["z", "a"]
        integrals = ui.integral_band
        assert {p.lane for p in integrals.placements} == {0}
        assert len(integrals.range_pixels) == 8
        assert any(p.label.text == "3.00*" for p in integrals.placements)
