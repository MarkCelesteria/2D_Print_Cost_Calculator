import os
from types import SimpleNamespace

from PIL import Image

from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QSize
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QLineEdit, QPushButton, QFileDialog, QMessageBox, QWidget, QDialogButtonBox, QApplication,
    QSizePolicy, QFrame, QScrollArea, QSlider, QInputDialog,
)

from printcost.core import idphoto
from printcost.core import pricing
from printcost.core.analyzer import IMAGE_EXTS, load_image_rgb
from printcost.ui.icons import make_icon
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


# ---------------- sheet preview ----------------

class SheetPreview(QWidget):
    """One printed sheet, drawn to scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 420)
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

    def __init__(self, entry, unit_index):
        super().__init__()
        self.setObjectName("sub")
        self._unit = unit_index
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 6, 8, 6)
        box.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.combo = QComboBox()
        self.combo.addItems([n for n, _ in idphoto.SIZES] + [idphoto.CUSTOM])
        top.addWidget(self.combo, 1)
        self.qty = QSpinBox()
        self.qty.setRange(1, idphoto.MAX_QTY)
        self.qty.setSuffix(" pcs")
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
        self.unit_label = QLabel(UNITS[unit_index][0])
        cl.addWidget(QLabel("Size"))
        cl.addWidget(self.w_spin, 1)
        cl.addWidget(QLabel("×"))
        cl.addWidget(self.h_spin, 1)
        cl.addWidget(self.unit_label)
        box.addWidget(self.custom)

        self.sig_check = QCheckBox("Signature / name inside the photo (bottom)")
        self.sig_check.setToolTip("The photo keeps its exact size. The bottom part becomes a white strip for the\n"
                                  "signature and name, and the face is framed a little higher.")
        box.addWidget(self.sig_check)

        self.set_entry(entry)
        self.combo.currentIndexChanged.connect(self._combo_changed)
        self.qty.valueChanged.connect(self.changed)
        self.w_spin.valueChanged.connect(self.changed)
        self.h_spin.valueChanged.connect(self.changed)
        self.sig_check.toggled.connect(self.changed)

    def set_sig_enabled(self, on):
        self.sig_check.setEnabled(on)
        if not on:
            self.sig_check.setChecked(False)

    @staticmethod
    def _spin():
        sb = QDoubleSpinBox()
        sb.setRange(0.1, 500)
        sb.setDecimals(2)
        sb.setSingleStep(0.1)
        return sb

    def _factor(self):
        return UNITS[self._unit][1]

    def set_entry(self, e):
        for w in (self.combo, self.qty, self.w_spin, self.h_spin):
            w.blockSignals(True)
        if e["name"] in idphoto.SIZE_MAP:
            self.combo.setCurrentText(e["name"])
        else:
            self.combo.setCurrentText(idphoto.CUSTOM)
        self.w_spin.setValue(e["w"] / self._factor())
        self.h_spin.setValue(e["h"] / self._factor())
        self.qty.setValue(e["qty"])
        self.sig_check.blockSignals(True)
        self.sig_check.setChecked(bool(e.get("sig")))
        self.sig_check.blockSignals(False)
        for w in (self.combo, self.qty, self.w_spin, self.h_spin):
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

    def set_unit(self, unit_index):
        """Change the unit of the custom boxes without changing the size they hold."""
        w, h = self.w_spin.value() * self._factor(), self.h_spin.value() * self._factor()
        self._unit = unit_index
        self.unit_label.setText(UNITS[unit_index][0])
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
        self.unit_index = 0

        root = QHBoxLayout(self)
        root.setSpacing(16)
        left = QVBoxLayout()
        root.addLayout(left, 0)
        right = QVBoxLayout()
        root.addLayout(right, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(430)
        holder = QWidget()
        form = QGridLayout(holder)
        form.setContentsMargins(0, 0, 8, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        scroll.setWidget(holder)
        left.addWidget(scroll, 1)

        row = 0
        self.source_label = QLabel("")
        self.source_label.setWordWrap(True)
        form.addWidget(self.source_label, row, 0, 1, 2)
        change = QPushButton("Change…")
        change.setObjectName("secondary")
        change.clicked.connect(self._change_source)
        form.addWidget(change, row, 2)
        row += 1

        form.addWidget(QLabel("Sheet paper"), row, 0)
        self.paper_combo = QComboBox()
        self.paper_combo.addItems([p["name"] for p in self.paper_sizes])
        want = (job["spec"]["paper"]["name"] if job else (paper or {}).get("name"))
        if want in [p["name"] for p in self.paper_sizes]:
            self.paper_combo.setCurrentText(want)
        form.addWidget(self.paper_combo, row, 1, 1, 2)
        row += 1

        form.addWidget(QLabel("Sheets"), row, 0)
        self.orient_combo = QComboBox()
        for text, key in ORIENTATIONS:
            self.orient_combo.addItem(text, key)
        self.orient_combo.setCurrentIndex(max(0, self.orient_combo.findData(remembered["orientation"])))
        form.addWidget(self.orient_combo, row, 1, 1, 2)
        row += 1

        form.addWidget(QLabel("Quick set"), row, 0)
        self.set_combo = QComboBox()
        form.addWidget(self.set_combo, row, 1)
        sets_btns = QHBoxLayout()
        sets_btns.setSpacing(4)
        self.save_set_btn = QPushButton("Save")
        self.save_set_btn.setObjectName("secondary")
        self.save_set_btn.setToolTip("Remember the sizes below as a quick set")
        self.save_set_btn.clicked.connect(self._save_set)
        self.del_set_btn = QPushButton("Delete")
        self.del_set_btn.setObjectName("secondary")
        self.del_set_btn.setToolTip("Delete one of your saved sets")
        self.del_set_btn.clicked.connect(self._delete_set)
        sets_btns.addWidget(self.save_set_btn)
        sets_btns.addWidget(self.del_set_btn)
        form.addLayout(sets_btns, row, 2)
        row += 1

        header = QLabel("Photos (size and how many)")
        header.setObjectName("cardHeader")
        form.addWidget(header, row, 0, 1, 3)
        row += 1
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        form.addLayout(self.rows_box, row, 0, 1, 3)
        row += 1
        add_row = QHBoxLayout()
        self.add_btn = QPushButton("+ Add another size")
        self.add_btn.setObjectName("secondary")
        self.add_btn.clicked.connect(lambda: self._add_row())
        add_row.addWidget(self.add_btn, 1)
        add_row.addWidget(QLabel("Custom sizes in"))
        self.unit_combo = QComboBox()
        self.unit_combo.addItems([u for u, _ in UNITS])
        add_row.addWidget(self.unit_combo)
        form.addLayout(add_row, row, 0, 1, 3)
        row += 1

        form.addWidget(QLabel("Gap between photos (in)"), row, 0, 1, 2)
        self.gap_spin = self._inch_spin(remembered["gap_in"], 1.0)
        self.gap_spin.setToolTip("White space between photos, for cutting.")
        form.addWidget(self.gap_spin, row, 2)
        row += 1
        form.addWidget(QLabel("Printer margin (in)"), row, 0, 1, 2)
        self.margin_spin = self._inch_spin(remembered["margin_in"], 2.0)
        self.margin_spin.setToolTip("White border your printer can't print on.")
        form.addWidget(self.margin_spin, row, 2)
        row += 1
        self.lines_check = QCheckBox("Light cutting lines between photos")
        self.lines_check.setChecked(bool(remembered["lines"]))
        form.addWidget(self.lines_check, row, 0, 1, 3)
        row += 1
        self.easy_check = QCheckBox("Easy cutting (straight cuts across the sheet)")
        self.easy_check.setToolTip("Lays photos in full-width bands so a paper cutter can make every cut edge to edge.\n"
                                   "With a gap of 0, small photos fill the exact space of a bigger one.")
        self.easy_check.setChecked(bool(remembered["easy"]))
        form.addWidget(self.easy_check, row, 0, 1, 3)
        row += 1

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
        form.addLayout(pos_row, row, 0, 1, 3)
        row += 1

        sig_row = QHBoxLayout()
        sig_row.addWidget(QLabel("Signature"))
        self.sig_thumb = QLabel("None")
        self.sig_thumb.setObjectName("sigThumb")
        self.sig_thumb.setStyleSheet(f"QLabel#sigThumb {{ background: white; border: 1px solid {BORDER}; "
                                     f"border-radius: 6px; color: {MUTED}; }}")
        self.sig_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sig_thumb.setFixedSize(150, 44)
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
        form.addLayout(sig_row, row, 0, 1, 3)
        row += 1
        form.addWidget(QLabel("Strip height (% of photo height)"), row, 0, 1, 2)
        self.sig_pct_spin = QDoubleSpinBox()
        self.sig_pct_spin.setRange(8, 60)
        self.sig_pct_spin.setDecimals(0)
        self.sig_pct_spin.setValue(float(remembered["sig_pct"]))
        form.addWidget(self.sig_pct_spin, row, 2)
        row += 1
        form.addWidget(QLabel("Name (optional)"), row, 0)
        self.sig_name = QLineEdit()
        self.sig_name.setPlaceholderText("Printed under the signature")
        self.sig_name.setText(remembered.get("sig_name", ""))
        form.addWidget(self.sig_name, row, 1, 1, 2)
        row += 1
        sig_hint = QLabel("Tick “Signature / name inside the photo” on the sizes that need it. The photo keeps "
                          "its exact size; the face is framed higher to leave room at the bottom.")
        sig_hint.setObjectName("muted")
        sig_hint.setWordWrap(True)
        form.addWidget(sig_hint, row, 0, 1, 3)
        row += 1

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-weight: 700;")
        form.addWidget(self.summary_label, row, 0, 1, 3)
        row += 1
        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        form.addWidget(self.note_label, row, 0, 1, 3)
        row += 1

        form.addWidget(QLabel("Pricing"), row, 0)
        self.pricing_combo = QComboBox()
        for text, key in PRICING_MODES:
            self.pricing_combo.addItem(text, key)
        self.pricing_combo.setCurrentIndex(max(0, self.pricing_combo.findData(remembered["pricing_mode"])))
        form.addWidget(self.pricing_combo, row, 1, 1, 2)
        row += 1
        self.fee_label = QLabel("Price")
        form.addWidget(self.fee_label, row, 0)
        self.fee_spin = QDoubleSpinBox()
        self.fee_spin.setRange(0, 1_000_000)
        self.fee_spin.setDecimals(2)
        self.fee_spin.setValue(float(remembered["fee"] or 0))
        form.addWidget(self.fee_spin, row, 1)
        self.fee_hint = QLabel("")
        self.fee_hint.setObjectName("muted")
        self.fee_hint.setWordWrap(True)
        form.addWidget(self.fee_hint, row + 1, 0, 1, 3)
        row += 2
        form.setRowStretch(row, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
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
        self._load_saved_signature()
        entries = remembered.get("entries") or idphoto.DEFAULTS["entries"]
        for e in entries[:idphoto.MAX_ROWS]:
            self._add_row(dict(e), refresh=False)
        self.resize(1040, 760)

        if not self._set_source(path):
            self.src = None
            return

        for w in (self.paper_combo, self.orient_combo, self.pricing_combo):
            w.currentIndexChanged.connect(self._refresh)
        self.unit_combo.currentIndexChanged.connect(self._unit_changed)
        self.set_combo.activated.connect(self._set_chosen)
        for w in (self.gap_spin, self.margin_spin, self.fee_spin):
            w.valueChanged.connect(self._refresh)
        self.lines_check.toggled.connect(self._refresh)
        self.easy_check.toggled.connect(self._refresh)
        self.sig_pct_spin.valueChanged.connect(self._refresh)
        self.sig_name.textChanged.connect(self._refresh)
        self._busy = False
        self._refresh()

    # ---------------- helpers ----------------

    @staticmethod
    def _inch_spin(value, maximum):
        sb = QDoubleSpinBox()
        sb.setRange(0.0, maximum)
        sb.setDecimals(2)
        sb.setSingleStep(0.05)
        sb.setValue(float(value))
        return sb

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
            "sig_name": self.sig_name.text().strip(),
            "signature_path": self.sig_path if uses_sig else None,
            "focus": self.focus,
            "paper": {"name": p["name"], "width_in": p["width_in"], "height_in": p["height_in"]},
        }

    def _set_source(self, path):
        try:
            img = load_image_rgb(path)
        except Exception as e:
            QMessageBox.warning(self, "Can't use this file", str(e))
            return False
        self.full_size = img.size
        thumb = img.copy()
        thumb.thumbnail((THUMB_PX, THUMB_PX))
        self.img, self.src, self.path = thumb, img, path
        self.source_label.setText(os.path.basename(path))
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
        row = EntryRow(entry, self.unit_index)
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

    def _unit_changed(self):
        self.unit_index = self.unit_combo.currentIndex()
        for r in self.rows:
            r.set_unit(self.unit_index)

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

    def _sig_file(self):
        return os.path.join(os.path.dirname(pricing.SETTINGS_PATH), "signature.png")

    def _load_saved_signature(self):
        path = self._sig_file()
        img = idphoto.load_signature(path) if os.path.exists(path) else None
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
            saved = idphoto.save_signature(sig, self._sig_file())
        except Exception as e:
            QMessageBox.warning(self, "Can't use this signature", str(e))
            return
        self._set_signature(sig, saved)
        self._refresh()

    def _clear_signature(self):
        try:
            os.remove(self._sig_file())
        except OSError:
            pass
        self._set_signature(None, None)
        self._refresh()

    # ---------------- picture ----------------

    def _change_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a photo", os.path.dirname(self.path), PICK_FILTER)
        if path and self._set_source(path):
            self._reset_options()

    def _reset_options(self):
        """Back to the defaults (used when a different photo is chosen). Pricing is kept."""
        d = idphoto.DEFAULTS
        self._replace_rows(d["entries"])
        self._busy = True
        self.orient_combo.setCurrentIndex(0)
        self.gap_spin.setValue(d["gap_in"])
        self.margin_spin.setValue(d["margin_in"])
        self.lines_check.setChecked(d["lines"])
        self.easy_check.setChecked(d["easy"])
        self.sig_pct_spin.setValue(d["sig_pct"])
        self.sig_name.clear()
        self.sheet_index = 0
        self._busy = False
        self._set_focus(d["focus"])

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
        if spec["easy"] and spec["gap_in"] > 0 and len(spec["entries"]) > 1:
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

    # ---------------- result ----------------

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