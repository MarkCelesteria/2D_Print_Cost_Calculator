import copy

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QMenu, QSizePolicy,
)

from printcost.core import idphoto
from printcost.ui.preview_widget import pil_to_qpixmap
from printcost.ui.style import BORDER, MAGENTA, PREVIEW_BG

SNAP_PX = 9        # how close (on screen) a photo has to be to a neighbour before it snaps
CELL_PPI = 120     # resolution of the little pictures drawn on the canvas
PAD = 18


class LayoutCanvas(QWidget):
    """One sheet drawn to scale. Photos can't overlap: a photo dropped on another pushes it aside.
    They snap only to the photos right next to them (and to the printable edge)."""
    changed = pyqtSignal()
    selection_changed = pyqtSignal()
    status = pyqtSignal(str)

    def __init__(self, img, spec, landscape, sheets, sig):
        super().__init__()
        self.setMinimumSize(540, 620)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.img, self.spec, self.sig = img, spec, sig
        self.sw, self.sh = idphoto.sheet_size(spec["paper"], landscape)
        self.bounds = idphoto.printable(spec, landscape)
        self.gap = spec["gap_in"]
        self.sheets = [[list(it) for it in sheet] for sheet in sheets] or [[]]
        self.cur, self.sel = 0, -1
        self._drag = None
        self._wanted = None     # where a photo was dragged to when there is no room for it (drawn red)
        self._guides = []
        self._pix = {}
        self._undo = []

    # ---------------- state ----------------

    @property
    def items(self):
        return self.sheets[self.cur]

    def counts(self):
        out = [0] * len(self.spec["entries"])
        for sheet in self.sheets:
            for it in sheet:
                out[it[4]] += 1
        return out

    def result(self):
        return [[tuple(it) for it in sheet] for sheet in self.sheets if sheet]

    def _remember(self, snapshot=None):
        self._undo.append((copy.deepcopy(self.sheets) if snapshot is None else snapshot, self.cur))
        del self._undo[:-50]

    def undo(self):
        if not self._undo:
            return
        self.sheets, self.cur = self._undo.pop()
        self.cur = min(self.cur, len(self.sheets) - 1)
        self.sel = -1
        self.update()
        self.selection_changed.emit()
        self.changed.emit()

    def can_undo(self):
        return bool(self._undo)

    def set_sheet(self, i):
        self.cur = max(0, min(i, len(self.sheets) - 1))
        self.sel = -1
        self.update()
        self.selection_changed.emit()

    def add_sheet(self):
        self._remember()
        self.sheets.append([])
        self.set_sheet(len(self.sheets) - 1)
        self.changed.emit()

    def delete_sheet(self):
        if len(self.sheets) > 1 and not self.items:
            self._remember()
            del self.sheets[self.cur]
            self.set_sheet(min(self.cur, len(self.sheets) - 1))
            self.changed.emit()

    def delete_selected(self):
        if 0 <= self.sel < len(self.items):
            self._remember()
            del self.items[self.sel]
            self.sel = -1
            self.update()
            self.selection_changed.emit()
            self.changed.emit()

    def add_photo(self, idx, at=None):
        """Add a photo of entry `idx`: next to what is there, or at the point `at` (inches). Photos in the
        way are pushed aside. Returns False when there is no room."""
        e = self.spec["entries"][idx]
        w, h = e["w"], e["h"]
        if at is None:
            spot = idphoto.find_spot(self.items, w, h, self.bounds, self.gap)
            x, y = spot if spot else idphoto.clamp_rect((self.sw - w) / 2, (self.sh - h) / 2, w, h, self.bounds)
        else:
            x, y = idphoto.clamp_rect(at[0] - w / 2, at[1] - h / 2, w, h, self.bounds)
            thr = SNAP_PX / self._geom()[0]
            x, y, _ = idphoto.snap_move((x, y, w, h), self.items, self.bounds, self.gap, thr)
        trial = [list(it) for it in self.items] + [[x, y, w, h, idx]]
        result = idphoto.resolve(trial, len(trial) - 1, self.bounds, self.gap)
        if result is None:
            self.status.emit("No room for it on this sheet. Add a sheet or remove a photo.")
            return False
        self._remember()
        self.sheets[self.cur] = result
        self.sel = len(result) - 1
        self.update()
        self.selection_changed.emit()
        self.changed.emit()
        return True

    # ---------------- geometry ----------------

    def _geom(self):
        s = min((self.width() - 2 * PAD) / self.sw, (self.height() - 2 * PAD) / self.sh)
        return s, (self.width() - self.sw * s) / 2, (self.height() - self.sh * s) / 2

    def _to_in(self, pt):
        s, ox, oy = self._geom()
        return (pt.x() - ox) / s, (pt.y() - oy) / s

    def _rect_px(self, it):
        s, ox, oy = self._geom()
        return QRectF(ox + it[0] * s, oy + it[1] * s, it[2] * s, it[3] * s)

    def _hit(self, x, y):
        for i, it in enumerate(self.items):
            if it[0] <= x <= it[0] + it[2] and it[1] <= y <= it[1] + it[3]:
                return i
        return -1

    def _cell(self, idx):
        if idx not in self._pix:
            e = self.spec["entries"][idx]
            self._pix[idx] = pil_to_qpixmap(
                idphoto.render_cell(self.img, e, self.spec["focus"], self.spec, self.sig, CELL_PPI))
        return self._pix[idx]

    # ---------------- painting ----------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(PREVIEW_BG))
        s, ox, oy = self._geom()
        paper = QRectF(ox, oy, self.sw * s, self.sh * s)
        p.fillRect(paper, QColor("white"))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawRect(paper)
        L, T, R, B = self.bounds
        p.setPen(QPen(QColor("#b8c4d3"), 1, Qt.PenStyle.DashLine))
        p.drawRect(QRectF(ox + L * s, oy + T * s, (R - L) * s, (B - T) * s))   # where the printer can print

        for i, it in enumerate(self.items):
            r = self._rect_px(it)
            pix = self._cell(it[4])
            p.drawPixmap(r, pix, QRectF(pix.rect()))
            p.setPen(QPen(QColor("#94a3b8"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
        if 0 <= self.sel < len(self.items):
            p.setPen(QPen(QColor(MAGENTA), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._rect_px(self.items[self.sel]))

        p.setPen(QPen(QColor(MAGENTA), 1, Qt.PenStyle.DashLine))
        for kind, v, a, b in self._guides:     # lines that show what the photo snapped to
            if kind == "v":
                p.drawLine(QPointF(ox + v * s, oy + a * s), QPointF(ox + v * s, oy + b * s))
            else:
                p.drawLine(QPointF(ox + a * s, oy + v * s), QPointF(ox + b * s, oy + v * s))
        if self._wanted:                       # no room there: show where it was dropped, in red
            r = self._rect_px(self._wanted)
            p.setPen(QPen(QColor("#dc2626"), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(220, 38, 38, 40))
            p.drawRect(r)

    # ---------------- mouse and keys ----------------

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = self._to_in(event.position())
        self.sel = self._hit(x, y)
        if self.sel >= 0:
            it = self.items[self.sel]
            self._drag = {"i": self.sel, "dx": x - it[0], "dy": y - it[1], "all": copy.deepcopy(self.sheets),
                          "moved": False}
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()
        self.selection_changed.emit()

    def mouseMoveEvent(self, event):
        x, y = self._to_in(event.position())
        d = self._drag
        if d is None:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._hit(x, y) >= 0 else Qt.CursorShape.ArrowCursor)
            return
        i = d["i"]
        start = d["all"][self.cur]
        w, h = start[i][2], start[i][3]
        nx, ny = idphoto.clamp_rect(x - d["dx"], y - d["dy"], w, h, self.bounds)
        self._guides = []
        if not (event.modifiers() & Qt.KeyboardModifier.AltModifier):   # Alt = move freely
            thr = SNAP_PX / self._geom()[0]
            others = [it for k, it in enumerate(start) if k != i]
            nx, ny, self._guides = idphoto.snap_move((nx, ny, w, h), others, self.bounds, self.gap, thr)
            nx, ny = idphoto.clamp_rect(nx, ny, w, h, self.bounds)
        trial = copy.deepcopy(start)
        trial[i][0], trial[i][1] = nx, ny
        result = idphoto.resolve(trial, i, self.bounds, self.gap)   # others are pushed from their start spots
        if result is None:
            self._wanted, self._guides = (nx, ny, w, h), []
        else:
            self.sheets[self.cur] = result
            self._wanted = None
            d["moved"] = True
        self.update()

    def mouseReleaseEvent(self, event):
        d, self._drag = self._drag, None
        self._guides, self._wanted = [], None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if d and d["moved"] and d["all"] != self.sheets:
            self._remember(d["all"])
            self.changed.emit()
        self.update()

    def contextMenuEvent(self, event):
        x, y = self._to_in(QPointF(event.pos()))
        i = self._hit(x, y)
        if i >= 0:
            self.sel = i
            self.selection_changed.emit()
            self.update()
        menu = QMenu(self)
        for idx, e in enumerate(self.spec["entries"]):
            action = menu.addAction(f"Add {idphoto.size_name(e['w'], e['h'])} here")
            action.triggered.connect(lambda checked=False, k=idx: self.add_photo(k, (x, y)))
        if i >= 0:
            menu.addSeparator()
            menu.addAction("Delete this photo").triggered.connect(self.delete_selected)
        menu.exec(event.globalPos())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif event.key() == Qt.Key.Key_Z and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.undo()
        else:
            super().keyPressEvent(event)


class LayoutEditorDialog(QDialog):
    """Arrange the photos by hand. `result_layout` = (landscape, sheets) after OK."""

    def __init__(self, parent, img, spec, landscape, sheets, sig=None):
        super().__init__(parent)
        self.setWindowTitle("Arrange by hand")
        self.spec, self.landscape = spec, landscape
        self.result_layout = None

        root = QHBoxLayout(self)
        root.setSpacing(16)
        self.canvas = LayoutCanvas(img, spec, landscape, sheets, sig)
        root.addWidget(self.canvas, 1)

        side = QVBoxLayout()
        side.setSpacing(10)
        root.addLayout(side)
        title = QLabel("Arrange by hand")
        title.setObjectName("cardHeader")
        side.addWidget(title)
        hint = QLabel("Drag a photo to move it. It snaps to the photos next to it and pushes others out of the "
                      "way, so nothing overlaps. Hold Alt to move without snapping. Right-click to add a photo "
                      "right where you click.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setFixedWidth(250)
        side.addWidget(hint)

        nav = QHBoxLayout()
        self.prev_btn, self.next_btn = QPushButton("‹"), QPushButton("›")
        for b in (self.prev_btn, self.next_btn):
            b.setObjectName("secondary")
            b.setFixedWidth(40)
        self.prev_btn.clicked.connect(lambda: self._go(-1))
        self.next_btn.clicked.connect(lambda: self._go(1))
        self.sheet_label = QLabel("")
        self.sheet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.sheet_label, 1)
        nav.addWidget(self.next_btn)
        side.addLayout(nav)
        sheet_row = QHBoxLayout()
        self.add_sheet_btn = QPushButton("+ Sheet")
        self.del_sheet_btn = QPushButton("Delete sheet")
        for b in (self.add_sheet_btn, self.del_sheet_btn):
            b.setObjectName("secondary")
            sheet_row.addWidget(b)
        self.add_sheet_btn.clicked.connect(self.canvas.add_sheet)
        self.del_sheet_btn.clicked.connect(self.canvas.delete_sheet)
        self.del_sheet_btn.setToolTip("Only an empty sheet can be deleted")
        side.addLayout(sheet_row)

        add_row = QHBoxLayout()
        self.size_combo = QComboBox()
        for e in spec["entries"]:
            self.size_combo.addItem(idphoto.size_name(e["w"], e["h"]))
        add_btn = QPushButton("Add photo")
        add_btn.setObjectName("secondary")
        add_btn.clicked.connect(lambda: self.canvas.add_photo(self.size_combo.currentIndex()))
        add_row.addWidget(self.size_combo, 1)
        add_row.addWidget(add_btn)
        side.addLayout(add_row)

        edit_row = QHBoxLayout()
        self.del_btn = QPushButton("Delete photo")
        self.undo_btn = QPushButton("Undo")
        for b in (self.del_btn, self.undo_btn):
            b.setObjectName("secondary")
            edit_row.addWidget(b)
        self.del_btn.clicked.connect(self.canvas.delete_selected)
        self.undo_btn.clicked.connect(self.canvas.undo)
        side.addLayout(edit_row)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setFixedWidth(250)
        side.addWidget(self.info)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.note.setFixedWidth(250)
        side.addWidget(self.note)
        side.addStretch()

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("Use this layout")
        self.ok_btn.setObjectName("cta")
        self.ok_btn.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_btn)
        side.addLayout(buttons)

        self.canvas.changed.connect(self._refresh)
        self.canvas.selection_changed.connect(self._refresh)
        self.canvas.status.connect(self.note.setText)
        self.resize(940, 780)
        self._refresh()

    def _go(self, step):
        self.canvas.set_sheet(self.canvas.cur + step)
        self._refresh()

    def _refresh(self):
        c = self.canvas
        n = len(c.sheets)
        self.sheet_label.setText(f"Sheet {c.cur + 1} of {n} · {len(c.items)} photo(s)")
        self.prev_btn.setEnabled(c.cur > 0)
        self.next_btn.setEnabled(c.cur < n - 1)
        self.del_sheet_btn.setEnabled(n > 1 and not c.items)
        self.del_btn.setEnabled(0 <= c.sel < len(c.items))
        self.undo_btn.setEnabled(c.can_undo())
        counts = c.counts()
        self.info.setText("\n".join(
            f"{k} × {idphoto.size_name(e['w'], e['h'])}" for k, e in zip(counts, self.spec["entries"]) if k))
        self.ok_btn.setEnabled(sum(counts) > 0)
        if self.note.text() and self.sender() is c:
            self.note.setText("")

    def _accept(self):
        self.result_layout = (self.landscape, self.canvas.result())
        self.accept()