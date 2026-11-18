"""Integral table, bounds previews and plot handles backed by document commands."""
import pyqtgraph as pg
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bruker_nmr.domain.integrals import (
    edit_region,
    has_overlaps,
    integral_snapshot,
    region_by_id,
)
from bruker_nmr.ui.compact import fit_columns, vertical_scroll
from bruker_nmr.ui.handles import allow_control_pan, menu_action


def notice_text(issue):
    descriptions = {
        "markup:stale_or_invalid": "Saved NMR-go details did not match the current files. Loaded native Bruker annotations.",
        "intrng:unsupported_bias_slope": "Operator bias/slope correction is unsupported; geometry retained, values unverified.",
        "integrals:source_conflict": "Operator annotation sources disagree; assign a reference to supported geometry.",
        "integrals:reference_required": "Operator scale unavailable; assign a reference.",
        "integrals:table_geometry_reduced_precision": "Ranges imported from rounded table coordinates (reduced precision).",
        "integrals:normalization_unavailable": "Operator normalization could not be verified; assign a reference.",
        "procs:invalid_intscl": "Stored operator scale is invalid; table consistency was checked separately.",
        "peaks:binary_or_ranges_not_imported": "Picked-peak XML is missing; proprietary peak data/ranges are not imported.",
    }
    if issue in descriptions:
        return descriptions[issue]
    parts = issue.split(":")
    if len(parts) == 3 and parts[1] == "invalid_rows":
        return f"{parts[0]}: {parts[2]} malformed or out-of-range records omitted."
    return f"{parts[0]}: optional annotations are incomplete or unsupported; spectrum preserved."


def fit_dialog(dialog, width, height):
    available = dialog.screen().availableGeometry()
    dialog.resize(min(width, max(160, available.width() - 60)),
                  min(height, max(120, available.height() - 80)))


class BoundsDialog(QDialog):
    def __init__(self, grid, a, b, preview, parent):
        super().__init__(parent)
        self.setWindowTitle("Integral boundaries")
        form = QFormLayout(self)
        self.high, self.low = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, value in ((self.high, a), (self.low, b)):
            spin.setDecimals(9)
            spin.setRange(grid.minimum, grid.offset)
            spin.setSingleStep(grid.step)
            spin.setValue(value)
        form.addRow("High ppm", self.high)
        form.addRow("Low ppm", self.low)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        form.addRow(self.notice)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                       QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)

        def validate():
            try:
                preview(self.high.value(), self.low.value())
                self.notice.setText("Boundaries snap to the full-resolution grid. Apply is one undoable edit.")
                valid = True
            except ValueError as error:
                self.notice.setText(str(error))
                valid = False
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)

        self.high.valueChanged.connect(validate)
        self.low.valueChanged.connect(validate)
        validate()
        fit_dialog(self, 430, 250)


