"""Explicit selection of verified acquisition/processing choices."""
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from bruker_nmr.io.errors import InputError


class ExperimentChooser(QDialog):
    def __init__(self, choices, parent=None, *, allow_raw=False):
        super().__init__(parent)
        self.choices = choices
        self.allow_raw = allow_raw
        self.setWindowTitle("Choose spectrum")
        available = self.screen().availableGeometry()
        self.resize(min(950, max(320, available.width() - 40)),
                    min(400, max(280, available.height() - 60)))
        layout = QVBoxLayout(self)
        description = QLabel("Choose an experiment and processing number. "
                             "Processed spectra open directly. Raw-only inputs use independent FID processing.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.table = QTableWidget(len(choices), 8)
        self.table.setHorizontalHeaderLabels(["Location", "Experiment", "Processing", "Nucleus", "Dimensions",
                                              "Processed", "FID input", "Notes"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for row, choice in enumerate(choices):
            notes = "; ".join(str(InputError(code.split(":")[-1])) for code in choice.issues)
            # Relative folders distinguish datasets with the same experiment/proc numbers.
            # They are displayed locally only, never copied into logs or diagnostics.
            values = [choice.locator[0], choice.experiment_number, choice.processing_number, choice.nucleus,
                      choice.dimension, "Available" if choice.processed_available else "Unavailable",
                      "Valid input" if choice.raw_available else "Unavailable", notes]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem("—" if value is None else str(value)))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        note = QLabel("Raw processing starts unphased with a separate annotation/normalization state.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open |
                                        QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.table.itemSelectionChanged.connect(self._update)
        self.table.itemDoubleClicked.connect(lambda *_: self.accept() if self.selection() else None)
        layout.addWidget(self.buttons)

    def selection(self):
        row = self.table.currentRow()
        if row < 0 or not (self.choices[row].processed_available or
                          self.allow_raw and self.choices[row].raw_available):
            return None
        return self.choices[row]

    def _update(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(self.selection() is not None)

    def accept(self):
        if self.selection() is not None:
            super().accept()
