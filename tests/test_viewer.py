"""GUI input, asynchronous open/cancel and chooser tests on synthetic data only."""
import hashlib
import shutil

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from bruker_nmr.domain.document import ViewState
from bruker_nmr.ui.chooser import ExperimentChooser
from bruker_nmr.ui.loading import discover_choices, load_choice
from bruker_nmr.ui.main_window import ViewerWindow
from tests.synthetic_bruker import experiment, status

IDENTITY = {"version": "0.2.0", "source_commit": "a" * 40}


def window(qtbot, **kwargs):
    result = ViewerWindow(IDENTITY, **kwargs)
    qtbot.addWidget(result)
    result.show()
    qtbot.waitExposed(result)
    return result


class QueuedPool:
    def __init__(self):
        self.jobs = []

    def start(self, job):
        self.jobs.append(job)

    def run_next(self):
        self.jobs.pop(0).run()


def test_chooser_explicit_and_unsupported_rows(qtbot, tmp_path):
    exp, proc, _, p = experiment(tmp_path)
    shutil.copytree(proc, proc.with_name("2"))
    bad = dict(p, PPARMOD=1)
    status(proc.with_name("2") / "procs", bad)
    choices = discover_choices(exp)
    dialog = ExperimentChooser(choices)
    qtbot.addWidget(dialog)
    dialog.show()
    button = dialog.buttons.button(QDialogButtonBox.StandardButton.Open)
    assert not button.isEnabled()
    dialog.table.selectRow(1)
    assert dialog.selection() is None and not button.isEnabled()
    dialog.table.selectRow(0)
    assert dialog.selection() == choices[0] and button.isEnabled()
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert dialog.result() == dialog.DialogCode.Accepted


def test_worker_open_missing_imaginary_capabilities(qtbot, tmp_path):
    exp, proc, _, _ = experiment(tmp_path, imaginary=False)
    before = (proc / "1r").read_bytes()
    ui = window(qtbot)
    ui.begin_open(exp)
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.document.current.data.opening_mode == "operator"
    assert "Imaginary data: missing" in ui.summary.text()
    assert "missing" in ui.editing_actions["Phase"].toolTip()
    assert not ui.editing_actions["Phase"].isEnabled()
    assert ui.export_button.isEnabled()
    assert (proc / "1r").read_bytes() == before
    ui.show_demo()
    assert ui.document.current.data.opening_mode == "demo"
    assert ui.export_button.isEnabled()


def test_open_folder_dialog_and_cancel_keep_view(qtbot, monkeypatch, tmp_path):
    exp, _, _, _ = experiment(tmp_path)
    ui = window(qtbot)
    monkeypatch.setattr("bruker_nmr.ui.main_window.QFileDialog.getExistingDirectory", lambda *a: "")
    original = ui.document
    ui.open_folder()
    assert ui.document is original
    monkeypatch.setattr("bruker_nmr.ui.main_window.QFileDialog.getExistingDirectory", lambda *a: str(exp))
    ui.open_folder_action.trigger()
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.document.current.data.opening_mode == "operator"


def test_new_open_and_cancel_cannot_replace_current(qtbot, tmp_path):
    exp, _, _, _ = experiment(tmp_path)
    pool = QueuedPool()
    ui = window(qtbot, pool=pool)
    first = ui.document
    ui.begin_open(exp)
    old_job = pool.jobs[0]
    ui.cancel_loading()
    old_job.run()
    assert ui.document is first
    pool.jobs.clear()
    ui.begin_open(exp)
    discovery = pool.jobs.pop()
    discovery.run()  # Enqueues its processed read.
    stale_read = pool.jobs.pop()
    ui.show_demo()   # A new document revokes the old load ticket.
    replacement = ui.document
    stale_read.run()
    assert ui.document is replacement
    assert not ui.loading


def test_newer_open_wins_and_error_keeps_document(qtbot, monkeypatch, tmp_path):
    exp, _, _, _ = experiment(tmp_path)
    pool = QueuedPool()
    ui = window(qtbot, pool=pool)
    errors = []
    monkeypatch.setattr(ui, "show_input_error", lambda code: errors.append(code))
    ui.begin_open(exp)
    old = pool.jobs.pop()
    ui.begin_open(exp)
    old.run()
    assert ui.loading
    pool.run_next()
    pool.run_next()
    assert not ui.loading and ui.document.current.data.opening_mode == "operator"
    current = ui.document
    ui.begin_open(tmp_path / "missing")
    pool.run_next()
    assert errors == ["source_not_found"]
    assert ui.document is current


def test_changed_revision_and_chooser_cancel(qtbot, monkeypatch, tmp_path):
    exp, proc, _, _ = experiment(tmp_path)
    shutil.copytree(proc, proc.with_name("2"))
    pool = QueuedPool()
    ui = window(qtbot, pool=pool)
    monkeypatch.setattr(ui, "choose_candidate", lambda choices: None)
    before = ui.document
    ui.begin_open(exp)
    pool.run_next()
    assert ui.document is before and not ui.loading
    ui.begin_open(exp)
    ui.document.apply_view(ViewState(0, 10, -1, 2))
    pool.run_next()
    assert ui.document is before and ui.document.current.view.x_min == 0
    assert not ui.loading


def test_selection_change_rejected(tmp_path):
    from bruker_nmr.io.errors import InputError
    exp, _, a, _ = experiment(tmp_path)
    selected, = discover_choices(exp)
    a["NUC1"] = "13C"
    status(exp / "acqus", a)
    with pytest.raises(InputError, match="Source changed"):
        load_choice(exp, selected)


