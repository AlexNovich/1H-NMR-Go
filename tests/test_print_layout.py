"""Independent print geometry, narrow-range labels and metadata privacy/retention."""

import zipfile
from dataclasses import replace
from io import BytesIO

import numpy as np
import pymupdf
import pytest
from pypdf import PdfReader

from bruker_nmr.domain.document import Document, DocumentData, ViewState
from bruker_nmr.export.current_pdf import PAGE_HEIGHT, SpectrumPage
from bruker_nmr.export.print_layout import PrintLayoutError, clipped_positions
from bruker_nmr.export.snapshot import ViewSnapshot
from bruker_nmr.io.source import Source
from bruker_nmr.pdf_examples import examples
from bruker_nmr.processing.fid import Recipe, immutable
from bruker_nmr.ui.loading import Choice, RawJob, load_choice
from tests.synthetic_bruker import experiment, status


def test_print_has_all_rotated_integrals_and_exact_brackets_without_routine_footer():
    s = examples()["full"]
    page = SpectrumPage(s)
    try:
        assert len(page.geometry.integral_placements) == 18 and not page.geometry.hidden_integrals
        assert all(t.get_rotation() == 90 for t in page.integral_texts)
        assert len({round(t.get_position()[1], 9) for t in page.integral_texts}) == 1
        assert all("H" not in p.label.text for p in page.geometry.integral_placements)
        text = PdfReader(BytesIO(page.pdf_bytes())).pages[0].extract_text()
        assert "2.00*" in text
        assert all(
            k not in text
            for k in (
                "Baseline Off",
                "Integral scale:",
                "hidden by Auto",
                "Amplitude",
                "Chemical shift",
            )
        )
        with pymupdf.open(stream=page.pdf_bytes(), filetype="pdf") as pdf:
            rects = []
            for block in pdf[0].get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"] in {
                            p.label.text for p in page.geometry.integral_placements
                        }:
                            assert abs(line["dir"][0]) < 1e-5 and line["dir"][1] < -0.9
                            rects.append(pymupdf.Rect(span["bbox"]))
            assert len(rects) == 18
            for i, r in enumerate(rects):
                assert 0 < r.y0 < r.y1 < PAGE_HEIGHT
                assert not any(r.intersects(other) for other in rects[i + 1 :])
            # Every range has an actual bracket at the precise calibrated X bounds.
            left, bottom, width, height = page.geometry.plot
            drawings = pdf[0].get_drawings()
            for row in s.integrals:
                a = (
                    left
                    + (s.state.view.x_max - row.high)
                    / (s.state.view.x_max - s.state.view.x_min)
                    * width
                )
                b = (
                    left
                    + (s.state.view.x_max - row.low)
                    / (s.state.view.x_max - s.state.view.x_min)
                    * width
                )
                assert any(
                    abs(d["rect"].x0 - a) < 0.1
                    and abs(d["rect"].x1 - b) < 0.1
                    and d["rect"].height <= 5.1
                    for d in drawings
                )
    finally:
        page.close()


def crowded_document(count):
    x = immutable(np.linspace(12, 0, 65536, endpoint=False))
    doc = Document(DocumentData(x, immutable(np.ones(len(x))), "demo"))
    for i in range(count):
        doc.add_integral(11 - i * 0.1, 10.96 - i * 0.1)
    return doc


def test_collision_blocks_instead_of_dropping_or_staggering():
    doc = crowded_document(60)
    with pytest.raises(PrintLayoutError, match="one readable row"):
        SpectrumPage(ViewSnapshot(doc.current))
    with pytest.raises(ValueError, match="one aligned integral row"):
        ViewSnapshot(doc.current, integral_bands=2)


@pytest.mark.parametrize("view", [(0, 12), (3.92, 4.08), (-0.3, 0.3), (999.9, 1000.1)])
def test_ticks_represent_actual_positions_and_minor_ticks_adapt(view):
    s = examples()["expanded"]
    v = ViewState(*view, -0.03, 1.05)
    p = SpectrumPage(ViewSnapshot(replace(s.state, view=v)))
    try:
        p.preview_rgba(72)
        positions = p.ax.get_xticks()
        for pos, label in zip(positions, p.ax.get_xticklabels()):
            if v.x_min <= pos <= v.x_max:
                assert float(label.get_text().replace("−", "-")) == pytest.approx(pos, abs=1e-9)
        if view == (0, 12):
            assert np.allclose(np.diff(positions), 1)
        assert len(p.ax.xaxis.get_minorticklocs()) > 0
    finally:
        p.close()


def test_clipping_indicators_use_full_grid_and_cover_sub_bin_boundary_crossings():
    x = immutable(np.array([4.0, 3.0, 2.0, 1.0, 0.0]))
    y = immutable(np.array([0.0, 10.0, 0.0, 8.0, 0.0]))
    v = ViewState(0.5, 3.5, -0.1, 2)
    assert clipped_positions(x, y, v) == (1.0, 3.0)
    assert clipped_positions(x, y, ViewState(2.9, 2.95, 0, 2))
    doc = Document(DocumentData(x, y, "demo"))
    doc.navigate(v)
    p = SpectrumPage(ViewSnapshot(doc.current))
    try:
        assert p.upper_clips == (1.0, 3.0)
        indicator = next(t for t in p.ax.lines if t.get_label() == "upper clipping")
        assert list(indicator.get_xdata()) == [1.0, 3.0]
        assert len(doc.current.peaks.records) == 0
    finally:
        p.close()


