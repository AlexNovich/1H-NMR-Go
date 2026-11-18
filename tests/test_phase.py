"""Analytic phase truth, transaction boundaries and cancellable proposals."""

import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialogButtonBox

from bruker_nmr.domain.document import Document, DocumentData, LoadGate
from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.phase_benchmark import analytic, noisy_observation, regional_metrics
from bruker_nmr.processing.fid import RawResult, Recipe, immutable
from bruker_nmr.processing.phase import Phase, PhaseCancelled, auto_phase, rotate
from tests.test_viewer import window


def raw_document(z):
    from bruker_nmr.domain.integrals import IntegralGrid
    x = immutable(12 - np.arange(len(z)) * 12/len(z))
    real, imag = immutable(z.real), immutable(z.imag)
    output = RawResult(immutable(np.zeros(32, dtype=complex)), x, real, imag,
                       Recipe(len(z)), {}, IntegralGrid(real, x[0], 12/len(z)))
    return Document(DocumentData(x, real, 'raw', output, True))





@pytest.mark.parametrize('pivot', [0, .23, .5, 1])
def test_manual_independent_formula_inverse_and_immutable(pivot):
    _, truth, _ = analytic(1024)
    phase = Phase(63, -91, pivot)
    # Direct analytic expression, not rotate-to-create expected data.
    expected = truth * np.exp(1j*np.pi/180*(63-91*(np.arange(1024)/1024-pivot)))
    actual = rotate(truth, phase)
    assert np.allclose(actual, expected, atol=1e-14)
    assert np.allclose(rotate(actual, Phase(-63, 91, pivot)), truth, atol=1e-14)
    assert not actual.flags.writeable
    assert not np.shares_memory(actual, truth)


@pytest.mark.parametrize('phase', [Phase(47, -83), Phase(-115, 135), Phase(155, 0)])
def test_blind_clean_regional_truth(phase):
    x, truth, windows = analytic()
    distorted = truth * np.exp(1j*np.deg2rad(phase.p0+phase.p1*(x-phase.pivot)))
    proposal = auto_phase(distorted)
    corrected = rotate(distorted, proposal.phase)
    occupied = np.logical_or.reduce([abs(x-c)<8*w for c, w in windows])
    error = np.angle(corrected[occupied]/truth[occupied], deg=True)
    assert max(abs(error)) <= 1
    assert proposal.confident
    for row in regional_metrics(x, truth, corrected, windows):
        assert row['relative_area_error'] <= .02
        assert row['dispersion_over_absorption'] <= .05


@pytest.mark.parametrize('kind', ['broad', 'solvent', 'noise', 'sparse', 'mixed'])
def test_uncertain_cases_visible_and_manual_rescue(kind):
    x, truth, windows = analytic(kind=kind)
    observed = noisy_observation(truth) if kind == 'noise' else truth
    distorted = observed * np.exp(1j*np.deg2rad(47-83*(x-.5)))
    proposal = auto_phase(distorted)
    assert not proposal.confident
    assert 'Low confidence' in proposal.reason
    corrected = rotate(distorted, Phase(-47, 83))
    assert np.allclose(corrected, observed, atol=1e-13)
    for row in regional_metrics(x, observed, corrected, windows):
        assert row['relative_area_error'] < 1e-12


def test_phase_state_areas_reference_undo_reset_and_source():
    _, truth, _ = analytic(1024)
    doc = raw_document(truth)
    original = doc.current
    doc.add_integral(10, 9)
    before = doc.current
    old_area = integral_snapshot(before.integrals, before.data.integration)[0].area
    assert doc.apply_phase(Phase(20, -10))
    after = doc.current
    assert after.data.phase_steps == (Phase(20, -10),)
    row = integral_snapshot(after.integrals, after.data.integration)[0]
    assert row.area != old_area and row.value == pytest.approx(1)
    assert np.array_equal(before.data.real, truth.real)
    doc.undo()
    assert doc.current is before
    doc.redo()
    assert doc.current is after
    doc.reset()
    assert doc.current.data is original.data
    assert not doc.current.integrals.regions


def test_invalid_reference_does_not_mutate_history():
    _, truth, _ = analytic(1024)
    doc = raw_document(truth)
    doc.add_integral(10, 9)
    before, count = doc.current, doc.undo_count
    with pytest.raises(ValueError):
        doc.apply_phase(Phase(180))
    assert doc.current is before and doc.undo_count == count


@pytest.mark.parametrize('invalid', [np.zeros(32), np.ones((2, 4)), [1, np.nan]*20])
def test_invalid_auto(invalid):
    with pytest.raises(ValueError):
        auto_phase(invalid)


