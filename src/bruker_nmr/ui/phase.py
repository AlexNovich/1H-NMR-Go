"""Transactional phase preview with cancellable off-thread blind proposals."""
import threading

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRunnable, QSignalBlocker, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from bruker_nmr.domain.document import LoadGate
from bruker_nmr.processing.phase import Phase, PhaseCancelled, auto_phase
from bruker_nmr.ui.handles import allow_control_pan
from bruker_nmr.ui.integrals import fit_dialog
from bruker_nmr.ui.loading import JobSignals
from bruker_nmr.ui.notices import ProcessingNotice


class PhaseJob(QRunnable):
    def __init__(self, ticket, data):
        super().__init__()
        self.ticket, self.data = ticket, data
        self.cancelled = threading.Event()
        self.signals = JobSignals()

    def run(self):
        result = error = None
        try:
            result = auto_phase(self.data, cancelled=self.cancelled.is_set)
        except PhaseCancelled:
            pass
        except Exception:
            error = 'Automatic phase failed; use manual controls. Spectrum unchanged.'
        if self.cancelled.is_set():
            result = error = None
        self.signals.finished.emit(self, self.ticket, result, error)


class PhaseDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.document = viewer.document
        self.revision = self.document.revision
        self.autoscale = self.document.current.view == self.document.current.full_view()
        self.base = self.document.current.data.phase_source.complex_data
        self.grid = np.arange(len(self.base)) / len(self.base)
        self.gate = LoadGate()
        self.jobs = set()
        self.setWindowTitle('Relative phase — preview only until Apply')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.controls = []
        self.sliders = []
        self.step_buttons = []
        for name, limit, initial, step in [('PH0 (degrees)', 720, 0, .1),
                                          ('PH1 (degrees / sweep)', 1440, 0, .5)]:
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(-limit, limit)
            spin.setSingleStep(step)
            spin.setValue(initial)
            spin.setMinimumWidth(max(spin.sizeHint().width(),
                                     spin.fontMetrics().horizontalAdvance('-1440.000') + 48))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(-int(limit*10), int(limit*10))
            factor = 10
            slider.setValue(round(initial*factor))
            slider.valueChanged.connect(lambda value, s=spin, f=factor: s.setValue(value/f))
            spin.valueChanged.connect(lambda value, s=slider, f=factor: self.sync_slider(s, value, f))
            row = QHBoxLayout()
            minus, plus = QPushButton('−'), QPushButton('+')
            for button in (minus, plus):
                button.setFixedWidth(32)
                button.setAutoDefault(False)
            minus.clicked.connect(spin.stepDown)
            plus.clicked.connect(spin.stepUp)
            minus.setAccessibleName(name + ' decrease')
            plus.setAccessibleName(name + ' increase')
            row.addWidget(minus)
            row.addWidget(spin)
            row.addWidget(plus)
            row.addWidget(slider, 1)
            self.step_buttons.append((minus, plus))
            form.addRow(name, row)
            self.controls.append(spin)
            self.sliders.append((slider, factor))
        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(.01, 30)
        self.jog_step.setDecimals(2)
        self.jog_step.setValue(.1)
        self.jog_step.setToolTip('Jog degrees; Shift multiplies by 10, Alt divides by 10. Numeric entry stays available.')
        form.addRow('PH0 jog step (PH1 ×5)' ,self.jog_step)
        for index in (0,1):
            for button,direction in zip(self.step_buttons[index],(-1,1)):
                button.clicked.disconnect()
                button.clicked.connect(lambda checked=False,i=index,d=direction:self.jog(i,d))
        self._pivot = .5
        self.pivot_ppm = QDoubleSpinBox()
        self.pivot_ppm.setDecimals(7)
        self.pivot_origin = float(self.document.current.data.ppm[0])+self.document.current.calibration
        self.pivot_span = float(self.document.current.data.ppm[1]-self.document.current.data.ppm[0])*len(self.base)
        self.pivot_ppm.setRange(*sorted((self.pivot_origin,self.pivot_origin+self.pivot_span)))
        self.pivot_ppm.setSingleStep(abs(self.pivot_span)/len(self.base))
        self.pivot_ppm.setValue(self.pivot_origin+self._pivot*self.pivot_span)
        form.addRow('Pivot ppm (drag dashed line)',self.pivot_ppm)
        self.pivot_line=pg.InfiniteLine(pos=self.pivot_ppm.value(),angle=90,movable=True,
                                      pen=pg.mkPen('#67757f',style=Qt.PenStyle.DashLine))
        self.pivot_line.setBounds((self.pivot_ppm.minimum(), self.pivot_ppm.maximum()))
        allow_control_pan(self.pivot_line)
        viewer.plot.addItem(self.pivot_line)
        self.pivot_line.sigPositionChanged.connect(lambda:self.pivot_ppm.setValue(self.pivot_line.value()))
        self.pivot_ppm.valueChanged.connect(self.set_pivot_ppm)
        self.compare=QPushButton('Compare original (Space)')
        self.compare.setCheckable(True)
        self.compare.setShortcut('Space')
        self.compare.toggled.connect(self.compare_changed)
        form.addRow(self.compare)
        self.note = ProcessingNotice('Relative correction to the current complex spectrum. Baseline Off. '
                           'Imported PHC values are never applied. Integrals remain unchanged in preview; '
                           'Apply recomputes areas/reference. Imported scale becomes unverified. '
                           'Changing pivot keeps PH0 fixed and changes the preview.')
        self.refit_notice = QLabel()
        if self.document.current.data.baseline is not None:
            self.refit_notice.setText('Existing baseline is omitted from phase preview. Apply invalidates it; '
                                      'refit baseline afterwards. Cancel restores it.')
            self.refit_notice.setWordWrap(True)
            form.addRow(self.refit_notice)
        self.note.setWordWrap(True)
        self.notes_button=QPushButton('Scientific notes')
        self.notes_button.setCheckable(True)
        self.notes_button.setToolTip(self.note.text())
        self.notes_button.toggled.connect(self.note.set_expanded)
        self.note.hide()
        form.addRow(self.note.brief)
        form.addRow(self.notes_button)
        form.addRow(self.note)
        self.auto = QPushButton('Auto Phase — propose')
        self.stop = QPushButton('Stop search')
        self.stop.setEnabled(False)
        form.addRow(self.auto, self.stop)
        self.auto.clicked.connect(self.start_auto)
        self.stop.clicked.connect(self.cancel_search)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply |
                                       QDialogButtonBox.StandardButton.Cancel)
        outer.addWidget(self.buttons)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.preview)
        for spin in self.controls:
            spin.valueChanged.connect(self.changed)
        fit_dialog(self, 670, 420)

    def jog(self,index,direction):
        modifiers=QApplication.keyboardModifiers()
        factor=10 if modifiers & Qt.KeyboardModifier.ShiftModifier else .1 if modifiers & Qt.KeyboardModifier.AltModifier else 1
        spin=self.controls[index]
        spin.setValue(spin.value()+direction*self.jog_step.value()*factor*(5 if index==1 else 1))

    def set_pivot_ppm(self, value):
        self.update_pivot((value-self.pivot_origin)/self.pivot_span)
        self.changed()

    def update_pivot(self, fraction):
        self._pivot = max(0.0, min(1.0, fraction))
        value = self.pivot_origin+self._pivot*self.pivot_span
        with QSignalBlocker(self.pivot_ppm), QSignalBlocker(self.pivot_line):
            self.pivot_ppm.setValue(value)
            self.pivot_line.setValue(value)

    def compare_changed(self):
        self.autoscale=False
        self.preview()

    def sync_slider(self, slider, value, factor):
        slider.blockSignals(True)
        slider.setValue(round(value*factor))
        slider.blockSignals(False)

    def phase(self):
        return Phase(self.controls[0].value(), self.controls[1].value(), self._pivot)

    def changed(self):
        self.cancel_search()
        self.timer.start()

    def valid(self):
        return (self.viewer.document is self.document and self.document.revision == self.revision)

    def preview(self):
        if not self.valid():
            self.reject()
            return
        phase = self.phase()
        angle = np.deg2rad(phase.p0 + phase.p1*(self.grid-phase.pivot))
        real = self.base.real*np.cos(angle) - self.base.imag*np.sin(angle)
        self.viewer._phase_preview = self.base.real if self.compare.isChecked() else real
        if self.autoscale:
            low, high = float(real.min()), float(real.max())
            span = max(high-low, 1e-20)
            self.viewer.view_box.setYRange(low-.05*span, high+.08*span, padding=0)
        self.viewer._draw_curve()

    def cancel_search(self):
        self.gate.cancel()
        for job in self.jobs:
            job.cancelled.set()
        self.stop.setEnabled(False)
        self.auto.setEnabled(True)
        if self.jobs:
            self.note.setText('Search canceled; manual controls remain available.')

    def start_auto(self):
        self.cancel_search()
        self.auto.setEnabled(False)
        self.stop.setEnabled(True)
        self.note.setText('Searching off the UI thread… Stop/Cancel retains the current spectrum.')
        job = PhaseJob(self.gate.begin(self.document), self.base)
        self.jobs.add(job)
        job.signals.finished.connect(self.finished_search)
        self.viewer.pool.start(job)

    @Slot(object, object, object, object)
    def finished_search(self, job, ticket, result, error):
        self.jobs.discard(job)
        if not self.valid() or not self.isVisible() or not self.gate.accepts(ticket, self.document):
            return
        self.stop.setEnabled(False)
        self.auto.setEnabled(True)
        if error or result is None:
            self.note.setText(error or 'Search canceled; use manual controls.')
            return
        for spin, value in zip(self.controls, (result.phase.p0, result.phase.p1)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        for spin, (slider, factor) in zip(self.controls, self.sliders):
            self.sync_slider(slider, spin.value(), factor)
        self.update_pivot(result.phase.pivot)
        refit = ' Apply removes the current baseline; refit it afterwards.' if self.document.current.data.baseline else ''
        self.note.setText(('Proposal — ' if result.confident else 'LOW CONFIDENCE — ') + result.reason + refit)
        self.preview()

    def apply(self):
        self.timer.stop()
        self.cancel_search()
        if not self.valid():
            self.reject()
            return
        try:
            self.document.apply_phase(self.phase())
        except ValueError:
            self.note.setText('Cannot apply: reference became nonpositive or history capacity exceeded. '
                              'Cancel and change/remove the reference, or reduce SI. Spectrum unchanged.')
            return
        self.accept()

    def done(self, result):
        self.timer.stop()
        self.cancel_search()
        self.viewer._phase_preview = None
        self.viewer.plot.removeItem(self.pivot_line)
        self.base = self.grid = None
        self.document = None
        super().done(result)
        self.viewer._render_document()
