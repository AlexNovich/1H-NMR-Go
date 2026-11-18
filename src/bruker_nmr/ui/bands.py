"""Metric-sized peak fan-out and integral bands outside numerical ordinate space."""
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.domain.labels import (
    Label,
    format_clipped_integral,
    format_peak_label,
    label_priority,
    orthogonal_leaders,
    place_peak_labels,
    visible_peaks,
)
from bruker_nmr.ui.handles import menu_action


class AnnotationBand(QWidget):
    def __init__(self, owner, kind):
        super().__init__(owner)
        self.owner, self.kind = owner, kind
        self.rows = self.placements = self.hidden = ()
        self.hit_boxes = []
        self._height_records = None
        self._height_calibration = self._height_font = None
        self.setMinimumWidth(0)
        self.setFixedHeight(self.fontMetrics().height()*2+28)

    def pixel(self, ppm):
        plot = self.owner.plot
        scene = self.owner.view_box.mapViewToScene(pg.Point(ppm, 0))
        point = plot.viewportTransform().map(scene)
        origin = self.mapFromGlobal(plot.viewport().mapToGlobal(QPoint()))
        return point.x() + origin.x()


    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, 'range_pixels'):
            self.rebuild()

    def rebuild(self):
        state = self.owner.document.current
        view = self.owner.view_box.snapshot()
        left, right = sorted((self.pixel(view.x_min), self.pixel(view.x_max)))
        left, right = max(0, left), min(self.width(), right)
        right = max(left, right)
        labels = []
        metrics = self.fontMetrics()
        self.hit_boxes = []
        self.range_pixels = {}
        self.text_lengths = {}
        if self.kind == 'peaks':
            if state.peaks.label_mode != 'hidden':
                preview = getattr(getattr(self.owner, 'peaks', None), 'preview_position', None)
                for peak in visible_peaks(state,view,preview):
                    ppm = peak.ppm+state.calibration
                    if peak.label_visible and view.x_min <= ppm <= view.x_max:
                        text = format_peak_label(peak, state.calibration)
                        priority = label_priority(peak,getattr(getattr(self.owner,'peaks',None),'selected',None))
                        labels.append(Label(peak.id, self.pixel(ppm), metrics.height()+4, text, priority, -ppm))
                        self.text_lengths[peak.id] = metrics.horizontalAdvance(text)+4
            self.placements, self.hidden = place_peak_labels(labels, left, right,
                spacing=metrics.height()+7, auto=state.peaks.label_mode == 'auto')
            font_key = self.font().toString()
            if (self._height_records is not state.peaks.records
                    or self._height_calibration != state.calibration or self._height_font != font_key):
                # Reserve the full document's widest label (including a review badge),
                # so zoom/suppression cannot resize the canvas beneath the pointer.
                self._peak_height = max((metrics.horizontalAdvance(format_peak_label(p,state.calibration)
                    + ('' if p.needs_review else ' ?'))+4 for p in state.peaks.records),
                    default=metrics.height())+32
                self._height_records = state.peaks.records
                self._height_calibration, self._height_font = state.calibration, font_key
            height = self._peak_height
            visible = state.peaks.label_mode != 'hidden' and any(p.label_visible for p in state.peaks.records)
            note = 'Vertical peak labels; ? means review after processing. Auto hides only labels, never records.'
        else:
            if state.show_integrals:
                for row in self.rows:
                    if row.high < view.x_min or row.low > view.x_max:
                        continue
                    low, high = max(row.low, view.x_min), min(row.high, view.x_max)
                    a, b = sorted((self.pixel(low), self.pixel(high)))
                    self.range_pixels[row.id] = (a, b)
                    text = format_clipped_integral(row,view)
                    labels.append(Label(row.id, (a+b)/2, metrics.height()+2, text,
                                        1 if row.reference else 0))
                    self.text_lengths[row.id] = metrics.horizontalAdvance(text)+4
            self.placements, self.hidden = place_peak_labels(
                labels, left, right, spacing=metrics.height()+5, auto=True)
            self.routes, self.route_levels = orthogonal_leaders(self.placements)
            self.highest_elbow = max(1, max(self.route_levels, default=0))
            height = max((metrics.horizontalAdvance(format_clipped_integral(row, state.view) + "‹›") + 5
                          for row in self.rows), default=metrics.height()) + 30
            visible = state.show_integrals and bool(self.rows)
            note = 'Full integral values in H; * is the reference integral. Right-click for actions.'
        self.setFixedHeight(height)
        self.setToolTip(note + (f' {len(self.hidden)} labels omitted here; all remain in the table.' if self.hidden else ''))
        self.setVisible(visible)
        self.update()

    def set_rows(self, rows=None):
        self.rows = (integral_snapshot(self.owner.document.current.integrals,
                     self.owner.document.current.grid) if rows is None else rows)
        self.rebuild()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('white'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#31556f'), 1))
        self.hit_boxes = []
        metrics = self.fontMetrics()
        if self.kind == 'integrals':
            for key, (a, b) in self.range_pixels.items():
                painter.drawLine(QPointF(a, 9), QPointF(b, 9))
                painter.drawLine(QPointF(a, 4), QPointF(a, 9))
                painter.drawLine(QPointF(b, 4), QPointF(b, 9))
                self.hit_boxes.append((QRectF(a-3, 2, b-a+6, 10), key))
        for index, placed in enumerate(self.placements):
            label = placed.label
            if self.kind == 'integrals':
                anchor, center = self.routes[index]
                bottom = self.height()-4
                length = self.text_lengths[label.id]
                top = bottom-length
                elbow = 12 + 18 * (1 - self.route_levels[index] /
                                    self.highest_elbow)
                painter.drawLine(QPointF(anchor, 9), QPointF(anchor, elbow))
                painter.drawLine(QPointF(anchor, elbow), QPointF(center, elbow))
                painter.drawLine(QPointF(center, elbow), QPointF(center, top))
                box = QRectF(placed.left, top, label.width, length)
                painter.save()
                painter.translate(center, bottom)
                painter.rotate(-90)
                painter.drawText(QRectF(0, -label.width/2, length, label.width),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label.text)
                painter.restore()
            else:
                center = placed.left+label.width/2
                bottom = self.height()-26
                length = self.text_lengths[label.id]
                box = QRectF(placed.left, bottom-length, label.width, length)
                painter.save()
                painter.translate(center, bottom)
                painter.rotate(-90)
                painter.drawText(QRectF(0,-label.width/2,length,label.width),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,label.text)
                painter.restore()
                painter.drawLine(QPointF(center,bottom+2),QPointF(center,bottom+6))
                painter.drawLine(QPointF(center,bottom+6),QPointF(label.center,self.height()-7))
                painter.drawLine(QPointF(label.center,self.height()-7),QPointF(label.center,self.height()))
            self.hit_boxes.append((box, label.id))
        if self.hidden:
            painter.setPen(QColor('#74420b'))
            painter.drawText(3, metrics.ascent()+2, f'+{len(self.hidden)}')

    def mousePressEvent(self, event):
        hits = list(dict.fromkeys(key for box,key in self.hit_boxes if box.contains(event.position())))
        if not hits:
            return super().mousePressEvent(event)
        if self.kind == 'integrals':
            if len(hits) > 1 and event.button() == Qt.MouseButton.RightButton:
                menu = QMenu(self.owner)
                point = event.globalPosition().toPoint()
                for id in hits:
                    menu_action(menu, f'Integral {next(i+1 for i,r in enumerate(self.rows) if r.id == id)}…',
                        self.owner, lambda id=id:self.owner.integrals.context_menu(point,id))
                menu.popup(event.globalPosition().toPoint())
                self._menu = menu
            else:
                self.owner.integrals.selected = hits[0]
                self.owner.selected_editor = self.owner.integrals
                if event.button() == Qt.MouseButton.RightButton:
                    self.owner.integrals.context_menu(event.globalPosition().toPoint(),hits[0])
                else:
                    self.owner.integrals.refresh_table()
                    self.owner.integrals._set_handle_modes()
        else:
            self.owner.peaks.select(hits[0])
            if event.button() == Qt.MouseButton.RightButton:
                self.owner.peaks.context_menu(event.globalPosition().toPoint(),hits[0])
        event.accept()

    def mouseDoubleClickEvent(self,event):
        hits=list(dict.fromkeys(key for box,key in self.hit_boxes if box.contains(event.position())))
        if not hits or event.button()!=Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        if self.kind=='integrals':
            self.owner.integrals.selected=hits[0]
            self.owner.selected_editor=self.owner.integrals
            self.owner.integrals.edit_dialog()
        else:
            self.owner.peaks.select(hits[0])
            self.owner.peaks.edit_dialog()
        event.accept()
