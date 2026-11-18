"""Frozen current-view PDF acceptance, only independent artificial snapshots."""

from dataclasses import replace

from pypdf import PdfReader
from PySide6.QtWidgets import QApplication

from bruker_nmr.export.current_pdf import SpectrumPage, write_page
from bruker_nmr.export.snapshot import ViewSnapshot
from bruker_nmr.pdf_examples import examples
from bruker_nmr.ui.pdf import PdfExportDialog


def exercise_current_pdf(window, result_path):
    snapshots = examples()
    outputs = {}
    for name, snapshot in snapshots.items():
        page = SpectrumPage(snapshot)
        try:
            path = result_path.with_name(f"{result_path.stem}-{name}.pdf")
            # Explicit diagnostic sidecars in the caller-selected self-test directory.
            write_page(path, page, overwrite=True)
            pdf = PdfReader(path)
            if len(pdf.pages) != 1 or abs(float(pdf.pages[0].mediabox.width) - 841.8898) > 0.5:
                raise ValueError("Current-view A4 PDF check failed")
            if snapshot.caption not in pdf.pages[0].extract_text():
                raise ValueError("Current-view PDF caption check failed")
            for font in pdf.pages[0]["/Resources"]["/Font"].values():
                font = font.get_object()
                if font["/Subtype"] != "/Type0":
                    raise ValueError("Current-view PDF font check failed")
                descendant = font["/DescendantFonts"][0].get_object()
                if "/FontFile2" not in descendant["/FontDescriptor"].get_object():
                    raise ValueError("Current-view PDF embedding check failed")
            if list(pdf.pages[0].images):
                raise ValueError("Current-view PDF must retain vector paths")
            if name == "full" and not page.geometry.hidden_peaks:
                raise ValueError("Dense artificial PDF suppression check failed")
            if name == "expanded" and not any(
                p.label.text.startswith("‹") and p.label.text.endswith("›")
                for p in page.geometry.integral_placements
            ):
                raise ValueError("Clipped full integral PDF check failed")
            text = pdf.pages[0].extract_text()
            if "Amplitude" in text or "Chemical shift" in text or "Baseline Off" in text:
                raise ValueError("Compact print axes/footer check failed")
            if page.geometry.hidden_integrals or any(
                t.get_rotation() != 90 for t in page.integral_texts
            ):
                raise ValueError("Compact integral labels must all be present and vertical")
            if name == "full" and (
                len(page.geometry.integral_placements) != 18 or "2.00*" not in text
            ):
                raise ValueError("Compact reference value check failed")
            if name == "full" and len(page.geometry.caption_lines) != 3:
                raise ValueError("Three-line compact header check failed")
            outputs[name] = path.name
        finally:
            page.close()
    preview = PdfExportDialog(window, snapshots["full"])
    try:
        preview.show()
        QApplication.processEvents()
        available = preview.screen().availableGeometry()
        fits = (
            preview.frameGeometry().width() <= available.width()
            and preview.frameGeometry().height() <= available.height()
        )
        if not fits or preview.page is None or preview.preview.image.isNull():
            raise ValueError("Current-view PDF preview geometry check failed")
        image_path = result_path.with_name(result_path.stem + "-pdf-preview.png")
        if not preview.grab().save(str(image_path)):
            raise ValueError("Synthetic PDF preview capture failed")
    finally:
        preview.reject()
        preview.deleteLater()
    artificial_header = "Synthetic prefilled header | 1H"
    state = snapshots["expanded"].state
    metadata_preview = PdfExportDialog(
        window,
        ViewSnapshot(replace(state, data=replace(state.data, print_header=artificial_header))),
    )
    try:
        if metadata_preview.caption.text() != artificial_header:
            raise ValueError("Prefilled metadata header check failed")
        metadata_preview.caption.clear()
        metadata_preview.rebuild_preview()
        if metadata_preview.page is None or metadata_preview.page.geometry.caption_lines:
            raise ValueError("Erased metadata header check failed")
    finally:
        metadata_preview.reject()
        metadata_preview.deleteLater()
    return {"pdf_examples": outputs, "pdf_preview_fits_screen": fits}
