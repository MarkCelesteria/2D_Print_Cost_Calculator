import math

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QPen, QPolygonF

from style import PRIMARY_DARK

_cache = {}
ROUND = Qt.PenCapStyle.RoundCap
JOIN = Qt.PenJoinStyle.RoundJoin


def _poly(*pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _stroke(p, c, width=6):
    p.setPen(QPen(c, width, Qt.PenStyle.SolidLine, ROUND, JOIN))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _fill(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)


def _head(p, c, tip, angle, size=13):
    """Arrow head whose point is `tip`, pointing toward `angle` (degrees, screen coordinates, 0 = right)."""
    a = math.radians(angle)
    ux, uy = math.cos(a), math.sin(a)
    bx, by = tip[0] - ux * size, tip[1] - uy * size
    px, py = -uy * size * 0.8, ux * size * 0.8
    _fill(p, c)
    p.drawPolygon(_poly(tip, (bx + px, by + py), (bx - px, by - py)))


def _arc_arrow(p, c, cx, cy, r, start, span, width=6.5):
    """An arc (math angles, counter-clockwise positive) with an arrow head on its end."""
    _stroke(p, c, width)
    p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), int(start * 16), int(span * 16))
    e = math.radians(start + span)
    ex, ey = cx + r * math.cos(e), cy - r * math.sin(e)
    tx, ty = (-math.sin(e), -math.cos(e)) if span > 0 else (math.sin(e), math.cos(e))
    _head(p, c, (ex + tx * 6, ey + ty * 6), math.degrees(math.atan2(ty, tx)))


def _plus_badge(p, c, white, cx=47, cy=46, r=12):
    p.setPen(QPen(white, 3))
    p.setBrush(c)
    p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
    p.setPen(QPen(white, 3.5, Qt.PenStyle.SolidLine, ROUND))
    p.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
    p.drawLine(QPointF(cx, cy - 5), QPointF(cx, cy + 5))


# ---------------- icons (64 x 64 canvas) ----------------

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
    p.setBrush(c)
    p.drawPolygon(_poly((14, 6), (50, 6), (50, 58), (43, 52), (36, 58), (29, 52), (22, 58), (14, 52)))
    p.setBrush(white)
    for y, w in ((16, 20), (27, 20), (38, 12)):
        p.drawRoundedRect(QRectF(22, y, w, 4), 2, 2)


def _tag(p, c, white):
    p.setPen(QPen(c, 6, Qt.PenStyle.SolidLine, ROUND, JOIN))
    p.setBrush(c)
    p.drawPolygon(_poly((11, 15), (35, 15), (53, 32), (35, 49), (11, 49)))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(white)
    p.drawEllipse(QRectF(16, 27, 10, 10))                       # hole


def _undo(p, c, white):
    _arc_arrow(p, c, 33, 36, 17, -35, 215)


def _redo(p, c, white):
    p.translate(64, 0)
    p.scale(-1, 1)
    _arc_arrow(p, c, 33, 36, 17, -35, 215)


def _reset(p, c, white):
    _arc_arrow(p, c, 32, 32, 19, 65, 295)
    _fill(p, c)
    p.drawEllipse(QRectF(27, 27, 10, 10))                       # back to the start


def _image_add(p, c, white):
    _fill(p, c)
    p.drawRoundedRect(QRectF(5, 9, 46, 38), 6, 6)               # picture
    _fill(p, white)
    p.drawEllipse(QRectF(14, 16, 9, 9))                         # sun
    p.drawPolygon(_poly((9, 42), (22, 29), (30, 37), (36, 31), (47, 42), (47, 43), (9, 43)))
    _plus_badge(p, c, white)


def _trash(p, c, white):
    _fill(p, c)
    p.drawRoundedRect(QRectF(11, 14, 42, 7), 3, 3)              # lid
    p.drawRoundedRect(QRectF(24, 7, 16, 8), 3, 3)               # handle
    p.drawPolygon(_poly((15, 25), (49, 25), (46, 55), (18, 55)))
    _fill(p, white)
    for x in (25, 31, 37):
        p.drawRoundedRect(QRectF(x, 30, 3, 20), 1.5, 1.5)


def _crop(p, c, white):
    _stroke(p, c, 7)
    p.setPen(QPen(c, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
    p.drawPolyline(_poly((19, 4), (19, 45), (60, 45)))
    p.drawPolyline(_poly((4, 19), (45, 19), (45, 60)))


def _position(p, c, white):
    _stroke(p, c, 5)
    p.drawRoundedRect(QRectF(11, 6, 42, 52), 6, 6)               # the page
    _fill(p, c)
    p.drawRoundedRect(QRectF(19, 14, 20, 14), 3, 3)             # the picture placed on it


def _document(p, c, white, x=12):
    _fill(p, c)
    p.drawPolygon(_poly((x, 6), (x + 24, 6), (x + 38, 20), (x + 38, 58), (x, 58)))
    _fill(p, white)
    p.drawPolygon(_poly((x + 24, 6), (x + 24, 20), (x + 38, 20)))
    for y in (30, 39, 48):
        p.drawRoundedRect(QRectF(x + 7, y, 24, 4), 2, 2)


def _pages(p, c, white):
    _document(p, c, white, x=8)
    _plus_badge(p, c, white, cx=48, cy=47)


def _page_setup(p, c, white):
    _stroke(p, c, 5)
    p.drawRoundedRect(QRectF(8, 6, 28, 40), 5, 5)                # a portrait page
    p.setPen(QPen(white, 3.5))
    p.setBrush(c)
    p.drawRoundedRect(QRectF(24, 30, 34, 25), 5, 5)              # and a landscape one in front of it


def _copy(p, c, white):
    _stroke(p, c, 5)
    p.drawRoundedRect(QRectF(22, 6, 32, 38), 5, 5)               # back sheet
    p.setPen(QPen(white, 3))
    p.setBrush(c)
    p.drawRoundedRect(QRectF(9, 19, 32, 38), 5, 5)               # front sheet


def _paste(p, c, white):
    _fill(p, c)
    p.drawRoundedRect(QRectF(8, 11, 48, 48), 7, 7)               # board
    p.drawRoundedRect(QRectF(21, 5, 22, 13), 4, 4)               # clip
    _fill(p, white)
    p.drawRoundedRect(QRectF(17, 26, 30, 26), 3, 3)             # the sheet on it
    _fill(p, c)
    for y in (32, 39, 46):
        p.drawRoundedRect(QRectF(21, y, 22, 3), 1.5, 1.5)


def _poster(p, c, white):
    _stroke(p, c, 5)
    p.drawRoundedRect(QRectF(8, 8, 48, 48), 6, 6)               # the whole poster
    p.drawLine(QPointF(32, 8), QPointF(32, 56))                 # cut into sheets
    p.drawLine(QPointF(8, 32), QPointF(56, 32))


def _check(p, c, white):
    _stroke(p, c, 9)
    p.drawPolyline(_poly((13, 34), (26, 47), (51, 18)))


def _close(p, c, white):
    _stroke(p, c, 9)
    p.drawLine(QPointF(16, 16), QPointF(48, 48))
    p.drawLine(QPointF(48, 16), QPointF(16, 48))


_DRAW = {"printer": _printer, "receipt": _receipt, "tag": _tag, "undo": _undo, "redo": _redo, "reset": _reset,
         "image-add": _image_add, "trash": _trash, "crop": _crop, "position": _position, "pages": _pages,
         "copy": _copy, "paste": _paste, "page-setup": _page_setup, "check": _check, "close": _close,
         "poster": _poster}


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