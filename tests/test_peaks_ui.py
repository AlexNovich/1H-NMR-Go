"""Actual peak/control/axis gestures, annotation layout and calibrated baseline UI."""

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QMessageBox

from bruker_nmr.domain.document import ViewState
from bruker_nmr.domain.integrals import Peak
from bruker_nmr.domain.labels import Label, place_labels
from bruker_nmr.domain.peaks import PeakState
from tests.test_peaks_state import fixture
from tests.test_viewer import drag, points, window


@pytest.fixture
def ui(qtbot, monkeypatch):
    result = window(qtbot)
    result.document = fixture()
    monkeypatch.setattr(result, "confirm_discard", lambda: True)
    result._render_document()
    return result


def at(ui, x, y):
    return ui.plot.mapFromScene(ui.view_box.mapViewToScene(QPointF(x, y)))


def test_click_manual_peak_table_hiding_zero_and_delete(ui, qtbot):
    ui.pick_action.trigger()
    qtbot.mouseClick(ui.plot.viewport(), Qt.MouseButton.LeftButton, pos=at(ui, 7.01, 0.5))
    assert len(ui.document.current.peaks.records) == 1
    peak = ui.document.current.peaks.records[0]
    before = ui.document.current
    ui.peaks.show_table()
    qtbot.waitExposed(ui.peaks)
    checkbox = ui.peaks.table.item(0, 0)
    checkbox.setCheckState(Qt.CheckState.Unchecked)
    assert not ui.document.current.peaks.by_id(peak.id).label_visible
    assert len(ui.document.current.peaks.records) == 1
    ui.peaks.mode.setCurrentText("All")
    assert not ui.document.current.peaks.by_id(peak.id).label_visible
    ui.peaks.unhide_all()
    assert ui.document.current.peaks.by_id(peak.id).label_visible
    ui.peaks.zero_selected()
    assert peak.ppm + ui.document.current.calibration == 0
    assert ui.document.current.data is before.data
    ui.peaks.delete_selected()
    assert not ui.document.current.peaks.records
    ui.undo()
    assert len(ui.document.current.peaks.records) == 1


def test_actual_selected_peak_drag_is_one_command_and_no_zoom(ui, qtbot):
    ui.peaks.add_at(7)
    ui.pick_action.trigger()
    qtbot.wait(50)
    line = ui.peaks.handle
    start = ui.plot.mapFromScene(line.mapToScene(line.boundingRect().center()))
    stop = start + QPoint(35, 0)
    before = ui.document.current
    count = ui.document.undo_count
    drag(ui, start, stop, qtbot)
    assert ui.document.undo_count == count + 1
    assert ui.document.current.peaks.records[0].ppm != before.peaks.records[0].ppm
    assert ui.document.current.view == before.view
    ui.undo()
    assert ui.document.current is before


def control_drag(ui, start, stop, qtbot):
    viewport = ui.plot.viewport()
    hover = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(start),
        QPointF(viewport.mapToGlobal(start)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
    )
    QApplication.sendEvent(viewport, hover)
    qtbot.wait(30)
    qtbot.mousePress(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=start
    )
    for t in [0.25, 0.5, 0.75, 1]:
        qtbot.wait(30)
        p = QPointF(start + (stop - start) * t)
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            p,
            QPointF(viewport.mapToGlobal(p.toPoint())),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
        )
        QApplication.sendEvent(viewport, event)
    qtbot.mouseRelease(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=stop
    )
    QApplication.processEvents()


