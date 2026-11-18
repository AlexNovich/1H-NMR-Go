"""View transitions, source ownership and worker-authority invariants without Qt."""
from dataclasses import replace

import numpy as np
import pytest

from bruker_nmr.demo import make_demo
from bruker_nmr.domain.display import display_trace
from bruker_nmr.domain.document import Document, DocumentData, LoadGate, ViewState


def document(**kwargs):
    return Document(DocumentData.from_demo(make_demo()), **kwargs)


def test_separate_view_history_preserves_source_redo_and_edit_capacity():
    doc = document()
    opening = doc.opening
    doc.add_integral(4,3.2)
    edited = doc.current
    doc.undo()
    revision = doc.revision
    for i in range(200):
        doc.navigate(ViewState(i*.001,10+i*.001,-.1,1.2))
    assert doc.can_redo and doc.undo_count == 0 and doc.view_count == 50
    assert doc.revision == revision and not doc.integration_modified
    for _ in range(50):
        assert doc.previous_view()
    assert not doc.previous_view()
    doc.redo()
    assert doc.current is edited and doc.view_count == 0
    for i in range(200):
        doc.navigate(ViewState(0,10,-.1,2+i))
    assert doc.undo_count == 1
    assert doc.undo() and doc.current is opening
    assert doc.current.data.real is opening.data.real


def test_history_bound_keeps_opening_and_named_edits_clear_redo():
    doc = document(max_commands=20)
    for i in range(60):
        doc.appearance(line_width=1+i*.01)
    assert doc.undo_count == 20 and doc.undo_label == 'Appearance'
    assert doc.retained_bytes < doc.memory_limit
    doc.undo()
    assert doc.can_redo and doc.redo_label == 'Appearance'
    doc.navigate(ViewState(1,9,-1,9))
    assert doc.can_redo
    doc.add_integral(4,3.2)
    assert not doc.can_redo and doc.undo_label == 'Add integral'
    assert doc.view_count == 0
    doc.reset()
    assert doc.current.view == doc.opening.view
    assert doc.current.data is doc.opening.data


def test_invalid_source_history_and_view():
    with pytest.raises(ValueError):
        DocumentData(np.array([1., 2.]), np.ones(2), "demo")
    with pytest.raises(ValueError):
        document(max_commands=19)
    with pytest.raises(ValueError):
        document(memory_limit=1)
    for bounds in [(1, 0, 0, 1), (0, 1, 1, 1), (0, float("inf"), 0, 1)]:
        with pytest.raises(ValueError):
            ViewState(*bounds)


def test_load_generation_identity_revision_and_cancel():
    gate = LoadGate()
    doc = document()
    old = gate.begin(doc)
    assert gate.accepts(old, doc)
    newer = gate.begin(doc)
    assert not gate.accepts(old, doc)
    assert gate.accepts(newer, doc)
    doc.apply_view(replace(doc.current.view, x_min=0))
    assert gate.accepts(newer, doc)  # Edit tickets survive navigation.
    doc.appearance(line_width=.7)
    assert not gate.accepts(newer, doc)
    fresh = gate.begin(doc)
    assert not gate.accepts(fresh, document())
    gate.cancel()
    assert not gate.accepts(fresh, doc)


@pytest.mark.parametrize("descending", [False, True])
def test_envelope_preserves_narrow_extrema_and_source(descending):
    x = np.linspace(0, 10, 100000)
    y = np.zeros(len(x))
    y[12345], y[12346], y[77777] = 100, -99, 123
    before = y.tobytes()
    if descending:
        x, y = x[::-1], y[::-1]
    trace = display_trace(x, y, 0, 10, 500)
    assert len(trace.x) <= 1004
    assert {100, -99, 123} <= set(trace.y)
    assert trace.x[0] == 0 and trace.x[-1] == 10
    assert np.all(np.diff(trace.x) > 0)
    assert y[::(-1 if descending else 1)].tobytes() == before
    zoom = display_trace(x, y, 1.234, 1.235, 500)
    assert {100, -99} <= set(zoom.y)
    assert len(display_trace(x, y, 20, 21, 500).x) == 0


def test_envelope_flat_tail_and_invalid_limits():
    x = np.linspace(0, 10, 10003)
    y = np.ones(len(x))
    y[-2] = -10
    trace = display_trace(x, y, 0, 10, 100)
    assert -10 in trace.y
    assert trace.x[-1] == x[-1]
    with pytest.raises(ValueError):
        display_trace(x, y, 1, 0, 100)
