from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout
from printcost.ui.popups import _label


class HelpPopup(QFrame):
    def __init__(self, text, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("helpPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(_label(text, 270))


class HelpButton(QToolButton):
    """A small round question mark. Click it for a short explanation."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setObjectName("help")
        self.setText("?")
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("What is this?")
        self._text = text
        self._popup = None
        self.clicked.connect(self._show)

    def _show(self):
        self._popup = HelpPopup(self._text, self)
        self._popup.adjustSize()
        pos = self.mapToGlobal(QPoint(0, self.height() + 6))
        screen = self.screen().availableGeometry()
        pos.setX(max(screen.left(), min(pos.x(), screen.right() - self._popup.width())))
        if pos.y() + self._popup.height() > screen.bottom():
            pos.setY(self.mapToGlobal(QPoint(0, 0)).y() - self._popup.height() - 6)
        self._popup.move(pos)
        self._popup.show()


def with_help(widget, text):
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(widget)
    row.help = HelpButton(text)
    row.addWidget(row.help)
    row.addStretch()
    return row