"""Explicit anchor entry and transactional baseline/area preview."""
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.processing.baseline import Anchor, fit_baseline
from bruker_nmr.ui.compact import fit_columns, value_item, vertical_scroll
from bruker_nmr.ui.integrals import fit_dialog
from bruker_nmr.ui.notices import ProcessingNotice


class _BaselineScrollArea(QScrollArea):
    def resizeEvent(self, event):
        body = self.widget()
        if body is not None:
            # Windows can retain a wider height-for-width layout after the scrollbar appears.
            body.setFixedWidth(event.size().width())
        super().resizeEvent(event)


class BaselineDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer, self.document = viewer, viewer.document
        self.revision = self.document.revision
        self.calibration = self.document.current.calibration
        current = self.document.current.data
        self.source = current.phase_source
        self.anchors = [] if current.baseline is None else [Anchor(a.high+self.calibration, a.low+self.calibration) for a in current.baseline.fit.anchors]
        self.candidate = None
        self.overlays = []
        self.setWindowTitle('Baseline — explicit quiet anchors; preview before Apply')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        outer = QVBoxLayout(self)
        scroll = _BaselineScrollArea()
        scroll.setWidgetResizable(True)
        vertical_scroll(scroll)
        # Reserve the gutter before Qt measures the wrapped notices and tables.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setMinimumSize(0, 0)
        body = QWidget()
        layout = QVBoxLayout(body)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.mode = QComboBox()
        self.mode.addItems(['Constant', 'Linear'])
        if current.baseline is not None:
            self.mode.setCurrentText(current.baseline.fit.mode.title())
        form.addRow('Correction', self.mode)
        self.high, self.low = QDoubleSpinBox(), QDoubleSpinBox()
        grid = self.source.integration.shifted(self.calibration)
        for spin in (self.high, self.low):
            spin.setDecimals(7)
            spin.setRange(grid.minimum, grid.offset)
            spin.setSingleStep(grid.step)
            spin.clear()
            spin.lineEdit().setPlaceholderText('Choose ppm')
            spin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        form.addRow('Anchor high (ppm)', self.high)
        form.addRow('Anchor low (ppm)', self.low)
        self.add = QPushButton('Add anchor')
        self.add.setEnabled(False)
        self.add.setToolTip('Enter both bounds explicitly, with high ppm above low ppm.')
        for spin in (self.high, self.low):
            spin.lineEdit().textChanged.connect(self.update_bounds)
            spin.valueChanged.connect(self.update_bounds)
        self.remove = QPushButton('Remove selected')
        self.clear = QPushButton('Clear anchors')
        for button in (self.add, self.remove, self.clear):
            layout.addWidget(button)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['High (ppm)', 'Low (ppm)'])
        fit_columns(self.table, compact=())
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumSize(0, 60)
        self.table.setMaximumHeight(110)
        layout.addWidget(self.table)
        self.exclude = QCheckBox('Exclude integrals')
        self.exclude.setToolTip('Exclude points inside integral ranges when fitting the baseline.')
        self.exclude.setChecked(True if current.baseline is None else current.baseline.fit.excluded_integrals)
        layout.addWidget(self.exclude)
        self.draw_anchor=QPushButton('Draw quiet range')
        self.draw_anchor.setCheckable(True)
        self.draw_anchor.setToolTip('Drag on the spectrum to add quiet ranges. Press Esc to stop drawing.')
        self.draw_anchor.toggled.connect(self.anchor_mode)
        layout.addWidget(self.draw_anchor)
        self.preview_button = QPushButton('Preview')
        self.preview_button.setToolTip('Fit the baseline and preview the corrected integral areas.')
        layout.addWidget(self.preview_button)
        self.areas = QTableWidget(0, 4)
        self.areas.setHorizontalHeaderLabels(['#', 'Before', 'After', 'H preview'])
        self.areas.horizontalHeaderItem(0).setToolTip('Integral number')
        fit_columns(self.areas)
        self.areas.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for column in (1, 2):
            self.areas.horizontalHeaderItem(column).setToolTip('Signed full-grid area in amplitude × ppm; H is separately normalized.')
        self.areas.setMinimumSize(0, 40)
        self.areas.setMaximumHeight(140)
        layout.addWidget(self.areas)
        self.note = ProcessingNotice('Choose visibly quiet ranges using the ppm bounds above; Add each explicitly. '
                           'Linear needs at least two separated anchors; Constant needs one. '
                           'No spectral edge is assumed quiet. Integral exclusion cannot identify quiet regions. '
                           'The dashed curve is the proposed baseline; labels on the plot remain committed values. '
                           'Apply recalculates areas/reference; Cancel retains the current spectrum. '
                           'Reopening always fits the exact pre-baseline data. Uniform contamination is undetectable.')
        self.note.setWordWrap(True)
        self.notes_button=QPushButton('Scientific notes')
        self.notes_button.setCheckable(True)
        self.notes_button.setToolTip(self.note.text())
        self.notes_button.toggled.connect(self.note.set_expanded)
        self.note.hide()
        layout.addWidget(self.note.brief)
        layout.addWidget(self.notes_button)
        layout.addWidget(self.note)
        self.disable = QPushButton('Disable baseline')
        self.disable.setToolTip('Restore the spectrum from before the applied baseline correction.')
        self.disable.setEnabled(current.baseline is not None)
        layout.addWidget(self.disable)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply |
                                       QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(False)
        outer.addWidget(self.buttons)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.disable.clicked.connect(self.off)
        self.add.clicked.connect(self.add_anchor)
        self.remove.clicked.connect(self.remove_anchor)
        self.clear.clicked.connect(self.clear_anchors)
        self.timer=QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.preview)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.exclude.toggled.connect(self.invalidate)
        self.preview_button.clicked.connect(self.preview)
        self.refresh_anchors()
        fit_dialog(self, 760, 650)

    def anchor_mode(self,checked):
        box=self.viewer.view_box
        box.finish_pending()
        if checked:
            self.viewer.peaks.clear_ghost()
            box.integrating=box.picking=False
            self.viewer.zoom_action.setChecked(True)
            box.setMouseMode(box.RectMode)
        box.anchoring=checked

    def valid(self):
        return (self.viewer.document is self.document and self.document is not None
                and self.document.revision == self.revision)

    def clear_overlays(self):
        for item in self.overlays:
            self.viewer.plot.removeItem(item)
        self.overlays.clear()

    def invalidate(self):
        if hasattr(self,'timer') and self.anchors:
            self.timer.start()
        self.candidate = None
        self.viewer._baseline_preview = None
        self.clear_overlays()
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(False)
        self.areas.setRowCount(0)
        self.viewer._draw_curve()

    def refresh_anchors(self):
        self.table.setRowCount(len(self.anchors))
        for row, anchor in enumerate(self.anchors):
            self.table.setItem(row, 0, value_item(f'{anchor.high:.7f}'))
            self.table.setItem(row, 1, value_item(f'{anchor.low:.7f}'))

    def update_bounds(self):
        self.add.setEnabled(self.high.hasAcceptableInput() and self.low.hasAcceptableInput()
                            and self.high.value() > self.low.value())

    def add_anchor(self):
        try:
            if not self.high.hasAcceptableInput() or not self.low.hasAcceptableInput():
                raise ValueError('Enter both anchor bounds explicitly before adding a range.')
            anchor = Anchor(self.high.value(), self.low.value())
            if len(self.anchors) >= 64:
                raise ValueError('At most 64 anchors are supported')
            self.anchors.append(anchor)
            self.invalidate()
            self.refresh_anchors()
            self.high.clear()
            self.low.clear()
            self.note.setText('Anchor added. Preview checks grid overlap, exclusions and fit conditioning. '
                              'Selected ranges must be quiet; broad or uniform signals may be subtracted.')
        except ValueError as error:
            self.note.setText(str(error))

    def remove_anchor(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.anchors):
            self.anchors.pop(row)
            self.invalidate()
            self.refresh_anchors()

    def clear_anchors(self):
        self.anchors.clear()
        self.invalidate()
        self.refresh_anchors()
        self.note.setText('No anchors. Leave correction Off or explicitly select quiet ranges.')

    def preview(self):
        if not self.valid():
            self.reject()
            return
        self.invalidate()
        self.timer.stop()
        try:
            excluded = self.document.current.integrals.regions if self.exclude.isChecked() else ()
            fit = fit_baseline(self.source.ppm, self.source.real,
                               tuple(Anchor(a.high-self.calibration, a.low-self.calibration)
                                     for a in self.anchors),
                               mode=self.mode.currentText().lower(), excluded=excluded,
                               peak_positions=tuple(p.ppm for p in self.document.current.peaks.records))
            candidate = self.source.with_baseline(fit)
            state = self.document.baseline_integrals(candidate)
            before = integral_snapshot(self.document.current.integrals, self.document.current.grid)
            after = integral_snapshot(state, candidate.integration)
            self.areas.setRowCount(len(before))
            for row, (old, new) in enumerate(zip(before, after)):
                values = [str(row+1), f'{old.area:.4g}', f'{new.area:.4g}',
                          '?' if new.value is None else f'{new.value:.4g}']
                details = [values[0], f'{old.area:.7g}', f'{new.area:.7g}',
                           'Unverified' if new.value is None else f'{new.value:.5f}']
                for column, (value, detail) in enumerate(zip(values, details)):
                    self.areas.setItem(row, column, value_item(value, detail=detail))
            pen = pg.mkPen('#a65d00', width=1.5, style=Qt.PenStyle.DashLine)
            ends = [float(min(self.source.ppm)), float(max(self.source.ppm))]
            line = self.viewer.plot.plot([v+self.calibration for v in ends], fit.values(ends), pen=pen)
            self.overlays.append(line)
            for anchor in fit.anchors:
                region = pg.LinearRegionItem((anchor.low+self.calibration, anchor.high+self.calibration), movable=False,
                                             brush=pg.mkBrush(0, 130, 100, 20))
                self.viewer.plot.addItem(region)
                self.overlays.append(region)
            self.candidate = candidate
            self.viewer._baseline_preview = candidate.real
            self.viewer._draw_curve()
            warnings = ' '.join(fit.warnings)
            self.note.setText((warnings+' ' if warnings else '') +
                              'Preview only. Anchors are user-selected, never verified signal-free. '
                              'The fit is extrapolated outside anchor centers; review the entire spectrum. '
                              'Plot integral labels remain committed values; the table shows preview areas. '
                              'Apply retains a fixed imported/frozen scale and recomputes an assigned reference.')
            self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(True)
        except (ValueError, FloatingPointError) as error:
            self.note.setText(str(error))

    def apply(self):
        if not self.valid():
            self.reject()
            return
        if self.candidate is None:
            self.note.setText('Preview a valid fit before Apply.')
            return
        try:
            self.document.apply_baseline(self.candidate.baseline.fit, prepared=self.candidate)
        except ValueError as error:
            self.note.setText(str(error))
            return
        self.accept()

    def off(self):
        if not self.valid():
            self.reject()
            return
        try:
            self.document.disable_baseline()
        except ValueError as error:
            self.note.setText(str(error))
            return
        self.accept()

    def done(self, result):
        self.timer.stop()
        self.viewer.view_box.anchoring=False
        self.viewer._baseline_preview = None
        self.clear_overlays()
        self.candidate = self.source = self.document = None
        super().done(result)
        self.viewer._render_document()
