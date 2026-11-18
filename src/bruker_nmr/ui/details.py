"""Local-only parameter inspector; no file reads, printing, persistence or logging."""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bruker_nmr.ui.integrals import fit_dialog


def show_details(owner):
    dialog = QDialog(owner)
    dialog.setWindowTitle("Spectrum details — source parameters")
    layout = QVBoxLayout(dialog)
    notice = QLabel(
        "Original verified status parameters are separate from current processing. "
        "Missing fields are not invented. Values remain local and are not added to PDF."
    )
    notice.setWordWrap(True)
    layout.addWidget(notice)
    tabs = QTabWidget()
    layout.addWidget(tabs, 1)
    overview = QPlainTextEdit(owner.summary.toolTip())
    overview.setReadOnly(True)
    tabs.addTab(overview, "Overview")
    parameters = owner.document.current.data.source_parameters
    for title, values in [
        ("Acquisition (acqus)", parameters.acquisition),
        ("Processing (procs)", parameters.processing),
    ]:
        panel = QWidget()
        body = QVBoxLayout(panel)
        search = QLineEdit()
        search.setPlaceholderText("Filter parameter names")
        body.addWidget(search)
        output = QPlainTextEdit()
        output.setReadOnly(True)
        body.addWidget(output, 1)

        def update(term="", rows=values, target=output):
            selected = [(k, v) for k, v in rows if term.casefold() in k.casefold()]
            target.setPlainText(
                "\n\n".join(f"{k} = {v}" for k, v in selected)
                if rows
                else "No verified source parameters available."
            )

        update()
        search.textChanged.connect(update)
        tabs.addTab(panel, title)
    current = owner.document.current
    text = (
        "Current processing is independent of original procs.\n"
        f"Mode: {current.data.opening_mode}\nCalibration offset: {current.calibration:g} ppm\n"
        f"Baseline: {current.data.baseline is not None}\n"
    )
    if current.data.opening_mode == "raw":
        recipe = current.data.operator.recipe
        text += (
            f"SI: {recipe.si}\nLB: {recipe.lb:g} Hz\n"
            f"Operator reconstruction phases: {recipe.known_phase}\n"
        )
    text += f"Relative phase steps: {current.data.phase_steps}"
    recipe_text = QPlainTextEdit(text)
    recipe_text.setReadOnly(True)
    tabs.addTab(recipe_text, "Current processing")
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    fit_dialog(dialog, 820, 600)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()
