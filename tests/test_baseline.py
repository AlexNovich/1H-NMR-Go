"""Independent baseline truth, explicit anchor failures and reversible state."""
from dataclasses import replace

import numpy as np
import pytest

from bruker_nmr.domain.document import Document, DocumentData, ViewState
from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.processing.baseline import Anchor, fit_baseline
from bruker_nmr.processing.fid import immutable
from bruker_nmr.processing.phase import Phase
from tests.test_phase import raw_document


def fixture(n=16384, *, slope=.007, origin=6.):
    x = np.linspace(14, -2, n, endpoint=False)
    # Compact analytic features have exactly clean, independently declared anchors.
    narrow = np.maximum(0, 1-abs(x-5.5)/.08)**2
    broad = .3*np.maximum(0, 1-((x-2)/1.5)**2)**2
    broad[abs(x-2) >= 1.5] = 0
    edge = .4*np.maximum(0, 1-((x-13)/.4)**2)**2
    edge[abs(x-13) >= .4] = 0
    signal = narrow+broad+edge
    background = .12+slope*(x-origin)
    return x, signal, background


def data_document(x, real):
    return Document(DocumentData(immutable(x), immutable(real), 'demo'))


@pytest.mark.parametrize('mode,slope', [('constant', 0), ('linear', .007), ('linear', -.02)])
def test_known_background_broad_narrow_edge_areas(mode, slope):
    x, signal, background = fixture(slope=slope)
    anchors = (Anchor(11, 10), Anchor(.2, -.5))
    fit = fit_baseline(x, signal+background, anchors, mode=mode)
    corrected = signal+background-fit.values(x)
    assert np.allclose(corrected, signal, atol=1e-13)
    assert fit.slope == pytest.approx(slope, abs=1e-13)
    for high, low in [(5.6, 5.4), (3.5, .5), (13.4, 12.6)]:
        region = (x <= high) & (x > low)
        assert abs(np.sum(corrected[region])/np.sum(signal[region])-1) < .01


def test_baseline_off_cancel_apply_disable_and_mixed_undo():
    x, signal, background = fixture()
    doc = data_document(x, signal+background)
    assert doc.current.data.baseline is None
    doc.add_integral(3.5, .5)
    doc.assign_protons(doc.current.integrals.regions[0].id, 3)
    view = ViewState(0, 4, -.05, .6)
    doc.apply_view(view)
    before = doc.current
    fit = fit_baseline(x, before.data.real, (Anchor(11, 10), Anchor(.2, -.5)), mode='linear')
    candidate = before.data.with_baseline(fit)
    # Preview is numerical only, with no document or history mutation.
    assert doc.current is before
    count = doc.undo_count
    assert np.array_equal(before.data.real, signal+background)
    doc.apply_baseline(fit, prepared=candidate)
    assert doc.undo_count == count+1 and doc.current.view == view
    row = integral_snapshot(doc.current.integrals, doc.current.data.integration)[0]
    assert row.value == pytest.approx(3)
    applied = doc.current
    assert np.allclose(applied.data.real, signal, atol=1e-13)
    doc.disable_baseline()
    assert doc.current.data is before.data
    assert integral_snapshot(doc.current.integrals, doc.current.data.integration)[0].value == pytest.approx(3)
    doc.undo()
    assert doc.current is applied
    doc.undo()
    assert doc.current is before
    doc.redo()
    assert doc.current is applied
    doc.reset()
    assert doc.current.data is doc.opening.data


def test_baseline_is_not_cumulative_and_frozen_scale_retained():
    x, signal, bg = fixture(slope=0)
    doc = data_document(x, signal+bg)
    doc.add_integral(3.5, .5)
    doc.add_integral(5.6, 5.4)
    doc.delete_integral(doc.current.integrals.normalization.anchor_id)
    norm = doc.current.integrals.normalization
    original = doc.current.data
    fit = fit_baseline(x, original.real, (Anchor(11, 10),))
    doc.apply_baseline(fit)
    once = doc.current.data.real.copy()
    doc.apply_baseline(fit)
    assert np.array_equal(doc.current.data.real, once)
    assert doc.current.integrals.normalization == norm
    doc.disable_baseline()
    assert doc.current.data is original and doc.current.integrals.normalization == norm


