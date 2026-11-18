"""Independent packing truth and actual pre-PDF history/colour/pointer interactions."""

import itertools

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QWheelEvent
from PySide6.QtWidgets import QApplication, QColorDialog, QPushButton
from scipy.optimize import minimize

from bruker_nmr.domain.document import ViewState
from bruker_nmr.domain.labels import (
    Label,
    format_integral,
    format_peak_label,
    place_peak_labels,
    spread,
)
from tests.test_peaks_state import fixture
from tests.test_peaks_ui import at
from tests.test_viewer import window


@pytest.fixture
def ui(qtbot, monkeypatch):
    result = window(qtbot)
    result.document = fixture()
    monkeypatch.setattr(result, "confirm_discard", lambda: True)
    result._render_document()
    return result


@pytest.mark.parametrize(
    "centers", [(-2.0, 1.0, 7.0), (2.0, 2.0, 2.0), (0.0, 4.0, 5.0), (3.8, 4.0, 4.1)]
)
def test_spread_matches_independent_constrained_optimizer(centers):
    actual = np.array(spread(centers, 1.5, 0.0, 6.0))
    truth = minimize(
        lambda x: np.sum((x - np.array(centers)) ** 2),
        [0.0, 3.0, 6.0],
        constraints={"type": "ineq", "fun": lambda x: np.diff(x) - 1.5},
        bounds=[(0, 6)] * 3,
        method="SLSQP",
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    assert truth.success
    assert np.allclose(actual, truth.x, atol=2e-6)
    assert np.all(np.diff(actual) >= 1.5 - 1e-12)
    # An independent coarse exhaustive feasible grid cannot beat the continuous result.
    score = np.sum((actual - centers) ** 2)
    for candidate in itertools.combinations(np.linspace(0, 6, 13), 3):
        if min(np.diff(candidate)) >= 1.5:
            assert np.sum((np.array(candidate) - centers) ** 2) >= score - 1e-9


def test_label_geometry_priority_density_and_formatting():
    assert spread([], 1, 0, 1) == ()
    for args in [([2, 1], 1, 0, 4), ([np.nan], 1, 0, 1), ([0, 1], 2, 0, 1), ([0], 0, 0, 1)]:
        with pytest.raises(ValueError):
            spread(*args)
    labels = tuple(
        Label(str(i), 50 + i * 0.001, 10, str(i), float("inf") if i == 19999 else i)
        for i in range(20000)
    )
    placements, hidden = place_peak_labels(labels, 0, 100, spacing=12)
    assert len(placements) == 8 and len(hidden) == 19992
    assert "19999" in {p.label.id for p in placements}
    assert all(0 <= p.left <= 90 for p in placements)
    assert all(b.left - a.left >= 12 - 1e-9 for a, b in zip(placements, placements[1:]))
    all_labels, hidden = place_peak_labels(labels[:30], 0, 100, spacing=12, auto=False)
    assert len(all_labels) == 30 and not hidden
    assert format_integral(2, True) == "2.00 H (ref)"
    from bruker_nmr.domain.integrals import Peak

    assert format_peak_label(Peak("p", 7.2, 1, 0, needs_review=True), -7) == "0.200 ?"
    assert "*" not in format_integral(2, True)


def test_colour_is_validated_undoable_and_does_not_change_arrays(ui):
    doc = ui.document
    before = doc.current
    for bad in ["red", "#123", "#12345678", "#xxxxxx", 4]:
        with pytest.raises(ValueError):
            doc.appearance(trace_color=bad)
        assert doc.current is before
    doc.appearance(trace_color="#a034ce")
    assert doc.integration_modified  # Colour alone must trigger discard protection.
    doc.undo()
    assert not doc.integration_modified
    doc.appearance(trace_color="#A034CE", line_width=0.6)
    assert doc.current.trace_color == "#a034ce" and doc.current.data is before.data
    doc.add_peak(7, 0.1)
    doc.set_zero(doc.current.peaks.records[0].id)
    assert doc.current.trace_color == "#a034ce"
    doc.undo()
    doc.undo()
    doc.undo()
    assert doc.current is before


def test_actual_appearance_colour_choose_cancel_and_undo(ui, qtbot, monkeypatch):
    from PySide6.QtWidgets import QAbstractSpinBox, QDialog

    observed = []
    accept = False

    def choose(picker):
        components = picker.findChildren(QAbstractSpinBox)
        observed.append(bool(components) and all(
            component.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
            for component in components
        ))
        picker.setCurrentColor(QColor("#802c90"))
        if accept:
            picker.accept()
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QColorDialog, "exec", choose)
    before = ui.document.current
    ui.appearance_dialog()
    button = ui.workbench.appearance.findChild(QPushButton, "trace_colour")
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert ui.document.current is before
    accept = True
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert button.text() == "#802c90"
    assert button.icon().pixmap(16, 16).toImage().pixelColor(8, 8) == QColor("#802c90")
    assert ui.document.undo_label == "Appearance"
    assert ui.curve.opts["pen"].color().name() == "#802c90"
    assert ui.workbench.inspector.isVisible()
    assert observed == [True, True]
    ui.undo()
    assert ui.document.current is before
    assert button.text() == before.trace_color