class IntegralEditor(QDialog):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Tool)
        self.owner = owner
        self.selected = None
        self._document_id = None
        self._rendering = False
        self.rois, self.labels = {}, {}
        self._label_text = {}
        self._preview_ticket = None
        self.setWindowTitle("Integrals")
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        vertical_scroll(scroll)
        scroll.setMinimumSize(0, 0)
        container = QWidget()
        scroll.setWidget(container)
        outer.addWidget(scroll)
        layout = QVBoxLayout(container)
        self.normalization = QLabel()
        self.normalization.setWordWrap(True)
        layout.addWidget(self.normalization)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["#", "High", "Low", "H", "Import", "Ref."])
        self.table.horizontalHeaderItem(0).setToolTip("Region number")
        self.table.horizontalHeaderItem(1).setToolTip("High endpoint in ppm")
        self.table.horizontalHeaderItem(2).setToolTip("Low endpoint in ppm")
        self.table.horizontalHeaderItem(3).setToolTip("Current normalized value in H")
        self.table.horizontalHeaderItem(5).setToolTip("Reference integral")
        fit_columns(self.table, compact=(0, 5))
        self.table.horizontalHeaderItem(4).setToolTip("Original operator values; not recalculated after edits.")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumSize(0, 40)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)
        controls = QGridLayout()
        self.add_button = QPushButton("New range…")
        self.edit_button = QPushButton("Edit bounds…")
        self.assign_button = QPushButton("Assign H…")
        self.delete_button = QPushButton("Delete")
        self.clear_button = QPushButton("Clear all")
        for i, (button, callback) in enumerate([
            (self.add_button, self.new_dialog), (self.edit_button, self.edit_dialog),
            (self.assign_button, self.assign_dialog), (self.delete_button, self.delete_selected),
            (self.clear_button, self.clear_all),
        ]):
            controls.addWidget(button, i // 2, i % 2)
            button.clicked.connect(callback)
        layout.addLayout(controls)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.notices = QLabel()
        self.notices.setWordWrap(True)
        layout.addWidget(self.notices)
        self.help = QLabel("Reference edit rescales; deletion freezes. Clear all starts fresh.")
        self.help.setToolTip("Integrate mode: draw a range; drag lower bracket boundaries to edit. "
                             "Reference edits retain assigned H. Operator values retain the import. "
                             "Use File → Save annotations to store integrals in processed pdata.")
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        fit_dialog(self, 700, 440)

    @property
    def document(self):
        return self.owner.document

    def show_table(self):
        self.refresh_table()
        self.owner.workbench.show_table(self)

    def refresh_table(self, state=None, *, select_first=True):
        state = self.document.current.integrals if state is None else state
        grid = self.document.current.grid
        rows = integral_snapshot(state, grid)
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                def shown(value, missing):
                    return missing if value is None else f"{value:.4g}"

                texts = [str(index + 1), f"{row.high:.4g}", f"{row.low:.4g}",
                         shown(row.value, "?"), shown(row.operator_value, "—"),
                         "✓" if row.reference else ""]
                details = [texts[0], f"{row.high:.7f} ppm", f"{row.low:.7f} ppm",
                           "Unverified" if row.value is None else f"{row.value:.5f} H",
                           "—" if row.operator_value is None else f"{row.operator_value:.5f}",
                           f"Reference: {state.normalization.protons:g} H" if row.reference else ""]
                for column, (text, detail) in enumerate(zip(texts, details)):
                    item = self.table.item(index, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self.table.setItem(index, column, item)
                    if item.text() != text:
                        item.setText(text)
                    item.setToolTip(detail)
                self.table.item(index, 0).setData(Qt.ItemDataRole.UserRole, row.id)
            if self.selected not in {r.id for r in rows}:
                self.selected = rows[0].id if rows and select_first else None
            if self.selected:
                index = next(i for i, r in enumerate(rows) if r.id == self.selected)
                if self.table.currentRow() != index:
                    self.table.selectRow(index)
            else:
                self.table.setCurrentCell(-1, -1)
                self.table.clearSelection()
        norm = state.normalization
        descriptions = {
        "markup:stale_or_invalid": "Saved NMR-go details did not match the current files. Loaded native Bruker annotations.","absent": "No scale — first positive manual integral becomes 1.00 H.",
                        "imported": "Imported fixed scale — no reference inferred.",
                        "unverified": "Operator normalization unverified — assign a positive reference.",
                        "anchor": f"Explicit reference: {norm.protons or 0:g} H.",
                        "frozen": "Frozen scale — reference deleted; other values retained."}
        self.normalization.setText(descriptions[norm.kind])
        notices = [notice_text(i) for i in self.document.current.data.annotations.issues]
        if has_overlaps(state):
            notices.append("Overlapping ranges: values are independent; no grand total.")
        self.notices.setText("Notices: " + "; ".join(notices) if notices else "")
        selected = next((r for r in rows if r.id == self.selected), None)
        self.detail.setText(f"Selected signed area: {selected.area:.7g} ppm × a.u." +
                            (" — imported bias/slope unsupported" if not selected.correction_supported else "")
                            if selected else "Select a range or draw one in Integrate mode.")
        available = not self.owner.loading
        self.add_button.setEnabled(available)
        for button in (self.edit_button, self.assign_button, self.delete_button):
            button.setEnabled(available and self.selected is not None)
        self.clear_button.setEnabled(available and (bool(rows) or norm.kind != "absent"))
        self._update_labels(rows)

    def _selection_changed(self):
        self.owner.selected_editor = self
        if self.table.currentRow() >= 0:
            item = self.table.item(self.table.currentRow(), 0)
            self.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.refresh_table()
        self._set_handle_modes()

    def sync_plot(self, *, select_first=True):
        self._rendering = True
        try:
            if self._document_id != self.document.document_id:
                self.selected = None
                self._document_id = self.document.document_id
            self._preview_ticket = None
            for roi in self.rois.values():
                roi.sigRegionChanged.disconnect()
                roi.sigRegionChangeFinished.disconnect()
                self.owner.plot.removeItem(roi)
                roi.deleteLater()
            for label in self.labels.values():
                self.owner.plot.removeItem(label)
                label.deleteLater()
            self.rois, self.labels = {}, {}
            self._label_text = {}
            rows = integral_snapshot(self.document.current.integrals, self.document.current.grid)
            for row in rows:
                roi = pg.LinearRegionItem((row.low, row.high), bounds=(
                    self.document.current.grid.minimum,
                    self.document.current.grid.offset),
                    brush=(22, 78, 128, 18), pen=pg.mkPen("#416c8a", width=1),
                    span=(.02, .12), swapMode="sort")
                roi.setZValue(10)
                self.owner.plot.addItem(roi, ignoreBounds=True)
                doc_id = self.document.document_id
                roi.sigRegionChanged.connect(lambda *_, key=row.id, ticket=doc_id:
                                             self._roi_changed(key, ticket))
                roi.sigRegionChangeFinished.connect(lambda *_, key=row.id, ticket=doc_id:
                                                    self._roi_finished(key, ticket))
                self.rois[row.id] = roi
                allow_control_pan(roi)
                for line in roi.lines:
                    allow_control_pan(line)
            self.refresh_table(select_first=select_first)
            self._set_handle_modes()
            self.update_positions()
        finally:
            self._rendering = False

    def _set_handle_modes(self):
        for id, roi in self.rois.items():
            roi.setVisible(self.document.current.show_integrals)
            roi.setMovable(self.owner.view_box.integrating and not self.owner.loading)
            pen = pg.mkPen("#9a4d00" if id == self.selected else "#416c8a", width=1.4)
            for line in roi.lines:
                line.setPen(pen)

    def _update_labels(self, rows):
        self.owner.integral_band.set_rows(rows)

    def update_positions(self):
        self.owner.integral_band.rebuild()

    def context_menu(self, point, id=None):
        if self.owner.loading:
            return
        if id is not None:
            self.selected = id
        menu = QMenu(self.owner)
        if self.selected is not None:
            target = self.selected
            def for_target(action):
                def invoke():
                    self.selected = target
                    action()
                return invoke
            menu_action(menu, 'Edit integral bounds…', self.owner, for_target(self.edit_dialog))
            menu_action(menu, 'Assign reference H…', self.owner, for_target(self.assign_dialog))
            menu_action(menu, 'Delete integral', self.owner, for_target(self.delete_selected))
            menu.addSeparator()
        menu_action(menu, 'New integral…', self.owner, self.new_dialog)
        menu_action(menu, 'Integral table…', self.owner, self.show_table)
        menu_action(menu, 'Clear all integrals', self.owner, self.clear_all)
        menu.popup(point)
        self._menu = menu

    def _preview(self, id, a, b):
        state = edit_region(self.document.current.integrals, self.document.current.grid, id, a, b)
        integral_snapshot(state, self.document.current.grid)
        return state

    def _roi_changed(self, id, doc_id):
        if self._rendering or self.owner.loading or doc_id != self.document.document_id:
            return
        self.owner.view_box.finish_pending()
        if self._preview_ticket is None:
            self._preview_ticket = (doc_id, self.document.revision)
        if self._preview_ticket != (doc_id, self.document.revision):
            return
        self.selected = id
        try:
            preview = self._preview(id, *self.rois[id].getRegion())
            self.refresh_table(preview)
            self.owner.statusBar().showMessage("Integral preview — release to apply one edit.")
        except ValueError as error:
            self.owner.statusBar().showMessage("Invalid preview; prior scale retained. " + str(error))
            self.notices.setText("Invalid preview: " + str(error))
        self.update_positions()

    def _roi_finished(self, id, doc_id):
        if self._rendering or self.owner.loading or doc_id != self.document.document_id:
            return
        if self._preview_ticket not in (None, (doc_id, self.document.revision)):
            return
        error = None
        try:
            self.document.apply_integrals(self._preview(id, *self.rois[id].getRegion()))
        except ValueError as failure:
            error = str(failure)
        self.owner._render_document()
        if error:
            self.owner.statusBar().showMessage("Edit not applied: " + error)
            self.notices.setText(error)

    def _apply(self, action):
        if self.owner.loading:
            return
        self.owner.view_box.finish_pending()
        self.owner.cancel_processing()
        try:
            action()
        except ValueError as error:
            self.notices.setText(str(error))
            self.owner.statusBar().showMessage(str(error))
            return
        self.owner._render_document()

    def create_range(self, a, b):
        def create():
            self.document.add_integral(a, b)
            self.selected = self.document.current.integrals.regions[-1].id
        self._apply(create)

    def delete_selected(self):
        self._apply(lambda: self.document.delete_integral(self.selected))

    def clear_all(self):
        self._apply(self.document.clear_integrals)

    def assign(self, count):
        self._apply(lambda: self.document.assign_protons(self.selected, count))

    def new_dialog(self):
        grid = self.document.current.grid
        x, _ = self.owner.view_box.viewRange()
        a, b = min(grid.offset, x[1]), max(grid.minimum, x[0])
        dialog = BoundsDialog(grid, a, b, lambda high, low: grid.bounds(high, low), self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.create_range(dialog.high.value(), dialog.low.value())
        finally:
            dialog.deleteLater()

    def edit_dialog(self):
        if self.selected is None:
            return
        grid = self.document.current.grid
        region = region_by_id(self.document.current.integrals, self.selected)
        dialog = BoundsDialog(grid, grid.ppm(region.left), grid.ppm(region.right),
                              lambda a, b: self._preview(self.selected, a, b), self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._apply(lambda: self.document.edit_integral(
                    self.selected, dialog.high.value(), dialog.low.value()))
        finally:
            dialog.deleteLater()

    def assign_dialog(self):
        if self.selected is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Assign reference proton count")
        form = QFormLayout(dialog)
        count = QDoubleSpinBox()
        count.setDecimals(6)
        count.setRange(.000001, 1e6)
        count.setValue(1)
        form.addRow("Protons (H)", count)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.assign(count.value())
        finally:
            dialog.deleteLater()
