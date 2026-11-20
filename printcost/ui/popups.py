from PyQt6 import QtWidgets
import math

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QTextDocument
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                             QVBoxLayout, QWidget)

from printcost.ui.modal import ModalDialog
from printcost.ui.style import BORDER, PRIMARY, SOFT, TEXT, YELLOW, YELLOW_SOFT

_Native = getattr(QtWidgets.QMessageBox, "_native", QtWidgets.QMessageBox)
SB = _Native.StandardButton
IC = _Native.Icon
WIDTH = 400
_CUSTOM = 0x10000000


def _label(text, width, fmt=Qt.TextFormat.AutoText, name=None):
    """A wrapped label with an exact size. QLabel's own height guess for wrapped rich text can come out a line
    short on some fonts/scales, which cuts the last line, so measure the text ourselves and add a little room."""
    lab = QLabel(text)
    lab.setTextFormat(fmt)
    lab.setWordWrap(True)
    if name:
        lab.setObjectName(name)
    lab.ensurePolished()
    doc = QTextDocument()
    doc.setDefaultFont(lab.font())
    doc.setDocumentMargin(0)
    rich = fmt == Qt.TextFormat.RichText or (fmt == Qt.TextFormat.AutoText and Qt.mightBeRichText(text))
    doc.setHtml(text) if rich else doc.setPlainText(text)
    doc.setTextWidth(width)
    height = max(math.ceil(doc.size().height()), lab.heightForWidth(width))
    lab.setFixedSize(width, height + lab.fontMetrics().height() // 2 + 2)
    return lab


def _button(text, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
        b.setStyleSheet("padding: 7px 22px;")
    else:
        b.setObjectName("secondary")
    return b


class _Badge(QWidget):
    GLYPHS = {IC.Information: ("i", PRIMARY, "white"), IC.Warning: ("!", YELLOW, TEXT),
              IC.Critical: ("×", "#dc2626", "white"), IC.Question: ("?", PRIMARY, "white")}

    def __init__(self, icon):
        super().__init__()
        self.setFixedSize(40, 40)
        self._glyph = self.GLYPHS.get(icon)

    def paintEvent(self, event):
        glyph, fill, ink = self._glyph
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(fill))
        p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        font = QFont("Segoe UI", 17)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(ink))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, glyph)


