import os

from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QFileDialog, QMessageBox, QWidget, QDialogButtonBox, QApplication, QSizePolicy,
)

import poster
import pricing
from analyzer import IMAGE_EXTS
from notes import label_style
from preview_widget import pil_to_qpixmap
from style import BORDER, PREVIEW_BG, ACCENT_LINE, PRIMARY

PRESETS = [("2×2", (2, 2)), ("3×2", (3, 2)), ("3×3", (3, 3)), ("4×3", (4, 3)), ("Custom", None)]
PRICING_MODES = [
    ("Calculated (ink + paper per sheet)", "calculated"),
    ("Flat poster fee", "flat"),
    ("Fixed price per sheet", "per_sheet"),
]
MIN_OK_DPI = 100
PICK_FILTER = "Pictures and PDFs (" + " ".join(f"*{e}" for e in IMAGE_EXTS + (".pdf",)) + ")"


class PosterPreview(QWidget):
    """The whole poster as one picture, with the tile grid and the print-order numbers on top."""
    tile_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pix = None
        self._L = None
        self.selected = 0

    def set_state(self, pil_img, L, selected):
        self._pix = pil_to_qpixmap(pil_img) if pil_img is not None else None
        self._L, self.selected = L, selected
        self.update()

    def _geometry(self):
        L = self._L
        pad = 10
        s = min((self.width() - 2 * pad) / L["W"], (self.height() - 2 * pad) / L["H"])
        return (self.width() - L["W"] * s) / 2, (self.height() - L["H"] * s) / 2, s

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PREVIEW_BG))
        if self._L is None or self._pix is None:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        L = self._L
        ox, oy, s = self._geometry()
        poster_rect = QRectF(ox, oy, L["W"] * s, L["H"] * s)
        p.drawPixmap(poster_rect, self._pix, QRectF(self._pix.rect()))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawRect(poster_rect)

        cols, rows = L["cols"], L["rows"]
        font = QFont(p.font())
        for idx in range(cols * rows):
            r, c = divmod(idx, cols)
            x0, y0, x1, y1 = poster.tile_box(L, c, r)
            tile = QRectF(ox + x0 * s, oy + y0 * s, (x1 - x0) * s, (y1 - y0) * s)
            if idx == self.selected:
                p.fillRect(tile, QColor(8, 145, 178, 70))
            pen = QPen(QColor(ACCENT_LINE), 2 if idx == self.selected else 1)
            pen.setStyle(Qt.PenStyle.DashLine if idx != self.selected else Qt.PenStyle.SolidLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(tile)

            big = min(tile.width(), tile.height()) >= 56
            text = f"{idx + 1}\n{poster.tile_label(idx, cols)}" if big else str(idx + 1)
            font.setPixelSize(int(max(9, min(18, tile.height() * 0.28))))
            font.setBold(True)
            p.setFont(font)
            box = p.boundingRect(tile, Qt.AlignmentFlag.AlignCenter, text).adjusted(-7, -3, 7, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 27, 45, 175))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor("white"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event):
        if self._L is None:
            return
        L = self._L
        ox, oy, s = self._geometry()
        x, y = (event.position().x() - ox) / s, (event.position().y() - oy) / s
        if not (0 <= x <= L["W"] and 0 <= y <= L["H"]):
            return
        c = min(L["cols"] - 1, int(x // L["sx"]))
        r = min(L["rows"] - 1, int(y // L["sy"]))
        self.tile_clicked.emit(r * L["cols"] + c)


CROP_MIN = 0.1  # the box can't get smaller than 10% of the picture
HANDLE = 9
CURSORS = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
           "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
           "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
           "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
           "move": Qt.CursorShape.SizeAllCursor}


class CropCanvas(QWidget):
    def __init__(self, pil_img, crop):
        super().__init__()
        self.setMinimumSize(460, 360)
        self.setMouseTracking(True)
        self.pix = pil_to_qpixmap(pil_img)
        l, t, r, b = crop
        self.box = [l, t, 1 - r, 1 - b]  # x0, y0, x1, y1 as fractions of the picture
        self._drag = None

    def result(self):
        x0, y0, x1, y1 = self.box
        return (x0, y0, 1 - x1, 1 - y1)

    def _img_rect(self):
        pad = 12
        s = min((self.width() - 2 * pad) / self.pix.width(), (self.height() - 2 * pad) / self.pix.height())
        w, h = self.pix.width() * s, self.pix.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _box_rect(self):
        R = self._img_rect()
        x0, y0, x1, y1 = self.box
        return QRectF(R.x() + x0 * R.width(), R.y() + y0 * R.height(), (x1 - x0) * R.width(), (y1 - y0) * R.height())

    def _handles(self):
        r = self._box_rect()
        cx, cy = r.center().x(), r.center().y()
        return {"tl": (r.left(), r.top()), "t": (cx, r.top()), "tr": (r.right(), r.top()),
                "r": (r.right(), cy), "br": (r.right(), r.bottom()), "b": (cx, r.bottom()),
                "bl": (r.left(), r.bottom()), "l": (r.left(), cy)}

    def _hit(self, pos):
        for name, (x, y) in self._handles().items():
            if abs(pos.x() - x) <= HANDLE and abs(pos.y() - y) <= HANDLE:
                return name
        return "move" if self._box_rect().contains(pos) else None

    def paintEvent(self, event):
        p = QPainter(self)
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
        p.setPen(QPen(QColor(PRIMARY), 1))
        p.setBrush(QColor("white"))
        for x, y in self._handles().values():
            p.drawRect(QRectF(x - 5, y - 5, 10, 10))

    def mousePressEvent(self, event):
        hit = self._hit(event.position())
        if hit:
            self._drag = (hit, event.position(), list(self.box))

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseMoveEvent(self, event):
        if self._drag is None:
            hit = self._hit(event.position())
            self.setCursor(CURSORS.get(hit, Qt.CursorShape.ArrowCursor))
            return
        name, start, b0 = self._drag
        R = self._img_rect()
        dx, dy = (event.position().x() - start.x()) / R.width(), (event.position().y() - start.y()) / R.height()
        x0, y0, x1, y1 = b0
        if name == "move":
            w, h = x1 - x0, y1 - y0
            x0 = min(max(0.0, x0 + dx), 1 - w)
            y0 = min(max(0.0, y0 + dy), 1 - h)
            x1, y1 = x0 + w, y0 + h
        else:
            if "l" in name:
                x0 = min(max(0.0, b0[0] + dx), x1 - CROP_MIN)
            if "r" in name:
                x1 = max(min(1.0, b0[2] + dx), x0 + CROP_MIN)
            if "t" in name:
                y0 = min(max(0.0, b0[1] + dy), y1 - CROP_MIN)
            if "b" in name:
                y1 = max(min(1.0, b0[3] + dy), y0 + CROP_MIN)
        self.box = [x0, y0, x1, y1]
        self.update()


class CropDialog(QDialog):
    """Drag the box over the part of the picture to keep."""

    def __init__(self, parent, pil_img, crop):
        super().__init__(parent)
        self.setWindowTitle("Crop picture")
        self.result_crop = None
        lay = QVBoxLayout(self)
        hint = QLabel("Drag the edges or corners to crop. Drag inside the box to move it.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        self.canvas = CropCanvas(pil_img, crop)
        lay.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        full = QPushButton("Whole picture")
        full.setObjectName("secondary")
        full.clicked.connect(lambda: (setattr(self.canvas, "box", [0.0, 0.0, 1.0, 1.0]), self.canvas.update()))
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        apply_btn = QPushButton("Apply crop")
        apply_btn.setObjectName("primary")
        apply_btn.clicked.connect(self._apply)
        row.addWidget(full)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(apply_btn)
        lay.addLayout(row)
        self.resize(640, 540)

    def _apply(self):
        self.result_crop = self.canvas.result()
        self.accept()


class PosterDialog(QDialog):
    """Pick a picture, a grid and the options; builds the tiled PDF when you press Create poster."""

    def __init__(self, parent, path, settings, paper, job=None):
        super().__init__(parent)
        self.setWindowTitle("Poster")
        self.settings = settings
        self.paper_sizes = settings["paper_sizes"]
        self.result_data = None
        self.src = None
        self.selected = 0
        self._img_cache = None
        self._busy = True  # no refresh while the controls are being filled in

        remembered = settings.get("poster") or {}
        saved = dict(poster.DEFAULTS)
        saved.update(pricing_mode=remembered.get("pricing_mode", "calculated"),
                     fee=remembered.get("fee", 0.0))
        if job:
            saved.update(job["spec"])
            saved.update(pricing_mode=job["pricing"]["mode"], fee=job["pricing"]["fee"])
        page = job["page"] if job else 0

        root = QHBoxLayout(self)
        root.setSpacing(16)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        root.addLayout(form, 0)
        self.preview = PosterPreview()
        self.preview.tile_clicked.connect(self._select_tile)
        right = QVBoxLayout()
        right.addWidget(self.preview, 1)
        self.tile_label = QLabel("")
        self.tile_label.setObjectName("muted")
        self.tile_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.tile_label)
        root.addLayout(right, 1)

        row = 0
        self.source_label = QLabel("")
        self.source_label.setWordWrap(True)
        form.addWidget(self.source_label, row, 0, 1, 2)
        change = QPushButton("Change…")
        change.setObjectName("secondary")
        change.clicked.connect(self._change_source)
        form.addWidget(change, row, 2)
        row += 1

        form.addWidget(QLabel("Source page"), row, 0)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        form.addWidget(self.page_spin, row, 1)
        self.page_row = (form.itemAtPosition(row, 0).widget(), self.page_spin)
        row += 1

        form.addWidget(QLabel("Sheet paper"), row, 0)
        self.paper_combo = QComboBox()
        self.paper_combo.addItems([p["name"] for p in self.paper_sizes])
        want = saved.get("paper", {}).get("name") if job else (paper or {}).get("name")
        if want in [p["name"] for p in self.paper_sizes]:
            self.paper_combo.setCurrentText(want)
        form.addWidget(self.paper_combo, row, 1, 1, 2)
        row += 1

        form.addWidget(QLabel("Sheets"), row, 0)
        self.orient_combo = QComboBox()
        self.orient_combo.addItem("Portrait", False)
        self.orient_combo.addItem("Landscape", True)
        self.orient_combo.setCurrentIndex(1 if saved["landscape"] else 0)
        form.addWidget(self.orient_combo, row, 1, 1, 2)
        row += 1

        self.size_check = QCheckBox("Set image size (sheets are worked out for me)")
        self.size_check.setChecked(bool(saved.get("target")))
        form.addWidget(self.size_check, row, 0, 1, 3)
        row += 1
        form.addWidget(QLabel("Width × height (in)"), row, 0)
        tw, th = saved.get("target") or (24, 36)
        self.w_spin = self._inch_spin(tw, 2000, 0.5)
        self.h_spin = self._inch_spin(th, 2000, 0.5)
        sz = QHBoxLayout()
        sz.addWidget(self.w_spin)
        self.link_btn = QPushButton("×")
        self.link_btn.setObjectName("secondary")
        self.link_btn.setCheckable(True)
        self.link_btn.setChecked(True)
        self.link_btn.setFixedWidth(30)
        self.link_btn.setStyleSheet("padding: 6px 0;")
        self.link_btn.setToolTip("Linked (blue): changing one size changes the other, keeping the picture's shape.\n"
                                 "Click to unlink: the picture is stretched to exactly the width × height you type.")
        sz.addWidget(self.link_btn)
        sz.addWidget(self.h_spin)
        form.addLayout(sz, row, 1, 1, 2)
        row += 1

        form.addWidget(QLabel("Grid"), row, 0)
        self.preset_combo = QComboBox()
        for name, _ in PRESETS:
            self.preset_combo.addItem(name)
        form.addWidget(self.preset_combo, row, 1)
        self.suggest_btn = QPushButton("Suggest")
        self.suggest_btn.setObjectName("secondary")
        self.suggest_btn.setToolTip("Pick the grid that best matches the picture's shape")
        self.suggest_btn.clicked.connect(self._suggest)
        form.addWidget(self.suggest_btn, row, 2)
        row += 1

        form.addWidget(QLabel("Columns × rows"), row, 0)
        cr = QHBoxLayout()
        self.cols_spin, self.rows_spin = QSpinBox(), QSpinBox()
        times = QLabel("×")
        times.setAlignment(Qt.AlignmentFlag.AlignCenter)
        times.setFixedWidth(30)
        for sb, v in ((self.cols_spin, saved["cols"]), (self.rows_spin, saved["rows"])):
            sb.setRange(1, poster.MAX_GRID)
            sb.setValue(v)
        cr.addWidget(self.cols_spin, 1)
        cr.addWidget(times)
        cr.addWidget(self.rows_spin, 1)
        form.addLayout(cr, row, 1, 1, 2)
        row += 1

        self.size_label = QLabel("")
        self.size_label.setWordWrap(True)
        self.size_label.setStyleSheet("font-weight: 700;")
        form.addWidget(self.size_label, row, 0, 1, 3)
        row += 1

        form.addWidget(QLabel("Picture fit"), row, 0)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["Fit (blank bands if shape differs)", "Fill (crop the edges)"])
        self.fit_combo.setCurrentIndex(1 if saved["fill"] else 0)
        form.addWidget(self.fit_combo, row, 1, 1, 2)
        row += 1

        self.crop = tuple(saved["crop"])
        crop_row = QHBoxLayout()
        self.crop_btn = QPushButton("Crop picture…")
        self.crop_btn.setObjectName("secondary")
        self.crop_btn.setToolTip("Drag a box over the part of the picture to keep")
        self.crop_btn.clicked.connect(self._crop_picture)
        self.crop_reset = QPushButton("Reset crop")
        self.crop_reset.setObjectName("secondary")
        self.crop_reset.setEnabled(any(self.crop))
        self.crop_reset.clicked.connect(lambda: self._set_crop((0, 0, 0, 0)))
        crop_row.addWidget(self.crop_btn, 1)
        crop_row.addWidget(self.crop_reset)
        form.addLayout(crop_row, row, 0, 1, 3)
        row += 1

        form.addWidget(QLabel("Printer margin (in)"), row, 0)
        self.margin_spin = self._inch_spin(saved["margin_in"], 1.0)
        self.margin_spin.setToolTip("White border your printer can't print on. 0 = borderless.")
        form.addWidget(self.margin_spin, row, 1)
        form.addWidget(QLabel("0 = borderless"), row, 2)
        row += 1

        form.addWidget(QLabel("Overlap (in)"), row, 0)
        self.overlap_spin = self._inch_spin(saved["overlap_in"], 2.0)
        self.overlap_spin.setToolTip("Strip shared by neighbouring sheets, for trimming and gluing.")
        form.addWidget(self.overlap_spin, row, 1)
        form.addWidget(QLabel("e.g. 0.25 in"), row, 2)
        row += 1

        self.marks_check = QCheckBox("Crop / alignment marks")
        self.marks_check.setChecked(saved["marks"])
        form.addWidget(self.marks_check, row, 0, 1, 3)
        row += 1
        self.labels_check = QCheckBox("Sheet labels (A1, A2, B1…) — they print on the page")
        self.labels_check.setChecked(saved["labels"])
        form.addWidget(self.labels_check, row, 0, 1, 3)
        row += 1

        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        form.addWidget(self.note_label, row, 0, 1, 3)
        row += 1

        form.addWidget(QLabel("Pricing"), row, 0)
        self.pricing_combo = QComboBox()
        for text, key in PRICING_MODES:
            self.pricing_combo.addItem(text, key)
        idx = self.pricing_combo.findData(saved["pricing_mode"])
        self.pricing_combo.setCurrentIndex(max(0, idx))
        form.addWidget(self.pricing_combo, row, 1, 1, 2)
        row += 1
        self.fee_label = QLabel("Fee")
        form.addWidget(self.fee_label, row, 0)
        self.fee_spin = QDoubleSpinBox()
        self.fee_spin.setRange(0, 1_000_000)
        self.fee_spin.setDecimals(2)
        self.fee_spin.setValue(float(saved["fee"] or 0))
        form.addWidget(self.fee_spin, row, 1)
        self.fee_hint = QLabel("")
        self.fee_hint.setObjectName("muted")
        self.fee_hint.setWordWrap(True)
        form.addWidget(self.fee_hint, row + 1, 0, 1, 3)
        row += 2

        form.setRowStretch(row, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_btn = QPushButton("Create poster")
        self.create_btn.setObjectName("primary")
        self.buttons.addButton(self.create_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        form.addWidget(self.buttons, row + 1, 0, 1, 3)

        self.path = path
        if not self._set_source(path, page):
            self.src = None
        elif job and job["spec"].get("target"):
            self.link_btn.setChecked(not job["spec"].get("stretch"))
        elif self.link_btn.isChecked():
            self._link_from_width()
        self._sync_preset()
        for w in (self.paper_combo, self.orient_combo, self.fit_combo, self.pricing_combo):
            w.currentIndexChanged.connect(self._refresh)
        self.preset_combo.currentIndexChanged.connect(self._preset_chosen)
        for w in (self.cols_spin, self.rows_spin):
            w.valueChanged.connect(self._grid_edited)
        for w in (self.margin_spin, self.overlap_spin, self.fee_spin, self.page_spin):
            w.valueChanged.connect(self._refresh)
        self.w_spin.valueChanged.connect(self._w_edited)
        self.h_spin.valueChanged.connect(self._h_edited)
        self.link_btn.toggled.connect(self._link_toggled)
        for w in (self.marks_check, self.labels_check, self.size_check):
            w.toggled.connect(self._refresh)
        self._busy = False
        self._refresh()


    def _aspect(self):
        cw, ch = poster.cropped_size(self.src, self._spec())
        return cw / ch

    @staticmethod
    def _set_quiet(spin, value):
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    def _link_from_width(self):
        self._set_quiet(self.h_spin, self.w_spin.value() / self._aspect())

    def _w_edited(self):
        if self.link_btn.isChecked() and self.src is not None:
            self._set_quiet(self.h_spin, self.w_spin.value() / self._aspect())
        self._refresh()

    def _h_edited(self):
        if self.link_btn.isChecked() and self.src is not None:
            self._set_quiet(self.w_spin, self.h_spin.value() * self._aspect())
        self._refresh()

    def _link_toggled(self, linked):
        if linked and self.src is not None:
            self._link_from_width()
        self._refresh()

    def _crop_picture(self):
        if self.src is None:
            return
        dlg = CropDialog(self, poster.source_picture(self.src), self.crop)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._set_crop(dlg.result_crop)

    def _set_crop(self, crop):
        self.crop = tuple(crop)
        self.crop_reset.setEnabled(any(self.crop))
        if self.link_btn.isChecked() and self.src is not None:
            self._link_from_width()  # the cropped picture has a new shape
        self._refresh()


    # ---------------- helpers ----------------

    @staticmethod
    def _inch_spin(value, maximum, minimum=0.0):
        sb = QDoubleSpinBox()
        sb.setRange(minimum, maximum)
        sb.setDecimals(2)
        sb.setSingleStep(0.05)
        sb.setValue(float(value))
        return sb

    def done(self, code):
        if self.src is not None:
            self.src.close()
            self.src = None
        super().done(code)

    def _paper(self):
        return next(p for p in self.paper_sizes if p["name"] == self.paper_combo.currentText())

    def _spec(self):
        p = self._paper()
        return {
            "cols": self.cols_spin.value(), "rows": self.rows_spin.value(),
            "landscape": bool(self.orient_combo.currentData()),
            "fill": self.fit_combo.currentIndex() == 1,
            "crop": self.crop,
            "target": ((self.w_spin.value(), self.h_spin.value())
                       if self.size_check.isChecked() else None),
            "stretch": self.size_check.isChecked() and not self.link_btn.isChecked(),
            "overlap_in": self.overlap_spin.value(), "margin_in": self.margin_spin.value(),
            "marks": self.marks_check.isChecked(), "labels": self.labels_check.isChecked(),
            "paper": {"name": p["name"], "width_in": p["width_in"], "height_in": p["height_in"]},
        }

    def _auto_grid(self):
        """Wanted image size -> fewest sheets. Returns True if it fits in the allowed grid."""
        g = poster.grid_for_size(self._paper(), self.w_spin.value(), self.h_spin.value(),
                                 self.margin_spin.value(), self.overlap_spin.value())
        self._busy = True
        self.cols_spin.setValue(min(g["cols"], poster.MAX_GRID))
        self.rows_spin.setValue(min(g["rows"], poster.MAX_GRID))
        self.orient_combo.setCurrentIndex(1 if g["landscape"] else 0)
        self._busy = False
        self._sync_preset()
        return g["cols"] <= poster.MAX_GRID and g["rows"] <= poster.MAX_GRID

    def _set_source(self, path, page=0):
        try:
            new = poster.Source(path, page)
        except Exception as e:
            QMessageBox.warning(self, "Can't use this file", str(e))
            return False
        if self.src is not None:
            self.src.close()
        self.src, self.path = new, path
        self.source_label.setText(os.path.basename(path))
        is_pdf = new.doc is not None
        for w in self.page_row:
            w.setVisible(is_pdf)
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, new.page_count))
        self.page_spin.setValue(new.page + 1)
        self.page_spin.blockSignals(False)
        return True

    def _reset_options(self):
        """Back to the defaults (used when a different picture is chosen)."""
        d = poster.DEFAULTS
        self._busy = True
        self.cols_spin.setValue(d["cols"])
        self.rows_spin.setValue(d["rows"])
        self.orient_combo.setCurrentIndex(0)
        self.fit_combo.setCurrentIndex(0)
        self.margin_spin.setValue(d["margin_in"])
        self.overlap_spin.setValue(d["overlap_in"])
        self.marks_check.setChecked(False)
        self.labels_check.setChecked(False)
        self.size_check.setChecked(False)
        self.link_btn.setChecked(True)
        self.crop = (0, 0, 0, 0)
        self.crop_reset.setEnabled(False)
        self._set_quiet(self.w_spin, 24)
        self._link_from_width()  # height follows from the new picture's shape
        self._busy = False
        self._sync_preset()

    # ---------------- control handlers ----------------

    def _change_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture or PDF", os.path.dirname(self.path), PICK_FILTER)
        if path and self._set_source(path):
            self._reset_options()
            self._refresh()

    def _sync_preset(self):
        cur = (self.cols_spin.value(), self.rows_spin.value())
        names = [(n, g) for n, g in PRESETS if g == cur]
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex([n for n, _ in PRESETS].index(names[0][0]) if names else len(PRESETS) - 1)
        self.preset_combo.blockSignals(False)

    def _preset_chosen(self):
        grid = PRESETS[self.preset_combo.currentIndex()][1]
        if grid:
            self._busy = True
            self.cols_spin.setValue(grid[0])
            self.rows_spin.setValue(grid[1])
            self._busy = False
        self._refresh()

    def _grid_edited(self):
        if not self._busy:
            self._sync_preset()
            self._refresh()

    def _suggest(self):
        if self.src is None:
            return
        g = poster.suggest_grid(self.src.w / self.src.h, self._paper())
        self._busy = True
        self.cols_spin.setValue(g["cols"])
        self.rows_spin.setValue(g["rows"])
        self.orient_combo.setCurrentIndex(1 if g["landscape"] else 0)
        self._busy = False
        self._sync_preset()
        self._refresh()

    def _select_tile(self, idx):
        self.selected = idx
        self._refresh(rebuild_image=False)

    # ---------------- refresh ----------------

    def _refresh(self, *_, rebuild_image=True):
        if self._busy or self.src is None:
            return
        on = self.size_check.isChecked()
        for w in (self.preset_combo, self.cols_spin, self.rows_spin, self.suggest_btn,
                  self.orient_combo, self.fit_combo):
            w.setEnabled(not on)  # the size decides these (Fit / Fill no longer matters either)
        for w in (self.w_spin, self.h_spin, self.link_btn):
            w.setEnabled(on)
        fits = self._auto_grid() if on else True
        spec = self._spec()
        n = spec["cols"] * spec["rows"]
        self.selected = min(self.selected, n - 1)
        mode = self.pricing_combo.currentData()
        self.fee_spin.setEnabled(mode != "calculated")
        self.fee_label.setEnabled(mode != "calculated")
        self.fee_hint.setText({
            "calculated": "Each sheet is priced from its ink and paper, like any other document.",
            "flat": "One price for the whole poster. Printing only some sheets (a reprint) uses the calculated price.",
            "per_sheet": "Replaces the calculated price of every printed sheet.",
        }[mode])
        if self.src.doc is not None:
            self.src.set_page(self.page_spin.value() - 1)

        err = poster.validate(spec) or (
            None if fits else f"That size needs more than {poster.MAX_GRID} sheets across. Try a smaller size.")
        self.create_btn.setEnabled(err is None)
        if err:
            self.size_label.setText(err)
            self.size_label.setStyleSheet("font-weight: 700; color: #b91c1c;")
            return
        self.size_label.setStyleSheet("font-weight: 700;")
        d = poster.describe(spec)
        if spec["target"]:
            self.size_label.setText(
                f"{d['size']} image → {d['sheets']} sheets of {d['paper']} ({d['grid']}, covers {d['area']})")
        else:
            text = f"{d['grid']} pages of {d['paper']} = {d['size']}"
            if spec["margin_in"] > 0 or spec["overlap_in"] > 0:
                text += " (after trimming)"
            self.size_label.setText(f"{text} · {d['sheets']} sheets")

        notes = []
        dpi = poster.effective_dpi(self.src, spec)
        if dpi is not None and dpi < MIN_OK_DPI:
            notes.append(("warn", f"The picture is only about {dpi:.0f} dpi at this size, so it may look soft."))
        if d["sheets"] > 30:
            notes.append(("warn", f"{d['sheets']} sheets is a lot of paper."))
        if spec["margin_in"] == 0:
            notes.append(("info", "Borderless: the printer must support it, or the page edges will be cut off."))
        if notes:
            level = "warn" if any(l == "warn" for l, _ in notes) else "info"
            self.note_label.setStyleSheet(label_style(level))
            self.note_label.setText("  ".join(t for _, t in notes))
        if spec.get("stretch"):
            cw, ch = poster.cropped_size(self.src, spec)
            if abs((spec["target"][0] / spec["target"][1]) / (cw / ch) - 1) > 0.02:
                notes.append(("warn", "The picture is stretched, so its shape will change."))
        self.note_label.setVisible(bool(notes))

        L = poster.layout(spec)
        if rebuild_image or self._img_cache is None:
            self._img_cache = poster.preview_image(self.src, spec)[0]
        img = self._img_cache
        self.preview.set_state(img, L, self.selected)
        self.tile_label.setText(
            f"Selected: {poster.tile_label(self.selected, spec['cols'])} — sheet {self.selected + 1} of {n} "
            "(numbers show the print order)")

    # ---------------- result ----------------

    def _accept(self):
        spec = self._spec()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pdf = poster.build_poster_pdf(self.path, spec, self.src.page, src=self.src)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Couldn't build the poster", str(e))
            return
        QApplication.restoreOverrideCursor()

        mode, fee = self.pricing_combo.currentData(), self.fee_spin.value()
        self.settings["poster"] = dict(poster.DEFAULTS, pricing_mode=mode, fee=fee)
        try:
            pricing.save_settings(self.settings)
        except OSError:
            pass
        self.result_data = {
            "pdf": pdf, "tile": self.selected,
            "job": {"source": self.path, "page": self.src.page, "spec": spec,
                    "pricing": {"mode": mode, "fee": fee}},
        }
        self.accept()