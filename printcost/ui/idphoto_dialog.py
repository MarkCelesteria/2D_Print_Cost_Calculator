import math
import os
import re
import time
from types import SimpleNamespace

from PIL import Image

from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QSize, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QLineEdit, QPushButton, QFileDialog, QMessageBox, QWidget, QDialogButtonBox, QApplication,
    QSizePolicy, QFrame, QScrollArea, QSlider, QInputDialog, QTabWidget, QMenu,
)

from printcost.core import idphoto
from printcost.core import pricing
from printcost.core.analyzer import IMAGE_EXTS, load_image_rgb, make_temp_dir
from printcost.ui.icons import make_icon
from printcost.ui.idphoto_editor import LayoutEditorDialog
from printcost.ui.photo_edit_dialog import FitWidthScroll, PhotoEditDialog
from printcost.ui.notes import label_style
from printcost.ui.preview_widget import pil_to_qpixmap
from printcost.ui.style import BORDER, MUTED, PREVIEW_BG, PRIMARY, MAGENTA

PRICING_MODES = [
    ("Calculated (ink + paper per sheet)", "calculated"),
    ("Flat price for the whole job", "flat"),
    ("Fixed price per sheet", "per_sheet"),
    ("Fixed price per photo", "per_photo"),
]
UNITS = [("in", 1.0), ("cm", 1 / 2.54), ("mm", 1 / 25.4)]
ORIENTATIONS = [("Auto (fewest sheets)", "auto"), ("Portrait", "portrait"), ("Landscape", "landscape")]
PICK_FILTER = "Pictures (" + " ".join(f"*{e}" for e in IMAGE_EXTS) + ")"
THUMB_PX = 1600


def keep_path(kind, name, days=30):
    folder = os.path.join(pricing._app_data_dir(), kind)
    os.makedirs(folder, exist_ok=True)
    cutoff = time.time() - days * 86400
    for f in os.listdir(folder):
        p = os.path.join(folder, f)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass
    return os.path.join(folder, name)


# ---------------- inputs that ignore the mouse wheel unless clicked into ----------------
# (so scrolling the form never changes a margin, a quantity or a paper size by accident)

class NoWheelSpin(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        super().wheelEvent(event) if self.hasFocus() else event.ignore()


class NoWheelDouble(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        super().wheelEvent(event) if self.hasFocus() else event.ignore()


class NoWheelSlider(QSlider):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        super().wheelEvent(event) if self.hasFocus() else event.ignore()


class NoWheelCombo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # size to a few characters, not to the longest item, so the form never gets wider than its column
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)

    def wheelEvent(self, event):
        super().wheelEvent(event) if self.hasFocus() else event.ignore()


# ---------------- sheet preview ----------------

class SheetPreview(QWidget):
    """One printed sheet, drawn to scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pix = None

    def set_image(self, pil_img):
        self._pix = pil_to_qpixmap(pil_img) if pil_img is not None else None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PREVIEW_BG))
        if self._pix is None:
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pad = 12
        s = min((self.width() - 2 * pad) / self._pix.width(), (self.height() - 2 * pad) / self._pix.height())
        w, h = self._pix.width() * s, self._pix.height() * s
        rect = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        p.drawPixmap(rect, self._pix, QRectF(self._pix.rect()))
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)


# ---------------- positioning the face ----------------

class PositionCanvas(QWidget):
    """The picture with a box that has the photo's shape. Drag the box; the wheel or the slider zooms."""
    moved = pyqtSignal()

    def __init__(self, pil_img, aspect, focus):
        super().__init__()
        self.setMinimumSize(460, 420)
        self.pix = pil_to_qpixmap(pil_img)
        self.aspect = aspect
        self.focus = tuple(focus)
        self._drag = None
        self._normalise()

    def _box(self):
        return idphoto.focus_box(self.pix.width(), self.pix.height(), self.aspect, self.focus)

    def _normalise(self):
        """Store the zoom and centre the box really has (after clamping to the picture)."""
        x0, y0, x1, y1 = self._box()
        W, H = self.pix.width(), self.pix.height()
        self.focus = ((x0 + x1) / 2 / W, (y0 + y1) / 2 / H, (y1 - y0) / H)

    def set_zoom(self, hf):
        self.focus = (self.focus[0], self.focus[1], hf)
        self._normalise()
        self.update()
        self.moved.emit()

    def _img_rect(self):
        pad = 12
        s = min((self.width() - 2 * pad) / self.pix.width(), (self.height() - 2 * pad) / self.pix.height())
        w, h = self.pix.width() * s, self.pix.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _box_rect(self):
        R = self._img_rect()
        x0, y0, x1, y1 = self._box()
        k = R.width() / self.pix.width()
        return QRectF(R.x() + x0 * k, R.y() + y0 * k, (x1 - x0) * k, (y1 - y0) * k)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(PREVIEW_BG))
        R = self._img_rect()
        p.drawPixmap(R, self.pix, QRectF(self.pix.rect()))
        box = self._box_rect()
        dim = QColor(0, 0, 0, 130)
        p.fillRect(QRectF(R.left(), R.top(), R.width(), box.top() - R.top()), dim)
        p.fillRect(QRectF(R.left(), box.bottom(), R.width(), R.bottom() - box.bottom()), dim)
        p.fillRect(QRectF(R.left(), box.top(), box.left() - R.left(), box.height()), dim)
        p.fillRect(QRectF(box.right(), box.top(), R.right() - box.right(), box.height()), dim)
        p.setPen(QPen(QColor("white"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box)
        guide = QPen(QColor(255, 255, 255, 190), 1.5, Qt.PenStyle.DashLine)  # face guide
        p.setPen(guide)
        gw, gh = box.width() * 0.52, box.height() * 0.66
        p.drawEllipse(QRectF(box.center().x() - gw / 2, box.top() + box.height() * 0.12, gw, gh))
        p.setPen(QPen(QColor(MAGENTA), 1))
        p.drawLine(QPointF(box.center().x(), box.top()), QPointF(box.center().x(), box.top() + 8))

    def mousePressEvent(self, event):
        if self._box_rect().contains(event.position()):
            self._drag = (event.position(), self.focus)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event):
        if self._drag is None:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._box_rect().contains(event.position())
                           else Qt.CursorShape.ArrowCursor)
            return
        start, f0 = self._drag
        R = self._img_rect()
        dx = (event.position().x() - start.x()) / R.width()
        dy = (event.position().y() - start.y()) / R.height()
        self.focus = (f0[0] + dx, f0[1] + dy, f0[2])
        self._normalise()
        self.update()
        self.moved.emit()

    def wheelEvent(self, event):
        step = 0.04 if event.angleDelta().y() < 0 else -0.04   # wheel up = zoom in = smaller box
        self.set_zoom(min(1.0, max(0.1, self.focus[2] + step)))