@pytest.mark.parametrize("mode", ["zoom", "integral", "peak"])
def test_control_drag_pans_without_editing_handles(ui, qtbot, mode):
    ui.integrals.create_range(7.2, 6.8)
    ui.peaks.add_at(7)
    if mode == "integral":
        ui.integrate_action.trigger()
    elif mode == "peak":
        ui.pick_action.trigger()
    else:
        ui.zoom_action.trigger()
    qtbot.wait(50)
    if mode == "peak":
        line = ui.peaks.handle
        start = ui.plot.mapFromScene(line.mapToScene(line.boundingRect().center()))
    elif mode == "integral":
        line = next(iter(ui.integrals.rois.values())).lines[0]
        start = ui.plot.mapFromScene(line.mapToScene(line.boundingRect().center()))
    else:
        start = points(ui)[0]
    before = ui.document.current
    count = ui.document.undo_count
    control_drag(ui, start, start + QPoint(35, 20), qtbot)
    assert ui.document.current.view != before.view
    assert ui.document.current.integrals == before.integrals
    assert ui.document.current.peaks == before.peaks
    assert ui.document.undo_count == count and ui.document.view_count == 1
    ui.previous_view()
    assert ui.document.current == before


def test_wheel_on_horizontal_axis_only_scales_x(ui, qtbot):
    before = ui.document.current
    axis = ui.plot.getPlotItem().getAxis("bottom")
    point = ui.plot.mapFromScene(axis.mapToScene(axis.boundingRect().center()))
    event = QWheelEvent(
        QPointF(point),
        QPointF(ui.plot.viewport().mapToGlobal(point)),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(ui.plot.viewport(), event)
    qtbot.waitUntil(lambda: ui.document.view_count == 1)
    after = ui.document.current.view
    assert after.x_max - after.x_min < before.view.x_max - before.view.x_min
    assert after.y_min == pytest.approx(before.view.y_min)
    assert after.y_max == pytest.approx(before.view.y_max)


def test_integral_band_is_outside_trace_and_context_delete_undo(ui, qtbot):
    ui.integrals.create_range(7.2, 6.8)
    QApplication.processEvents()
    ui.integral_band.rebuild()
    ui.integral_band.repaint()
    plot_bottom = ui.plot.mapToGlobal(ui.plot.rect().bottomLeft()).y()
    assert ui.integral_band.mapToGlobal(QPoint(0, 0)).y() > plot_bottom
    assert ui.integral_band.placements
    key = ui.document.current.integrals.regions[0].id
    before = ui.document.current
    ui.integrals.context_menu(ui.mapToGlobal(QPoint(100, 100)), key)
    action = next(a for a in ui.integrals._menu.actions() if a.text() == "Delete integral")
    action.trigger()
    assert not ui.document.current.integrals.regions
    ui.undo()
    assert ui.document.current is before


def test_stale_context_cannot_delete_new_document_integral(ui):
    ui.integrals.create_range(7.2, 6.8)
    ui.integrals.context_menu(ui.mapToGlobal(QPoint(100, 100)))
    action = next(a for a in ui.integrals._menu.actions() if a.text() == "Clear all integrals")
    ui.show_demo()
    ui.integrals.create_range(8, 7)
    before = ui.document.current
    action.trigger()
    assert ui.document.current is before


def test_auto_label_layout_and_dense_records_survive_zoom(ui):
    records = tuple(Peak(f"p{i}", 7 + i * 0.003, float(60 - i), 0) for i in range(60))
    ui.document.apply_peaks(PeakState(records))
    ui._render_document()
    ui.peak_band.rebuild()
    count = len(ui.peak_band.hidden)
    assert count > 0 and len(ui.document.current.peaks.records) == 60
    ui.commit_view(ViewState(6.99, 7.02, -0.1, 1.2))
    ui.peak_band.rebuild()
    assert len(ui.peak_band.hidden) < count
    ui.document.appearance(label_mode="all")
    ui._render_document()
    ui.peak_band.rebuild()
    assert not ui.peak_band.hidden
    assert len(ui.document.current.peaks.records) == 60
    labels = [Label(str(i), 100 + i, 40, "test", i) for i in range(10)]
    placed, hidden = place_labels(labels, 0, 200, lanes=2)
    for a in placed:
        for b in placed:
            if a != b and a.lane == b.lane:
                assert a.left + a.label.width <= b.left or b.left + b.label.width <= a.left
    assert hidden


def test_appearance_dialog_width_and_calibrated_baseline(ui, qtbot):
    ui.peaks.add_at(7)
    ui.peaks.zero_selected()

    def accept():
        dialog = ui.workbench.appearance
        dialog.findChild(QDoubleSpinBox).setValue(0.5)

    QTimer.singleShot(30, accept)
    ui.appearance_dialog()
    qtbot.waitUntil(lambda: ui.document.current.line_width * 0.75 == 0.5)
    assert ui.document.current.line_width * 0.75 == pytest.approx(0.5)
    ui.open_baseline()
    panel = ui.baseline_dialog
    panel.high.setValue(4)
    panel.low.setValue(3)
    panel.add_anchor()
    panel.preview()
    assert panel.candidate is not None
    assert panel.candidate.baseline.fit.anchors[0].high == pytest.approx(
        11, abs=ui.document.current.grid.step
    )
    panel.apply()
    before = ui.document.current
    ui.open_phase()
    assert ui.view_box.snapshot() == before.view
    ui.phase_dialog.reject()
    assert ui.document.current is before


def test_discard_dialog_cancels_peak_only_edits_and_pdf_is_honest(qtbot):
    ui = window(qtbot)
    ui.document = fixture()
    ui.document.add_peak(7, 0.1)
    ui._render_document()

    def cancel():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        pdf = next(b for b in dialog.buttons() if "PDF" in b.text())
        assert pdf.isEnabled()
        dialog.button(QMessageBox.StandardButton.Cancel).click()

    QTimer.singleShot(30, cancel)
    assert not ui.confirm_discard()
    ui.confirm_discard = lambda: True
    ui.close()


def test_actual_right_click_integral_opens_commands(ui, qtbot):
    ui.integrals.create_range(7.2, 6.8)
    qtbot.wait(30)
    qtbot.mouseClick(ui.plot.viewport(), Qt.MouseButton.RightButton, pos=at(ui, 7, 0.4))
    qtbot.waitUntil(lambda: hasattr(ui.integrals, "_menu"))
    assert any(a.text() == "Assign reference H…" for a in ui.integrals._menu.actions())
    ui.integrals._menu.close()


def test_display_cache_preserves_full_grid_extrema_and_invalidates_on_zoom_style(ui):
    data = ui.document.current.data
    before = ui.document.current
    ui._draw_curve()
    first = ui._trace_cache
    x = ui.view_box.viewRange()[0]
    ui.view_box.setRange(xRange=(x[0] + 0.01, x[1] + 0.01), padding=0)
    ui._draw_curve()
    assert ui._trace_cache is first
    xx, yy = ui.curve.getData()
    indices = np.rint((xx - data.ppm[0]) / (data.ppm[1] - data.ppm[0])).astype(int)
    assert np.array_equal(yy, data.real[indices])
    assert max(yy) == max(data.real)
    ui.commit_view(ViewState(6.5, 7.5, -0.05, 0.3))
    assert ui._trace_cache is not first
    from PySide6.QtWidgets import QGraphicsItem

    assert ui.curve.curve.cacheMode() == QGraphicsItem.CacheMode.NoCache
    ui.document.appearance(line_width=0.5)
    ui._render_document()
    assert ui.curve.opts["pen"].widthF() == 0.5
    assert ui.document.current.data is before.data


def test_open_replacement_while_picking_disables_old_controls_and_handle(ui, tmp_path):
    from tests.synthetic_bruker import experiment
    from tests.test_viewer import QueuedPool

    exp, _, _, _ = experiment(tmp_path)
    ui.peaks.add_at(7)
    ui.pick_action.trigger()
    assert ui.peaks.handle is not None
    ui.pool = QueuedPool()
    ui.begin_open(exp)
    assert not ui.peaks.isEnabled()
    assert not ui.peaks.handle.movable
    ui.pool.run_next()
    ui.pool.run_next()
    assert not ui.loading and ui.peaks.isEnabled()
    assert not ui.document.current.peaks.records
    assert ui.peaks.handle is None
