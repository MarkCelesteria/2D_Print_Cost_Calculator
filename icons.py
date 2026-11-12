from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QPen, QPolygonF

from style import PRIMARY_DARK

_cache = {}


def _printer(p, c, white):
    p.setBrush(c)
    p.drawRoundedRect(QRectF(6, 24, 52, 26), 8, 8)              # body
    p.setPen(QPen(c, 5))
    p.setBrush(white)
    p.drawRoundedRect(QRectF(17, 7, 30, 19), 3, 3)              # paper in
    p.drawRoundedRect(QRectF(17, 38, 30, 19), 3, 3)             # paper out
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(45, 31, 6, 6))                         # light


def _receipt(p, c, white):
    pts = [(14, 6), (50, 6), (50, 58), (43, 52), (36, 58), (29, 52), (22, 58), (14, 52)]
    p.setBrush(c)
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    p.setBrush(white)
    for y, w in ((16, 20), (27, 20), (38, 12)):
        p.drawRoundedRect(QRectF(22, y, w, 4), 2, 2)


def _tag(p, c, white):
    pts = [(11, 15), (35, 15), (53, 32), (35, 49), (11, 49)]
    p.setPen(QPen(c, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(c)
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(white)
    p.drawEllipse(QRectF(16, 27, 10, 10))


_DRAW = {"printer": _printer, "receipt": _receipt, "tag": _tag}


def make_icon(name, color=PRIMARY_DARK):
    key = (name, color)
    if key not in _cache:
        pix = QPixmap(96, 96)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(1.5, 1.5)
        p.setPen(Qt.PenStyle.NoPen)
        _DRAW[name](p, QColor(color), QColor("white"))
        p.end()
        _cache[key] = QIcon(pix)
    return _cache[key]