def test_phase_invalidates_baseline_using_exact_pre_baseline_complex():
    x, signal, bg = fixture(slope=0)
    doc = raw_document(signal+bg+1j*np.zeros(len(x)))
    original = doc.current.data
    fit = fit_baseline(original.ppm, original.real, (Anchor(11, 10),))
    doc.apply_baseline(fit)
    before = doc.current
    doc.apply_phase(Phase(32, -18))
    assert doc.current.data.baseline is None
    expected = original.complex_data*np.exp(1j*np.deg2rad(32-18*(np.arange(len(x))/len(x)-.5)))
    assert np.allclose(doc.current.data.complex_data, expected)
    doc.undo()
    assert doc.current is before
    doc.apply_phase(Phase(0))
    assert doc.current.data.baseline is None


@pytest.mark.parametrize('mode,anchors', [
    ('constant', ()), ('linear', (Anchor(10, 9),)),
    ('linear', (Anchor(10, 9), Anchor(9.5, 8.5))),
    ('linear', (Anchor(10, 9.999), Anchor(9.998, 9.997))),
    ('constant', (Anchor(30, 20),)), ('cubic', (Anchor(10, 9),)),
])
def test_missing_overlap_degenerate_outside_anchors_reject(mode, anchors):
    x, signal, bg = fixture()
    with pytest.raises(ValueError):
        fit_baseline(x, signal+bg, anchors, mode=mode)


def test_clipped_and_excluded_anchors_and_insufficient_remaining_points():
    x, signal, bg = fixture(slope=0)
    fit = fit_baseline(x, signal+bg, (Anchor(15, 13.8),))
    assert fit.anchors[0].high == pytest.approx(14)
    assert fit.intercept == pytest.approx(.12)
    doc = data_document(x, signal+bg)
    doc.add_integral(11, 10)
    with pytest.raises(ValueError, match='exclusions'):
        fit_baseline(x, signal+bg, (Anchor(11, 10),), excluded=doc.current.integrals.regions)


def test_contaminated_anchors_warn_without_claiming_quiet_regions():
    x, signal, bg = fixture(slope=0)
    fit = fit_baseline(x, signal+bg, (Anchor(11, 10), Anchor(5.54, 5.46), Anchor(.2, -.5)))
    assert fit.warnings
    assert fit.intercept == pytest.approx(.12)
    # Uniform contamination cannot be distinguished from a constant background.
    unidentifiable = fit_baseline(x, signal+bg+.3, (Anchor(11, 10),))
    assert unidentifiable.intercept == pytest.approx(.42)


def test_wide_anchor_warning_is_advisory_and_does_not_change_the_fit():
    x, signal, bg = fixture(slope=0)
    anchor = Anchor(float(x.max()), float(x.min()))
    fit = fit_baseline(x, signal+bg, (anchor,))
    assert any('25%' in warning for warning in fit.warnings)
    assert fit.intercept == float(np.median((signal+bg)[:-1]))
    assert fit.slope == 0


def test_recorded_peak_warning_preserves_coefficients():
    x, signal, bg = fixture()
    anchors = (Anchor(11, 10), Anchor(.2, -.5))
    plain = fit_baseline(x, signal+bg, anchors, mode='linear')
    annotated = fit_baseline(x, signal+bg, anchors, mode='linear', peak_positions=(10.5,))
    assert any('recorded peak' in warning for warning in annotated.warnings)
    assert np.array_equal(plain.values(x), annotated.values(x))
    assert plain.summaries == annotated.summaries
    assert not fit_baseline(x, signal+bg, anchors, mode='linear', peak_positions=(5.5,)).warnings
    with pytest.raises(ValueError, match='finite native ppm'):
        fit_baseline(x, signal+bg, anchors, peak_positions=(np.nan,))