class MessageBox(ModalDialog):
    """Drop-in for the parts of QMessageBox this app uses."""
    dismiss_outside = True
    _native = _Native
    Icon = _Native.Icon
    StandardButton = _Native.StandardButton
    ButtonRole = _Native.ButtonRole
    _NAMES = {SB.Ok: "OK", SB.Cancel: "Cancel", SB.Yes: "Yes", SB.No: "No", SB.Close: "Close", SB.Save: "Save",
              SB.Discard: "Discard", SB.Retry: "Retry", SB.Ignore: "Ignore", SB.Abort: "Abort",
              SB.Apply: "Apply", SB.Open: "Open"}
    _ORDER = [SB.No, SB.Cancel, SB.Discard, SB.Close, SB.Ignore, SB.Abort, SB.Apply, SB.Open,
              SB.Retry, SB.Save, SB.Yes, SB.Ok]
    _CANCEL_LIKE = [SB.Cancel, SB.No, SB.Close, SB.Abort, SB.Discard, SB.Ignore]

    def __init__(self, *args):
        icon, title, text, buttons, parent = IC.NoIcon, "", "", SB.NoButton, None
        if args and (args[0] is None or isinstance(args[0], QWidget)):
            parent = args[0]
        elif args:
            icon, title, text = args[0], args[1], args[2]
            buttons = args[3] if len(args) > 3 else SB.NoButton
            parent = args[4] if len(args) > 4 else None
        super().__init__(parent)
        self._icon, self._text, self._info, self._detail = icon, text, "", ""
        self._fmt = Qt.TextFormat.AutoText
        self._std, self._extra = {}, []
        self._default = self._escape = self._clicked = None
        self.setWindowTitle(title)
        if buttons:
            self.setStandardButtons(buttons)

    # ---- QMessageBox API ----
    def setIcon(self, icon): self._icon = icon
    def setText(self, text): self._text = text
    def setInformativeText(self, text): self._info = text
    def setDetailedText(self, text): self._detail = text
    def setTextFormat(self, fmt): self._fmt = fmt
    def textFormat(self): return self._fmt
    def clickedButton(self): return self._clicked
    def button(self, which): return self._std.get(which)
    def setDefaultButton(self, b): self._default = self._std.get(b, b)
    def setEscapeButton(self, b): self._escape = self._std.get(b, b)

    def setStandardButtons(self, buttons):
        for sb in self._ORDER:
            if buttons & sb and sb not in self._std:
                self._std[sb] = self._make(self._NAMES[sb])

    def addButton(self, what, role=None):
        if isinstance(what, str):
            b = self._make(what)
            self._extra.append(b)
            return b
        self.setStandardButtons(what)
        return self._std[what]

    def _make(self, text):
        b = _button(text)
        b.clicked.connect(lambda _=False, b=b: self._pressed(b))
        return b

    def _pressed(self, b):
        self._clicked = b
        self.done(1)

    def reject(self):
        b = self._escape
        if b is None:
            b = next((self._std[s] for s in self._CANCEL_LIKE if s in self._std), None)
        if b is None and len(self._std) + len(self._extra) == 1:
            b = next(iter(self._std.values()), None) or self._extra[0]
        if b is not None:
            self._clicked = b
            super().reject()

    # ---- building and running ----
    def _build(self):
        std = [self._std[s] for s in self._ORDER if s in self._std]
        every = self._extra + std
        default = self._default if self._default in every else (every[-1] if every else None)
        order = [b for b in every if b is not default] + ([default] if default else [])

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 6, 20, 18)
        root.setSpacing(16)
        top = QHBoxLayout()
        top.setSpacing(14)
        if self._icon in _Badge.GLYPHS:
            top.addWidget(_Badge(self._icon), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        text_w = WIDTH - 40 - (54 if self._icon in _Badge.GLYPHS else 0)
        text = _label(self._text, text_w, self._fmt)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(text)
        if self._info:
            col.addWidget(_label(self._info, text_w, name="muted"))
        top.addLayout(col, 1)
        root.addLayout(top)
        if self._detail:
            box = QPlainTextEdit(self._detail)
            box.setReadOnly(True)
            box.setFixedHeight(130)
            root.addWidget(box)
        row = QHBoxLayout()
        row.addStretch()
        for b in order:
            if b is default:
                b.setObjectName("primary")
                b.setStyleSheet("padding: 7px 22px;")
            row.addWidget(b)
        root.addLayout(row)
        self._default_button = default
        self.fit_width = WIDTH

    def exec(self):
        self._build()
        super().exec()
        for sb, b in self._std.items():
            if b is self._clicked:
                return int(sb)
        return _CUSTOM + (self._extra.index(self._clicked) if self._clicked in self._extra else 0)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_default_button", None) is not None:
            self._default_button.setFocus()


def _static(icon):
    def show(parent, title, text, buttons=SB.Ok, defaultButton=SB.NoButton):
        box = MessageBox(icon, title, text, buttons, parent)
        if defaultButton:
            box.setDefaultButton(defaultButton)
        return SB(box.exec())
    return staticmethod(show)


MessageBox.information = _static(IC.Information)
MessageBox.warning = _static(IC.Warning)
MessageBox.critical = _static(IC.Critical)
MessageBox.question = _static(IC.Question)


class _Pick(ModalDialog):
    dismiss_outside = True

    def __init__(self, parent, title, label, items, current):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 6, 20, 18)
        lay.setSpacing(12)
        if label:
            lay.addWidget(_label(label, 380 - 40))
        self.list = QListWidget()
        self.list.addItems(items)
        self.list.setCurrentRow(max(0, min(current, len(items) - 1)))
        self.list.setFixedHeight(min(len(items), 9) * 36 + 6)
        self.list.setStyleSheet(
            f"QListWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; outline: 0; }}"
            f"QListWidget::item {{ padding: 8px 10px; }}"
            f"QListWidget::item:hover {{ background: {SOFT}; }}"
            f"QListWidget::item:selected {{ background: {YELLOW_SOFT}; color: {TEXT}; }}")
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        lay.addWidget(self.list)
        row = QHBoxLayout()
        row.addStretch()
        cancel, ok = _button("Cancel"), _button("OK", primary=True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.fit_width = 380
        self.list.setFocus()


def get_item(parent, title, label, items, current=0, editable=True, *args, **kwargs):
    items = list(items)
    dlg = _Pick(parent, title, label, items, current)
    if dlg.exec() == 1 and dlg.list.currentItem() is not None:
        return dlg.list.currentItem().text(), True
    return (items[current] if items else ""), False


class _Ask(ModalDialog):
    dismiss_outside = True

    def __init__(self, parent, title, label, echo, text):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 6, 20, 18)
        lay.setSpacing(12)
        if label:
            lay.addWidget(_label(label, 360 - 40))
        self.edit = QLineEdit(text)
        self.edit.setEchoMode(echo)
        self.edit.returnPressed.connect(self.accept)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.addStretch()
        cancel, ok = _button("Cancel"), _button("OK", primary=True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.fit_width = 360
        self.edit.setFocus()
        self.edit.selectAll()


def get_text(parent, title, label, echo=QLineEdit.EchoMode.Normal, text="", *args, **kwargs):
    dlg = _Ask(parent, title, label, echo, text)
    if dlg.exec() == 1:
        return dlg.edit.text(), True
    return text, False


def install():
    QtWidgets.QMessageBox = MessageBox
    QtWidgets.QInputDialog.getItem = staticmethod(get_item)
    QtWidgets.QInputDialog.getText = staticmethod(get_text)