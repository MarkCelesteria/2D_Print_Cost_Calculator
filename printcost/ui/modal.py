from PyQt6.QtCore import Qt, QEventLoop, QPoint, QRectF, QSize
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PyQt6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QVBoxLayout, QWidget

from printcost.ui.style import BG, BORDER

MARGIN = 16
RADIUS = 12
TITLE_H = 44


class _Card(QFrame):
    """The floating panel. When `movable`, dragging its title or any empty spot moves it, but never out of the host."""

    def __init__(self, host, movable):
        super().__init__(host)
        self.setObjectName("modalCard")
        self.setStyleSheet(f"QFrame#modalCard {{ background: {BG}; border: 1px solid {BORDER}; "
                           f"border-radius: {RADIUS}px; }}")
        self._movable = movable
        self._grab = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def mousePressEvent(self, event):
        if self._movable and event.button() == Qt.MouseButton.LeftButton:
            self._grab = (event.globalPosition().toPoint(), self.pos())
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._grab is None:
            return
        start, origin = self._grab
        delta = event.globalPosition().toPoint() - start
        host = self.parentWidget()
        x = min(max(origin.x() + delta.x(), 0), max(0, host.width() - self.width()))
        y = min(max(origin.y() + delta.y(), 0), max(0, host.height() - self.height()))
        self.move(x, y)
        host.update()

    def mouseReleaseEvent(self, event):
        self._grab = None


class _Host(QDialog):
    """A see-through window laid over the main window. It dims the app and catches taps outside the card."""

    def __init__(self, owner, area, dismiss_outside, cancel):
        super().__init__(owner, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("modalHost")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("QDialog#modalHost { background: transparent; }")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setGeometry(area)
        self.card = None
        self._dismiss = dismiss_outside
        self._cancel = cancel

    def reject(self):  # Esc
        self._cancel()

    def mousePressEvent(self, event):
        if self._dismiss and not self.card.geometry().contains(event.position().toPoint()):
            self._cancel()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(15, 27, 45, 120))
        if self.card is not None:
            r = self.card.geometry()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 8))
            for i in range(10, 0, -1):
                p.drawRoundedRect(r.adjusted(-i, -i + 4, i, i + 4), RADIUS + i, RADIUS + i)


class ModalDialog(QDialog):
    """A dialog that floats over a dimmed main window instead of opening as its own window.

    movable          drag it by its title or any empty spot (it stays inside the window)
    dismiss_outside  a tap outside the panel cancels it
    fill             take (almost) the whole screen, for big editors
    show_title       show the window title as a heading in the panel
    Esc always cancels. Nothing else changes: build it, call exec(), read the result, like any QDialog."""

    movable = False
    dismiss_outside = False
    fill = False
    show_title = True
    fit_width = 0
    _loop = None

    def exec(self):
        if self._loop is not None:
            return -1
        orig_parent, orig_flags = self.parentWidget(), self.windowFlags()
        owner = orig_parent.window() if orig_parent is not None else QApplication.activeWindow()
        screen = (owner.screen() if owner is not None else QApplication.primaryScreen()).availableGeometry()
        window_ok = owner is not None and owner.isVisible() and not owner.isMinimized() and not self.fill
        area = owner.geometry() if window_ok else screen

        if self.fit_width:
            self.ensurePolished()
            for child in self.findChildren(QWidget):
                child.ensurePolished()
            if self.layout() is not None:
                self.layout().activate()
            h = self.heightForWidth(self.fit_width)
            self.resize(self.fit_width, (h if h > 0 else self.sizeHint().height()) + 4)

        title_h = TITLE_H if self.show_title else 0
        chrome = QSize(2, title_h + 2)
        inner = (self.size() if self.testAttribute(Qt.WidgetAttribute.WA_Resized) else self.sizeHint())
        want = inner.expandedTo(self.minimumSizeHint()) + chrome

        def room(a):
            return a.size() - QSize(2 * MARGIN, 2 * MARGIN)

        if self.fill:
            want = room(area)
        elif window_ok and (want.width() > room(area).width() or want.height() > room(area).height()):
            area = screen
        card_size = want.boundedTo(room(area))

        host = _Host(owner, area, self.dismiss_outside, self.reject)
        card = _Card(host, self.movable)
        host.card = card
        lay = QVBoxLayout(card)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        if self.show_title:
            head = QLabel(self.windowTitle())
            head.setFixedHeight(title_h)
            head.setStyleSheet("font-size: 15px; font-weight: 700; padding: 0px 18px;")
            if self.movable:
                head.setCursor(Qt.CursorShape.SizeAllCursor)
            lay.addWidget(head)
        self.setParent(card, Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_Moved, True)
        lay.addWidget(self, 1)
        card.resize(card_size)
        card.move((area.width() - card.width()) // 2, (area.height() - card.height()) // 2)

        self._modal_host = host
        self._loop = QEventLoop()
        self.setResult(0)
        self.show()
        host.show()
        host.raise_()
        self._loop.exec()

        host.hide()
        self.hide()
        lay.removeWidget(self)
        self.setParent(orig_parent, Qt.WindowType.Widget) if orig_parent is not None \
            else self.setParent(None, orig_flags)
        host.deleteLater()
        self._modal_host = None
        self._loop = None
        return self.result()

    def done(self, code):
        super().done(code)
        if self._loop is not None:
            self._loop.quit()