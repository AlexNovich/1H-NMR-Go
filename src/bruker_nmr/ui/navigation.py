"""One view-history entry per rectangle/pan/wheel gesture, independent of edits."""
import math

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal

from bruker_nmr.domain.document import ViewState


class NavigationBox(pg.ViewBox):
    gesture_finished = Signal()
    integral_created = Signal(float, float)
    anchor_created = Signal(float,float)
    peak_clicked = Signal(float)
    peaks_boxed = Signal(float, float, float, float)

    def __init__(self):
        super().__init__(enableMenu=False, defaultPadding=0)
        self.setMouseMode(self.RectMode)
        self.invertX(True)
        self.disableAutoRange()
        self.active = True
        self.integrating = False
        self.anchoring = False
        self.picking = False
        self._control_pan = False
        self._drag_active = self._drag_cancelled = False
        self._wheel = QTimer(self)
        self._wheel.setSingleShot(True)
        self._wheel.setInterval(180)
        self._wheel.timeout.connect(self.gesture_finished)

    def finish_pending(self):
        pending = self._wheel.isActive() or self._drag_active
        self._wheel.stop()
        if self._drag_active:
            self._drag_active = False
            self._drag_cancelled = True
            self._control_pan = False
            self.rbScaleBox.hide()
        if pending:
            self.gesture_finished.emit()

    def cancel_pending(self):
        self._wheel.stop()
        self.rbScaleBox.hide()
        if self._drag_active:
            self._drag_cancelled = True
            self._drag_active = False
            self._control_pan = False

    def snapshot(self):
        x, y = self.viewRange()
        return ViewState(*x, *y)

    def mouseDragEvent(self, event, axis=None):
        if not self.active:
            event.ignore()
            return
        if event.isStart():
            self.finish_pending()
            self._drag_active = True
            self._drag_cancelled = False
        elif self._drag_cancelled:
            event.accept()
            return
        if event.isFinish():
            self._drag_active = False
        if event.button() == Qt.MouseButton.RightButton:
            event.ignore()  # No independent right-drag scaling that would bypass the toolbar mode.
            return
        if event.isStart():
            self._control_pan = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self._control_pan and event.button() == Qt.MouseButton.LeftButton:
            saved = self.state['mouseMode']
            self.state['mouseMode'] = self.PanMode
            try:
                super().mouseDragEvent(event, axis=axis)
            finally:
                self.state['mouseMode'] = saved
            if event.isFinish():
                self._control_pan = False
                self.gesture_finished.emit()
            return
        if self.picking and event.button() == Qt.MouseButton.LeftButton and axis is None:
            event.accept()
            if event.isFinish():
                self.rbScaleBox.hide()
                a = self.mapToView(event.buttonDownPos())
                b = self.mapToView(event.pos())
                self.peaks_boxed.emit(a.x(), a.y(), b.x(), b.y())
            else:
                self.updateScaleBox(event.buttonDownPos(), event.pos())
            return
        if (self.integrating or self.anchoring) and event.button() == Qt.MouseButton.LeftButton and axis is None:
            event.accept()
            if event.isFinish():
                self.rbScaleBox.hide()
                (self.anchor_created if self.anchoring else self.integral_created).emit(event.buttonDownPos().x(), event.pos().x())
            else:
                self.updateScaleBox(event.buttonDownPos(), event.pos())
            return
        super().mouseDragEvent(event, axis=axis)
        if event.isFinish():
            # Only Document owns history; discard ViewBox's separate rectangle backlog.
            self.axHistory = []
            self.axHistoryPointer = -1
            self.gesture_finished.emit()

    def mouseClickEvent(self, event):
        if (self.active and self.picking and event.button() == Qt.MouseButton.LeftButton
                and not event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            event.accept()
            self.peak_clicked.emit(self.mapToView(event.pos()).x())
            return
        super().mouseClickEvent(event)

    def wheelEvent(self, event, axis=None):
        if not self.active:
            event.ignore()
            return
        x, y = self.viewRange()
        factor = 0.85 ** max(-10, min(10, event.delta() / 120))
        modifiers = event.modifiers()
        horizontal = axis == 0 or bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            shift = (x[1]-x[0])*.1*max(-10,min(10,event.delta()/120))
            if not all(math.isfinite(v) for v in (shift,x[0]+shift,x[1]+shift)):
                event.ignore()
                return
            self.setRange(xRange=(x[0]+shift,x[1]+shift),padding=0)
            event.accept()
            self._wheel.start()
            return
        limits = x if horizontal else y
        span = (limits[1] - limits[0]) * factor
        if not math.isfinite(span) or not 1e-100 < span < 1e100:
            event.ignore()
            return
        if horizontal:
            anchor = self.mapSceneToView(event.scenePos()).x()
            self.setRange(xRange=(anchor+(x[0]-anchor)*factor,
                                  anchor+(x[1]-anchor)*factor), padding=0)
            event.accept()
            self._wheel.start()
            return
        anchor = 0 if y[0] <= 0 <= y[1] else (y[0] + y[1]) / 2
        self.setRange(yRange=(anchor + (y[0] - anchor) * factor,
                              anchor + (y[1] - anchor) * factor), padding=0)
        event.accept()
        self._wheel.start()
