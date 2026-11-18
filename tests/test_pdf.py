"""Independent PDF structure/raster/geometry and full-grid scientific invariants."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from pypdf import PdfReader

from bruker_nmr.domain.document import Document, DocumentData, ViewState
from bruker_nmr.domain.integrals import Peak
from bruker_nmr.domain.labels import format_peak_label
from bruker_nmr.domain.peaks import PeakState
from bruker_nmr.export.current_pdf import PAGE_HEIGHT, SpectrumPage, write_page
from bruker_nmr.export.print_layout import integral_text
from bruker_nmr.export.snapshot import ViewSnapshot
from bruker_nmr.pdf_examples import examples
from bruker_nmr.processing.fid import immutable


@pytest.fixture(scope="module")
def artificial():
    return examples()


@pytest.mark.parametrize("kind", ["full", "expanded"])
def test_vector_a4_fonts_content_clipping_and_coloured_geometry(artificial, kind):
    snapshot = artificial[kind]
    page = SpectrumPage(snapshot)
    try:
        before = snapshot.state.data.real.tobytes()
        payload = page.pdf_bytes()
        document = PdfReader(BytesIO(payload))
        assert len(document.pages) == 1
        pdf = document.pages[0]
        assert float(pdf.mediabox.width) == pytest.approx(841.8898, abs=0.5)
        assert float(pdf.mediabox.height) == pytest.approx(595.2756, abs=0.5)
        assert not list(pdf.images)  # The actual spectrum is a vector path.
        text = pdf.extract_text()
        assert snapshot.caption in text
        for placed in (*page.geometry.peak_placements, *page.geometry.integral_placements):
            assert placed.label.text in text
        for font in pdf["/Resources"]["/Font"].values():
            resource = font.get_object()
            assert resource["/Subtype"] == "/Type0"
            descendant = resource["/DescendantFonts"][0].get_object()
            assert descendant["/Subtype"] == "/CIDFontType2"
            assert "/FontFile2" in descendant["/FontDescriptor"].get_object()
        with pymupdf.open(stream=payload, filetype="pdf") as raster:
            sheet = raster[0]
            image = sheet.get_pixmap()
            pixels = np.frombuffer(image.samples, dtype=np.uint8)
            assert np.count_nonzero(pixels < 200) > 10000
            drawings = sheet.get_drawings()
            trace = max(drawings, key=lambda d: len(d["items"]))
            assert trace["width"] == pytest.approx(0.6, abs=0.001)
            assert trace["color"] == pytest.approx((128 / 255, 44 / 255, 144 / 255), abs=1e-5)
            left, bottom, width, height = page.geometry.plot
            # Actual vector spine strokes match the physical frame in page coordinates.
            horizontal = [
                d
                for d in drawings
                if abs(d["rect"].height) < 0.01 and d["rect"].width > width - 0.5
            ]
            assert any(abs(d["rect"].y0 - (PAGE_HEIGHT - bottom + 4)) < 0.5 for d in horizontal)
            assert not any(
                abs(d["rect"].y0 - (PAGE_HEIGHT - bottom - height)) < 0.5 for d in horizontal
            )
            assert not page.ax.yaxis.get_visible()
            assert not page.ax.spines["top"].get_visible()
            assert not page.ax.spines["right"].get_visible()
            assert not page.ax.spines["left"].get_visible()
            assert "Amplitude" not in text and "Chemical shift" not in text and "ppm" in text
            vertical = []
            for block in sheet.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    if abs(line["dir"][0]) < 1e-5 and line["dir"][1] < -0.9:
                        for span in line["spans"]:
                            if span["text"] in {
                                p.label.text for p in page.geometry.peak_placements
                            }:
                                vertical.append(pymupdf.Rect(span["bbox"]))
            assert len(vertical) == len(page.geometry.peak_placements)
            for i, a in enumerate(vertical):
                assert a.x0 >= left - 0.5 and a.x1 <= left + width + 0.5
                for b in vertical[i + 1 :]:
                    assert not a.intersects(b)
        assert page.ax.get_xlim() == (snapshot.state.view.x_max, snapshot.state.view.x_min)
        assert page.ax.get_ylim() == (snapshot.state.view.y_min, snapshot.state.view.y_max)
        assert snapshot.state.data.real.tobytes() == before
        assert len(snapshot.state.peaks.records) == 147
        assert len(page.geometry.peak_placements) + len(page.geometry.hidden_peaks) == len(
            tuple(
                p
                for p in snapshot.state.peaks.records
                if p.label_visible
                and snapshot.state.view.x_min
                <= p.ppm + snapshot.state.calibration
                <= snapshot.state.view.x_max
            )
        )
        if kind == "full":
            assert "2.00*" in text and len(page.geometry.hidden_peaks) > 0
            assert "hidden by Auto" not in text
            assert any("Auto hides" in n for n in page.preflight)
        else:
            assert any(
                p.label.text.startswith("‹") and p.label.text.endswith("›")
                for p in page.geometry.integral_placements
            )
    finally:
        page.close()


def test_snapshot_calibration_signed_full_integrals_and_no_implicit_title():
    x = immutable(np.linspace(10, 0, 10001))
    y = immutable(np.where(x > 5, 1.0, -0.5))
    doc = Document(
        DocumentData(x, y, "demo", raw_source=(Path("/independent/private-source"), None))
    )
    doc.add_integral(8, 6)
    doc.add_integral(4, 2)
    doc.assign_protons(doc.current.integrals.regions[0].id, 2)
    doc.apply_peaks(PeakState((Peak("p", 7, 1, 0, "manual", True, True),)))
    doc.set_zero("p")
    doc.appearance(trace_color="#006633", line_width=2)
    doc.navigate(ViewState(-0.5, 0.5, -0.6, 1.2))
    saved = ViewSnapshot(doc.current, "ЯМР α δ μ −0.5 $literal$", selected_peak="p")
    full = saved.integrals
    assert full[0].value == 2 and full[1].value == -1
    page = SpectrumPage(saved)
    try:
        text = PdfReader(BytesIO(page.pdf_bytes())).pages[0].extract_text()
        assert saved.caption in text and "private-source" not in text
        assert integral_text(full[0], saved.state.view) in text
        assert format_peak_label(saved.state.peaks.records[0], saved.state.calibration) in text
        assert "−0.4" in text or "−0.5" in text
        assert page.ax.lines[0].get_linewidth() == 1.5
        doc.appearance(trace_color="#ff0000")
        doc.navigate(ViewState(0, 10, -2, 3))
        doc.clear_integrals()
        assert page.snapshot.state is saved.state and saved.integrals == full
        assert page.ax.lines[0].get_color() == "#006633"
        assert not any(
            "selected" in a.get_label().lower() or "ghost" in a.get_label().lower()
            for a in page.ax.lines
        )
    finally:
        page.close()


@pytest.mark.parametrize(
    "mode,markers,show_integrals",
    [("hidden", False, False), ("all", False, True), ("auto", True, False)],
)
def test_visibility_retains_models_and_all_mode_keeps_crowding(
    artificial, mode, markers, show_integrals
):
    state = artificial["full"].state
    state = replace(
        state,
        peaks=replace(state.peaks, label_mode=mode, markers_visible=markers),
        show_integrals=show_integrals,
    )
    page = SpectrumPage(ViewSnapshot(state))
    try:
        assert len(state.peaks.records) == 147 and len(state.integrals.regions) == 18
        assert bool(page.geometry.peak_placements) == (mode != "hidden")
        if mode == "all":
            assert len(page.geometry.peak_placements) == 147 and not page.geometry.hidden_peaks
        assert bool(page.geometry.integral_placements) == show_integrals
        assert len(page.ax.lines) == (2 if markers else 1)
    finally:
        page.close()


@pytest.mark.parametrize("caption", ["x" * 1025, "bad\x00caption", "漢字", "M" * 240])
def test_caption_cannot_silently_clip_shrink_or_use_missing_glyphs(artificial, caption):
    with pytest.raises(ValueError):
        page = SpectrumPage(ViewSnapshot(artificial["full"].state, caption))
        page.close()


def test_preview_and_pdf_keep_identical_captured_geometry(artificial):
    page = SpectrumPage(artificial["expanded"])
    try:
        geometry = page.geometry
        limits = page.ax.get_xlim(), page.ax.get_ylim()
        rgba, size = page.preview_rgba(100)
        assert len(rgba) == size[0] * size[1] * 4
        assert size[0] / size[1] == pytest.approx(297 / 210, rel=0.003)
        payload = page.pdf_bytes()
        assert page.geometry is geometry and (page.ax.get_xlim(), page.ax.get_ylim()) == limits
        with pymupdf.open(stream=payload, filetype="pdf") as pdf:
            pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(100 / 72, 100 / 72), alpha=False)
            truth = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            preview = np.frombuffer(rgba, dtype=np.uint8).reshape(size[1], size[0], 4)[:, :, :3]
            # Different font/AA rasterizers; compare small-block ink coverage, not bytes.
            h, w = min(truth.shape[0], preview.shape[0]), min(truth.shape[1], preview.shape[1])

            def ink(a):
                a = a[: h // 8 * 8, : w // 8 * 8].mean(axis=2) < 220
                return a.reshape(h // 8, 8, w // 8, 8).mean(axis=(1, 3))

            assert np.mean(abs(ink(truth) - ink(preview))) < 0.035
    finally:
        page.close()


def test_atomic_save_no_overwrite_no_source_archive_or_partial_replacement(
    artificial, tmp_path, monkeypatch
):
    page = SpectrumPage(artificial["expanded"])
    target = tmp_path / "example.pdf"
    target.write_bytes(b"original")
    try:
        with pytest.raises(FileExistsError):
            write_page(target, page)
        assert target.read_bytes() == b"original"
        import bruker_nmr.export.current_pdf as writer

        def fail(*a, **k):
            raise PermissionError("synthetic protected target")

        monkeypatch.setattr(writer.os, "replace", fail)
        with pytest.raises(PermissionError):
            write_page(target, page, overwrite=True)
        assert target.read_bytes() == b"original" and not list(tmp_path.glob(".nmr-go-*"))
        source = tmp_path / "original.zip"
        source.write_bytes(b"original archive")
        with pytest.raises(ValueError):
            write_page(source, page, overwrite=True)
        assert source.read_bytes() == b"original archive"
    finally:
        page.close()


def test_exclusive_creation_fallback_and_existing_file_are_safe(artificial, tmp_path, monkeypatch):
    page = SpectrumPage(artificial["expanded"])
    import bruker_nmr.export.current_pdf as writer

    def unsupported(*a, **k):
        raise OSError("synthetic no hard links")

    monkeypatch.setattr(writer.os, "link", unsupported)
    target = tmp_path / "fallback.pdf"
    try:
        write_page(target, page)
        first = target.read_bytes()
        assert len(PdfReader(target).pages) == 1
        with pytest.raises(FileExistsError):
            write_page(target, page)
        assert target.read_bytes() == first
        assert not list(tmp_path.glob(".nmr-go-*"))
    finally:
        page.close()


def test_large_clipped_peak_stays_vector_and_uses_full_grid_extrema():
    x = immutable(np.linspace(12, 0, 262144, endpoint=False))
    values = np.ones(len(x)) * 0.02
    values[12345], values[12346], values[200000] = 1e7, -1e6, 0.7
    doc = Document(DocumentData(x, immutable(values), "demo"))
    doc.navigate(ViewState(0, 12, -0.1, 1.0))
    page = SpectrumPage(ViewSnapshot(doc.current))
    try:
        assert max(page.trace.y) == 1e7 and min(page.trace.y) == -1e6
        assert len(page.trace.x) < len(x) // 4
        payload = page.pdf_bytes()
        assert not list(PdfReader(BytesIO(payload)).pages[0].images)
        assert page.ax.get_ylim() == (-0.1, 1.0)
        with pymupdf.open(stream=payload, filetype="pdf") as pdf:
            assert pdf[0].get_pixmap().width > 800
    finally:
        page.close()