def test_cancel_in_objective_and_stale_ticket():
    _, truth, _ = analytic(1024)
    calls = 0
    def stop():
        nonlocal calls
        calls += 1
        return calls > 15
    with pytest.raises(PhaseCancelled):
        auto_phase(truth, cancelled=stop)
    assert calls == 16
    doc = raw_document(truth)
    gate = LoadGate()
    ticket = gate.begin(doc)
    doc.apply_phase(Phase(15))
    assert not gate.accepts(ticket, doc)


def test_gui_phase_preview_cancel_apply_and_undo(qtbot, monkeypatch):
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    assert not ui.editing_actions['Phase'].isEnabled()
    _, z, _ = analytic(1024)
    ui.document = raw_document(z)
    ui._render_document()
    initial, rev = ui.document.current, ui.document.revision
    ui.open_phase()
    panel = ui.phase_dialog
    panel.controls[0].setValue(30)
    qtbot.waitUntil(lambda: ui._phase_preview is not None)
    assert ui.document.current is initial and ui.document.revision == rev
    assert not np.array_equal(ui._phase_preview, initial.data.real)
    panel.reject()
    assert ui._phase_preview is None and ui.document.current is initial
    ui.open_phase()
    panel = ui.phase_dialog
    panel.controls[0].setValue(25)
    qtbot.mouseClick(panel.buttons.button(QDialogButtonBox.StandardButton.Apply),
                     __import__('PySide6.QtCore', fromlist=['Qt']).Qt.MouseButton.LeftButton)
    assert ui.document.current.data.phase_steps
    ui.undo()
    assert ui.document.current is initial
    ui.close()


def test_gui_auto_worker_cancel_and_manual_rescue(qtbot, monkeypatch):
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    _, z, _ = analytic(1024)
    ui.document = raw_document(z)
    ui._render_document()
    initial = ui.document.current
    ui.open_phase()
    panel = ui.phase_dialog
    panel.start_auto()
    panel.cancel_search()
    qtbot.waitUntil(lambda: not panel.jobs, timeout=30000)
    assert ui.document.current is initial
    ticks = []
    heartbeat = QTimer(ui)
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: ticks.append(1))
    heartbeat.start()
    panel.start_auto()
    qtbot.waitUntil(lambda: not panel.jobs, timeout=30000)
    heartbeat.stop()
    assert ticks, 'GUI event loop blocked by optimizer'
    assert 'Proposal' in panel.note.text() or 'LOW CONFIDENCE' in panel.note.text()
    assert ui.document.current is initial
    panel.reject()
    ui.close()


@pytest.mark.parametrize('args', [(np.nan, 0, .5), (0, np.inf, .5), (0, 0, -1), (721, 0, .5)])
def test_invalid_manual(args):
    with pytest.raises(ValueError):
        Phase(*args)


def test_imported_relative_phase_no_stored_phc(tmp_path):
    from bruker_nmr.io.source import Source
    from tests.test_reader import experiment
    exp, proc, _, _ = experiment(tmp_path, dtype=2)
    with Source(exp) as source:
        (candidate,) = source.discover()
        op = source.read_processed()
    doc = Document(DocumentData.from_operator(op, candidate))
    assert doc.current.data.can_phase
    original = op.complex_data.copy()
    p = Phase(26, -40, .2)
    doc.apply_phase(p)
    expected = original * np.exp(1j*np.pi/180*(26-40*(np.arange(len(original))/len(original)-.2)))
    assert np.allclose(doc.current.data.complex_data, expected)
    assert np.array_equal(op.complex_data, original)
    assert doc.current.integrals.normalization.kind in ('absent', 'unverified')
    doc.reset()
    assert doc.current.data.operator is op


def test_failed_worker_safe_error_and_cancellation(monkeypatch):
    from bruker_nmr.ui.phase import PhaseJob
    _, z, _ = analytic(1024)
    doc = raw_document(z)
    job = PhaseJob(LoadGate().begin(doc), z)
    def fail(*args, **kwargs):
        raise RuntimeError('untrusted internal detail')
    monkeypatch.setattr('bruker_nmr.ui.phase.auto_phase', fail)
    results = []
    job.signals.finished.connect(lambda *args: results.append(args))
    job.run()
    assert results[-1][2] is None
    assert 'untrusted' not in results[-1][3]
    job.cancelled.set()
    job.run()
    assert results[-1][2:] == (None, None)


