"""Noisy synthetic multiplet, full data against an independent local-maxima oracle."""

from dataclasses import replace

import numpy as np
import pytest

from bruker_nmr.domain.document import Document, DocumentData, ViewState
from bruker_nmr.domain.integrals import Peak
from bruker_nmr.processing.fid import immutable
from tests.test_peaks_ui import at
from tests.test_viewer import drag, window


@pytest.mark.parametrize("span", [(2.6, 5.5), (3.65, 4.4)])
def test_rectangle_picks_full_resolution_in_both_axes_at_different_zooms(qtbot, span):
    x = np.linspace(12, 0, 65536, endpoint=False)
    rng = np.random.default_rng(52)
    y = rng.normal(0, 0.00055, x.size)
    for c, h in ((3.5, 0.6), (3.91, 0.18), (4.02, 0.74), (4.10, 0.96), (4.20, 0.64), (4.56, 0.61)):
        y += h * np.exp(-(((x - c) / 0.007) ** 2))
    doc = Document(DocumentData(immutable(x), immutable(y), "demo"))
    centers = [
        i
        for i in range(1, len(x) - 1)
        if 3.73 <= x[i] <= 4.29 and 0.35 <= y[i] <= 0.84 and y[i] > max(y[i - 1], y[i + 1])
    ]
    assert {round(x[i], 2) for i in centers} == {4.02, 4.20}
    old = Peak("existing", float(x[centers[0]]), float(y[centers[0]]), 0, "manual", False)
    doc.apply_peaks(replace(doc.current.peaks, records=(old,)))
    ui = window(qtbot)
    ui.confirm_discard = lambda: True
    ui.document = doc
    ui._render_document()
    ui.commit_view(ViewState(*span, -0.15, 1.15))
    ui.pick_action.trigger()
    qtbot.wait(100)
    for _ in range(2):
        drag(ui, at(ui, 3.73, 0.35), at(ui, 4.29, 0.84), qtbot)
        assert [p.ppm for p in doc.current.peaks.records] == [x[i] for i in centers]
    assert doc.current.peaks.records[0] is old
    assert doc.undo_count == 2
    assert doc.current.view == ViewState(*span, -0.15, 1.15)
    ui.undo()
    assert doc.current.peaks.records == (old,)
    ui.redo()
    assert len(doc.current.peaks.records) == len(centers)
    ui.close()
