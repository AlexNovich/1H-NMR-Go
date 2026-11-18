"""Processed spectrum viewer with bounded loading and reversible navigation."""
import os

os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bruker_nmr.demo import make_demo
from bruker_nmr.domain.display import display_trace
from bruker_nmr.domain.document import Document, DocumentData, LoadGate
from bruker_nmr.domain.style import GRID_COLOUR, TRACE_PX_TO_PT
from bruker_nmr.export.snapshot import ViewSnapshot
from bruker_nmr.io.errors import InputError
from bruker_nmr.processing.fid import MAX_SI, Recipe, automatic_si, si_choices
from bruker_nmr.ui.bands import AnnotationBand
from bruker_nmr.ui.baseline import BaselineDialog
from bruker_nmr.ui.chooser import ExperimentChooser
from bruker_nmr.ui.handles import menu_action
from bruker_nmr.ui.integrals import IntegralEditor
from bruker_nmr.ui.loading import OpenJob, RawJob, SaveJob
from bruker_nmr.ui.navigation import NavigationBox
from bruker_nmr.ui.pdf import PdfExportDialog
from bruker_nmr.ui.peaks import PeakEditor
from bruker_nmr.ui.phase import PhaseDialog
from bruker_nmr.ui.workbench import Workbench


class ViewerWindow(QMainWindow):
    def __init__(self, identity: dict, *, pool=None):
        super().__init__()
        self.identity = identity
        self.pool = pool if pool is not None else QThreadPool(self)
        if pool is None:
            self.pool.setMaxThreadCount(2)
        self.gate = LoadGate()
        self._jobs = set()
        self.loading = False
        self.saving = False
        self._rendering = False
        self._phase_preview = None
        self.phase_dialog = None
        self.baseline_dialog = None
        self._baseline_preview = None
        self._trace_cache = None
        self._drawn_width = None
        self._pointer_pressed = False
        self._last_pointer = None
        self.spectrum = make_demo()
        self.document = Document(DocumentData.from_demo(self.spectrum))
        self.setWindowTitle("NMR-go — spectrum viewer")
        available = self.screen().availableGeometry()
        width = max(320, available.width() - 40)
        height = max(300, available.height() - 60)
        self.setMinimumSize(min(640, width), min(480, height))
        self.resize(min(1050, width), min(720, height))
        content = QWidget()
        self.main_scroll = QScrollArea()
        self.main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setWidget(content)
        self.setCentralWidget(self.main_scroll)
        layout = QVBoxLayout(content)
        layout.setSpacing(0)

        self.summary = QLabel()
        self.summary.setWordWrap(False)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        layout.addWidget(self.summary)
        self.view_box = NavigationBox()
        self.plot = pg.PlotWidget(background="w", viewBox=self.view_box)
        self.plot.setMinimumSize(80,100)
        self.plot.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)
        self.plot.setLabel("bottom", "Chemical shift", units="ppm")
        self.plot.setLabel("left", "Amplitude", units="a.u.")
        self.plot.getPlotItem().hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.getAxis('bottom').setPen('#374151')
        self.plot.getAxis('bottom').setTextPen('#374151')
        self.plot.getAxis('left').enableAutoSIPrefix(False)
        self.plot.getAxis('left').setPen('#374151')
        self.plot.getAxis('left').setTextPen('#374151')
        self.plot_grid = pg.PlotCurveItem(
            pen=pg.mkPen(GRID_COLOUR, width=0.5, style=Qt.PenStyle.DashLine),
            connect='pairs',
        )
        self.view_box.addItem(self.plot_grid, ignoreBounds=True)
        self.plot_grid.setZValue(-20)
        self.plot_grid.hide()
        self.grid_action = self._action(None, 'Show grid', self.toggle_grid, checkable=True)
        self.curve = self.plot.plot([], [], antialias=True)
        self.peak_band = AnnotationBand(self, 'peaks')
        layout.addWidget(self.peak_band)
        layout.addWidget(self.plot,1)
        self.integral_band = AnnotationBand(self, 'integrals')
        layout.addWidget(self.integral_band)
        self.export_action = QAction('Export current view PDF…',self)
        self.export_action.setShortcut(QKeySequence('Ctrl+E'))
        self.export_action.triggered.connect(self.export_pdf)
        self.addAction(self.export_action)
        self.save_action = self._action(None, "Save annotations", self.save_annotations, "Ctrl+S")
        self.addAction(self.save_action)
        self.pdf_dialog = None
        self._make_actions()
        self.integrals = IntegralEditor(self)
        self.peaks = PeakEditor(self)
        self.workbench = Workbench(self)
        self.editing_actions['Reference'].setText('Set selected peak to 0 ppm')
        self.editing_actions['Reference'].triggered.connect(self.peaks.zero_selected)
        self.view_box.peak_clicked.connect(self.peaks.add_at)
        self.view_box.peaks_boxed.connect(self.pick_rectangle)
        self.plot.scene().sigMouseClicked.connect(self.context_clicked)
        self.view_box.integral_created.connect(self._create_integral_from_drag)
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(16)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.timeout.connect(self._draw_curve)
        self.view_box.sigRangeChanged.connect(self._schedule_draw)
        self.view_box.sigResized.connect(self._schedule_draw)
        self.view_box.gesture_finished.connect(self._commit_gesture)
        self.cursor_readout = QLabel('Cursor: —')
        self.cursor_readout.setToolTip('Calibrated ppm and committed full-resolution sample amplitude; no interpolation.')
        self.statusBar().addPermanentWidget(self.cursor_readout)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(30)
        self._cursor_timer.timeout.connect(self._update_cursor)
        self.plot.scene().sigMouseMoved.connect(self._pointer_moved)
        self.plot.viewport().installEventFilter(self)
        self._set_loading(False)
        self._render_document()

    def _action(self, toolbar, text, callback, shortcut=None, checkable=False):
        action = QAction(text, self)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        if toolbar is not None:
            toolbar.addAction(action)
        return action

    def _make_actions(self):
        files = navigation = None
        self.open_zip_action = self._action(files, "Open ZIP…", self.open_zip, "Ctrl+O")
        self.open_folder_action = self._action(files, "Open Folder…", self.open_folder, "Ctrl+Shift+O")
        self.demo_action = self._action(files, "Demo", self.show_demo)
        self.cancel_action = self._action(files, "Cancel loading", self.cancel_loading, "Escape")
        modes = QActionGroup(self)
        self.zoom_action = self._action(navigation, "Rectangle Zoom", self._set_zoom, "Z", True)
        self.pan_action = self._action(navigation, "Pan", self._set_pan, "P", True)
        modes.addAction(self.zoom_action)
        modes.addAction(self.pan_action)
        self.zoom_action.setChecked(True)
        self.full_action = self._action(navigation, "Full View", self.full_view, "F")
        self.previous_action = self._action(navigation, "Previous view", self.previous_view)
        self.previous_action.setShortcuts([QKeySequence("Backspace"), QKeySequence("Alt+Left")])
        self.undo_action = self._action(navigation, "Undo", self.undo, "Ctrl+Z")
        self.redo_action = self._action(navigation, "Redo", self.redo, "Ctrl+Y")
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Y"),QKeySequence("Ctrl+Shift+Z")])
        self.reset_action = self._action(navigation, "Reset", self.reset)
        self._navigation_actions = [self.zoom_action, self.pan_action, self.full_action,
                                    self.undo_action, self.redo_action, self.reset_action, self.previous_action]
        self.editing_actions = {}
        for text in ["Integrate", "Pick Peaks", "Phase", "Baseline", "Reference", "Process from FID"]:
            action = QAction(text, self)
            action.setEnabled(False)
            action.setToolTip("Not available in this viewer build.")
            self.editing_actions[text] = action
        self.integrate_action = self.editing_actions["Integrate"]
        self.integrate_action.setCheckable(True)
        self.integrate_action.setShortcut(QKeySequence("I"))
        self.integrate_action.setToolTip("Drag to create integral ranges; drag lower brackets to edit.")
        self.integrate_action.triggered.connect(self._set_integrate)
        modes.addAction(self.integrate_action)
        self._navigation_actions.append(self.integrate_action)
        self.editing_actions['Process from FID'].triggered.connect(self.process_from_fid)
        self.editing_actions['Phase'].triggered.connect(self.open_phase)
        self.editing_actions['Baseline'].triggered.connect(self.open_baseline)
        self.pick_action = self.editing_actions['Pick Peaks']
        self.pick_action.setCheckable(True)
        self.pick_action.setShortcut(QKeySequence('K'))
        self.pick_action.triggered.connect(self._set_pick)
        modes.addAction(self.pick_action)
        self._navigation_actions.append(self.pick_action)

    def open_phase(self):
        if self.loading or not self.document.current.data.can_phase:
            return
        self.view_box.finish_pending()
        self._close_baseline_dialog()
        if self.phase_dialog is None or not self.phase_dialog.isVisible():
            self.phase_dialog = PhaseDialog(self)
        self.workbench.show_process(self.phase_dialog)

    def cancel_processing(self):
        active=any(p is not None and p.isVisible() for p in (self.phase_dialog,self.baseline_dialog))
        self._close_phase_dialog()
        self._close_baseline_dialog()
        if active:
            self.statusBar().showMessage('Processing preview canceled before the edit.')

    def _close_phase_dialog(self):
        self._phase_preview = None
        if self.phase_dialog is not None and self.phase_dialog.isVisible():
            self.phase_dialog.reject()

    def open_baseline(self):
        if self.loading:
            return
        self.view_box.finish_pending()
        self._close_phase_dialog()
        if self.baseline_dialog is None or not self.baseline_dialog.isVisible():
            self.baseline_dialog = BaselineDialog(self)
        self.workbench.show_process(self.baseline_dialog)

    def _close_baseline_dialog(self):
        self._baseline_preview = None
        if self.baseline_dialog is not None and self.baseline_dialog.isVisible():
            self.baseline_dialog.reject()

    def _set_zoom(self):
        if self.baseline_dialog is not None and self.baseline_dialog.isVisible():
            self.baseline_dialog.draw_anchor.setChecked(False)
        self.peaks.clear_ghost()
        self.view_box.finish_pending()
        self.view_box.anchoring = False
        self.view_box.integrating = False
        self.view_box.picking = False
        self.peaks.sync_handle()
        self.view_box.setMouseMode(self.view_box.RectMode)
        self.integrals._set_handle_modes()
        self.statusBar().showMessage("Rectangle Zoom: drag a rectangle over the spectrum.")

    def _set_pan(self):
        if self.baseline_dialog is not None and self.baseline_dialog.isVisible():
            self.baseline_dialog.draw_anchor.setChecked(False)
        self.peaks.clear_ghost()
        self.view_box.finish_pending()
        self.view_box.anchoring = False
        self.view_box.integrating = False
        self.view_box.picking = False
        self.peaks.sync_handle()
        self.view_box.setMouseMode(self.view_box.PanMode)
        self.integrals._set_handle_modes()
        self.statusBar().showMessage("Pan: drag to move the view. Wheel changes vertical scale.")

    def _set_integrate(self):
        if self.baseline_dialog is not None and self.baseline_dialog.isVisible():
            self.baseline_dialog.draw_anchor.setChecked(False)
        self.peaks.clear_ghost()
        self.view_box.finish_pending()
        self.view_box.anchoring = False
        self.view_box.picking = False
        self.peaks.sync_handle()
        self.view_box.integrating = True
        self.integrals._set_handle_modes()
        self.statusBar().showMessage("Integrate: drag a range. Drag lower bracket handles to edit boundaries.")

    def _set_pick(self):
        if self.baseline_dialog is not None and self.baseline_dialog.isVisible():
            self.baseline_dialog.draw_anchor.setChecked(False)
        self.peaks.clear_ghost()
        self.view_box.finish_pending()
        self.view_box.anchoring = False
        self.view_box.integrating = False
        self.view_box.picking = True
        self.integrals._set_handle_modes()
        self.peaks.sync_handle()
        self.statusBar().showMessage('Pick Peaks: click near an extremum or drag a rectangle around maxima. Ctrl+drag pans.')

    def appearance_dialog(self):
        if hasattr(self.workbench, 'appearance'):
            self.workbench.show_appearance()
            self._refresh_appearance()
            return
        dialog = QWidget(self)
        dialog.setWindowTitle('Spectrum appearance')
        form = QFormLayout(dialog)
        width = QDoubleSpinBox()
        width.setRange(.2 * TRACE_PX_TO_PT, 6 * TRACE_PX_TO_PT)
        width.setDecimals(2)
        width.setSingleStep(.1)
        width.setValue(self.document.current.line_width * TRACE_PX_TO_PT)
        width.setSuffix(" pt")
        width.setKeyboardTracking(False)
        integrals = QCheckBox('Show integral brackets and values')
        integrals.setChecked(self.document.current.show_integrals)
        markers = QCheckBox('Show peak markers')
        markers.setChecked(self.document.current.peaks.markers_visible)
        labels = QComboBox()
        labels.addItems(['Auto','All','Hidden'])
        labels.setCurrentText(self.document.current.peaks.label_mode.title())
        colour = QPushButton(self.document.current.trace_color)
        colour.setObjectName('trace_colour')
        def show_colour(value):
            sample = QPixmap(16, 16)
            sample.fill(QColor(value))
            colour.setIcon(QIcon(sample))
            colour.setText(value)

        def choose_colour():
            picker = QColorDialog(QColor(colour.text()), dialog)
            picker.setWindowTitle('Trace colour')
            picker.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
            for component in picker.findChildren(QAbstractSpinBox):
                component.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            if picker.exec() == QDialog.DialogCode.Accepted:
                apply(trace_color=picker.selectedColor().name())
            picker.deleteLater()

        show_colour(colour.text())
        colour.clicked.connect(choose_colour)
        form.addRow('Line width', width)
        form.addRow('Line colour', colour)
        form.addRow(integrals)
        form.addRow(markers)
        form.addRow('Peak labels',labels)
        def refresh():
            state = self.document.current
            with (QSignalBlocker(width), QSignalBlocker(integrals),
                  QSignalBlocker(markers), QSignalBlocker(labels)):
                width.setValue(state.line_width * TRACE_PX_TO_PT)
                integrals.setChecked(state.show_integrals)
                markers.setChecked(state.peaks.markers_visible)
                labels.setCurrentText(state.peaks.label_mode.title())
            show_colour(state.trace_color)
            dialog.setEnabled(not self.loading)

        def apply(**change):
            if self.loading:
                return
            self.view_box.finish_pending()
            self.cancel_processing()
            if self.document.appearance(**change):
                self._render_document()

        width.valueChanged.connect(lambda value: apply(line_width=value / TRACE_PX_TO_PT))
        integrals.toggled.connect(lambda checked: apply(show_integrals=checked))
        markers.toggled.connect(lambda checked: apply(markers=checked))
        labels.currentTextChanged.connect(lambda text: apply(label_mode=text.lower()))
        self._refresh_appearance = refresh
        self.workbench.show_appearance(dialog)
        refresh()

    def context_clicked(self, event):
        if self.loading or event.button() != Qt.MouseButton.RightButton:
            return
        if not self.view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        position = self.view_box.mapSceneToView(event.scenePos()).x()
        point = self.plot.viewport().mapToGlobal(self.plot.mapFromScene(event.scenePos()))
        peak_id = self.peaks.hit_test(event.scenePos())
        if peak_id is not None:
            self.peaks.context_menu(point, peak_id)
            return
        rows = self.integral_band.rows
        hits = [r for r in rows if r.low <= position <= r.high]
        if len(hits) == 1:
            self.integrals.context_menu(point,hits[0].id)
            return
        menu = QMenu(self)
        for row in hits:
            menu_action(menu, f'Integral {rows.index(row)+1}…', self,
                           lambda id=row.id:self.integrals.context_menu(point,id))
        menu_action(menu, 'New integral…',self,self.integrals.new_dialog)
        menu_action(menu, 'Integral table…',self,self.integrals.show_table)
        menu_action(menu, 'Peak table…',self,self.peaks.show_table)
        menu_action(menu, 'Appearance…',self,self.appearance_dialog)
        menu.popup(point)
        self._context_menu = menu

    def eventFilter(self, watched, event):
        if watched is self.plot.viewport():
            if event.type() in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
                self.peaks.clear_ghost()
                self._last_pointer = None
                self.cursor_readout.setText('Cursor: —')
            if event.type() == QEvent.Type.MouseButtonPress:
                self._pointer_pressed = True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._pointer_pressed = False
        return super().eventFilter(watched,event)

    def _pointer_moved(self, point):
        self._last_pointer = point
        if not self._cursor_timer.isActive():
            self._cursor_timer.start()

    def _update_cursor(self):
        point = self._last_pointer
        if (point is None or self.loading or self._pointer_pressed
                or not self.view_box.sceneBoundingRect().contains(point)):
            self.peaks.clear_ghost()
            return
        state = self.document.current
        position = self.view_box.mapSceneToView(point).x()
        native = position-state.calibration
        grid = state.data.integration
        if not grid.minimum <= native <= grid.offset:
            self.peaks.clear_ghost()
            self.cursor_readout.setText('Cursor: outside measured range')
            return
        value = state.data.real[self.peaks.sample(native)]
        self.cursor_readout.setText(f'δ {position:.4f} ppm | I {value:.5g} a.u.')
        if self.view_box.picking and not QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            self.peaks.show_ghost(position)
        else:
            self.peaks.clear_ghost()

    def _create_integral_from_drag(self, start, end):
        # Drag-event positions are in ViewBox coordinates; convert into data coordinates.
        a = self.view_box.mapToView(pg.Point(start, 0)).x()
        b = self.view_box.mapToView(pg.Point(end, 0)).x()
        self.integrals.create_range(a, b)

    def _set_loading(self, loading):
        self.peaks.clear_ghost()
        self.loading = loading
        self.editing_actions["Baseline"].setEnabled(not loading)
        self.editing_actions["Phase"].setEnabled(not loading and self.document.current.data.can_phase)
        self.view_box.active = not loading
        self.cancel_action.setEnabled(loading and not self.saving)
        self.workbench.set_file_busy(self.saving)
        self.save_action.setEnabled(not loading and self.document.source_location is not None)
        self.export_button.setEnabled(not loading)
        self.export_action.setEnabled(not loading)
        self.integrals_button.setEnabled(not loading)
        self.peaks_button.setEnabled(not loading)
        self.peaks.setEnabled(not loading)
        if self.peaks.handle is not None:
            self.peaks.handle.setMovable(not loading)
            self.peaks.handle.setVisible(not loading and self.view_box.picking)
        self.workbench.appearance_action.setEnabled(not loading)
        if hasattr(self, "_refresh_appearance"):
            self._refresh_appearance()
        self.editing_actions['Reference'].setEnabled(not loading and bool(self.document.current.peaks.records))
        self.editing_actions['Process from FID'].setEnabled(not loading and self.document.current.data.raw_available)
        for action in self._navigation_actions:
            action.setEnabled(not loading)
        if not loading:
            self._update_history_actions()
        self.integrals.refresh_table()
        self.integrals._set_handle_modes()

    def pick_rectangle(self, x0, y0, x1, y1):
        if self.loading:
            return
        self.peaks.clear_ghost()
        self.cancel_processing()
        try:
            count = self.document.add_peaks_in_rectangle(x0, y0, x1, y1)
        except ValueError as error:
            self.statusBar().showMessage(str(error))
            return
        if count:
            self._render_document()
        self.statusBar().showMessage(f'Added {count} peak(s).' if count else 'No new maxima in the rectangle.')

    def _update_history_actions(self):
        self.undo_action.setEnabled(not self.loading and self.document.can_undo)
        self.redo_action.setEnabled(not self.loading and self.document.can_redo)
        self.previous_action.setEnabled(not self.loading and bool(self.document.view_count))
        for action,prefix,label in ((self.undo_action,'Undo',self.document.undo_label),
                                    (self.redo_action,'Redo',self.document.redo_label)):
            action.setText(f'{prefix} {label}' if label else prefix)
            action.setToolTip(f'{prefix} edit: {label}' if label else f'No edit to {prefix.lower()}')

    def _schedule_draw(self, *args):
        self.peaks.clear_ghost()
        self._last_pointer = None
        if hasattr(self, '_cursor_timer'):
            self._cursor_timer.stop()
            self.cursor_readout.setText('Cursor: —')
        if not self._rendering and not self._redraw_timer.isActive():
            self._redraw_timer.start()

    def toggle_grid(self, visible):
        self.plot_grid.setVisible(visible)
        self._update_grid()

    def _update_grid(self):
        if not self.grid_action.isChecked():
            return
        (left, right), (bottom, top) = self.view_box.viewRange()
        xs, ys = [], []
        for name, low, high, size in (
            ('bottom', left, right, self.view_box.width()),
            ('left', bottom, top, self.view_box.height()),
        ):
            if size <= 0 or not low < high:
                continue
            ticks = self.plot.getAxis(name).tickValues(low, high, size)
            for value in ticks[0][1] if ticks else ():
                if low < value < high:
                    if name == 'bottom':
                        xs.extend((value, value))
                        ys.extend((bottom, top))
                    else:
                        xs.extend((left, right))
                        ys.extend((value, value))
        self.plot_grid.setData(xs, ys)

    def _draw_curve(self):
        data = self.document.current.data
        x, _ = self.view_box.viewRange()
        width = max(1, int(self.view_box.width() * self.devicePixelRatioF()))
        real = data.real
        if self._phase_preview is not None:
            real = self._phase_preview
        elif self._baseline_preview is not None:
            real = self._baseline_preview
        calibration = self.document.current.calibration
        span = x[1]-x[0]
        low, high = x[0]-calibration, x[1]-calibration
        cache = self._trace_cache
        reuse = (cache is not None and cache['ppm'] is data.ppm and cache['real'] is real
                 and cache['width'] == width and cache['calibration'] == calibration
                 and abs(cache['span']-span) <= span*1e-8
                 and cache['low'] <= low and high <= cache['high'])
        if not reuse:
            first, last = low-span*.1, high+span*.1
            trace = display_trace(data.ppm, real, first, last, width)
            self.curve.setData(trace.x+calibration, trace.y)
            self.display_point_count = len(trace.x)
            self._trace_cache = dict(ppm=data.ppm, real=real, width=width, calibration=calibration,
                                     span=span, low=first, high=last, trace=trace)
        else:
            trace = cache['trace']
        self._update_grid()
        appearance = (self.document.current.line_width,self.document.current.trace_color)
        if self._drawn_width != appearance:
            self.curve.setPen(pg.mkPen(appearance[1],width=appearance[0]))
            self._drawn_width = appearance
        # Cache only a bounded, entirely visible Y extent. A clipped tall solvent
        # must never create a huge offscreen raster cache when zooming its wings.
        y = self.view_box.viewRange()[1]
        bounded = len(trace.y) and y[0] <= min(trace.y) and max(trace.y) <= y[1]
        mode = (QGraphicsItem.CacheMode.DeviceCoordinateCache if bounded
                else QGraphicsItem.CacheMode.NoCache)
        self.curve.curve.setCacheMode(mode)
        if hasattr(self, "integrals"):
            self.integrals.update_positions()
        if hasattr(self,"peaks"):
            self.peaks.update_plot(real)

    def _render_document(self, *, select_first=True):
        self.workbench.sync_document()
        if hasattr(self, "_refresh_appearance"):
            self._refresh_appearance()
        self.save_action.setEnabled(not self.loading and self.document.source_location is not None)
        self.peaks.clear_ghost()
        if hasattr(self,'cursor_readout'):
            self.cursor_readout.setText('Cursor: —')
        if (self.baseline_dialog is not None and self.baseline_dialog.isVisible()
                and not self.baseline_dialog.valid()):
            self._close_baseline_dialog()
        if (self.phase_dialog is not None and self.phase_dialog.isVisible()
                and not self.phase_dialog.valid()):
            self._close_phase_dialog()
        self._rendering = True
        try:
            self.view_box.cancel_pending()
            self.view_box.disableAutoRange()
            view = self.document.current.view
            self.view_box.setRange(xRange=(view.x_min, view.x_max),
                                   yRange=(view.y_min, view.y_max), padding=0)
            self._draw_curve()
            data = self.document.current.data
            demo = data.opening_mode == "demo"
            if demo:
                summary = "Synthetic demonstration — no experimental data loaded"
            elif data.opening_mode == 'raw':
                recipe = data.operator.recipe
                summary = (f"Independent FID — {'phased' if data.phase_steps else 'unphased'} | SI {recipe.si} | LB {recipe.lb:g} Hz | "
                           f"Baseline {data.baseline.fit.mode if data.baseline else 'Off'} | "
                           + ("User-referenced axis\n" if self.document.current.referenced else
                              "Acquisition-referenced axis; chemical zero unverified\n") +
                           "Raw arbitrary units (NumPy 1/SI); operator integrals/scale are not transferred.")
            else:
                summary = (f"Operator data — 1H / 1D | Experiment {data.experiment_number or '—'} / "
                           f"Processing {data.processing_number or '—'} | {len(data.real)} points | "
                           f"{data.operator.provenance.processed['SF']:.3f} MHz | "
                           f"Imaginary data: {'present' if data.can_phase else 'missing'}")
                summary += (f"\nIntegrals: {len(self.document.current.integrals.regions)} | "
                            f"Peaks: {len(self.document.current.peaks.records)} | Labels: {self.document.current.peaks.label_mode}")
            if data.baseline is not None:
                summary += f"\nBaseline {data.baseline.fit.mode}; explicit anchors; review corrected areas."
            if self.document.current.referenced:
                summary += f"\nUser axis offset {self.document.current.calibration:+.6f} ppm; source arrays unchanged."
            self.editing_actions['Baseline'].setEnabled(not self.loading)
            self.editing_actions['Baseline'].setToolTip('Explicit constant/linear quiet anchors; preview, Apply/Cancel/Off, Undo.')
            self.export_button.setEnabled(not self.loading)
            self.export_action.setEnabled(not self.loading)
            self.export_button.setToolTip("Preview and export the captured current view as one landscape A4 PDF (Ctrl+E).")
            phase = ("Relative manual/automatic phase; preview, Apply/Cancel, Undo."
                     if data.can_phase else "Imaginary data missing. Phase requires 1i or Process from FID.")
            self.editing_actions["Phase"].setToolTip(phase)
            self.editing_actions["Phase"].setEnabled(not self.loading and data.can_phase)
            if data.phase_steps:
                summary += (f"\nRelative phase corrections: {len(data.phase_steps)}; "
                            "areas recomputed. Check the reference scale.")
            self.editing_actions["Process from FID"].setToolTip(
                "Independent FID processing / options. Undo restores the prior mode and integrals."
                if data.raw_available else "No verified FID input available.")
            self._update_history_actions()
            self.editing_actions['Process from FID'].setEnabled(not self.loading and data.raw_available)
            self.integrals.sync_plot(select_first=select_first)
            self.peaks.sync_plot(select_first=select_first)
            self.editing_actions['Reference'].setEnabled(not self.loading and bool(self.document.current.peaks.records))
            self.summary.setToolTip(summary)
            kind = {'demo':'Synthetic demonstration','operator':'Operator 1H','raw':'Independent FID — '+('phased' if data.phase_steps else 'unphased')}[data.opening_mode]
            compact = f'{kind} | {len(data.real)} points | {len(self.document.current.integrals.regions)} integrals | {len(self.document.current.peaks.records)} peaks'
            if data.opening_mode != 'demo' and not data.can_phase:
                compact += ' | Imaginary data: missing'
            if self.document.current.integrals.regions and self.document.current.integrals.normalization.kind in ('absent','unverified'):
                compact += ' | Scale unverified'
            self.summary.setText(compact)
            self.statusBar().showMessage(self.document.current.notice or "Navigate — zoom and calibration do not change areas.")
        finally:
            self._rendering = False

    def _commit_gesture(self):
        if not self.loading:
            try:
                self.document.navigate(self.view_box.snapshot())
                if self.phase_dialog is not None and self.phase_dialog.isVisible():
                    self.phase_dialog.autoscale = False
            except ValueError:
                self._render_document()
            self._update_history_actions()

    def commit_view(self, view):
        self.view_box.finish_pending()
        self.document.apply_view(view)
        if self.phase_dialog is not None and self.phase_dialog.isVisible():
            self.phase_dialog.autoscale = False
        self._render_document()

    def previous_view(self):
        self.view_box.finish_pending()
        self.document.previous_view()
        self._render_document()

    def full_view(self):
        self.commit_view(self.document.current.full_view())

    def undo(self):
        self.view_box.finish_pending()
        self.cancel_processing()
        self.document.undo()
        self._render_document()

    def redo(self):
        self.view_box.finish_pending()
        self.cancel_processing()
        self.document.redo()
        self._render_document()

    def reset(self):
        self.view_box.finish_pending()
        self.cancel_processing()
        self.document.reset()
        self._render_document()

    def show_demo(self):
        if self.saving or not self.confirm_discard():
            return
        self.cancel_loading()
        self.document = Document(DocumentData.from_demo(self.spectrum))
        self._render_document()

    def open_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Bruker ZIP", "", "ZIP archives (*.zip)")
        if path:
            self.begin_open(path)

    def open_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Open Bruker folder")
        if path:
            self.begin_open(path)

    def begin_open(self, path):
        if self.saving or not self.confirm_discard():
            return
        self.view_box.finish_pending()
        self.cancel_loading()
        ticket = self.gate.begin(self.document)
        self._set_loading(True)
        self.statusBar().showMessage("Checking source…")
        self._start_job(OpenJob(ticket, path))

    def _start_job(self, job):
        job.view_revision = self.document.view_revision
        self._jobs.add(job)
        job.signals.finished.connect(self._on_job)
        self.pool.start(job)

    def choose_candidate(self, choices):
        if len(choices) == 1 and (choices[0].processed_available or choices[0].raw_available):
            return choices[0]
        dialog = ExperimentChooser(choices, self, allow_raw=True)
        try:
            return dialog.selection() if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    @Slot(object, object, object, object)
    def _on_job(self, job, ticket, result, error):
        self._jobs.discard(job)
        if (not self.gate.accepts(ticket, self.document)
                or job.view_revision != self.document.view_revision):
            # If only document revision changed, end this request without replacing anything.
            if ticket.generation == self.gate.generation:
                self.cancel_loading()
            return
        if error:
            self._set_loading(False)
            self.show_input_error(error)
            return
        if result is None:
            self.cancel_loading()
            return
        if job.choice is None:
            choice = self.choose_candidate(result)
            if choice is None:
                self.cancel_loading()
                return
            if self.gate.accepts(ticket, self.document):
                if choice.processed_available:
                    self.statusBar().showMessage("Reading processed spectrum…")
                    self._start_job(OpenJob(ticket, job.path, choice))
                else:
                    self._pending_raw_choice = choice
                    try:
                        recipe = self.processing_options()
                    finally:
                        self._pending_raw_choice = None
                    if recipe is None:
                        self.cancel_loading()
                    else:
                        self._start_job(RawJob(ticket, job.path, choice, recipe))
        else:
            try:
                if isinstance(job, RawJob) and job.transition:
                    self.document.apply_raw(result)
                    document = self.document
                else:
                    document = Document(result)
            except ValueError:
                self._set_loading(False)
                self.show_input_error("unsupported_display_range")
                return
            self.document = document
            self.gate.cancel()
            self._set_loading(False)
            self._render_document()
            if isinstance(job, RawJob):
                self.statusBar().showMessage((self.document.current.notice or
                    'Initial independent FFT retained as Reset snapshot; baseline Off.') +
                    ' Choose Phase → Auto Phase to preview a proposal.')

    def processing_options(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Independent FID processing')
        form = QFormLayout(dialog)
        lb, size = QDoubleSpinBox(), QComboBox()
        size.setObjectName('fid_si')
        lb.setRange(0, 100)
        lb.setDecimals(3)
        lb.setValue(.3)
        data = self.document.current.data
        choice = getattr(self, '_pending_raw_choice', None)
        if choice is None and self.document.source_location is not None:
            choice = self.document.source_location[1]
        points = choice.raw_points if choice is not None else None
        current = None
        if data.opening_mode == 'raw' and getattr(self, '_pending_raw_choice', None) is None:
            points = len(data.operator.fid)
            current = data.operator.recipe.si
            lb.setValue(data.operator.recipe.lb)
        options = list(si_choices(points))
        if points is None or options:
            label = f'Automatic ({automatic_si(points)} points)' if options else 'Automatic'
            size.addItem(label, 0)
        if options and current is not None and points <= current <= MAX_SI and not current & (current - 1):
            options = sorted(set(options + [current]))
        for value in options:
            label = f'{value} ({value // 1024}k)' if value >= 1024 else str(value)
            if current == value and value not in si_choices(points):
                label += ' — current'
            size.addItem(label, value)
        if current is not None:
            index = size.findData(current)
            if index >= 0:
                size.setCurrentIndex(index)
        form.addRow('EM line broadening (Hz)', lb)
        form.addRow('SI (points)', size)
        size.setToolTip('Valid FFT sizes for this FID; no acquired points are truncated.')
        note = QLabel(('This FID exceeds the supported FFT size limit. ' if points is not None and not options else '') +
                      'Automatic normally uses at least twice the complex FID length, capped at 4194304 points. '
                      'Manual choices cover the minimum valid power of two and up to four times that size. '
                      'Independent unphased FFT, known delay once; baseline Off. '
                      'Reprocessing starts with baseline Off and fresh integrals. Operator phases and integral scale are not reused. Use Phase to propose automatic correction or preview manual controls.')
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(size.count() > 0)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        try:
            return Recipe(size.currentData(), lb.value()) if dialog.exec() == QDialog.DialogCode.Accepted and size.count() else None
        finally:
            dialog.deleteLater()

    def process_from_fid(self):
        if self.loading or not self.document.current.data.raw_available:
            return
        recipe = self.processing_options()
        if recipe is None:
            return
        data = self.document.current.data
        path, choice = self.document.source_location
        cached = ((data.operator.fid, data.operator.acquisition) if data.opening_mode == 'raw' else None)
        self.view_box.finish_pending()
        self.cancel_loading()
        ticket = self.gate.begin(self.document)
        self._set_loading(True)
        self.statusBar().showMessage('Processing FID… Cancel retains the current document.')
        self._start_job(RawJob(ticket, path, choice, recipe, transition=True, cached=cached, header=self.document.current.data.print_header,
                               source_parameters=data.source_parameters))

    def cancel_loading(self):
        if self.saving:
            return
        self._close_phase_dialog()
        self._close_baseline_dialog()
        self.gate.cancel()
        for job in self._jobs:
            job.cancelled.set()
        self._set_loading(False)
        self.statusBar().showMessage("Loading canceled. Current spectrum retained.")

    def show_input_error(self, code):
        self.statusBar().showMessage("Open failed. Current spectrum retained.")
        QMessageBox.warning(self, "Could not open spectrum", str(InputError(code)))

    def save_annotations(self):
        if self.loading or self._pointer_pressed:
            return
        document = self.document
        if not document.can_save_markup:
            QMessageBox.information(self, 'Save annotations',
                'Save requires the original processed spectrum. Open the processed pdata or '
                'undo independent FID, phase or baseline processing before saving annotations.')
            return
        if ((self.phase_dialog is not None and self.phase_dialog.isVisible())
                or (self.baseline_dialog is not None and self.baseline_dialog.isVisible())):
            self.statusBar().showMessage('Apply or cancel the processing preview before saving.')
            return
        path, choice = document.source_location
        destination = None
        if path.is_file():
            destination = QFileDialog.getExistingDirectory(
                self, 'Choose a parent folder for the complete extracted Bruker dataset')
            if not destination:
                return
        self.view_box.finish_pending()
        self.saving = True
        self._set_loading(True)
        job = SaveJob(path, choice.locator, document.current, document.markup_revision, destination)
        self._jobs.add(job)
        self.statusBar().showMessage('Saving annotations and recovery copy…')
        job.signals.finished.connect(self._on_save)
        self.pool.start(job)

    @Slot(object, object, object, object)
    def _on_save(self, job, _ticket, result, error):
        self._jobs.discard(job)
        self.saving = False
        if error is None:
            path, revision, backup = result
            self.document.mark_saved(path, revision)
        self._set_loading(False)
        if error is not None:
            QMessageBox.warning(self, 'Could not save annotations', str(InputError(error)))
            self.statusBar().showMessage('Save failed. Current edits are still open.')
        else:
            self.statusBar().showMessage(f'Annotations saved. Recovery copy: {backup}')
            if job.destination is not None:
                box = QMessageBox(self)
                box.setWindowTitle('Dataset saved')
                box.setTextFormat(Qt.TextFormat.PlainText)
                box.setText(f'Working from the extracted folder:\n{path}')
                box.exec()

    def export_pdf(self):
        if self.loading or self._pointer_pressed:
            self.statusBar().showMessage('Finish loading or the current gesture before exporting.')
            return False
        if ((self.phase_dialog is not None and self.phase_dialog.isVisible()) or
                (self.baseline_dialog is not None and self.baseline_dialog.isVisible())):
            self.statusBar().showMessage('Apply or cancel the processing preview before exporting.')
            return False
        self.view_box.finish_pending()
        snapshot = ViewSnapshot(self.document.current, selected_peak=self.peaks.selected,
                                show_grid=self.grid_action.isChecked())
        dialog = PdfExportDialog(self,snapshot)
        self.pdf_dialog = dialog
        try:
            dialog.exec()
            return dialog.saved
        finally:
            self.pdf_dialog = None
            dialog.deleteLater()

    def closeEvent(self, event):
        if self.saving or not self.confirm_discard():
            event.ignore()
            return
        self.view_box.cancel_pending()
        self.cancel_loading()
        self.integrals.hide()
        self.peaks.hide()
        super().closeEvent(event)

    def confirm_discard(self):
        if not self.document.integration_modified:
            return True
        dialog = QMessageBox(QMessageBox.Icon.Warning, 'Discard spectrum edits?',
            'Some spectrum edits are unsaved. Use File → Save annotations for a processed spectrum. '
            'Phase, baseline and trace appearance edits stay in memory. Export PDF captures the view.',
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Discard, self)
        export = dialog.addButton('Export PDF…', QMessageBox.ButtonRole.ActionRole)
        export.setEnabled(not self.loading)
        export.setToolTip('Preview/save the current spectrum, then return here without discarding edits.')
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        try:
            result = dialog.exec()
            wants_export = dialog.clickedButton() is export
        finally:
            dialog.deleteLater()
        if wants_export:
            self.export_pdf()
            return False
        return result == QMessageBox.StandardButton.Discard
