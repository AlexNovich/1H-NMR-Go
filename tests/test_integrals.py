"""Independent finite-window truth, normalization transitions and view invariance."""
import math
from dataclasses import replace

import numpy as np
import pytest

from bruker_nmr.domain.document import Document, DocumentData, ViewState
from bruker_nmr.domain.integrals import (
    IntegralGrid,
    IntegralState,
    Normalization,
    Region,
    add_region,
    assign_reference,
    edit_region,
    has_overlaps,
    integral_snapshot,
)


def grid():
    return IntegralGrid(np.ones(101), 10, .1)


def test_half_bin_ties_adjacency_and_signed_area():
    g = IntegralGrid(np.arange(-10, 11, dtype=float), 20, 1)
    assert g.bounds(19.5, 17.5) == (0, 2)  # np.rint(0.5)=0; np.rint(2.5)=2.
    assert g.bounds(18.5, 15.5) == (2, 4)
    a, b = Region("a", 0, 5), Region("b", 5, 10)
    assert g.area(a) + g.area(b) == sum(range(-10, 0))
    assert g.area(a) < 0
    assert not has_overlaps(IntegralState((a, b)))
    assert has_overlaps(IntegralState((a, replace(b, left=4))))
    with pytest.raises(ValueError):
        g.bounds(19.9, 19.8)
    with pytest.raises(ValueError):
        g.bounds(21, 18)
    assert not g.prefix.flags.writeable
    with pytest.raises(ValueError):
        g.prefix.setflags(write=True)


def test_reference_edit_delete_frozen_add_undo_and_clear_sequence():
    y = np.ones(101)
    ppm = np.linspace(10, 0, len(y))
    data = DocumentData(np.frombuffer(ppm.tobytes()), np.frombuffer(y.tobytes()), "demo")
    d = Document(data)
    d.add_integral(9, 8)
    anchor = d.current.integrals.regions[0].id
    assert integral_snapshot(d.current.integrals, data.integration)[0].value == pytest.approx(1)
    d.add_integral(7, 6)
    d.assign_protons(anchor, 2)
    before = d.current
    d.edit_integral(anchor, 9, 7.5)
    rows = integral_snapshot(d.current.integrals, data.integration)
    assert rows[0].value == pytest.approx(2)
    assert rows[1].value == pytest.approx(4 / 3)
    changed = d.current
    d.delete_integral(anchor)
    assert d.current.integrals.normalization.kind == "frozen"
    assert integral_snapshot(d.current.integrals, data.integration)[0].value == rows[1].value
    d.add_integral(5, 4)
    assert d.current.integrals.normalization.kind == "frozen"
    assert integral_snapshot(d.current.integrals, data.integration)[1].value == rows[1].value
    d.undo()
    d.undo()
    assert d.current == changed
    d.undo()
    assert d.current == before
    d.redo()
    d.clear_integrals()
    assert d.current.integrals == IntegralState()
    d.add_integral(5, 4)
    assert integral_snapshot(d.current.integrals, data.integration)[0].value == pytest.approx(1)
    d.reset()
    assert d.current.integrals == d.opening.integrals
    d.undo()
    assert d.integration_modified


def test_imported_uncertainty_and_no_inferred_anchor():
    g = grid()
    imported = IntegralState((Region("imported", 10, 20),), Normalization("imported", 1.0,
                                                                                     provenance="test"))
    assert imported.normalization.anchor_id is None
    extra = add_region(imported, g, 7, 6)
    assert extra.normalization == imported.normalization
    uncertain = replace(imported, normalization=Normalization("unverified", provenance="conflict"))
    extra = add_region(uncertain, g, 7, 6)
    assert extra.normalization.kind == "unverified"
    assert all(row.value is None for row in integral_snapshot(extra, g))
    assigned = assign_reference(extra, g, extra.regions[0].id, 2)
    assert assigned.normalization.kind == "anchor"


def test_invalid_anchor_preview_preserves_prior_scale_and_negative_nonreference():
    g = IntegralGrid(np.r_[np.ones(20), -np.ones(20), np.zeros(20), np.ones(41)], 10, .1)
    s = add_region(IntegralState(), g, 9.9, 9)
    anchor = s.regions[0].id
    for a, b in [(7.9, 7), (5.9, 5), (9.01, 9.02)]:
        with pytest.raises(ValueError):
            edit_region(s, g, anchor, a, b)
    assert s.normalization.protons == 1
    negative = add_region(s, g, 7.9, 7)
    assert integral_snapshot(negative, g)[1].value < 0
    with pytest.raises(ValueError):
        assign_reference(negative, g, negative.regions[1].id, 2)
    for count in [0, -1, math.inf, math.nan]:
        with pytest.raises(ValueError):
            assign_reference(s, g, anchor, count)
    unsupported = replace(s, regions=(replace(s.regions[0], bias=1),))
    with pytest.raises(ValueError):
        assign_reference(unsupported, g, anchor, 2)


def test_lorentzian_finite_window_truth_convergence():
    errors = []
    # Sum rule is checked against an analytic finite interval, not an infinite peak.
    for n in [1001, 2001, 4001]:
        ppm = np.linspace(10, 0, n)
        center, gamma = 5.13, .2
        y = gamma**2 / ((ppm - center)**2 + gamma**2)
        g = IntegralGrid(y, 10, 10 / (n - 1))
        left, right = g.bounds(7, 3)
        region = Region("analytic", left, right)
        exact = gamma * (math.atan((7 - center) / gamma) - math.atan((3 - center) / gamma))
        errors.append(abs(g.area(region) - exact) / exact)
    assert errors[2] < errors[1] < errors[0] < .001


def test_mixed_history_snapshot_and_full_resolution_invariance():
    ppm = np.linspace(10, 0, 10001)
    real = np.ones(len(ppm))
    d = Document(DocumentData(np.frombuffer(ppm.tobytes()), np.frombuffer(real.tobytes()), "demo"))
    states = []
    for i in range(25):
        if i % 2:
            d.apply_view(ViewState(1, 9, -1, 2 + i))
        else:
            before = d.current
            d.add_integral(9 - i * .1, 8.9 - i * .1)
            states.append((before,d.current))
    expected = integral_snapshot(d.current.integrals, d.current.data.integration)
    for width in [10, 100, 1000]:
        from bruker_nmr.domain.display import display_trace
        display_trace(ppm, real, 4, 5, width)
        assert integral_snapshot(d.current.integrals, d.current.data.integration) == expected
    for before,after in reversed(states):
        assert d.undo() and d.current is before
    for before,after in states:
        assert d.redo() and d.current is after
    assert d.retained_bytes < d.memory_limit
    assert d.current.data is d.opening.data
