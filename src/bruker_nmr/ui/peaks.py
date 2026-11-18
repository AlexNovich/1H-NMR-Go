"""Peak table, full-resolution manual picking and one selected movable handle."""
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bruker_nmr.domain.labels import format_ppm
from bruker_nmr.domain.peaks import nearest_extremum
from bruker_nmr.ui.compact import fit_columns, value_item, vertical_scroll
from bruker_nmr.ui.handles import allow_control_pan, menu_action
from bruker_nmr.ui.integrals import fit_dialog


class PeakEditor(QDialog):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Tool)
        self.owner = owner
        self.selected = None
        self.preview_position = None
        self.handle = None
        self.ticket = None
        self._document_id = None
        self._plot_data = self._plot_real = self._plot_records = None
        self._plot_calibration = self._plot_preview = None
        self.markers = pg.ScatterPlotItem(size=7, symbol='+', pen=pg.mkPen('#a24422', width=1.2))
        owner.view_box.addItem(self.markers, ignoreBounds=True)
        self.markers.sigClicked.connect(self.clicked)
        self.ghost = pg.ScatterPlotItem(size=10,symbol='o',pen=pg.mkPen('#a24422',width=1.2),brush=None)
        self.ghost.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        owner.view_box.addItem(self.ghost,ignoreBounds=True)
        self.ghost_index = None
        self.ghost.hide()
        self.setWindowTitle('Peaks — visibility never deletes records')
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        vertical_scroll(scroll)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        row = QHBoxLayout()
        self.show_markers = QCheckBox('Show markers')
        self.mode = QComboBox()
        self.mode.addItems(['Auto', 'All', 'Hidden'])
        row.addWidget(self.show_markers)
        row.addWidget(QLabel('Labels'))
        row.addWidget(self.mode)
        layout.addLayout(row)
        self.count = QLabel()
        self.count.setWordWrap(True)
        layout.addWidget(self.count)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Label', 'ppm', 'Origin', 'Amp.', '?'])
        self.table.horizontalHeaderItem(3).setToolTip('Current sample amplitude in arbitrary units. k, M, G, T are scale factors.')
        self.table.horizontalHeaderItem(4).setToolTip('Peak needs review')
        fit_columns(self.table, compact=(0, 4))
        self.table.setMinimumSize(0, 70)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        controls = QGridLayout()
        self.add_button = QPushButton('Add near ppm…')
        self.edit_button = QPushButton('Edit ppm…')
        self.zero_button = QPushButton('Set to 0 ppm')
        actions = (
            (self.add_button, self.add_dialog),
            (self.edit_button, self.edit_dialog),
            (self.zero_button, self.zero_selected),
            (QPushButton('Mark reviewed'), self.review),
            (QPushButton('Delete'), self.delete_selected),
            (QPushButton('Clear peaks'), self.clear),
            (QPushButton('Show all labels'), self.unhide_all),
        )
        for index, (button, callback) in enumerate(actions):
            controls.addWidget(button, index // 2, index % 2, 1, 2 if index == 6 else 1)
            button.clicked.connect(callback)
        layout.addLayout(controls)
        self.note = QLabel('Pick Peaks: click near an extremum or drag a rectangle around maxima. '
                           'Edit ppm enters an exact marker position. Hidden labels retain their records. '
                           '? in a label means review the pick after phase/baseline/recipe changes. '
                           'Auto shows labels that fit. File → Save annotations stores picks in processed pdata.')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.show_markers.toggled.connect(self.visibility)
        self.mode.currentTextChanged.connect(self.visibility)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.itemChanged.connect(self.label_changed)
        fit_dialog(self, 790, 500)

    @staticmethod
    def amplitude_text(value):
        for factor, suffix in ((1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k')):
            if abs(value) >= factor:
                return f'{value / factor:.3g}{suffix}'
        return f'{value:.4g}'

    @property
    def document(self):
        return self.owner.document

    def _apply(self, action):
        if self.owner.loading:
            return
        self.owner.view_box.finish_pending()
        self.owner.cancel_processing()
        try:
            action()
        except ValueError as error:
            self.note.setText(str(error))
            self.owner.statusBar().showMessage(str(error))
            return
        self.owner._render_document()

    def show_table(self):
        self.refresh_table()
        self.owner.workbench.show_table(self)

    def sample(self, native):
        data = self.document.current.data
        index = np.clip(np.rint((np.asarray(native)-data.ppm[0])/(data.ppm[1]-data.ppm[0])).astype(int),
                        0, len(data.real)-1)
        return index

    def refresh_table(self):
        state = self.document.current
        with QSignalBlocker(self.table), QSignalBlocker(self.show_markers), QSignalBlocker(self.mode):
            self.show_markers.setChecked(state.peaks.markers_visible)
            self.mode.setCurrentText(state.peaks.label_mode.title())
            self.table.setRowCount(len(state.peaks.records))
            for row, peak in enumerate(state.peaks.records):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                               Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(Qt.CheckState.Checked if peak.label_visible else Qt.CheckState.Unchecked)
                check.setData(Qt.ItemDataRole.UserRole, peak.id)
                self.table.setItem(row, 0, check)
                ppm = peak.ppm + state.calibration
                amplitude = state.data.real[self.sample(peak.ppm)]
                self.table.setItem(row, 1, value_item(format_ppm(ppm), detail=f'{ppm:.7f} ppm'))
                self.table.setItem(row, 2, value_item(peak.source))
                self.table.setItem(row, 3, value_item(self.amplitude_text(amplitude), detail=f'{amplitude:.7g} a.u.'))
                self.table.setItem(row, 4, value_item('?' if peak.needs_review else '—',
                    detail='Review this peak' if peak.needs_review else 'Reviewed'))
                if peak.id == self.selected:
                    self.table.selectRow(row)
            if self.selected is None:
                self.table.setCurrentCell(-1, -1)
                self.table.clearSelection()
        enabled = not self.owner.loading
        self.add_button.setEnabled(enabled)
        self.edit_button.setEnabled(enabled and self.selected is not None)
        self.zero_button.setEnabled(enabled and self.selected is not None)
        self.update_count()

    def update_count(self):
        peaks = self.document.current.peaks
        self.count.setText(f'{len(peaks.records)} records; {len(self.owner.peak_band.placements)} labels shown; '
                           f'{len(self.owner.peak_band.hidden)} Auto-suppressed in view; '
                           f'{sum(not p.label_visible for p in peaks.records)} individually hidden.')

    def visibility(self):
        self._apply(lambda: self.document.appearance(markers=self.show_markers.isChecked(),
                                                     label_mode=self.mode.currentText().lower()))

    def label_changed(self, item):
        if item.column() == 0:
            self._apply(lambda: self.document.peak_label(item.data(Qt.ItemDataRole.UserRole),
                                                        item.checkState() == Qt.CheckState.Checked))

    def selection_changed(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is not None:
            self.select(item.data(Qt.ItemDataRole.UserRole), refresh=False)

    def select(self, id, *, refresh=True):
        self.document.current.peaks.by_id(id)
        self.selected = id
        self.owner.selected_editor = self
        self.sync_handle()
        self.owner.peak_band.rebuild()
        if refresh and self.isVisible():
            self.refresh_table()

    def clicked(self, item, spots, event):
        if spots:
            self.select(spots[0].data())

    def update_plot(self, real=None):
        state = self.document.current
        records = state.peaks.records
        real = state.data.real if real is None else real
        if (self._plot_data is not state.data or self._plot_real is not real
                or self._plot_records is not records or self._plot_calibration != state.calibration
                or self._plot_preview != self.preview_position):
            native = np.array([p.ppm for p in records])
            positions = native+state.calibration
            if self.preview_position is not None:
                id, position = self.preview_position
                for i, peak in enumerate(records):
                    if peak.id == id:
                        positions[i] = position
                        native[i] = position-state.calibration
            self.markers.setData(positions, real[self.sample(native)], data=[p.id for p in records])
            self._plot_data, self._plot_real, self._plot_records = state.data, real, records
            self._plot_calibration, self._plot_preview = state.calibration, self.preview_position
        self.markers.setVisible(state.peaks.markers_visible)
        self.owner.peak_band.rebuild()
        self.update_count()

    def sync_plot(self, *, select_first=True):
        state = self.document.current
        if self._document_id != self.document.document_id:
            self.selected = None
            self._document_id = self.document.document_id
        if self.selected not in {p.id for p in state.peaks.records}:
            self.selected = state.peaks.records[0].id if state.peaks.records and select_first else None
        self.preview_position = self.ticket = None
        self.sync_handle()
        self.update_plot()
        if self.isVisible():
            self.refresh_table()

    def sync_handle(self):
        if self.handle is not None:
            self.handle.sigPositionChanged.disconnect()
            self.handle.sigPositionChangeFinished.disconnect()
            self.owner.view_box.removeItem(self.handle)
            self.handle.deleteLater()
            self.handle = None
        if self.owner.view_box.picking and self.selected is not None and not self.owner.loading:
            peak = self.document.current.peaks.by_id(self.selected)
            grid = self.document.current.grid
            self.handle = pg.InfiniteLine(peak.ppm+self.document.current.calibration, movable=True,
                bounds=(grid.minimum, grid.offset), pen=pg.mkPen('#a24422', width=1.2), span=(.2,.9))
            allow_control_pan(self.handle)
            self.owner.view_box.addItem(self.handle, ignoreBounds=True)
            self.handle.sigPositionChanged.connect(self.preview_move)
            self.handle.sigPositionChangeFinished.connect(self.finish_move)

    def preview_move(self):
        if self.ticket is None:
            self.owner.view_box.finish_pending()
            self.ticket = (self.document.document_id, self.document.revision)
        if self.ticket == (self.document.document_id, self.document.revision):
            self.preview_position = (self.selected, self.handle.value())
            self.update_plot()

    def finish_move(self):
        if self.ticket == (self.document.document_id, self.document.revision):
            position = self.handle.value()
            self._apply(lambda: self.document.move_peak(self.selected, position))

    def clear_ghost(self):
        self.ghost.hide()
        self.ghost_index = None

    def pick_radius(self):
        limits = self.owner.view_box.viewRange()[0]
        # Same bounded full-grid window for click and ghost, <=65539 samples.
        step = self.document.current.grid.step
        return min(max(3*step,(limits[1]-limits[0])*.015),32768*step)

    def show_ghost(self, position):
        state = self.document.current
        try:
            index = nearest_extremum(state.data.ppm,state.data.real,
                                    position-state.calibration,self.pick_radius())
        except ValueError:
            self.clear_ghost()
            return
        self.ghost_index = index
        self.ghost.setData([state.data.ppm[index]+state.calibration],[state.data.real[index]])
        self.ghost.show()

    def hit_test(self, scene_point, radius=6):
        if not self.markers.isVisible():
            return None
        x,y = self.markers.getData()
        if x is None:
            return None
        best = None
        for peak,a,b in zip(self.document.current.peaks.records,x,y):
            point = self.owner.view_box.mapViewToScene(QPointF(float(a),float(b)))
            distance = (point.x()-scene_point.x())**2+(point.y()-scene_point.y())**2
            if distance <= radius**2 and (best is None or distance < best[0]):
                best = (distance,peak.id)
        return None if best is None else best[1]

    def add_at(self, position):
        self.owner.view_box.finish_pending()
        radius = self.pick_radius()
        def add():
            self.document.add_peak(position, radius)
            self.selected = self.document.current.peaks.records[-1].id
        self._apply(add)

    def add_dialog(self):
        grid = self.document.current.grid
        value, accepted = QInputDialog.getDouble(self.owner, 'Add near an extremum', 'Peak near ppm',
                    (grid.offset+grid.minimum)/2, grid.minimum, grid.offset, 7)
        if accepted:
            self.add_at(value)

    def edit_dialog(self):
        if self.selected is None:
            return
        grid = self.document.current.grid
        value = self.document.current.peaks.by_id(self.selected).ppm+self.document.current.calibration
        value, accepted = QInputDialog.getDouble(self.owner, 'Peak position', 'Marker ppm', value,
                                                 grid.minimum, grid.offset, 7)
        if accepted:
            self._apply(lambda: self.document.move_peak(self.selected, value))

    def delete_selected(self):
        self._apply(lambda: self.document.delete_peak(self.selected))

    def clear(self):
        self._apply(self.document.clear_peaks)

    def zero_selected(self):
        if self.selected is None:
            self.show_table()
            self.note.setText('Select a peak before setting its position to zero.')
            return
        self._apply(lambda: self.document.set_zero(self.selected))

    def review(self):
        self._apply(lambda: self.document.review_peak(self.selected))

    def unhide_all(self):
        self._apply(lambda: self.document.apply_peaks(replace(self.document.current.peaks,
            records=tuple(replace(p, label_visible=True) for p in self.document.current.peaks.records))))

    def context_menu(self, point, id=None):
        if id is not None:
            self.select(id)
        menu = QMenu(self.owner)
        if self.selected is not None:
            target = self.selected
            def for_target(action):
                def invoke():
                    self.select(target)
                    action()
                return invoke
            for text, action in [('Edit peak ppm…', self.edit_dialog), ('Set peak to 0 ppm', self.zero_selected),
                                 ('Delete peak', self.delete_selected), ('Mark reviewed', self.review)]:
                menu_action(menu, text, self.owner, for_target(action))
            peak = self.document.current.peaks.by_id(self.selected)
            menu_action(menu, 'Hide this label' if peak.label_visible else 'Show this label', self.owner,
                lambda: self._apply(lambda:self.document.peak_label(peak.id, not peak.label_visible)))
        menu_action(menu, 'Peak table…', self.owner, self.show_table)
        menu_action(menu, 'Add near ppm…', self.owner, self.add_dialog)
        menu_action(menu, 'Clear all peaks', self.owner, self.clear)
        menu.popup(point)
        self._menu = menu