class PositionDialog(QDialog):
    def __init__(self, parent, pil_img, aspect, focus):
        super().__init__(parent)
        self.setWindowTitle("Position the face")
        self.result_focus = None
        lay = QVBoxLayout(self)
        hint = QLabel("Drag the box over the face. Use the slider or the mouse wheel to zoom. "
                      "The dashed oval is only a guide: check the head-size rule for your ID.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.canvas = PositionCanvas(pil_img, aspect, focus)
        lay.addWidget(self.canvas, 1)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(10, 100)
        self.slider.setInvertedAppearance(True)   # right = closer
        self.slider.setValue(round(self.canvas.focus[2] * 100))
        self.slider.valueChanged.connect(lambda v: self._slider_moved(v))
        self.canvas.moved.connect(self._sync_slider)
        zoom_row.addWidget(self.slider, 1)
        lay.addLayout(zoom_row)
        row = QHBoxLayout()
        reset = QPushButton("Reset")
        reset.setObjectName("secondary")
        reset.clicked.connect(self._reset)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Use this position")
        ok.setObjectName("primary")
        ok.clicked.connect(self._apply)
        row.addWidget(reset)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.resize(660, 640)

    def _slider_moved(self, v):
        self.canvas.set_zoom(v / 100)

    def _sync_slider(self):
        self.slider.blockSignals(True)
        self.slider.setValue(round(self.canvas.focus[2] * 100))
        self.slider.blockSignals(False)

    def _reset(self):
        self.canvas.focus = (0.5, 0.5, 1.0)
        self.canvas._normalise()
        self.canvas.update()
        self._sync_slider()

    def _apply(self):
        self.result_focus = self.canvas.focus
        self.accept()


# ---------------- one row = one size and how many ----------------

class EntryRow(QFrame):
    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)
    max_requested = pyqtSignal(object)

    def __init__(self, entry):
        super().__init__()
        self.setObjectName("sub")
        self._unit = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.combo = NoWheelCombo()
        self.combo.addItems([n for n, _ in idphoto.SIZES] + [idphoto.CUSTOM])
        top.addWidget(self.combo, 1)
        self.qty = NoWheelSpin()
        self.qty.setRange(1, idphoto.MAX_QTY)
        self.qty.setFixedWidth(70)
        self.qty.setToolTip("How many photos of this size")
        top.addWidget(self.qty)
        self.max_btn = QPushButton("Max")
        self.max_btn.setObjectName("secondary")
        self.max_btn.setFixedWidth(46)
        self.max_btn.setStyleSheet("padding: 6px 0;")
        self.max_btn.setToolTip("Fill one sheet with this size")
        self.max_btn.clicked.connect(lambda: self.max_requested.emit(self))
        top.addWidget(self.max_btn)
        self.del_btn = QPushButton()
        self.del_btn.setIcon(make_icon("trash"))
        self.del_btn.setIconSize(QSize(14, 14))
        self.del_btn.setFixedWidth(34)
        self.del_btn.setStyleSheet("padding: 6px 0;")
        self.del_btn.setToolTip("Remove this size")
        self.del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(self.del_btn)
        box.addLayout(top)

        self.custom = QWidget()
        self.custom.setObjectName("customSize")
        self.custom.setStyleSheet("QWidget#customSize { background: transparent; }")
        cl = QHBoxLayout(self.custom)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.w_spin, self.h_spin = self._spin(), self._spin()
        self.unit_combo = NoWheelCombo()
        self.unit_combo.addItems([u for u, _ in UNITS])
        cl.addWidget(self.w_spin, 1)
        cl.addWidget(QLabel("×"))
        cl.addWidget(self.h_spin, 1)
        cl.addWidget(self.unit_combo)
        box.addWidget(self.custom)

        self.sig_check = QCheckBox("Signature / name inside the photo")
        self.sig_check.setToolTip("The photo keeps its exact size. The bottom part becomes a white strip for the\n"
                                  "signature and name, and the face is framed a little higher.")
        box.addWidget(self.sig_check)

        self.set_entry(entry)
        self.combo.currentIndexChanged.connect(self._combo_changed)
        self.unit_combo.currentIndexChanged.connect(self._unit_changed)
        self.qty.valueChanged.connect(self.changed)
        self.w_spin.valueChanged.connect(self.changed)
        self.h_spin.valueChanged.connect(self.changed)
        self.sig_check.toggled.connect(self.changed)

    @staticmethod
    def _spin():
        sb = NoWheelDouble()
        sb.setRange(0.1, 500)
        sb.setDecimals(2)
        sb.setSingleStep(0.1)
        return sb

    def _factor(self):
        return UNITS[self._unit][1]

    def set_entry(self, e):
        for w in (self.combo, self.qty, self.w_spin, self.h_spin, self.sig_check):
            w.blockSignals(True)
        self.combo.setCurrentText(e["name"] if e["name"] in idphoto.SIZE_MAP else idphoto.CUSTOM)
        self.w_spin.setValue(e["w"] / self._factor())
        self.h_spin.setValue(e["h"] / self._factor())
        self.qty.setValue(e["qty"])
        self.sig_check.setChecked(bool(e.get("sig")))
        for w in (self.combo, self.qty, self.w_spin, self.h_spin, self.sig_check):
            w.blockSignals(False)
        self.custom.setVisible(self.combo.currentText() == idphoto.CUSTOM)

    def _combo_changed(self):
        name = self.combo.currentText()
        if name in idphoto.SIZE_MAP:  # keep the boxes in step so "Custom" starts from the last preset
            w, h = idphoto.SIZE_MAP[name]
            self.w_spin.blockSignals(True)
            self.h_spin.blockSignals(True)
            self.w_spin.setValue(w / self._factor())
            self.h_spin.setValue(h / self._factor())
            self.w_spin.blockSignals(False)
            self.h_spin.blockSignals(False)
        self.custom.setVisible(name == idphoto.CUSTOM)
        self.changed.emit()

    def _unit_changed(self, index):
        """Another unit for the custom boxes: the size they hold stays the same."""
        w, h = self.w_spin.value() * self._factor(), self.h_spin.value() * self._factor()
        self._unit = index
        for sb, v in ((self.w_spin, w), (self.h_spin, h)):
            sb.blockSignals(True)
            sb.setValue(v / self._factor())
            sb.blockSignals(False)

    def entry(self):
        name, sig = self.combo.currentText(), self.sig_check.isChecked()
        if name in idphoto.SIZE_MAP:
            return idphoto.make_entry(name, self.qty.value(), sig=sig)
        w, h = self.w_spin.value() * self._factor(), self.h_spin.value() * self._factor()
        return idphoto.make_entry(idphoto.size_name(w, h), self.qty.value(), w, h, sig=sig)


