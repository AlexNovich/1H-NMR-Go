"""Frozen dependency checks on independent temporary data, never user spectra."""
import importlib.metadata
import shutil
import struct
import tempfile
import time
from pathlib import Path

import numpy as np
from nmrglue.fileio import bruker
from pypdf import PdfReader
from scipy.optimize import minimize_scalar

from bruker_nmr.demo_input import write_demo_input
from bruker_nmr.export.demo_pdf import write_demo_pdf


class SmokeCheckError(RuntimeError):
    """A fixed synthetic-check message, safe to include in self-test diagnostics."""


def exercise_dependencies(window, result_path: Path) -> dict:
    # Exercise a native NumPy transform against an independently known constant DFT.
    impulse = np.zeros(16)
    impulse[0] = 1
    if not np.array_equal(np.fft.fft(impulse), np.ones(16)):
        raise SmokeCheckError("FFT smoke failed")
    fit = minimize_scalar(lambda x: (x - 2.5)**2, bounds=(-5, 5), method="bounded")
    if not fit.success or abs(fit.x - 2.5) > 1e-7:
        raise SmokeCheckError("Optimizer smoke failed")
    with tempfile.TemporaryDirectory(prefix="nmr-go-synthetic-") as folder:
        root = Path(folder)
        status = root / "acqus"
        status.write_text("##TITLE= Independently generated smoke fixture\n"
                          "##JCAMPDX= 5.00\n##$TD= 8\n##$NUC1= <1H>\n##END=\n",
                          encoding="ascii")
        parameters = bruker.read_jcamp(str(status))
        if parameters["TD"] != 8 or parameters["NUC1"] != "1H":
            raise SmokeCheckError("JCAMP smoke failed")
        binary = root / "fid"
        binary.write_bytes(struct.pack("<8i", 1, -2, 3, -4, 5, -6, 7, -8))
        _, decoded = bruker.read_binary(str(binary), shape=(8,), cplex=False, big=False,
                                        isfloat=False)
        if not np.array_equal(decoded, [1, -2, 3, -4, 5, -6, 7, -8]):
            raise SmokeCheckError("Binary smoke failed")
        # Use the production asynchronous chooser/reader route, with generated data only.
        from PySide6.QtWidgets import QApplication

        from bruker_nmr.domain.document import ViewState
        processed = root / "pdata/1"
        processed.mkdir(parents=True)
        a = {"NUC1": "1H", "PARMODE": 0, "SFO1": 300, "SW_h": 2400, "TD": 8,
             "DTYPA": 0, "BYTORDA": 0, "AQ_mod": 3, "GRPDLY": 0}
        p = {"PPARMOD": 0, "SI": 8, "DTYPP": 0, "BYTORDP": 0, "NC_proc": 2,
             "OFFSET": 12, "SW_p": 2400, "SF": 300, "STSR": 0, "STSI": 8,
             "XDIM": 0, "FT_mod": 6}
        for path, parameters in [(status, a), (processed / "procs", p)]:
            lines = ["##TITLE= Independent viewer smoke"]
            for key, value in parameters.items():
                text = f"<{value}>" if isinstance(value, str) else str(value)
                lines.append(f"##${key}= {text}")
            path.write_text("\n".join(lines + ["##END="]) + "\n", encoding="ascii")
        (processed / "1r").write_bytes(binary.read_bytes())
        (processed / "1i").write_bytes(binary.read_bytes())
        errors = []
        window.show_input_error = lambda code: errors.append(code)
        window.begin_open(root)
        deadline = time.monotonic() + 30
        while window.loading and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(.01)
        if (errors or window.loading or window.document.current.data.opening_mode != "operator"
                or not window.document.current.data.can_phase
                or not np.array_equal(window.document.current.data.real, decoded * 4)):
            raise SmokeCheckError("Processed viewer smoke failed")
        opening = window.document.opening
        window.commit_view(ViewState(6, 10, -20, 20))
        window.previous_action.trigger()
        if window.document.current.view != opening.view:
            raise SmokeCheckError("Viewer undo smoke failed")
        window.commit_view(ViewState(6, 10, -20, 20))
        window.full_action.trigger()
        if window.document.current.view != opening.view:
            raise SmokeCheckError("Viewer full view smoke failed")
        QApplication.processEvents()
        # A real multiple-choice layout must fit the same scaled logical screen too.
        from bruker_nmr.ui.chooser import ExperimentChooser
        from bruker_nmr.ui.loading import discover_choices
        shutil.copytree(processed, processed.with_name("2"))
        chooser = ExperimentChooser(discover_choices(root), window)
        chooser.show()
        QApplication.processEvents()
        available = chooser.screen().availableGeometry()
        chooser_fits = (chooser.frameGeometry().width() <= available.width()
                        and chooser.frameGeometry().height() <= available.height())
        chooser.close()
        if not chooser_fits:
            raise SmokeCheckError("Chooser screen geometry smoke failed")
        example = root / "synthetic-example.zip"
        write_demo_input(example)
        window.begin_open(example)
        deadline = time.monotonic() + 30
        while window.loading and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(.01)
        if (errors or window.loading or window.document.current.data.opening_mode != "operator"
                or window.document.current.data.can_phase
                or not np.array_equal(window.document.current.data.real,
                                       window.spectrum.amplitude[::-1])):
            raise SmokeCheckError("Viewer ZIP and missing-imaginary smoke failed")
        from bruker_nmr.domain.integrals import integral_snapshot
        data = window.document.current.data
        state = window.document.current.integrals
        if (len(state.regions) != 3 or len(data.annotations.peaks) != 4
                or state.normalization.kind != "imported" or state.normalization.anchor_id):
            raise SmokeCheckError("Operator annotation smoke failed")
        # Verify the compiled writer against a full synthetic ZIP-to-folder round trip.
        from bruker_nmr.domain.document import Document
        from bruker_nmr.io.save_markup import save_markup
        from bruker_nmr.ui.loading import load_choice

        working = Document(window.document.current.data)
        working.delete_integral(working.current.integrals.regions[0].id)
        anchor = working.current.integrals.regions[0]
        working.assign_protons(anchor.id, 2.0)
        view = working.current.view
        working.add_peaks_in_rectangle(view.x_min, view.y_min, view.x_max, view.y_max)
        working.delete_peak(working.current.peaks.records[0].id)
        before_zip = example.read_bytes()
        choice = working.source_location[1]
        saved_folder, _, _ = save_markup(example, choice.locator, working.current,
                                         working.markup_revision, root)
        reopened = Document(load_choice(saved_folder, choice))
        old_values = integral_snapshot(working.current.integrals, working.current.grid)
        new_values = integral_snapshot(reopened.current.integrals, reopened.current.grid)
        if ([(x.high, x.low, x.value, x.reference) for x in old_values]
                != [(x.high, x.low, x.value, x.reference) for x in new_values]
                or [x.ppm for x in working.current.peaks.records]
                != [x.ppm for x in reopened.current.peaks.records]
                or example.read_bytes() != before_zip):
            raise SmokeCheckError("Annotation Save/reopen smoke failed")
        shutil.rmtree(saved_folder)
        # Complete compiled peak/visibility/reference/appearance workflow on fake pdata.
        annotated_opening = window.document.current
        peak = annotated_opening.peaks.records[0]
        before_areas = integral_snapshot(state, annotated_opening.grid)
        window.document.peak_label(peak.id, False)
        window.document.appearance(markers=False, label_mode='hidden', line_width=.6, trace_color='#753399')
        window.document.set_zero(peak.id)
        window._render_document()
        shifted = window.document.current
        after_areas = integral_snapshot(shifted.integrals, shifted.grid)
        if (len(shifted.peaks.records) != 4 or shifted.peaks.by_id(peak.id).label_visible
                or shifted.peaks.markers_visible or peak.ppm+shifted.calibration != 0
                or shifted.data is not annotated_opening.data
                or [r.area for r in before_areas] != [r.area for r in after_areas]
                or window.curve.opts['pen'].widthF() != .6
                or window.curve.opts['pen'].color().name() != '#753399'):
            raise SmokeCheckError('Peak/reference/appearance isolation smoke failed')
        for _ in range(3):
            window.undo()
        if window.document.current is not annotated_opening:
            raise SmokeCheckError('Peak/reference exact Undo smoke failed')
        # Navigation preserves edit Redo and saved edit views in the frozen program.
        from dataclasses import replace

        from bruker_nmr.domain.labels import format_integral, format_peak_label, spread
        window.document.appearance(trace_color='#008855')
        saved = window.document.current
        window.undo()
        window.commit_view(ViewState(-1,5,-.1,1.2))
        if not window.document.can_redo or window.document.undo_count != 0:
            raise SmokeCheckError('Separate edit/view Redo smoke failed')
        window.redo()
        if window.document.current is not saved or window.document.view_count:
            raise SmokeCheckError('Saved edit-view restoration smoke failed')
        window.undo()
        if (format_integral(2,True) != '2.00 H (ref)' or
                not format_peak_label(replace(peak,needs_review=True)).endswith(' ?') or
                spread([2,2,2],1,0,4) != (1.,2.,3.)):
            raise SmokeCheckError('Shared label geometry/badges smoke failed')
        window.peaks.add_at(peak.ppm)
        if len(window.document.current.peaks.records) != 5:
            raise SmokeCheckError('Full-resolution manual picking smoke failed')
        window.undo()
        window.peaks.show_table()
        QApplication.processEvents()
        available = window.peaks.screen().availableGeometry()
        peak_table_fits = (window.peaks.frameGeometry().width() <= available.width()
                           and window.peaks.frameGeometry().height() <= available.height())
        window.peaks.hide()
        window.workbench.inspector.hide()
        if not peak_table_fits:
            raise SmokeCheckError('Peak table screen geometry smoke failed')
        window._render_document()
        QApplication.processEvents()
        if not window.grab().save(str(result_path.with_name(result_path.stem+'-main.png'))):
            raise SmokeCheckError('Synthetic main review image smoke failed')
        first = state.regions[0].id
        window.integrals.selected = first
        window.integrals.assign(2)
        before = integral_snapshot(window.document.current.integrals, data.integration)
        roi = window.integrals.rois[first]
        low, high = roi.getRegion()
        roi.setRegion(((low + high) / 2, high))
        after = integral_snapshot(window.document.current.integrals, data.integration)
        if abs(after[0].value - 2) > 1e-10 or after[1].value == before[1].value:
            raise SmokeCheckError("Live reference edit smoke failed")
        window.integrals.selected = first
        window.integrals.delete_selected()
        remaining = integral_snapshot(window.document.current.integrals, data.integration)
        if (window.document.current.integrals.normalization.kind != "frozen"
                or remaining[0].value != after[1].value):
            raise SmokeCheckError("Frozen normalization smoke failed")
        window.integrals.create_range(8, 7)
        if window.document.current.integrals.normalization.kind != "frozen":
            raise SmokeCheckError("Frozen add smoke failed")
        window.integrals.clear_all()
        window.integrals.create_range(8, 7)
        if abs(integral_snapshot(window.document.current.integrals, data.integration)[0].value - 1) > 1e-10:
            raise SmokeCheckError("Fresh reference smoke failed")
        window.undo()
        window.redo()
        window.reset()
        window.integrals.show_table()
        QApplication.processEvents()
        available = window.integrals.screen().availableGeometry()
        integral_table_fits = (window.integrals.frameGeometry().width() <= available.width()
                              and window.integrals.frameGeometry().height() <= available.height())
        window.integrals.hide()
        if not integral_table_fits:
            raise SmokeCheckError("Integral table screen geometry smoke failed")
        from bruker_nmr.processing.fid import Recipe
        previous_options = window.processing_options
        window.processing_options = lambda: Recipe(0)
        try:
            window.process_from_fid()
            deadline = time.monotonic() + 30
            while window.loading and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(.01)
            if errors or window.loading or window.document.current.data.opening_mode != 'raw':
                raise SmokeCheckError('Independent raw processing smoke failed')
            if window.document.current.integrals.regions or window.document.current.integrals.normalization.scale is not None:
                raise SmokeCheckError('Raw annotation isolation smoke failed')
            # Native/frozen phase transaction: paint preview, Cancel, Apply, Undo.
            unphased = window.document.current
            window.open_phase()
            phase = window.phase_dialog
            QApplication.processEvents()
            available = phase.screen().availableGeometry()
            phase_fits = (phase.frameGeometry().width() <= available.width()
                          and phase.frameGeometry().height() <= available.height())
            for spin, (minus, plus) in zip(phase.controls, phase.step_buttons):
                if spin.width() < spin.fontMetrics().horizontalAdvance('-1440.000')+28:
                    raise SmokeCheckError('Phase numeric field width smoke failed')
                previous = spin.value()
                plus.click()
                if abs(spin.value()-previous-spin.singleStep()) > 1e-10:
                    raise SmokeCheckError('Phase fine increment smoke failed')
                minus.click()
                if abs(spin.value()-previous) > 1e-10:
                    raise SmokeCheckError('Phase fine decrement smoke failed')
            phase.controls[0].setValue(25)
            phase.preview()
            if not phase.grab().save(str(result_path.with_name(result_path.stem+'-phase.png'))):
                raise SmokeCheckError('Synthetic phase review image smoke failed')
            if window.document.current is not unphased or window._phase_preview is None:
                raise SmokeCheckError('Phase preview transaction smoke failed')
            phase.reject()
            if window.document.current is not unphased or window._phase_preview is not None:
                raise SmokeCheckError('Phase cancel transaction smoke failed')
            window.open_phase()
            phase = window.phase_dialog
            phase.controls[0].setValue(25)
            phase.apply()
            if not window.document.current.data.phase_steps or not phase_fits:
                raise SmokeCheckError('Phase apply/screen geometry smoke failed')
            window.undo()
            if window.document.current is not unphased:
                raise SmokeCheckError('Phase undo smoke failed')
            from bruker_nmr.phase_benchmark import analytic
            from bruker_nmr.processing.phase import auto_phase, rotate
            x, truth, _ = analytic()
            distorted = truth * np.exp(1j*np.deg2rad(47-83*(x-.5)))
            proposal = auto_phase(distorted)
            if not proposal.confident or not np.isfinite(rotate(distorted, proposal.phase)).all():
                raise SmokeCheckError('Automatic phase smoke failed')
            window.undo()
            if window.document.current.data.opening_mode != 'operator':
                raise SmokeCheckError('Raw/operator undo smoke failed')
        finally:
            window.processing_options = previous_options
    # Independent compact peak + affine background: exact quiet anchors, no corpus.
    from bruker_nmr.domain.document import Document, DocumentData
    from bruker_nmr.processing.fid import immutable
    ppm = np.linspace(12, 0, 8192, endpoint=False)
    signal = np.maximum(0, 1-np.abs(ppm-5)/.2)**2
    baseline_data = DocumentData(immutable(ppm), immutable(signal+.1+.01*ppm), 'demo')
    window.document = Document(baseline_data)
    window.document.add_integral(5.3, 4.7)
    window._render_document()
    baseline_before = window.document.current
    window.open_baseline()
    panel = window.baseline_dialog
    QApplication.processEvents()
    available = panel.screen().availableGeometry()
    baseline_fits = (panel.frameGeometry().width() <= available.width()
                     and panel.frameGeometry().height() <= available.height())
    panel.preview()
    if panel.candidate is not None:
        raise SmokeCheckError('No-anchor baseline smoke failed')
    panel.mode.setCurrentText('Linear')
    for high, low in ((11, 10), (2, 1)):
        panel.high.setValue(high)
        panel.low.setValue(low)
        panel.add_anchor()
    panel.preview()
    if panel.candidate is None or panel.areas.rowCount() != 1 or not baseline_fits:
        raise SmokeCheckError('Baseline preview/area/screen smoke failed')
    if window.document.current is not baseline_before:
        raise SmokeCheckError('Baseline preview mutation smoke failed')
    if not panel.grab().save(str(result_path.with_name(result_path.stem+'-baseline.png'))):
        raise SmokeCheckError('Synthetic baseline review image smoke failed')
    panel.reject()
    if window.document.current is not baseline_before or window._baseline_preview is not None:
        raise SmokeCheckError('Baseline cancel smoke failed')
    window.open_baseline()
    panel = window.baseline_dialog
    panel.mode.setCurrentText('Linear')
    for high, low in ((11, 10), (2, 1)):
        panel.high.setValue(high)
        panel.low.setValue(low)
        panel.add_anchor()
    panel.preview()
    panel.apply()
    baseline_applied = window.document.current
    if (baseline_applied.data.baseline is None
            or not np.allclose(baseline_applied.data.real, signal, atol=1e-12)):
        raise SmokeCheckError('Baseline analytic Apply smoke failed')
    window.open_baseline()
    window.baseline_dialog.off()
    if window.document.current.data is not baseline_data:
        raise SmokeCheckError('Baseline disable smoke failed')
    window.undo()
    if window.document.current is not baseline_applied:
        raise SmokeCheckError('Baseline disable Undo smoke failed')
    window.undo()
    if window.document.current is not baseline_before:
        raise SmokeCheckError('Baseline Apply Undo smoke failed')
    pdf = result_path.with_suffix(".pdf")
    write_demo_pdf(pdf, window.spectrum, window.identity)
    reader = PdfReader(pdf)
    if len(reader.pages) != 1:
        raise SmokeCheckError("PDF page smoke failed")
    page = reader.pages[0]
    if abs(float(page.mediabox.width) - 297 / 25.4 * 72) > 0.5:
        raise SmokeCheckError("PDF width smoke failed")
    if abs(float(page.mediabox.height) - 210 / 25.4 * 72) > 0.5:
        raise SmokeCheckError("PDF height smoke failed")
    if "Chemical shift" not in page.extract_text():
        raise SmokeCheckError("PDF text smoke failed")
    from bruker_nmr.pdf_smoke import exercise_current_pdf
    try:
        current_pdf = exercise_current_pdf(window,result_path)
    except ValueError as error:
        raise SmokeCheckError(str(error)) from None
    from PySide6.QtWidgets import QToolBar
    workbench_fits = True
    for editor in (window.integrals,window.peaks):
        editor.show_table()
        QApplication.processEvents()
        available=window.screen().availableGeometry()
        workbench_fits &= (window.frameGeometry().width() <= available.width()
                           and window.frameGeometry().height() <= available.height()
                           and not editor.isWindow())
    window.workbench.inspector.hide()
    if not workbench_fits or len([b for b in window.findChildren(QToolBar) if b.isVisible()]) != 1:
        raise SmokeCheckError('Embedded workbench geometry smoke failed')
    # Force an actual Qt paint through the same PyQtGraph widget as interactive use.
    if window.grab().isNull() or len(window.plot.listDataItems()) != 1:
        raise SmokeCheckError("Qt plot smoke failed")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QPlainTextEdit, QTabWidget

    from bruker_nmr.ui.icons import NAMES
    if window.windowIcon().isNull():
        raise SmokeCheckError('Application identity icon missing')
    actions = [a for a in window.workbench.bar.actions() if not a.isSeparator()]
    if len(actions) != len(NAMES) or any(a.icon().isNull() for a in actions):
        raise SmokeCheckError('Semantic toolbar icons incomplete')
    details_checked = []

    def check_details():
        dialog = QApplication.activeModalWidget()
        tabs = dialog.findChild(QTabWidget)
        frame = dialog.frameGeometry()
        available = dialog.screen().availableGeometry()
        details_checked.append(tabs.count() == 4 and bool(dialog.findChildren(QPlainTextEdit))
                               and frame.width() <= available.width()
                               and frame.height() <= available.height())
        if isinstance(dialog, QDialog):
            dialog.reject()

    QTimer.singleShot(30, check_details)
    window.workbench.details()
    if details_checked != [True]:
        raise SmokeCheckError('Full source parameter inspector smoke failed')
    versions = {name: importlib.metadata.version(name) for name in (
        "numpy", "scipy", "nmrglue", "PySide6-Essentials", "pyqtgraph", "matplotlib", "pypdf")}
    return {"checks": ["numpy_fft", "scipy_optimizer", "nmrglue_jcamp_binary",
                       "qt_plot_paint", "landscape_pdf", "processed_viewer_open", "normalized_integrals", "independent_fid", "relative_phase", "explicit_baseline", "peaks_reference_appearance", "separate_history_labels_colour", "current_view_pdf", "embedded_workbench", "compact_print_layout", "candidate_identity_parameters", "saved_markup_roundtrip"], "packages": versions,
            **current_pdf, "workbench_fits_screen": workbench_fits, "pdf_file": pdf.name, "chooser_fits_screen": chooser_fits,
            "integral_table_fits_screen": integral_table_fits, "phase_fits_screen": phase_fits, "baseline_fits_screen": baseline_fits, "peak_table_fits_screen": peak_table_fits,
            "review_images": [result_path.stem+suffix for suffix in ("-main.png", "-phase.png", "-baseline.png", "-pdf-preview.png")]}
