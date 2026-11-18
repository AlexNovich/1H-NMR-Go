"""Peak truth, coordinate-only reference and mixed immutable-state recovery."""

import numpy as np
import pytest

from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.domain.peaks import PeakState, nearest_extremum
from bruker_nmr.processing.baseline import Anchor, fit_baseline
from bruker_nmr.processing.phase import Phase
from tests.test_phase import raw_document


def fixture():
    x = np.linspace(12, 0, 4096, endpoint=False)
    y = np.exp(-(((x - 7) / 0.08) ** 2)) - 0.5 * np.exp(-(((x - 3) / 0.1) ** 2)) + 0.05
    doc = raw_document(y.astype(complex))
    return doc


def test_manual_positive_negative_peak_and_no_display_picking():
    doc = fixture()
    doc.add_peak(7.02, 0.1)
    doc.add_peak(3.01, 0.1)
    a, b = doc.current.peaks.records
    assert a.ppm == pytest.approx(7, abs=doc.current.grid.step)
    assert b.ppm == pytest.approx(3, abs=doc.current.grid.step)
    assert a.source == "manual" and b.intensity < 0
    for position, radius in [(20, 0.1), (7, 0), (np.nan, 0.1), (10, 0.01)]:
        with pytest.raises(ValueError):
            nearest_extremum(doc.current.data.ppm, doc.current.data.real, position, radius)


def test_zero_is_metadata_only_and_moves_all_display_coordinates():
    doc = fixture()
    doc.add_integral(7.3, 6.7)
    doc.add_peak(7, 0.1)
    before = doc.current
    peak = before.peaks.records[0]
    areas = integral_snapshot(before.integrals, before.grid)
    doc.set_zero(peak.id)
    after = doc.current
    assert after.data is before.data and after.integrals is before.integrals
    assert after.peaks is before.peaks
    assert peak.ppm + after.calibration == 0
    assert after.grid.prefix is before.grid.prefix
    shifted = integral_snapshot(after.integrals, after.grid)
    assert shifted[0].area == areas[0].area and shifted[0].value == areas[0].value
    assert shifted[0].high == pytest.approx(areas[0].high - peak.ppm)
    doc.undo()
    assert doc.current is before
    doc.redo()
    doc.edit_integral(before.integrals.regions[0].id, 0.2, -0.2)
    assert doc.current.integrals.regions[0].left > 0


def test_label_visibility_never_deletes_and_numeric_position_is_bounded():
    doc = fixture()
    doc.add_peak(7, 0.1)
    id = doc.current.peaks.records[0].id
    doc.peak_label(id, False)
    doc.appearance(markers=False, label_mode="hidden", line_width=0.5)
    assert len(doc.current.peaks.records) == 1
    doc.appearance(markers=True, label_mode="all")
    assert not doc.current.peaks.by_id(id).label_visible
    doc.peak_label(id, True)
    doc.move_peak(id, 6.98)
    assert doc.current.peaks.by_id(id).ppm == 6.98
    for position in (np.inf, 20, -4):
        before = doc.current
        with pytest.raises(ValueError):
            doc.move_peak(id, position)
        assert doc.current is before
    doc.delete_peak(id)
    assert not doc.current.peaks.records
    doc.undo()
    assert len(doc.current.peaks.records) == 1
    with pytest.raises(ValueError):
        PeakState(label_mode="threshold")


