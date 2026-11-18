from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from printcost.ui.icons import make_icon
from printcost.ui.style import MAGENTA_DARK, MUTED, TEXT, YELLOW


class DropOverlay(QWidget):
    def __init__(self, parent, title="Drop your file here", hint="PDF, Word or images"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title, self.hint = title, hint
        self.hide()

    def show_over(self):
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRoundedRect(box, 16, 16)

        pen = QPen(QColor(YELLOW), 3, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 16, 16)

        cx, cy = box.center().x(), box.center().y()
        p.drawPixmap(int(cx - 32), int(cy - 84), make_icon("pages", MAGENTA_DARK, "white").pixmap(64, 64))

        title = QFont("Segoe UI", 16)
        title.setBold(True)
        p.setFont(title)
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(box.x(), cy - 8, box.width(), 32), Qt.AlignmentFlag.AlignCenter, self.title)

        p.setFont(QFont("Segoe UI", 10))
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(box.x(), cy + 26, box.width(), 22), Qt.AlignmentFlag.AlignCenter, self.hint)