def test_limited_axis_ascending_and_no_integrals():
    x = np.linspace(4.2, 3.8, 1024)
    signal = np.maximum(0, 1-abs(x-4)/.02)
    bg = .2+.02*(x-4)
    anchors = (Anchor(4.18, 4.15), Anchor(3.85, 3.82))
    for xx, yy in [(x, signal+bg), (x[::-1], (signal+bg)[::-1])]:
        fit = fit_baseline(xx, yy, anchors, mode='linear')
        assert np.allclose(yy-fit.values(xx), signal if xx[0]>xx[-1] else signal[::-1])


def test_invalid_anchor_data_and_stale_preview():
    for high, low in [(1, 1), (np.nan, 0), (0, 2)]:
        with pytest.raises(ValueError):
            Anchor(high, low)
    x, signal, bg = fixture()
    for xx, yy in [(x, np.full(len(x), np.nan)), (x, np.ones((2, 2))),
                   (np.ones(len(x)), signal), (x, signal.astype(complex))]:
        with pytest.raises(ValueError):
            fit_baseline(xx, yy, (Anchor(11, 10),))
    doc = data_document(x, signal+bg)
    fit = fit_baseline(x, signal+bg, (Anchor(11, 10),))
    candidate = doc.current.data.with_baseline(fit)
    doc.current = replace(doc.current, data=DocumentData(immutable(x), immutable(signal), 'demo'))
    before = doc.current
    with pytest.raises(ValueError, match='Stale'):
        doc.apply_baseline(fit, prepared=candidate)
    assert doc.current is before


def test_contaminated_lorentzian_edges_cannot_prove_broad_area_preservation():
    x = np.linspace(14, -2, 65536, endpoint=False)
    truth = 1/(1+((x-6)/1.5)**2)
    bg = .1+.01*x
    doc = data_document(x, truth+bg)
    original = doc.current
    # Explicitly choosing tails still does not make those tails signal-free.
    fit = fit_baseline(x, truth+bg, (Anchor(14, 13), Anchor(-1, -1.9)), mode='linear')
    proposal = truth+bg-fit.values(x)
    region = (x >= 3) & (x < 9)
    assert abs(sum(proposal[region])/sum(truth[region])-1) > .01
    assert doc.current is original and doc.current.data.baseline is None


def test_invalid_reference_apply_keeps_prior_state():
    x, signal, bg = fixture(slope=0)
    signal = -.06 * ((x < 6) & (x > 5))
    doc = data_document(x, signal+bg)
    doc.add_integral(6, 5)
    before, count = doc.current, doc.undo_count
    fit = fit_baseline(x, signal+bg, (Anchor(11, 10),))
    with pytest.raises(ValueError, match='Reference area'):
        doc.apply_baseline(fit)
    assert doc.current is before and doc.undo_count == count


def setup_ui(qtbot, monkeypatch):
    from tests.test_viewer import window
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    x, signal, bg = fixture()
    ui.document = data_document(x, signal+bg)
    ui.document.add_integral(3.5, .5)
    ui._render_document()
    return ui


def set_anchors(panel):
    panel.mode.setCurrentText('Linear')
    for high, low in [(11, 10), (.2, -.5)]:
        panel.high.setValue(high)
        panel.low.setValue(low)
        panel.add_anchor()