def drag(ui, start, stop, qtbot):
    viewport = ui.plot.viewport()
    # Real users hover the handle before pressing. Let newly created scene items
    # claim their drag before a native Windows press can be routed to the background.
    QApplication.processEvents()
    qtbot.wait(25)
    hover = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(start),
                        QPointF(viewport.mapToGlobal(start)), Qt.MouseButton.NoButton,
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, hover)
    qtbot.wait(25)
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    # Explicit button state keeps QGraphicsScene drag deterministic offscreen.
    for t in [.25, .5, .75, 1]:
        # Respect PyQtGraph's real mouse-rate limit. Zero-time bursts can all be
        # dropped on fast Windows runners, making a second drag become a click.
        qtbot.wait(25)
        pos = QPointF(start + (stop - start) * t)
        event = QMouseEvent(QMouseEvent.Type.MouseMove, pos,
                            QPointF(viewport.mapToGlobal(pos.toPoint())),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(viewport, event)
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=stop)
    QApplication.processEvents()


def points(ui):
    rect = ui.view_box.sceneBoundingRect()
    a = ui.plot.mapFromScene(rect.topLeft() + QPointF(rect.width() * .2, rect.height() * .2))
    b = ui.plot.mapFromScene(rect.topLeft() + QPointF(rect.width() * .65, rect.height() * .65))
    return a, b


def test_rectangle_pan_full_view_and_history(qtbot):
    ui = window(qtbot)
    qtbot.wait(50)
    opening = ui.document.opening
    raw = hashlib.sha256(opening.data.real.tobytes()).digest()
    a, b = points(ui)
    drag(ui, a, b, qtbot)
    assert ui.document.view_count == 1
    zoom = ui.document.current.view
    assert zoom.x_max - zoom.x_min < opening.view.x_max - opening.view.x_min
    assert zoom.y_max - zoom.y_min < opening.view.y_max - opening.view.y_min
    ui.pan_action.trigger()
    a, b = points(ui)
    drag(ui, a, b, qtbot)
    assert ui.document.view_count == 2
    assert ui.document.current.view != zoom
    ui.previous_action.trigger()
    assert ui.document.current.view == zoom
    ui.full_action.trigger()
    assert ui.document.current.view == opening.view
    ui.previous_action.trigger()
    assert ui.document.current.view == zoom
    ui.reset_action.trigger()
    assert ui.document.current.view == opening.view
    assert hashlib.sha256(opening.data.real.tobytes()).digest() == raw
    assert ui.document.current.data is opening.data


def test_vertical_wheel_burst_one_command(qtbot):
    ui = window(qtbot)
    qtbot.wait(50)
    before = ui.document.current.view
    viewport = ui.plot.viewport()
    position = ui.plot.mapFromScene(ui.view_box.sceneBoundingRect().center())
    for _ in range(3):
        event = QWheelEvent(QPointF(position), QPointF(viewport.mapToGlobal(position)),
                            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(viewport, event)
    qtbot.waitUntil(lambda: ui.document.view_count == 1, timeout=3000)
    after = ui.document.current.view
    assert after.x_min == pytest.approx(before.x_min)
    assert after.x_max == pytest.approx(before.x_max)
    assert after.y_max - after.y_min < before.y_max - before.y_min
    ui.previous_view()
    assert ui.document.current.view == before


def test_close_while_loading_invalidates_worker(qtbot, tmp_path):
    exp, _, _, _ = experiment(tmp_path)
    pool = QueuedPool()
    ui = window(qtbot, pool=pool)
    ui.begin_open(exp)
    doc = ui.document
    ui.close()
    pool.run_next()
    assert ui.document is doc
    assert not ui.loading


def test_keyboard_navigation_shortcuts(qtbot):
    ui = window(qtbot)
    ui.activateWindow()
    ui.setFocus()
    qtbot.wait(50)
    before = ui.document.current.view
    ui.commit_view(ViewState(1, 9, -.1, .6))
    qtbot.keyClick(ui, Qt.Key.Key_Backspace)
    assert ui.document.current.view == before
    ui.commit_view(ViewState(1, 9, -.1, .6))
    qtbot.keyClick(ui, Qt.Key.Key_Left, modifier=Qt.KeyboardModifier.AltModifier)
    assert ui.document.current.view == before
    qtbot.keyClick(ui, Qt.Key.Key_F)
    assert ui.document.current.view == before
    qtbot.keyClick(ui, Qt.Key.Key_P)
    assert ui.view_box.state["mouseMode"] == ui.view_box.PanMode


def test_real_modal_chooser_open_and_cancel(qtbot, tmp_path):
    from PySide6.QtCore import QTimer
    exp, proc, _, _ = experiment(tmp_path)
    shutil.copytree(proc, proc.with_name("2"))
    ui = window(qtbot)

    def respond(accept):
        timer = QTimer(ui)
        timer.setInterval(10)
        def tick():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, ExperimentChooser):
                timer.stop()
                if accept:
                    dialog.table.selectRow(1)
                    dialog.buttons.button(QDialogButtonBox.StandardButton.Open).click()
                else:
                    dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        timer.timeout.connect(tick)
        timer.start()
        return timer

    accept_timer = respond(True)
    ui.begin_open(exp)
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert not accept_timer.isActive()
    assert ui.document.current.data.processing_number == 2
    old = ui.document
    cancel_timer = respond(False)
    ui.begin_open(exp)
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert not cancel_timer.isActive()
    assert ui.document is old