def test_optional_header_from_status_and_title_survives_zip_raw_and_caption_privacy(tmp_path):
    exp, proc, a, _ = experiment(tmp_path)
    a.update(SOLVENT="CDCl3", NS=64)
    status(exp / "acqus", a)
    (proc / "title").write_text("Artificial ЯМР sample", encoding="utf-8")
    with Source(exp) as source:
        (candidate,) = source.discover()
        spec = source.read_processed()
        choice = Choice.from_candidate(candidate, source.root)
    assert "Artificial ЯМР sample" in spec.print_header
    assert "independent-fixture" in spec.print_header
    assert "CDCl3, 1H, 300 MHz, NS=64" in spec.print_header
    assert str(tmp_path) not in spec.print_header and "Artificial ЯМР" not in repr(spec)
    doc = Document(DocumentData.from_operator(spec, candidate))
    assert doc.current.data.print_header == spec.print_header
    archive = tmp_path / "artificial.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for f in exp.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(exp.parent.parent))
    with Source(archive) as source:
        zipped_header = source.read_processed().print_header
        assert "Artificial ЯМР sample" in zipped_header
        assert zipped_header.startswith("artificial\n")
        assert zipped_header.splitlines()[-1] == spec.print_header.splitlines()[-1]
    loaded = load_choice(exp, choice)
    job = RawJob(None, exp, choice, Recipe(16), header=loaded.print_header)
    results = []
    job.signals.finished.connect(lambda *args: results.append(args[2:]))
    job.run()
    output, error = results[0]
    assert error is None and output.print_header == spec.print_header
    assert "Artificial ЯМР" not in repr(output)


@pytest.mark.parametrize("payload", [b"\x00binary", b"x" * 4097, b"C:\\private\\measurement"])
def test_missing_or_invalid_optional_header_fields_are_omitted(tmp_path, payload):
    exp, proc, _, _ = experiment(tmp_path)
    (proc / "title").write_bytes(payload)
    with Source(exp) as source:
        spectrum = source.read_processed()
    assert "NS=" not in spectrum.print_header and "private" not in spectrum.print_header
    assert "binary" not in spectrum.print_header
    assert len(spectrum.real) == 8


def test_overlapping_ranges_keep_all_labels_and_warn_before_export():
    doc = crowded_document(1)
    doc.add_integral(11, 10.9)
    doc.add_integral(10.99, 10.95)
    page = SpectrumPage(ViewSnapshot(doc.current))
    try:
        assert len(page.integral_texts) == 3 and not page.geometry.hidden_integrals
        assert any("overlap" in n.lower() for n in page.preflight)
        assert any("overlap" in n.lower() for n in page.geometry.footer_lines)
        assert len(PdfReader(BytesIO(page.pdf_bytes())).pages) == 1
    finally:
        page.close()


def test_dense_upper_clipping_indicators_are_bounded_without_modifying_source():
    x = immutable(np.linspace(12, 0, 262144, endpoint=False))
    y = immutable(np.tile([0.0, 100.0], len(x) // 2))
    before = y.tobytes()
    positions = clipped_positions(x, y, ViewState(0, 12, -0.1, 1), max_marks=64)
    assert 1 <= len(positions) <= 64
    assert all(0 <= p <= 12 for p in positions)
    assert y.tobytes() == before


def test_long_header_proposal_uses_visible_ellipsis_and_preserves_facts():
    from bruker_nmr.export.current_pdf import proposed_header

    raw = (
        "independent-dataset\n"
        + ("Independent synthetic title " * 20)
        + "\nCDCl3, 1H, 300 MHz, NS=64"
    )
    text, shortened = proposed_header(raw)
    assert shortened and "…\nCDCl3, 1H, 300 MHz, NS=64" in text
    assert text.startswith("independent-dataset\n")
    snapshot = examples()["full"]
    page = SpectrumPage(replace(snapshot, caption=text))
    try:
        assert len(page.geometry.caption_lines) == 3
        assert (
            "CDCl3, 1H, 300 MHz, NS=64"
            in PdfReader(BytesIO(page.pdf_bytes())).pages[0].extract_text()
        )
    finally:
        page.close()


def test_very_long_optional_title_does_not_displace_frequency_or_scan_count(tmp_path):
    from bruker_nmr.export.current_pdf import proposed_header

    exp, proc, a, _ = experiment(tmp_path)
    a.update(SOLVENT="CDCl3", NS=64)
    status(exp / "acqus", a)
    (proc / "title").write_text("x" * 4096, encoding="utf-8")
    with Source(exp) as source:
        spectrum = source.read_processed()
    header = spectrum.print_header
    assert header.endswith("CDCl3, 1H, 300 MHz, NS=64") and "…" in header
    assert len(header) <= 1024
    proposal, _ = proposed_header(header)
    state = examples()["full"]
    page = SpectrumPage(replace(state, caption=proposal))
    try:
        assert len(page.geometry.caption_lines) <= 3
    finally:
        page.close()
