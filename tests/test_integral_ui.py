"""Actual GUI gestures, modal edits, previews and source/open lifecycle."""
import hashlib

import pytest
from PySide6.QtCore import QPoint, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QDoubleSpinBox

from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.ui.integrals import BoundsDialog
from tests.test_annotations import annotated
from tests.test_viewer import drag, points, window


@pytest.fixture
def ui(qtbot, monkeypatch):
    result = window(qtbot)
    monkeypatch.setattr(result, 'confirm_discard', lambda: True)
    yield result
    result.document.reset()
    result.integrals.close()
    result.close()


def values(ui):
    return integral_snapshot(ui.document.current.integrals, ui.document.current.data.integration)


def test_integrate_drag_creates_one_command_without_zoom(ui, qtbot):
    view = ui.document.current.view
    before = hashlib.sha256(ui.document.current.data.real.tobytes()).digest()
    ui.integrate_action.trigger()
    start, stop = points(ui)
    drag(ui, start, stop, qtbot)
    assert len(values(ui)) == 1
    assert values(ui)[0].value == pytest.approx(1)
    assert ui.document.current.view == view
    assert ui.document.undo_count == 1
    assert ui.export_button.isEnabled()
    ui.undo()
    assert not values(ui)
    ui.redo()
    assert len(values(ui)) == 1
    assert hashlib.sha256(ui.document.current.data.real.tobytes()).digest() == before


def test_actual_bracket_drag_reference_delete_frozen_clear(ui, qtbot):
    ui.integrate_action.trigger()
    ui.integrals.create_range(8, 7)
    reference = values(ui)[0].id
    ui.integrals.assign(2)
    ui.integrals.create_range(4, 3)
    second = values(ui)[1].value
    qtbot.wait(40)  # Settle axis/layout changes before choosing an item coordinate.
    line = ui.integrals.rois[reference].lines[1]
    start = ui.plot.mapFromScene(line.mapToScene(line.boundingRect().center()))
    stop = start + QPoint(-30, 0)
    before = ui.document.undo_count
    drag(ui, start, stop, qtbot)
    assert ui.document.undo_count == before + 1
    assert len(values(ui)) == 2  # Handle edit must not become a background range creation.
    assert values(ui)[0].value == pytest.approx(2)
    assert values(ui)[1].value != second
    retained = values(ui)[1].value
    ui.integrals.selected = reference
    ui.integrals.delete_selected()
    assert ui.document.current.integrals.normalization.kind == 'frozen'
    assert values(ui)[0].value == retained
    ui.integrals.create_range(2, 1)
    assert ui.document.current.integrals.normalization.kind == 'frozen'
    ui.integrals.clear_all()
    assert not values(ui)
    ui.integrals.create_range(8, 7)
    assert values(ui)[0].value == pytest.approx(1)


def test_invalid_reference_preview_preserves_state_and_history(ui):
    ui.integrals.create_range(8, 7)
    state = ui.document.current
    commands = ui.document.undo_count
    id = values(ui)[0].id
    roi = ui.integrals.rois[id]
    with QSignalBlocker(roi):
        roi.setRegion((7.5, 7.5))
    ui.integrals._roi_changed(id, ui.document.document_id)
    assert ui.document.current == state
    assert 'Invalid preview' in ui.integrals.notices.text()
    roi.sigRegionChangeFinished.emit(roi)
    assert ui.document.current == state
    assert ui.document.undo_count == commands
    assert ui.integrals.rois[id].getRegion()[0] < ui.integrals.rois[id].getRegion()[1]


def test_table_assign_modal_and_bounds_cancel(ui, qtbot):
    ui.integrals.create_range(8, 7)
    ui.integrals.show_table()
    qtbot.waitExposed(ui.integrals)
    before = ui.document.undo_count

    def assign_two():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QDoubleSpinBox).setValue(2)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()

    QTimer.singleShot(30, assign_two)
    qtbot.mouseClick(ui.integrals.assign_button, Qt.MouseButton.LeftButton)
    assert values(ui)[0].value == pytest.approx(2)
    assert ui.document.undo_count == before + 1
    assert ui.integrals.table.item(0, 5).text() == '✓'
    assert 'Reference' in ui.integrals.table.item(0, 5).toolTip()
    state = ui.document.current

    def cancel_bounds():
        dialog = QApplication.activeModalWidget()
        dialog.high.setValue(8.5)
        dialog.reject()

    QTimer.singleShot(30, cancel_bounds)
    qtbot.mouseClick(ui.integrals.edit_button, Qt.MouseButton.LeftButton)
    assert ui.document.current == state


def test_bounds_dialog_invalid_apply_disabled(ui, qtbot):
    ui.integrals.create_range(8, 7)
    id = values(ui)[0].id
    dialog = BoundsDialog(ui.document.current.data.integration, 8, 7,
                          lambda a, b: ui.integrals._preview(id, a, b), ui.integrals)
    qtbot.addWidget(dialog)
    dialog.high.setValue(7.5)
    dialog.low.setValue(7.5)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.high.setValue(8)
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_operator_annotations_import_undo_reset_and_loading_controls(ui, qtbot, tmp_path):
    exp, _, _ = annotated(tmp_path)
    ui.begin_open(exp)
    assert not ui.integrate_action.isEnabled()
    assert not ui.integrals.add_button.isEnabled()
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.integrate_action.isEnabled()
    assert len(values(ui)) == 3
    assert len(ui.document.current.data.annotations.peaks) == 2
    assert ui.document.current.integrals.normalization.anchor_id is None
    ui.integrals.selected = values(ui)[1].id
    ui.integrals.assign(2)
    assert ui.document.integration_modified
    ui.reset()
    assert not ui.document.integration_modified
    assert ui.document.current.integrals.normalization.kind == 'imported'
    ui.undo()
    assert ui.document.integration_modified


def test_dirty_document_open_demo_close_cancel_retains_edits(ui, monkeypatch, tmp_path):
    ui.integrals.create_range(8, 7)
    before = ui.document
    monkeypatch.setattr(ui, 'confirm_discard', lambda: False)
    ui.begin_open(tmp_path)
    assert not ui.loading and ui.document is before
    ui.show_demo()
    assert ui.document is before
    ui.close()
    assert ui.isVisible()
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)


def test_preview_does_not_apply_to_new_document(ui):
    ui.integrals.create_range(8, 7)
    old_doc = ui.document.document_id
    id = values(ui)[0].id
    ui.show_demo()
    new = ui.document
    ui.integrals._roi_finished(id, old_doc)
    assert ui.document is new and not values(ui)