@pytest.mark.parametrize('calibrate', [False, True])
def test_gui_baseline_requires_explicit_bounds_and_warns_on_hidden_peak(qtbot, monkeypatch, calibrate):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialogButtonBox
    ui = setup_ui(qtbot, monkeypatch)
    ui.document.clear_integrals()
    ui.document.add_peak(5.5, .1)
    peak = ui.document.current.peaks.records[0]
    ui.document.peak_label(peak.id, False)
    if calibrate:
        ui.document.set_zero(peak.id)
    ui._render_document()
    before, count = ui.document.current, ui.document.undo_count
    ui.open_baseline()
    panel = ui.baseline_dialog
    qtbot.waitUntil(lambda: panel.add.width() >= panel.add.minimumSizeHint().width())
    assert not panel.high.text() and not panel.low.text()
    assert not panel.add.isEnabled()
    qtbot.mouseClick(panel.add, Qt.MouseButton.LeftButton)
    panel.add_anchor()  # The slot itself also rejects unset bounds.
    panel.preview()
    assert not panel.anchors and panel.candidate is None
    assert not panel.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    panel.high.setValue(5.54+before.calibration)
    assert not panel.add.isEnabled()
    panel.low.setValue(5.46+before.calibration)
    assert panel.add.isEnabled()
    qtbot.mouseClick(panel.add, Qt.MouseButton.LeftButton)
    assert len(panel.anchors) == 1
    assert not panel.high.text() and not panel.low.text() and not panel.add.isEnabled()
    panel.preview()
    assert panel.candidate is not None
    assert 'recorded peak' in panel.note.text()
    assert any('recorded peak' in w for w in panel.candidate.baseline.fit.warnings)
    panel.reject()
    assert ui.document.current is before and ui.document.undo_count == count
    assert ui._baseline_preview is None
    ui.close()


def test_gui_baseline_preview_cancel_apply_off_undo(qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialogButtonBox
    ui = setup_ui(qtbot, monkeypatch)
    original, count = ui.document.current, ui.document.undo_count
    ui.open_baseline()
    panel = ui.baseline_dialog
    assert not panel.disable.isEnabled()
    panel.preview()
    assert panel.candidate is None
    assert not panel.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    set_anchors(panel)
    panel.preview()
    assert panel.candidate is not None
    assert ui._baseline_preview is panel.candidate.real
    assert panel.areas.rowCount() == 1
    assert len(panel.overlays) == 3  # baseline and two explicit anchor ranges
    assert ui.document.current is original and ui.document.undo_count == count
    panel.reject()
    assert ui._baseline_preview is None and not panel.overlays
    assert len(ui.plot.listDataItems()) == 1
    assert ui.document.current is original
    ui.open_baseline()
    panel = ui.baseline_dialog
    set_anchors(panel)
    panel.preview()
    panel.apply()
    applied = ui.document.current
    assert applied.data.baseline is not None
    assert ui._baseline_preview is None
    assert ui.document.undo_count == count+1
    ui.open_baseline()
    panel = ui.baseline_dialog
    assert panel.disable.isEnabled()
    assert len(panel.anchors) == 2
    panel.off()
    assert ui.document.current.data is original.data
    ui.undo()
    assert ui.document.current is applied
    ui.undo()
    assert ui.document.current is original
    ui.close()


@pytest.mark.parametrize('transition', ['show_demo', 'undo', 'reset', 'cancel_loading', 'close'])
def test_gui_baseline_stale_transition_cleans_overlays(qtbot, monkeypatch, transition):
    ui = setup_ui(qtbot, monkeypatch)
    ui.open_baseline()
    panel = ui.baseline_dialog
    set_anchors(panel)
    panel.preview()
    getattr(ui, transition)()
    assert ui._baseline_preview is None
    assert not panel.overlays and not panel.isVisible()
    assert len(ui.plot.listDataItems()) == 1
    ui.close()


def test_gui_phase_after_baseline_preview_cancel_and_apply(qtbot, monkeypatch):
    from tests.test_viewer import window
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    x, signal, bg = fixture(slope=0)
    ui.document = raw_document((signal+bg).astype(complex))
    source = ui.document.current.data
    fit = fit_baseline(source.ppm, source.real, (Anchor(11, 10),))
    ui.document.apply_baseline(fit)
    before = ui.document.current
    ui._render_document()
    ui.open_phase()
    panel = ui.phase_dialog
    assert 'refit' in panel.refit_notice.text()
    assert np.array_equal(panel.base, source.complex_data)
    panel.controls[0].setValue(25)
    panel.preview()
    panel.reject()
    assert ui.document.current is before
    ui.open_phase()
    panel = ui.phase_dialog
    panel.controls[0].setValue(25)
    panel.apply()
    assert ui.document.current.data.baseline is None
    ui.undo()
    assert ui.document.current is before
    ui.close()
