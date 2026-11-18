"""Project-owned vector strokes, rasterized at native icon resolutions by Qt."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

NAMES = (
    "zip",
    "folder",
    "save",
    "undo",
    "redo",
    "zoom",
    "pan",
    "full",
    "previous",
    "phase",
    "baseline",
    "integrate",
    "pick",
    "reference",
    "integrals",
    "peaks",
    "pdf",
)


def icon(name, colour="#253e56"):
    result = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 24, size / 24)
        p.setPen(
            QPen(
                QColor(colour),
                1.65,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )

        def line(*coords):
            path = QPainterPath(QPointF(*coords[:2]))
            for i in range(2, len(coords), 2):
                path.lineTo(*coords[i : i + 2])
            p.drawPath(path)

        def peak(offset=0):
            line(
                3,
                18 + offset,
                7,
                18 + offset,
                9,
                15 + offset,
                11,
                5 + offset,
                13,
                15 + offset,
                16,
                18 + offset,
                21,
                18 + offset,
            )

        def integral(offset=0):
            path = QPainterPath(QPointF(16 + offset, 4))
            path.cubicTo(10 + offset, 1, 14 + offset, 23, 8 + offset, 20)
            p.drawPath(path)

        def document():
            line(5, 21, 5, 3, 15, 3, 20, 8, 20, 21, 5, 21)
            line(15, 3, 15, 8, 20, 8)

        if name == "zip":
            document()
            line(7, 12, 10, 12, 7, 17, 10, 17)
            line(12, 12, 12, 17)
            line(15, 17, 15, 12, 18, 12, 18, 14.5, 15, 14.5)
        elif name == "folder":
            line(3, 9, 3, 5, 10, 5, 12, 8, 21, 8, 21, 10)
            line(2, 11, 22, 11, 18, 20, 4, 20, 2, 11)
        elif name == "zoom":
            p.drawEllipse(3, 3, 12, 12)
            line(14, 14, 21, 21)
            pen = p.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRect(6, 6, 6, 6)
        elif name == "pan":
            line(12, 3, 12, 21)
            line(3, 12, 21, 12)
            for coords in (
                (9, 6, 12, 3, 15, 6),
                (9, 18, 12, 21, 15, 18),
                (6, 9, 3, 12, 6, 15),
                (18, 9, 21, 12, 18, 15),
            ):
                line(*coords)
        elif name == "full":
            for coords in (
                (9, 3, 3, 3, 3, 9),
                (15, 3, 21, 3, 21, 9),
                (3, 15, 3, 21, 9, 21),
                (15, 21, 21, 21, 21, 15),
            ):
                line(*coords)
        elif name == "previous":
            p.drawEllipse(8, 3, 12, 12)
            line(18, 14, 22, 20)
            line(2, 10, 6, 10)
            line(5, 7, 2, 10, 5, 13)
        elif name == "save":
            line(4, 3, 17, 3, 21, 7, 21, 21, 3, 21, 3, 3, 4, 3)
            line(8, 3, 8, 9, 16, 9, 16, 3)
            line(7, 21, 7, 14, 17, 14, 17, 21)
        elif name in ("undo", "redo"):
            if name == "redo":
                p.translate(24, 0)
                p.scale(-1, 1)
            line(8, 4, 3, 9, 8, 14)
            path = QPainterPath(QPointF(3, 9))
            path.lineTo(14, 9)
            path.cubicTo(23, 9, 23, 21, 14, 21)
            p.drawPath(path)
        elif name == "phase":
            p.drawEllipse(4, 6, 16, 12)
            line(12, 2, 12, 22)
        elif name == "baseline":
            peak(-2)
            pen = p.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            line(3, 21, 21, 21)
        elif name == "integrate":
            integral()
        elif name == "pick":
            peak(2)
            p.drawEllipse(9, 3, 4, 4)
        elif name == "reference":
            p.drawEllipse(8, 6, 8, 12)
            line(2, 12, 5, 12)
            line(19, 12, 22, 12)
            line(12, 2, 12, 4)
            line(12, 20, 12, 22)
        elif name == "integrals":
            integral(-3)
            line(14, 8, 21, 8)
            line(14, 12, 21, 12)
            line(14, 16, 21, 16)
        elif name == "peaks":
            line(3, 18, 6, 18, 9, 9, 12, 18, 14, 18, 17, 6, 20, 18, 22, 18)
            p.drawEllipse(16, 2, 2, 2)
            p.drawEllipse(8, 5, 2, 2)
        elif name == "pdf":
            document()
            line(7, 17, 7, 12, 9, 12, 9, 14.5, 7, 14.5)
            line(11, 12, 11, 17, 13, 17, 14, 16, 14, 13, 13, 12, 11, 12)
            line(17, 17, 17, 12, 19, 12)
            line(17, 14.5, 19, 14.5)
        elif name == "app":
            p.setPen(QPen(QColor("#315b8a"), 1.8))
            p.drawRoundedRect(2, 2, 20, 20, 4, 4)
            line(4, 18, 7, 18, 9, 12, 10, 17, 12, 6, 14, 17, 16, 10, 18, 18, 20, 18)
        else:
            p.end()
            raise ValueError("Unknown project icon")
        p.end()
        result.addPixmap(pixmap)
    return result