def test_calibrated_baseline_disable_phase_and_twenty_mixed_actions():
    from bruker_nmr.domain.document import Document, DocumentData
    from bruker_nmr.processing.fid import Recipe, process

    n = 4096
    fid = np.exp(2j * np.pi * 300 * np.arange(n) / 3600 - np.pi * 3 * np.arange(n) / 3600)
    params = dict(SW_h=3600.0, BF1=300.0, O1=1800.0, GRPDLY=0.0, NC=0)
    raw = process(fid, params, Recipe(n, 0.3))
    doc = Document(DocumentData(raw.ppm, raw.real, "raw", raw, True))
    states = [doc.current]

    def act(fn):
        assert fn()
        states.append(doc.current)

    act(lambda: doc.add_integral(7.3, 6.7))
    ref = doc.current.integrals.regions[0].id
    act(lambda: doc.assign_protons(ref, 2))
    act(lambda: doc.add_peak(7, 0.1))
    peak = doc.current.peaks.records[0].id
    act(lambda: doc.set_zero(peak))
    act(lambda: doc.appearance(line_width=0.6))
    act(lambda: doc.peak_label(peak, False))
    act(lambda: doc.appearance(label_mode="all"))
    act(lambda: doc.appearance(markers=False))
    act(lambda: doc.appearance(markers=True))
    act(lambda: doc.peak_label(peak, True))
    data = doc.current.data
    fit = fit_baseline(data.ppm, data.real, (Anchor(11, 10),))
    act(lambda: doc.apply_baseline(fit))
    assert doc.current.peaks.by_id(peak).needs_review
    act(lambda: doc.review_peak(peak))
    act(doc.disable_baseline)
    assert doc.current.data is data and doc.current.calibration != 0
    act(lambda: doc.apply_phase(Phase(8)))
    act(lambda: doc.move_peak(peak, -0.01))
    act(lambda: doc.add_integral(-0.3, -0.5))
    act(lambda: doc.appearance(trace_color="#803060"))
    act(lambda: doc.delete_integral(ref))
    assert doc.current.integrals.normalization.kind == "frozen"
    act(lambda: doc.appearance(show_integrals=False))
    act(lambda: doc.delete_peak(peak))
    reprocessed = process(fid, params, Recipe(8192, 0.6))
    act(
        lambda: doc.apply_raw(
            DocumentData(reprocessed.ppm, reprocessed.real, "raw", reprocessed, True)
        )
    )
    assert doc.current.integrals.normalization.kind == "unverified"
    act(lambda: doc.assign_protons(doc.current.integrals.regions[0].id, 1))
    act(doc.clear_integrals)
    act(doc.reset)
    assert len(states) >= 22
    for expected in reversed(states[:-1]):
        assert doc.undo() and doc.current is expected
    for expected in states[1:]:
        assert doc.redo() and doc.current is expected
    assert not doc.can_redo


def test_reprocess_raw_preserves_ranges_reference_calibration_and_review():
    from bruker_nmr.domain.document import DocumentData
    from bruker_nmr.domain.integrals import IntegralGrid
    from bruker_nmr.processing.fid import RawResult, Recipe, immutable

    doc = fixture()
    doc.add_integral(7.3, 6.7)
    doc.add_peak(7, 0.1)
    doc.set_zero(doc.current.peaks.records[0].id)
    before = doc.current
    n = 8192
    x = immutable(12 - np.arange(n) * 12 / n)
    y = immutable(np.exp(-(((x - 7) / 0.08) ** 2)) + 0.05)
    output = RawResult(
        before.data.operator.fid,
        x,
        y,
        immutable(np.zeros(n)),
        Recipe(n),
        {},
        IntegralGrid(y, 12, 12 / n),
    )
    doc.apply_raw(DocumentData(x, y, "raw", output, True))
    assert doc.current.calibration == before.calibration
    assert len(doc.current.integrals.regions) == 1
    assert integral_snapshot(doc.current.integrals, doc.current.grid)[0].value == pytest.approx(1)
    assert doc.current.peaks.records[0].needs_review
    doc.undo()
    assert doc.current is before


def test_zero_offset_still_records_explicit_reference_and_rejects_outside_peak():
    from bruker_nmr.demo import make_demo
    from bruker_nmr.domain.document import Document, DocumentData
    from bruker_nmr.domain.integrals import Peak

    doc = Document(DocumentData.from_demo(make_demo()))
    doc.apply_peaks(PeakState((Peak("zero", 0.0, 1.0, 0, "manual"),)))
    before = doc.current
    doc.set_zero("zero")
    assert doc.current.referenced and doc.current.calibration == 0
    assert doc.current.data is before.data
    assert doc.integration_modified
    doc.undo()
    assert doc.current is before and not doc.current.referenced
    with pytest.raises(ValueError, match="outside"):
        doc.apply_peaks(PeakState((Peak("bad", 1e6, 1.0, 0, "manual"),)))
    assert doc.current is before
