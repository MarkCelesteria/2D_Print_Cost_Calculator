from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QPen
from PyQt6.QtCore import Qt, QTimer

from style import BORDER, MUTED, ACCENT_LINE, PREVIEW_BG

PREVIEW_SIZE = (260, 340)


def pil_to_qpixmap(pil_img):
    pil_img = pil_img.convert("RGB")
    data = pil_img.tobytes("raw", "RGB")
    qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


class PreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(*PREVIEW_SIZE)

        self._pixmap = None
        self._placeholder_text = "No file selected"
        self._scanning = False
        self._scan_y = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    # ---------------- content ----------------

    def show_placeholder(self, text):
        self._pixmap = None
        self._placeholder_text = text
        self.update()

    def show_image(self, pil_img):
        if pil_img is None:
            self.show_placeholder("No preview available\n(open the file to check it)")
            return
        self._pixmap = pil_to_qpixmap(pil_img)
        self.update()

    # ---------------- scanning animation ----------------

    def start_scan(self):
        self._scanning = True
        self._scan_y = 0
        self._timer.start(25)

    def stop_scan(self):
        self._scanning = False
        self._timer.stop()
        self.update()

    def _advance(self):
        self._scan_y += 9
        if self._scan_y > self.height():
            self._scan_y = 0  # loop back to the top for long-running files
        self.update()

    # ---------------- painting ----------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(PREVIEW_BG))
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        if self._pixmap is not None:
            x = (w - self._pixmap.width()) // 2
            y = (h - self._pixmap.height()) // 2
            painter.drawPixmap(x, y, self._pixmap)
        else:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect().adjusted(15, 0, -15, 0),
                              Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                              self._placeholder_text)

        if self._scanning:
            painter.setPen(QPen(QColor(ACCENT_LINE), 3))
            painter.drawLine(4, self._scan_y, w - 4, self._scan_y)
