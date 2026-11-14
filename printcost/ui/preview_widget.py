from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QPen
from PyQt6.QtCore import Qt, QTimer

from printcost.ui.style import BORDER, MUTED, MAGENTA, PREVIEW_BG

PREVIEW_SIZE = (260, 340)
SPINNER_SIZE = 64


def pil_to_qpixmap(pil_img):
    pil_img = pil_img.convert("RGB")
    data = pil_img.tobytes("raw", "RGB")
    qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


class Spinner(QWidget):
    """Small rotating loader. It's its own tiny widget, so each frame only repaints 64x64 pixels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SPINNER_SIZE, SPINNER_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self._step = 0
        self._timer.start(80)  # about 12 frames per second is plenty for a spinner
        self.show()
        self.raise_()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._step = (self._step + 1) % 12
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawEllipse(0, 0, SPINNER_SIZE, SPINNER_SIZE)

        p.translate(SPINNER_SIZE / 2, SPINNER_SIZE / 2)
        p.rotate(self._step * 30)
        for i in range(12):  # 12 spokes, the "tail" fades out
            color = QColor(MAGENTA)
            color.setAlpha(40 + int(215 * i / 11))
            p.setBrush(color)
            p.drawRoundedRect(-3, -25, 6, 11, 3, 3)
            p.rotate(30)


class PreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(*PREVIEW_SIZE)

        self._pixmap = None
        self._placeholder_text = "No file selected"
        self._scanning = False

        self.spinner = Spinner(self)
        self.spinner.move((PREVIEW_SIZE[0] - SPINNER_SIZE) // 2, (PREVIEW_SIZE[1] - SPINNER_SIZE) // 2)

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

    # ---------------- loading animation ----------------

    def start_scan(self):
        self._scanning = True
        self.spinner.start()
        self.update()  # one repaint to dim the page; the spinner repaints itself after that

    def stop_scan(self):
        self._scanning = False
        self.spinner.stop()
        self.update()

    # ---------------- painting ----------------

    def paintEvent(self, event):
        painter = QPainter(self)

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

        if self._scanning:  # dim the page while it's being scanned
            painter.fillRect(1, 1, w - 2, h - 2, QColor(255, 255, 255, 120))