def test_preview_stale_revision_discards_result(qtbot, monkeypatch):
    from bruker_nmr.processing.phase import Proposal
    from bruker_nmr.ui.phase import PhaseJob
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    _, z, _ = analytic(1024)
    ui.document = raw_document(z)
    ui._render_document()
    ui.open_phase()
    panel = ui.phase_dialog
    ticket = panel.gate.begin(ui.document)
    job = PhaseJob(ticket, z)
    ui.document.apply_phase(Phase(10))
    current = ui.document.current
    result = Proposal(Phase(45), 'weighted-acme', 1, True, 'test', 1)
    panel.finished_search(job, ticket, result, None)
    assert panel.controls[0].value() == 0
    panel.apply()
    assert not panel.isVisible() and ui.document.current is current
    ui.close()


@pytest.mark.parametrize('kind', ['weak_negative', 'branch'])
def test_review_phase_branch_and_weak_negative_peak(kind):
    x = np.arange(8192)/8192
    if kind == 'weak_negative':
        centers, amplitudes, width, p0, p1 = [.2, .5, .8], [1, .9, -.15], .0008, 35, -50
    else:
        centers, amplitudes, width, p0, p1 = [.36, .42, .88], [1, .9, .15], .001, 172, 145
    truth = sum(a*width/(width+1j*(x-c)) for c, a in zip(centers, amplitudes))
    observed = truth * np.exp(1j*np.deg2rad(p0+p1*(x-.5)))
    proposal = auto_phase(observed)
    corrected = rotate(observed, proposal.phase)
    if kind == 'weak_negative':
        assert not proposal.confident
        assert corrected.real[round(.8*8192)] < -.14
    else:
        occupied = np.logical_or.reduce([abs(x-c) < 8*width for c in centers])
        assert max(abs(np.angle(corrected[occupied]/truth[occupied], deg=True))) < 1
        assert proposal.confident
    assert -180 <= proposal.phase.p0 < 180


def test_shared_history_at_maximum_si():
    doc = raw_document(np.ones(4*1024*1024, dtype=complex))
    opening = doc.current
    doc.apply_phase(Phase(10))
    assert doc.retained_bytes < 256*1024*1024
    # Shared source, axis and original prefix are counted once across snapshots.
    assert doc.current.data.operator is opening.data.operator
    doc.undo()
    assert doc.current is opening


@pytest.mark.parametrize('transition', ['show_demo', 'undo', 'redo', 'reset', 'close', 'cancel_loading'])
def test_phase_preview_transition_cleanup(qtbot, monkeypatch, transition):
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    ui.document = raw_document(analytic(1024)[1])
    ui.document.apply_phase(Phase(5))
    if transition == 'redo':
        ui.document.undo()
    ui._render_document()
    ui.open_phase()
    panel = ui.phase_dialog
    panel.controls[0].setValue(30)
    panel.preview()
    panel.start_auto()
    jobs = list(panel.jobs)
    getattr(ui, transition)()
    assert ui._phase_preview is None
    assert not panel.isVisible()
    assert all(j.cancelled.is_set() for j in jobs)
    qtbot.waitUntil(lambda: not panel.jobs, timeout=30000)
    ui.close()


def test_phase_preserves_zoom_and_fine_buttons(qtbot, monkeypatch):
    from PySide6.QtCore import Qt

    from bruker_nmr.domain.document import ViewState
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    ui.document = raw_document(analytic(1024)[1])
    view = ViewState(2, 4, -.05, .25)
    ui.document.apply_view(view)
    ui._render_document()
    ui.open_phase()
    panel = ui.phase_dialog
    for spin, (minus, plus) in zip(panel.controls, panel.step_buttons):
        assert spin.width() >= spin.fontMetrics().horizontalAdvance('-1440.000') + 28
        start = spin.value()
        qtbot.mouseClick(plus, Qt.MouseButton.LeftButton)
        assert spin.value() == pytest.approx(start+spin.singleStep())
        qtbot.mouseClick(minus, Qt.MouseButton.LeftButton)
        assert spin.value() == pytest.approx(start)
    panel.controls[0].setValue(20)
    panel.preview()
    assert ui.view_box.snapshot() == view
    panel.apply()
    assert ui.document.current.view == view
    assert ui.view_box.snapshot() == view
    ui.close()


def test_noise_fixture_retains_noiseless_truth():
    _, truth, _ = analytic(kind='noise')
    _, clean, _ = analytic(kind='narrow')
    assert np.array_equal(truth, clean)
    observed = noisy_observation(truth)
    assert not np.array_equal(observed, truth)
    _, repeated, _ = analytic(kind='noise')
    assert np.array_equal(truth, repeated)


def test_entropy_uses_only_adjacent_full_grid_bins():
    from bruker_nmr.processing.phase import _adjacent_weights
    indices = np.array([0, 1, 2, 63, 64, 127])
    weights = np.array([1., 4., 1., 1., 9., 1.])
    assert np.array_equal(_adjacent_weights(indices, weights), [2, 2, 0, 3, 0])