def wheel(ui, point, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QWheelEvent(
        QPointF(point),
        QPointF(ui.plot.viewport().mapToGlobal(point)),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(ui.plot.viewport(), event)


def test_cursor_anchored_x_wheel_alias_and_shift_pan_one_burst(ui, qtbot):
    ui.peaks.add_at(7)
    ui.peaks.zero_selected()
    qtbot.wait(50)  # Settle the explicit calibration's summary/layout, before navigation.
    state = ui.document.current
    axis = ui.plot.getPlotItem().getAxis("bottom")
    box = axis.sceneBoundingRect()
    point = ui.plot.mapFromScene(QPointF(box.left() + 0.22 * box.width(), box.center().y()))
    scene = ui.plot.mapToScene(point)
    original = ui.view_box.mapSceneToView(scene).x()
    for _ in range(5):
        wheel(ui, point)
    qtbot.waitUntil(lambda: ui.document.view_count == 1)
    mapped = ui.view_box.mapViewToScene(QPointF(original, 0))
    assert abs(mapped.x() - scene.x()) <= 1
    assert ui.document.undo_label == "Set zero reference"
    ui.previous_view()
    assert ui.document.current.view == state.view
    center = at(ui, 0, 0.5)
    wheel(ui, center, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: ui.document.view_count == 1)
    first = ui.document.current.view
    wheel(ui, center, Qt.KeyboardModifier.ShiftModifier)
    qtbot.waitUntil(lambda: ui.document.view_count == 2)
    after = ui.document.current.view
    assert after.x_max - after.x_min == pytest.approx(first.x_max - first.x_min)
    assert after.x_min != first.x_min
    assert after.y_min == pytest.approx(first.y_min)


@pytest.mark.parametrize("mode", ["zoom", "integral", "peak"])
def test_actual_right_click_marker_ownership_in_all_modes(ui, qtbot, monkeypatch, mode):
    ui.peaks.add_at(7)
    getattr(
        ui, {"zoom": "zoom_action", "integral": "integrate_action", "peak": "pick_action"}[mode]
    ).trigger()
    qtbot.wait(50)
    peak = ui.document.current.peaks.records[0]
    x, y = ui.peaks.markers.getData()
    scene = ui.view_box.mapViewToScene(QPointF(float(x[0]), float(y[0])))
    assert ui.peaks.hit_test(scene) == peak.id
    assert ui.peaks.hit_test(scene + QPointF(7, 0)) is None
    assert ui.peaks.hit_test(scene + QPointF(0, 7)) is None
    calls = []
    monkeypatch.setattr(ui.peaks, "context_menu", lambda point, id=None: calls.append(id))
    qtbot.mouseClick(
        ui.plot.viewport(), Qt.MouseButton.RightButton, pos=ui.plot.mapFromScene(scene)
    )
    assert calls == [peak.id]
    ui.document.appearance(markers=False)
    ui._render_document()
    assert ui.peaks.hit_test(scene) is None


def test_pick_ghost_uses_same_bounded_full_grid_search_and_clears(ui, qtbot):
    ui.pick_action.trigger()
    qtbot.wait(30)
    before = ui.document.current
    point = at(ui, 7.01, 0.5)
    qtbot.mouseMove(ui.plot.viewport(), point)
    qtbot.waitUntil(lambda: ui.peaks.ghost_index is not None)
    index = ui.peaks.ghost_index
    assert "ppm" in ui.cursor_readout.text()
    assert ui.document.current is before and ui.document.undo_count == 0
    qtbot.mouseClick(ui.plot.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert ui.document.current.peaks.records[-1].ppm == before.data.ppm[index]
    assert ui.peaks.ghost_index is None
    ui.peaks.show_ghost(7)
    QApplication.sendEvent(ui.plot.viewport(), QEvent(QEvent.Type.Leave))
    assert ui.peaks.ghost_index is None
    assert ui.peaks.pick_radius() <= 32768 * ui.document.current.grid.step
    ui.show_demo()
    assert not ui.peaks.ghost.isVisible()


def test_menu_survives_navigation_but_expires_on_edit_and_metrics_scale(ui, qtbot):
    ui.peaks.add_at(7)
    ui.peaks.context_menu(ui.mapToGlobal(QPoint(30, 30)), ui.peaks.selected)
    delete = next(a for a in ui.peaks._menu.actions() if a.text() == "Delete peak")
    before = ui.document.current
    ui.commit_view(ViewState(6, 8, -0.1, 1.2))
    delete.trigger()
    assert not ui.document.current.peaks.records
    ui.undo()
    ui.peaks.context_menu(ui.mapToGlobal(QPoint(30, 30)), ui.peaks.selected)
    stale = next(a for a in ui.peaks._menu.actions() if a.text() == "Delete peak")
    ui.document.appearance(trace_color="#880022")
    stale.trigger()
    assert len(ui.document.current.peaks.records) == 1
    ui.peaks._menu.close()
    ui._render_document()
    old_height = ui.peak_band.height()
    ui.commit_view(ViewState(10, 11, -0.1, 1.2))
    qtbot.wait(30)
    assert ui.peak_band.isVisible() and not ui.peak_band.placements
    assert ui.peak_band.height() == old_height  # Empty-view labels never resize the canvas.
    ui.previous_view()
    font = QFont(ui.peak_band.font())
    font.setPointSizeF(font.pointSizeF() * 1.7)
    ui.peak_band.setFont(font)
    ui.peak_band.rebuild()
    qtbot.wait(30)
    assert ui.peak_band.height() > old_height
    assert all(
        p.label.width >= ui.peak_band.fontMetrics().height() for p in ui.peak_band.placements
    )
    assert before.data is ui.document.current.data


def test_colour_and_width_survive_phase_baseline_recipe_and_zero(ui):
    from bruker_nmr.domain.document import DocumentData
    from bruker_nmr.processing.baseline import Anchor, fit_baseline
    from bruker_nmr.processing.fid import RawResult, Recipe
    from bruker_nmr.processing.phase import Phase

    doc = ui.document
    doc.appearance(trace_color="#802c90", line_width=0.6)
    doc.apply_phase(Phase(2))
    fit = fit_baseline(doc.current.data.ppm, doc.current.data.real, (Anchor(11, 10),))
    doc.apply_baseline(fit)
    doc.disable_baseline()
    doc.add_peak(7, 0.1)
    doc.set_zero(doc.current.peaks.records[0].id)
    data = doc.current.data
    result = RawResult(
        data.operator.fid,
        data.ppm,
        data.real,
        data.phased_imaginary,
        Recipe(len(data.ppm), 0.6),
        data.operator.acquisition,
        data.integration,
    )
    doc.apply_raw(DocumentData(result.ppm, result.real, "raw", result, True))
    assert doc.current.trace_color == "#802c90" and doc.current.line_width == 0.6
    assert doc.view_count == 0
    doc.undo()
    assert doc.current.data is data and doc.current.trace_color == "#802c90"


def test_phase_proposal_and_apply_preserve_new_view_and_named_edit(ui):
    from bruker_nmr.processing.phase import Phase, Proposal
    from bruker_nmr.ui.phase import PhaseJob

    ui.open_phase()
    panel = ui.phase_dialog
    ticket = panel.gate.begin(ui.document)
    job = PhaseJob(ticket, panel.base)
    view = ViewState(6, 8, -0.1, 1.1)
    ui.commit_view(view)
    before = ui.document.current
    count = ui.document.undo_count
    proposal = Proposal(Phase(5), "weighted-acme", 1.0, True, "synthetic test", 1)
    panel.finished_search(job, ticket, proposal, None)
    assert panel.isVisible() and ui._phase_preview is not None
    assert ui.document.current is before and ui.view_box.snapshot() == view
    panel.apply()
    assert ui.document.undo_count == count + 1 and ui.document.undo_label == "Phase"
    assert ui.document.current.view == view
    ui.undo()
    assert ui.document.current is before


def test_late_raw_worker_cannot_overwrite_navigation(qtbot, monkeypatch, tmp_path):
    from bruker_nmr.processing.fid import Recipe
    from tests.synthetic_bruker import experiment
    from tests.test_viewer import QueuedPool

    root, _, _, _ = experiment(tmp_path)
    pool = QueuedPool()
    result = window(qtbot, pool=pool)
    monkeypatch.setattr(result, "confirm_discard", lambda: True)
    result.begin_open(root)
    pool.run_next()
    pool.run_next()
    data = result.document.current.data
    monkeypatch.setattr(result, "processing_options", lambda: Recipe(0))
    result.process_from_fid()
    view = ViewState(2, 6, -0.2, 1.3)
    result.document.navigate(view)
    pool.run_next()
    assert not result.loading
    assert result.document.current.data is data and result.document.current.view == view
    result.close()


def test_context_actions_keep_the_clicked_object_after_selection_changes(ui):
    ui.peaks.add_at(7)
    ui.peaks.add_at(3)
    first, second = ui.document.current.peaks.records
    ui.peaks.context_menu(ui.mapToGlobal(QPoint(30, 30)), first.id)
    delete = next(a for a in ui.peaks._menu.actions() if a.text() == "Delete peak")
    ui.peaks.select(second.id)
    delete.trigger()
    assert [p.id for p in ui.document.current.peaks.records] == [second.id]
    ui.peaks._menu.close()
    ui.integrals.create_range(7.2, 6.8)
    ui.integrals.create_range(3.2, 2.8)
    first, second = ui.document.current.integrals.regions
    ui.integrals.context_menu(ui.mapToGlobal(QPoint(30, 30)), first.id)
    delete = next(a for a in ui.integrals._menu.actions() if a.text() == "Delete integral")
    ui.integrals.selected = second.id
    delete.trigger()
    assert [r.id for r in ui.document.current.integrals.regions] == [second.id]
    ui.integrals._menu.close()


def test_edit_flushes_active_control_pan_and_old_drag_cannot_resume(ui, qtbot):
    from PySide6.QtGui import QMouseEvent

    viewport = ui.plot.viewport()
    start = at(ui, 7.5, 0.4)
    stop = start + QPoint(35, 15)
    qtbot.mousePress(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=start
    )
    for offset in (QPoint(15, 6), QPoint(25, 10)):
        qtbot.wait(40)
        point = QPointF(start + offset)
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            point,
            QPointF(viewport.mapToGlobal(point.toPoint())),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
        )
        QApplication.sendEvent(viewport, event)
    expected = ui.view_box.snapshot()
    assert expected != ui.document.current.view
    ui.peaks.add_at(7)
    assert ui.document.current.view == expected
    qtbot.wait(40)
    point = QPointF(stop)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        point,
        QPointF(viewport.mapToGlobal(stop)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
    )
    QApplication.sendEvent(viewport, event)
    qtbot.mouseRelease(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=stop
    )
    assert ui.view_box.snapshot() == expected and ui.document.current.view == expected
    ui.undo()
    assert ui.document.current.view == expected and not ui.document.current.peaks.records
