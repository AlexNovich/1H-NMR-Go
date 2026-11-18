"""Versioned artificial PDF examples, independent of every measurement/reference."""

from dataclasses import replace

import numpy as np

from bruker_nmr.domain.document import Document, DocumentData, ViewState
from bruker_nmr.domain.integrals import Peak
from bruker_nmr.domain.peaks import PeakState
from bruker_nmr.export.snapshot import ViewSnapshot
from bruker_nmr.processing.fid import immutable


def examples():
    x = np.linspace(12, 0, 65536, endpoint=False)
    centers = [1 + 0.6 * (i // 10) + 0.006 * (i % 10) for i in range(147)]
    real = 0.01 + sum(
        (0.35 + 0.08 * (i % 7)) * 0.0012**2 / ((x - center) ** 2 + 0.0012**2)
        for i, center in enumerate(centers)
    )
    doc = Document(DocumentData(immutable(x), immutable(real), "demo"))
    doc.apply_peaks(
        PeakState(
            tuple(
                Peak(
                    f"pdf-fake-{i}",
                    center,
                    float(real[np.argmin(abs(x - center))]),
                    0,
                    "manual",
                    True,
                    i % 15 == 0,
                )
                for i, center in enumerate(centers)
            )
        )
    )
    for i in range(18):
        high = 9.7 - i * 0.5
        doc.add_integral(high, high - 0.4)
    doc.assign_protons(doc.current.integrals.regions[0].id, 2)
    doc.appearance(trace_color="#802c90", line_width=0.8)
    full = ViewSnapshot(
        doc.current,
        "Synthetic full spectrum\n147 generated peak records, 18 integration ranges\nIndependent artificial fixture",
    )
    expanded = ViewSnapshot(
        replace(doc.current, view=ViewState(3.92, 4.08, -0.03, 0.65)),
        "Synthetic expanded multiplet — full integral values retained",
    )
    return {"full": full, "expanded": expanded}