# ---------------- the dialog ----------------

class IdPhotoDialog(QDialog):
    """Pick a photo, say which sizes and how many; builds sheets that print at the real size."""

    def __init__(self, parent, path, settings, paper, job=None):
        super().__init__(parent)
        self.setWindowTitle("ID photos")
        self.settings = settings
        self.paper_sizes = settings["paper_sizes"]
        self.result_data = None
        self.src = None            # the picture (None when it couldn't be opened; the tab checks this)
        self.img = None            # small copy for the preview
        self.full_size = (1, 1)
        self.path = path
        self.rows = []
        self.sig_img = None        # the signature (ink on white), or None
        self.sig_path = None
        self.manual = (job or {}).get("spec", {}).get("manual")   # a layout arranged by hand, or None
        self._manual_key = None
        self._manual_dropped = False
        self.sheet_index = 0
        self._plan = None
        self._busy = True

        saved = settings.get("idphoto") or {}
        remembered = dict(idphoto.DEFAULTS)
        remembered.update(pricing_mode=saved.get("pricing_mode", "calculated"), fee=saved.get("fee", 0.0))
        if job:
            remembered.update(job["spec"])
            remembered.update(pricing_mode=job["pricing"]["mode"], fee=job["pricing"]["fee"])
        self.focus = tuple(remembered.get("focus") or idphoto.DEFAULTS["focus"])

        root = QHBoxLayout(self)
        root.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(10)
        root.addLayout(left, 0)
        right = QVBoxLayout()
        root.addLayout(right, 1)

        form_scroll = FitWidthScroll()       # everything in one scrolling column (never slides sideways)
        form_scroll.setWidgetResizable(True)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        form_scroll.setFixedWidth(390)
        form_holder = QWidget()
        body = QVBoxLayout(form_holder)
        body.setContentsMargins(4, 4, 10, 4)
        body.setSpacing(12)
        form_scroll.setWidget(form_holder)
        left.addWidget(form_scroll, 1)

        def page(title):
            """A heading with a thin line, then a layout for that section's controls."""
            head = QLabel(title)
            head.setObjectName("cardHeader")
            head.setContentsMargins(0, 18 if body.count() else 0, 0, 0)
            body.addWidget(head)
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            body.addWidget(line)
            v = QVBoxLayout()
            v.setSpacing(12)
            body.addLayout(v)
            return v

        # ---- tab 1: photos ----
        v = page("Photos")
        src_row = QHBoxLayout()
        self.source_label = QLabel("")
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.source_label.setMinimumWidth(0)
        change = QPushButton("Change…")
        change.setObjectName("secondary")
        change.clicked.connect(self._change_source)
        src_row.addWidget(self.source_label, 1)
        src_row.addWidget(change)
        v.addLayout(src_row)
        self.edit_btn = QPushButton("Edit photo…")
        self.edit_btn.setObjectName("secondary")
        self.edit_btn.setToolTip("Remove the background, change its color, fix the lighting")
        self.edit_btn.clicked.connect(self._edit_photo)
        v.addWidget(self.edit_btn)

        pos_row = QHBoxLayout()
        self.pos_btn = QPushButton("Position the face…")
        self.pos_btn.setObjectName("secondary")
        self.pos_btn.setToolTip("Choose which part of the picture is used, and how close")
        self.pos_btn.clicked.connect(self._position)
        self.pos_reset = QPushButton("Reset")
        self.pos_reset.setObjectName("secondary")
        self.pos_reset.clicked.connect(lambda: self._set_focus(idphoto.DEFAULTS["focus"]))
        pos_row.addWidget(self.pos_btn, 1)
        pos_row.addWidget(self.pos_reset)
        v.addLayout(pos_row)

        set_row = QHBoxLayout()
        set_row.setSpacing(6)
        set_row.addWidget(QLabel("Quick set"))
        self.set_combo = NoWheelCombo()
        set_row.addWidget(self.set_combo, 1)
        self.save_set_btn = QPushButton("Save")
        self.save_set_btn.setObjectName("secondary")
        self.save_set_btn.setToolTip("Remember the sizes below as a quick set")
        self.save_set_btn.clicked.connect(self._save_set)
        self.del_set_btn = QPushButton("Delete")
        self.del_set_btn.setObjectName("secondary")
        self.del_set_btn.setToolTip("Delete one of your saved sets")
        self.del_set_btn.clicked.connect(self._delete_set)
        set_row.addWidget(self.save_set_btn)
        set_row.addWidget(self.del_set_btn)
        v.addLayout(set_row)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(10)
        v.addLayout(self.rows_box)
        self.add_btn = QPushButton("+ Add another size")
        self.add_btn.setObjectName("secondary")
        self.add_btn.clicked.connect(lambda: self._add_row())
        v.addWidget(self.add_btn)

        # ---- tab 2: sheet ----
        v = page("Sheet")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnMinimumWidth(0, 90)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Paper"), 0, 0)
        self.paper_combo = NoWheelCombo()
        self.paper_combo.addItems([p["name"] for p in self.paper_sizes])
        want = (job["spec"]["paper"]["name"] if job else (paper or {}).get("name"))
        if want in [p["name"] for p in self.paper_sizes]:
            self.paper_combo.setCurrentText(want)
        grid.addWidget(self.paper_combo, 0, 1)
        grid.addWidget(QLabel("Sheets"), 1, 0)
        self.orient_combo = NoWheelCombo()
        for text, key in ORIENTATIONS:
            self.orient_combo.addItem(text, key)
        self.orient_combo.setCurrentIndex(max(0, self.orient_combo.findData(remembered["orientation"])))
        grid.addWidget(self.orient_combo, 1, 1)
        grid.addWidget(QLabel("Gap (in)"), 2, 0)
        self.gap_spin = self._inch_spin(remembered["gap_in"], 1.0)
        self.gap_spin.setToolTip("White space between photos, for cutting.")
        grid.addWidget(self.gap_spin, 2, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(QLabel("Margin (in)"), 3, 0)
        self.margin_spin = self._inch_spin(remembered["margin_in"], 2.0)
        self.margin_spin.setToolTip("White border your printer can't print on.")
        grid.addWidget(self.margin_spin, 3, 1, Qt.AlignmentFlag.AlignLeft)
        v.addLayout(grid)

        self.lines_check = QCheckBox("Light cutting lines between photos")
        self.lines_check.setChecked(bool(remembered["lines"]))
        v.addWidget(self.lines_check)
        self.easy_check = QCheckBox("Easy cutting (straight cuts across the sheet)")
        self.easy_check.setToolTip("Lays photos in full-width bands so a paper cutter can make every cut edge to edge.\n"
                                   "With a gap of 0, small photos fill the exact space of a bigger one.")
        self.easy_check.setChecked(bool(remembered["easy"]))
        v.addWidget(self.easy_check)

        hand_row = QHBoxLayout()
        self.hand_btn = QPushButton("Arrange by hand…")
        self.hand_btn.setObjectName("secondary")
        self.hand_btn.setToolTip("Drag the photos where you want them. They can't overlap and snap to each other.")
        self.hand_btn.clicked.connect(self._arrange_by_hand)
        self.auto_btn = QPushButton("Automatic")
        self.auto_btn.setObjectName("secondary")
        self.auto_btn.setToolTip("Throw away the hand layout and arrange the photos automatically")
        self.auto_btn.clicked.connect(self._auto_layout)
        hand_row.addWidget(self.hand_btn, 1)
        hand_row.addWidget(self.auto_btn)
        v.addLayout(hand_row)
        self.hand_label = QLabel("")
        self.hand_label.setObjectName("muted")
        self.hand_label.setWordWrap(True)
        v.addWidget(self.hand_label)

        # ---- tab 3: signature ----
        v = page("Signature")
        sig_row = QHBoxLayout()
        sig_row.setSpacing(8)
        self.sig_thumb = QLabel("None")
        self.sig_thumb.setObjectName("sigThumb")
        self.sig_thumb.setStyleSheet(f"QLabel#sigThumb {{ background: white; border: 1px solid {BORDER}; "
                                     f"border-radius: 6px; color: {MUTED}; }}")
        self.sig_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sig_thumb.setFixedSize(170, 56)
        sig_row.addWidget(self.sig_thumb)
        self.sig_btn = QPushButton("Import…")
        self.sig_btn.setObjectName("secondary")
        self.sig_btn.setToolTip("A picture or scan of your signature. Dark ink on white paper, or a transparent PNG.")
        self.sig_btn.clicked.connect(self._import_signature)
        self.sig_clear = QPushButton("Clear")
        self.sig_clear.setObjectName("secondary")
        self.sig_clear.clicked.connect(self._clear_signature)
        sig_row.addWidget(self.sig_btn)
        sig_row.addWidget(self.sig_clear)
        sig_row.addStretch()
        v.addLayout(sig_row)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnMinimumWidth(0, 90)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Name"), 0, 0)
        self.sig_name = QLineEdit()
        self.sig_name.setPlaceholderText("Printed under the signature (optional)")
        self.sig_name.setText(remembered.get("sig_name", ""))
        grid.addWidget(self.sig_name, 0, 1)
        grid.addWidget(QLabel("Strip height"), 1, 0)
        strip = QHBoxLayout()
        self.sig_pct_spin = NoWheelDouble()
        self.sig_pct_spin.setRange(8, 60)
        self.sig_pct_spin.setDecimals(0)
        self.sig_pct_spin.setSuffix(" %")
        self.sig_pct_spin.setValue(float(remembered["sig_pct"]))
        self.sig_pct_spin.setMaximumWidth(110)
        self.sig_pct_spin.setToolTip("Height of the white strip, as a share of the photo height")
        strip.addWidget(self.sig_pct_spin)
        strip.addStretch()
        grid.addLayout(strip, 1, 1)
        self.sig_size = self._sig_slider(grid, 2, "Signature size", 30, 250, remembered.get("sig_size", 100), "{} %")
        self.sig_dy = self._sig_slider(grid, 3, "Up / down", -100, 100, remembered.get("sig_dy", 0), "{:+d} %")
        self.sig_dx = self._sig_slider(grid, 4, "Left / right", -100, 100, remembered.get("sig_dx", 0), "{:+d} %")
        self.name_size = self._sig_slider(grid, 5, "Name size", 50, 200, remembered.get("name_size", 100), "{} %")
        v.addLayout(grid)
        sig_hint = QLabel("Then tick “Signature / name inside the photo” on each size that needs it, in the "
                          "Photos tab. The photo keeps its exact size; the face is framed higher.")
        sig_hint.setObjectName("muted")
        sig_hint.setWordWrap(True)
        v.addWidget(sig_hint)

        # ---- tab 4: price ----
        v = page("Price")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnMinimumWidth(0, 90)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Pricing"), 0, 0)
        self.pricing_combo = NoWheelCombo()
        for text, key in PRICING_MODES:
            self.pricing_combo.addItem(text, key)
        self.pricing_combo.setCurrentIndex(max(0, self.pricing_combo.findData(remembered["pricing_mode"])))
        grid.addWidget(self.pricing_combo, 0, 1)
        self.fee_label = QLabel("Price")
        grid.addWidget(self.fee_label, 1, 0)
        self.fee_spin = NoWheelDouble()
        self.fee_spin.setRange(0, 1_000_000)
        self.fee_spin.setDecimals(2)
        self.fee_spin.setValue(float(remembered["fee"] or 0))
        self.fee_spin.setMaximumWidth(130)
        grid.addWidget(self.fee_spin, 1, 1, Qt.AlignmentFlag.AlignLeft)
        v.addLayout(grid)
        self.fee_hint = QLabel("")
        self.fee_hint.setObjectName("muted")
        self.fee_hint.setWordWrap(True)
        v.addWidget(self.fee_hint)

        body.addStretch()

        # ---- under the form: always visible ----
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-weight: 700;")
        left.addWidget(self.summary_label)
        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        left.addWidget(self.note_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.export_btn = QPushButton("Export image")
        self.export_btn.setObjectName("secondary")
        self.export_btn.setToolTip("Save the ID photo or the print sheets as picture files (PNG or JPG, 300 dpi)")
        self.export_menu = QMenu(self.export_btn)
        self.export_menu.aboutToShow.connect(self._fill_export_menu)
        self.export_btn.setMenu(self.export_menu)
        self.buttons.addButton(self.export_btn, QDialogButtonBox.ButtonRole.ActionRole)
        self.create_btn = QPushButton("Create ID sheets")
        self.create_btn.setObjectName("cta")
        self.buttons.addButton(self.create_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        left.addWidget(self.buttons)

        self.preview = SheetPreview()
        right.addWidget(self.preview, 1)
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("‹")
        self.next_btn = QPushButton("›")
        for b in (self.prev_btn, self.next_btn):
            b.setObjectName("secondary")
            b.setFixedWidth(40)
        self.prev_btn.clicked.connect(lambda: self._go(-1))
        self.next_btn.clicked.connect(lambda: self._go(1))
        self.sheet_label = QLabel("")
        self.sheet_label.setObjectName("muted")
        self.sheet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.sheet_label, 1)
        nav.addWidget(self.next_btn)
        right.addLayout(nav)

        self._fill_sets()
        self._load_job_signature(job)
        entries = remembered.get("entries") or idphoto.DEFAULTS["entries"]
        for e in entries[:idphoto.MAX_ROWS]:
            self._add_row(dict(e), refresh=False)
        self.resize(780, 700)

        if not self._set_source(path):
            self.src = None
            return

        for w in (self.paper_combo, self.orient_combo, self.pricing_combo):
            w.currentIndexChanged.connect(self._refresh)
        self.set_combo.activated.connect(self._set_chosen)
        for w in (self.gap_spin, self.margin_spin, self.fee_spin):
            w.valueChanged.connect(self._refresh)
        self.lines_check.toggled.connect(self._refresh)
        self.easy_check.toggled.connect(self._refresh)
        self.sig_pct_spin.valueChanged.connect(self._refresh)
        for s in (self.sig_size, self.sig_dy, self.sig_dx, self.name_size):
            s.valueChanged.connect(self._refresh)
        self.sig_name.textChanged.connect(self._refresh)
        self._manual_key = self._geom_key()
        self._busy = False
        self._refresh()
        if job is None:
            QTimer.singleShot(0, self._position)

    # ---------------- helpers ----------------

    @staticmethod
    def _inch_spin(value, maximum):
        sb = NoWheelDouble()
        sb.setRange(0.0, maximum)
        sb.setDecimals(2)
        sb.setSingleStep(0.05)
        sb.setValue(float(value))
        sb.setMaximumWidth(110)
        return sb

    def _sig_slider(self, grid, row, label, lo, hi, value, fmt):
        grid.addWidget(QLabel(label), row, 0)
        line = QHBoxLayout()
        s = NoWheelSlider()
        s.setRange(lo, hi)
        s.setValue(int(value))
        val = QLabel(fmt.format(int(value)))
        val.setObjectName("muted")
        val.setFixedWidth(46)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        s.valueChanged.connect(lambda x: val.setText(fmt.format(x)))
        line.addWidget(s, 1)
        line.addWidget(val)
        grid.addLayout(line, row, 1)
        return s

    def _paper(self):
        return next(p for p in self.paper_sizes if p["name"] == self.paper_combo.currentText())

    def _spec(self):
        p = self._paper()
        entries = [r.entry() for r in self.rows]
        uses_sig = self.sig_img is not None and any(e["sig"] for e in entries)
        return {
            "entries": entries,
            "orientation": self.orient_combo.currentData(),
            "gap_in": self.gap_spin.value(), "margin_in": self.margin_spin.value(),
            "lines": self.lines_check.isChecked(),
            "easy": self.easy_check.isChecked(),
            "sig_pct": self.sig_pct_spin.value(),
            "sig_size": self.sig_size.value(), "sig_dy": self.sig_dy.value(),
            "sig_dx": self.sig_dx.value(), "name_size": self.name_size.value(),
            "sig_name": self.sig_name.text().strip(),
            "signature_path": self.sig_path if uses_sig else None,
            "focus": self.focus,
            "manual": self.manual,
            "paper": {"name": p["name"], "width_in": p["width_in"], "height_in": p["height_in"]},
        }

    def _set_source(self, path, edited=False):
        try:
            img = load_image_rgb(path, keep_alpha=True)
        except Exception as e:
            QMessageBox.warning(self, "Can't use this file", str(e))
            return False
        self.full_size = img.size
        thumb = img.copy()
        thumb.thumbnail((THUMB_PX, THUMB_PX))
        self.img, self.src, self.path = thumb, img, path
        if not edited:
            self.orig_path, self.edit_state = path, None
        name = os.path.basename(self.orig_path) + (" (edited)" if edited else "")
        self.source_label.setText(self.source_label.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideMiddle, 220))
        self.source_label.setToolTip(name)
        return True

    def done(self, code):
        self.src = None
        super().done(code)

    # ---------------- rows ----------------

    def _add_row(self, entry=None, refresh=True):
        if len(self.rows) >= idphoto.MAX_ROWS:
            return
        if entry is None:
            used = {r.entry()["name"] for r in self.rows}
            name = next((n for n, _ in idphoto.SIZES if n not in used), idphoto.SIZES[0][0])
            entry = idphoto.make_entry(name, 4)
        row = EntryRow(entry)
        row.changed.connect(self._refresh)
        row.remove_requested.connect(self._remove_row)
        row.max_requested.connect(self._max_row)
        self.rows.append(row)
        self.rows_box.addWidget(row)
        self._update_row_buttons()
        if refresh:
            self._refresh()

    def _remove_row(self, row):
        if len(self.rows) <= 1:
            return
        self.rows.remove(row)
        self.rows_box.removeWidget(row)
        row.deleteLater()
        self._update_row_buttons()
        self._refresh()

    def _update_row_buttons(self):
        for r in self.rows:
            r.del_btn.setEnabled(len(self.rows) > 1)
        self.add_btn.setEnabled(len(self.rows) < idphoto.MAX_ROWS)

    def _max_row(self, row):
        spec = self._spec()
        n = idphoto.capacity(spec, self.rows.index(row))
        if n:
            row.qty.setValue(min(n, idphoto.MAX_QTY))

    def _replace_rows(self, entries):
        self._busy = True
        for r in self.rows:
            self.rows_box.removeWidget(r)
            r.deleteLater()
        self.rows = []
        for e in entries[:idphoto.MAX_ROWS]:
            self._add_row(dict(e), refresh=False)
        self._busy = False
        self._refresh()

    # ---------------- quick sets ----------------

    def _saved_sets(self):
        return (self.settings.get("idphoto") or {}).get("sets", {})

    def _fill_sets(self):
        self.set_combo.clear()
        self.set_combo.addItem("Choose a set…", None)
        for name, items in idphoto.SETS:
            self.set_combo.addItem(name, [idphoto.make_entry(n, q) for n, q in items])
        for name, entries in self._saved_sets().items():
            self.set_combo.addItem(f"★ {name}", [dict(e) for e in entries])
        self.del_set_btn.setEnabled(bool(self._saved_sets()))

    def _set_chosen(self, index):
        entries = self.set_combo.itemData(index)
        if entries:
            self._replace_rows(entries)
        self.set_combo.setCurrentIndex(0)

    def _save_set(self):
        name, ok = QInputDialog.getText(self, "Save set", "Name for this set:")
        name = name.strip()
        if not ok or not name:
            return
        store = self.settings.setdefault("idphoto", {})
        store.setdefault("sets", {})[name] = [r.entry() for r in self.rows]
        self._persist()
        self._fill_sets()

    def _delete_set(self):
        names = list(self._saved_sets())
        if not names:
            return
        name, ok = QInputDialog.getItem(self, "Delete set", "Which saved set?", names, 0, False)
        if ok and name:
            del self.settings["idphoto"]["sets"][name]
            self._persist()
            self._fill_sets()

    def _persist(self):
        try:
            pricing.save_settings(self.settings)
        except OSError:
            pass

    # ---------------- signature ----------------

    def _load_job_signature(self, job):
        """Only an ID sheet being edited brings its signature back; a new one starts without."""
        path = ((job or {}).get("spec") or {}).get("signature_path")
        img = idphoto.load_signature(path) if path and os.path.exists(path) else None
        self._set_signature(img, path if img is not None else None)

    def _set_signature(self, img, path):
        self.sig_img, self.sig_path = img, path
        if img is None:
            self.sig_thumb.setPixmap(QPixmap())
            self.sig_thumb.setText("None")
        else:
            self.sig_thumb.setPixmap(pil_to_qpixmap(img).scaled(
                142, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.sig_clear.setEnabled(img is not None)

    def _import_signature(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose your signature", "", PICK_FILTER)
        if not path:
            return
        try:
            with Image.open(path) as im:
                sig = idphoto.prepare_signature(im)
            saved = idphoto.save_signature(sig, keep_path("signatures", f"signature-{int(time.time())}.png"))
        except Exception as e:
            QMessageBox.warning(self, "Can't use this signature", str(e))
            return
        self._set_signature(sig, saved)
        self._refresh()

    def _clear_signature(self):
        self._set_signature(None, None)
        self._refresh()

    # ---------------- picture ----------------

    def _change_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a photo", os.path.dirname(self.orig_path), PICK_FILTER)
        if path and self._set_source(path):
            self._reset_options()
            self._position()

    def _crop_box(self, size, spec):
        """The part of the picture the photos actually use: the union of every size's crop at the current
        position, plus a little air. Editing only this part keeps the lighting judged on the head."""
        W, H = size
        boxes = [idphoto.focus_box(W, H, idphoto.photo_aspect(e, spec), self.focus) for e in spec["entries"]]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        px, py = 0.04 * (x1 - x0), 0.04 * (y1 - y0)
        return (max(0, math.floor(x0 - px)), max(0, math.floor(y0 - py)),
                min(W, math.ceil(x1 + px)), min(H, math.ceil(y1 + py)))

    @staticmethod
    def _focus_into_crop(focus, size, box, aspect):
        """The same crop, described relative to the cut-out picture instead of the whole one."""
        b = idphoto.focus_box(size[0], size[1], aspect, focus)
        bw, bh = box[2] - box[0], box[3] - box[1]
        return ((((b[0] + b[2]) / 2 - box[0]) / bw), (((b[1] + b[3]) / 2 - box[1]) / bh),
                min(1.0, (b[3] - b[1]) / bh))

    @staticmethod
    def _focus_out_of_crop(focus, size, box, crop_size, aspect):
        """Inverse of _focus_into_crop: where the current crop sits in the original picture."""
        b = idphoto.focus_box(crop_size[0], crop_size[1], aspect, focus)
        bw, bh = box[2] - box[0], box[3] - box[1]
        fx0, fx1, fy0, fy1 = b[0] / crop_size[0], b[2] / crop_size[0], b[1] / crop_size[1], b[3] / crop_size[1]
        ox0, ox1 = box[0] + fx0 * bw, box[0] + fx1 * bw
        oy0, oy1 = box[1] + fy0 * bh, box[1] + fy1 * bh
        return ((ox0 + ox1) / 2 / size[0], (oy0 + oy1) / 2 / size[1], min(1.0, (oy1 - oy0) / size[1]))

    def _ask_edit_resolution(self, box):
        """Asks how big the edited picture should be. Returns (ok, max_px); max_px None = keep the original size."""
        bw, bh = box[2] - box[0], box[3] - box[1]
        total = bw * bh
        big = " (slow, needs a lot of memory)" if total > 24e6 else ""
        options = [(f"Original size, {bw} × {bh} px ({total / 1e6:.1f} MP){big}", None)]
        for name, mp in (("High", 16), ("Medium", 8), ("Low (fastest)", 4)):
            if mp * 1e6 < total * 0.97:
                k = math.sqrt(mp * 1e6 / total)
                options.append((f"{name}, {round(bw * k)} × {round(bh * k)} px ({mp} MP)", mp * 1_000_000))
        if len(options) == 1:       # small picture: there is nothing to choose
            return True, None
        text = ("How big should the edited photo be?\n\nBigger keeps more detail but is slower and uses more "
                "memory. For printing, about 300 pixels per inch of the photo is enough.")
        choice, ok = QInputDialog.getItem(self, "Photo resolution", text, [o[0] for o in options],
                                          0 if total <= 16.5e6 else 1, False)
        if not ok:
            return False, None
        return True, next(v for label, v in options if label == choice)

    def _edit_photo(self):
        try:
            base = load_image_rgb(self.orig_path, keep_alpha=True)
        except Exception as e:
            QMessageBox.warning(self, "Can't use this file", str(e))
            return
        spec = self._spec()
        if not spec["entries"]:
            QMessageBox.information(self, "Edit photo", "Add at least one photo size first.")
            return
        try:
            aspect = idphoto.photo_aspect(spec["entries"][0], spec)
            state = self.edit_state if (self.edit_state and self.edit_state.get("box")) else None
            if state is not None:       # editing again: same part of the picture, so the cutout still fits
                box = tuple(state["box"])
                cur_size = self.src.size if self.src is not None else self.full_size
                orig_focus = self._focus_out_of_crop(self.focus, base.size, box, cur_size, aspect)
            else:
                box = self._crop_box(base.size, spec)
                orig_focus = self.focus
            ok, max_px = self._ask_edit_resolution(box)
            if not ok:
                return
            dlg = PhotoEditDialog(self, base, state, box, max_px=max_px)
        except Exception as e:
            QMessageBox.warning(self, "Can't open the photo editor", f"{type(e).__name__}: {e}")
            return
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        try:
            if dlg.result_image is None:
                if not self._set_source(self.orig_path):
                    return
                self.focus = tuple(orig_focus)
            else:
                stem = os.path.splitext(os.path.basename(self.orig_path))[0]
                path = keep_path("edited", f"{stem} (edited {int(time.time())}).png")
                dlg.result_image.save(path, "PNG", compress_level=1)
                if not self._set_source(path, edited=True):
                    return
                self.edit_state = dlg.result_state
                self.focus = self._focus_into_crop(orig_focus, base.size, box, aspect)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't use the edited photo", f"{type(e).__name__}: {e}")
            return
        self._set_focus(self.focus)

    def _reset_options(self):
        """Back to the defaults (used when a different photo is chosen). Only the pricing is kept."""
        dflt = idphoto.DEFAULTS
        self._replace_rows(dflt["entries"])
        self._busy = True
        self.orient_combo.setCurrentIndex(0)
        self.gap_spin.setValue(dflt["gap_in"])
        self.margin_spin.setValue(dflt["margin_in"])
        self.lines_check.setChecked(dflt["lines"])
        self.easy_check.setChecked(dflt["easy"])
        self.sig_pct_spin.setValue(dflt["sig_pct"])
        for s, key in ((self.sig_size, "sig_size"), (self.sig_dy, "sig_dy"),
                       (self.sig_dx, "sig_dx"), (self.name_size, "name_size")):
            s.setValue(dflt[key])
        self.sig_name.clear()
        self.manual = None
        self._set_signature(None, None)
        self.sheet_index = 0
        self._busy = False
        self._set_focus(dflt["focus"])

    def _position(self):
        if self.img is None:
            return
        spec = self._spec()
        dlg = PositionDialog(self, self.img, idphoto.photo_aspect(spec["entries"][0], spec), self.focus)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._set_focus(dlg.result_focus)

    def _set_focus(self, focus):
        self.focus = tuple(focus)
        self.pos_reset.setEnabled(tuple(self.focus) != tuple(idphoto.DEFAULTS["focus"]))
        self._refresh()

    def _go(self, step):
        self.sheet_index += step
        self._refresh()

    # ---------------- refresh ----------------

    def _refresh(self, *_):
        if self._busy or self.img is None:
            return
        mode = self.pricing_combo.currentData()
        self.fee_spin.setEnabled(mode != "calculated")
        self.fee_label.setEnabled(mode != "calculated")
        self.fee_hint.setText({
            "calculated": "Each sheet is priced from its ink and paper, like any other document.",
            "flat": "One price for the whole job. Reprinting only some sheets uses the calculated price.",
            "per_sheet": "Replaces the calculated price of every printed sheet.",
            "per_photo": "Price for each photo. Reprinting one sheet charges only for the photos on it.",
        }[mode])
        self.pos_reset.setEnabled(tuple(self.focus) != tuple(idphoto.DEFAULTS["focus"]))

        if self.manual is not None and self._geom_key() != self._manual_key:
            self.manual = None            # the sizes, paper or spacing changed, so the hand layout no longer fits
            self._manual_dropped = True
        self.auto_btn.setVisible(self.manual is not None)
        self.hand_label.setText("This sheet is arranged by hand." if self.manual is not None else "")

        spec = self._spec()
        err = idphoto.validate(spec)
        self.create_btn.setEnabled(err is None)
        if err:
            self.summary_label.setText(err)
            self.summary_label.setStyleSheet("font-weight: 700; color: #b91c1c;")
            self.note_label.hide()
            self.preview.set_image(None)
            self.sheet_label.setText("")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        self.summary_label.setStyleSheet("font-weight: 700;")
        landscape, sheets = idphoto.best_plan(spec)
        self._plan = (landscape, sheets)
        self.sheet_index = min(max(0, self.sheet_index), len(sheets) - 1)
        d = idphoto.describe(spec, sheets)
        self.summary_label.setText(
            f"{d['photos']} photo(s) on {d['sheets']} sheet(s) of {d['paper']} "
            f"({'landscape' if landscape else 'portrait'})")

        notes = []
        dpi = idphoto.effective_dpi(SimpleNamespace(width=self.full_size[0], height=self.full_size[1]), spec)
        if dpi is not None and dpi < idphoto.MIN_OK_DPI:
            notes.append(("warn", f"The picture is only about {dpi:.0f} dpi at this size, so it may look soft."))
        if self._manual_dropped:
            notes.append(("info", "The hand layout was cleared because the sizes or the sheet changed."))
            self._manual_dropped = False
        if spec["easy"] and spec["gap_in"] > 0 and len(spec["entries"]) > 1 and self.manual is None:
            notes.append(("info", "Tip: with a gap of 0, small photos fill the exact space of a bigger one "
                                  "(4 × 1×1 = one 2×2)."))
        if spec["margin_in"] == 0:
            notes.append(("info", "Borderless: the printer must support it, or the edges will be cut off."))
        if notes:
            level = "warn" if any(l == "warn" for l, _ in notes) else "info"
            self.note_label.setStyleSheet(label_style(level))
            self.note_label.setText("  ".join(t for _, t in notes))
        self.note_label.setVisible(bool(notes))

        img, _ = idphoto.preview_sheet(self.img, spec, landscape, sheets, self.sheet_index, sig=self.sig_img)
        self.preview.set_image(img)
        n = len(sheets)
        self.sheet_label.setText(f"Sheet {self.sheet_index + 1} of {n} · {d['counts'][self.sheet_index]} photo(s) "
                                 "(shown to scale)")
        self.prev_btn.setEnabled(self.sheet_index > 0)
        self.next_btn.setEnabled(self.sheet_index < n - 1)

    # ---------------- arranging by hand ----------------

    def _geom_key(self):
        """Everything the placement depends on. When it changes, a hand layout is dropped."""
        return (tuple((e["name"], round(e["w"], 4), round(e["h"], 4), e["qty"])
                      for e in (r.entry() for r in self.rows)),
                self.paper_combo.currentText(), self.orient_combo.currentData(),
                round(self.gap_spin.value(), 3), round(self.margin_spin.value(), 3), self.easy_check.isChecked())

    def _arrange_by_hand(self):
        spec = self._spec()
        err = idphoto.validate(spec)
        if err:
            QMessageBox.information(self, "Arrange by hand", err)
            return
        landscape, sheets = idphoto.best_plan(spec)
        dlg = LayoutEditorDialog(self, self.img, spec, landscape, sheets, self.sig_img)
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.result_layout:
            return
        landscape, new_sheets = dlg.result_layout
        counts = [0] * len(spec["entries"])
        for sheet in new_sheets:
            for it in sheet:
                counts[it[4]] += 1
        remap = {old: new for new, old in enumerate(i for i, c in enumerate(counts) if c > 0)}

        self._busy = True                  # the quantities now follow what is on the sheets
        for i in reversed(range(len(self.rows))):
            if i not in remap:
                row = self.rows.pop(i)
                self.rows_box.removeWidget(row)
                row.deleteLater()
        for old, new in remap.items():
            self.rows[new].qty.setValue(counts[old])
        self._update_row_buttons()
        moved = [[(x, y, w, h, remap[idx]) for x, y, w, h, idx in sheet] for sheet in new_sheets]
        self.manual = idphoto.make_manual(self._spec(), landscape, moved)
        self._manual_key = self._geom_key()
        self._busy = False
        self.sheet_index = 0
        self._refresh()

    def _auto_layout(self):
        self.manual = None
        self._refresh()

    # ---------------- result ----------------

    # ---------------- export as picture files ----------------

    def _fill_export_menu(self):
        self.export_menu.clear()
        spec = self._spec()
        err = idphoto.validate(spec)
        if err:
            self.export_menu.addAction(err).setEnabled(False)
            return
        for e in spec["entries"]:
            if e["qty"] > 0:
                act = self.export_menu.addAction(f"ID photo, {idphoto.size_name(e['w'], e['h'])}")
                act.triggered.connect(lambda checked=False, ent=dict(e): self._export_photo(ent))
        self.export_menu.addSeparator()
        n = len(idphoto.best_plan(spec)[1])
        act = self.export_menu.addAction("Print sheet" if n == 1 else f"All {n} print sheets")
        act.triggered.connect(self._export_sheets)

    def _ask_image_path(self, title, name):
        base = re.sub(r"[^\w.-]+", "_", os.path.splitext(os.path.basename(self.orig_path))[0]).strip("_") or "photo"
        start = os.path.join(os.path.dirname(self.orig_path), f"{base}_{name}.png")
        path, chosen = QFileDialog.getSaveFileName(self, title, start, "PNG image (*.png);;JPEG image (*.jpg)")
        if not path:
            return None
        if os.path.splitext(path)[1].lower() not in (".png", ".jpg", ".jpeg"):
            path += ".jpg" if "jpg" in chosen.lower() else ".png"
        return path

    def _export_photo(self, entry):
        spec = self._spec()
        path = self._ask_image_path("Export ID photo", re.sub(r"[^\w.-]+", "_", idphoto.size_name(entry["w"], entry["h"]).replace("×", "x")))
        if not path:
            return
        sig = self.sig_img if spec["signature_path"] else None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            im = idphoto.render_cell(self.src, entry, spec["focus"], spec, sig, idphoto.EXPORT_PPI)
            idphoto.save_image(im, path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Couldn't export", str(e))
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Exported", f"Saved:\n{path}")

    def _export_sheets(self):
        spec = self._spec()
        path = self._ask_image_path("Export print sheets", "sheet")
        if not path:
            return
        sig = self.sig_img if spec["signature_path"] else None
        stem, ext = os.path.splitext(path)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        saved = []
        try:
            landscape, sheets = idphoto.best_plan(spec)
            for i in range(len(sheets)):
                target = path if len(sheets) == 1 else f"{stem}_{i + 1}{ext}"
                idphoto.save_image(idphoto.render_sheet(self.src, spec, landscape, sheets, i, sig), target)
                saved.append(target)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Couldn't export", str(e))
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Exported", "Saved:\n" + "\n".join(saved))

    def _accept(self):
        spec = self._spec()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pdf, info = idphoto.build_pdf(self.path, spec)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Couldn't build the sheets", str(e))
            return
        QApplication.restoreOverrideCursor()

        mode, fee = self.pricing_combo.currentData(), self.fee_spin.value()
        store = self.settings.setdefault("idphoto", {})
        store.update(entries=spec["entries"], orientation=spec["orientation"], gap_in=spec["gap_in"],
                     margin_in=spec["margin_in"], lines=spec["lines"], easy=spec["easy"],
                     sig_pct=spec["sig_pct"], pricing_mode=mode, fee=fee)
        self._persist()
        self.result_data = {
            "pdf": pdf, "tile": 0,
            "job": {"kind": "id", "source": self.path, "page": 0, "spec": spec,
                    "pricing": {"mode": mode, "fee": fee},
                    "photos": info["photos"], "sheet_counts": info["counts"]},
        }
        self.accept()