import functools
import gc
import math
import traceback

import numpy as np
from PIL import Image, ImageOps
from PyQt6.QtCore import Qt, QEvent, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget, QProgressBar
)

from printcost.core import matting, photofx
from printcost.ui.style import BORDER, MAGENTA, PREVIEW_BG, PRIMARY
from printcost.ui.download_dialog import DownloadDialog
from printcost.ui.modal import ModalDialog

LIGHT_SLIDERS = [("exposure", "Exposure"), ("shadows", "Shadows"), ("highlights", "Highlights"),
                 ("contrast", "Contrast"), ("warmth", "Warmth"), ("balance", "Left / right"), ("even", "Even out")]
UNDO_LIMIT = 15
IMAGE_BG = "image"
_orphans = []
LIGHT_BRUSHES = ("lighten", "darken", "light_clear")
EDGE_BRUSHES = ("sharpen", "smooth", "edge_clear")
BRUSH_TYPES = [("None", "view"), ("Restore", "restore"),
               ("Erase", "erase"), ("Accessory", "accessory"),
               ("Lighten", "lighten"), ("Darken", "darken"),
               ("Sharpen edge", "sharpen"), ("Smooth edge", "smooth"),
               ("Clear light strokes", "light_clear"), ("Clear edge strokes", "edge_clear")]
BRUSH_HINTS = {
    "view": "Pick a brush above, then paint on the photo. Lighten and Darken work on the whole photo. "
            "Restore, Erase and the edge brushes need the background removed first.",
    "restore": "Brings back parts the cutout removed (hair, shoulders, glasses).",
    "erase": "Removes leftover background.",
    "accessory": "Paint over an accessory (hat, headwear, jewelry, glasses, scarf) that is missing or broken. It uses the high quality "
                 "shape, so run Remove background with High quality ticked first.",
    "lighten": "Brightens only where you paint (dark side of the hair, shadows). Raise Strength and paint "
               "again for more.",
    "darken": "Darkens only where you paint (shiny or too bright spots).",
    "sharpen": "Makes the outline crisper where you paint. Good for soft, fuzzy spots.",
    "smooth": "Evens out a rough outline where you paint and keeps it crisp. Very thin loose strands can "
              "disappear, so lower Strength if needed.",
    "light_clear": "Paint over earlier Lighten / Darken strokes to remove them.",
    "edge_clear": "Paint over earlier Sharpen / Smooth strokes to remove them.",
}
CUTOUT_MODES = ("restore", "erase", "accessory") + EDGE_BRUSHES      # tools that need a cutout
BRUSH_COLORS = {"restore": PRIMARY, "erase": MAGENTA, "accessory": "#dc2626", "lighten": "#f59e0b", "darken": "#475569",
                "light_clear": "#94a3b8", "sharpen": "#16a34a", "smooth": "#7c3aed", "edge_clear": "#94a3b8"}


class FitWidthScroll(QScrollArea):
    """Scrolls up and down only. The content is never wider than the visible area, so something long
    (a file name, a message) can't make the panel slide sideways."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().rangeChanged.connect(lambda *_: self._fit())

    def _fit(self):
        if self.widget() is not None:
            self.widget().setMaximumWidth(max(1, self.viewport().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


def guarded(fn):
    """Qt slots must never raise: in PyQt6 an uncaught exception inside one closes the whole program.
    Show what went wrong (and print it) instead, and keep the dialog alive."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            self._last = None                 # drop a half-finished brush stroke
            self._dirty = None
            self._report_error(e, fn.__name__)
    return wrapper


class NoWheelCombo(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSlider(QSlider):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        super().wheelEvent(event) if self.hasFocus() else event.ignore()


class PhotoCanvas(QWidget):
    pressed = pyqtSignal(float, float)
    dragged = pyqtSignal(float, float)
    released = pyqtSignal()
    zoom_changed = pyqtSignal(float)
    MAX_ZOOM = 8.0

    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 560)
        self.setMouseTracking(True)
        self.pix = None          # QImage that shares memory with self.array
        self.array = None
        self.mode = "view"
        self.radius = 12         # brush radius in SCREEN pixels: it looks the same at any zoom
        self.zoom = 1.0          # 1.0 = the whole picture fits the window
        self.pan = QPointF(0, 0)
        self._hover = None
        self._down = False
        self._panning = None     # (mouse start, pan at start, button)

    def set_array(self, arr):
        self.array = np.array(arr, dtype=np.uint8, order="C")    # a copy: the brush repaints it in place
        h, w = self.array.shape[:2]
        self.pix = QImage(self.array.data, w, h, w * 3, QImage.Format.Format_RGB888)
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self._update_cursor()
        self.update()

    def _update_cursor(self):
        if self.mode != "view":
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.zoom > 1.001 else Qt.CursorShape.ArrowCursor)

    # ---------------- view (zoom / pan) ----------------

    def image_scale(self):
        pad = 12
        fit = min((self.width() - 2 * pad) / self.pix.width(), (self.height() - 2 * pad) / self.pix.height())
        return fit * self.zoom

    def brush_radius_image(self):
        """Brush radius in picture pixels (it shrinks as you zoom in, so the brush stays the same on screen)."""
        return self.radius / self.image_scale() if self.pix is not None else self.radius

    def _rect(self):
        s = self.image_scale()
        w, h = self.pix.width() * s, self.pix.height() * s
        return QRectF((self.width() - w) / 2 + self.pan.x(), (self.height() - h) / 2 + self.pan.y(), w, h)

    def _clamp_pan(self):
        if self.pix is None or self.zoom <= 1.001:
            self.pan = QPointF(0, 0)
            return
        s = self.image_scale()
        lx = max(0.0, (self.pix.width() * s + self.width()) / 2 - 60)
        ly = max(0.0, (self.pix.height() * s + self.height()) / 2 - 60)
        self.pan = QPointF(min(max(self.pan.x(), -lx), lx), min(max(self.pan.y(), -ly), ly))

    def zoom_to(self, z, anchor=None):
        """Zoom so the picture point under `anchor` (widget coordinates; default = the middle) stays put."""
        if self.pix is None:
            return
        z = min(max(z, 1.0), self.MAX_ZOOM)
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        ix, iy = self._to_image(anchor)
        self.zoom = z
        r = self._rect()
        s = self.image_scale()
        self.pan = QPointF(self.pan.x() + anchor.x() - (r.x() + ix * s), self.pan.y() + anchor.y() - (r.y() + iy * s))
        self._clamp_pan()
        self._update_cursor()
        self.update()
        self.zoom_changed.emit(self.zoom)

    def zoom_by(self, factor, anchor=None):
        self.zoom_to(self.zoom * factor, anchor)

    def fit(self):
        self.zoom_to(1.0)

    def wheelEvent(self, event):
        event.accept()
        if self.pix is None:
            return
        ad, pd = event.angleDelta(), event.pixelDelta()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)      # pinch on a touchpad = Ctrl + wheel
        touchpad = (not pd.isNull()) or ad.x() != 0 or ad.y() % 120 != 0 or \
            event.phase() != Qt.ScrollPhase.NoScrollPhase
        if touchpad and not ctrl:        # two-finger swipe: move the picture (when zoomed in)
            if self.zoom > 1.001:
                d = QPointF(pd) if not pd.isNull() else QPointF(ad.x() * 0.5, ad.y() * 0.5)
                self.pan = QPointF(self.pan.x() + d.x(), self.pan.y() + d.y())
                self._clamp_pan()
                self.update()
        elif ad.y():                     # mouse wheel (or pinch): zoom
            self.zoom_by(1.2 ** (ad.y() / 120.0), event.position())

    def resizeEvent(self, event):
        self._clamp_pan()
        super().resizeEvent(event)

    def _to_image(self, pos):
        r = self._rect()
        k = self.pix.width() / r.width()
        return (pos.x() - r.x()) * k, (pos.y() - r.y()) * k

    # ---------------- painting ----------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PREVIEW_BG))
        if self.pix is None:
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 3)   # crisp pixels when zoomed right in
        r = self._rect()
        vis = r.intersected(QRectF(self.rect()))
        if vis.width() > 0 and vis.height() > 0:
            s = r.width() / self.pix.width()
            p.drawImage(vis, self.pix, QRectF((vis.x() - r.x()) / s, (vis.y() - r.y()) / s, vis.width() / s, vis.height() / s))
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if self._hover is not None and self.mode in BRUSH_COLORS and self._panning is None:
            rad = self.radius
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor("white"), 3))
            p.drawEllipse(self._hover, rad, rad)
            p.setPen(QPen(QColor(BRUSH_COLORS[self.mode]), 1.5))
            p.drawEllipse(self._hover, rad, rad)

    # ---------------- mouse ----------------

    def mousePressEvent(self, event):
        if self.pix is None:
            return
        btn = event.button()
        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or \
                (btn == Qt.MouseButton.LeftButton and self.mode == "view"):
            if self.zoom > 1.001:            # middle / right drag (or left drag when no tool is on) moves the picture
                self._panning = (event.position(), QPointF(self.pan), btn)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if btn != Qt.MouseButton.LeftButton:
            return
        self._down = True
        self.pressed.emit(*self._to_image(event.position()))

    def mouseMoveEvent(self, event):
        self._hover = event.position()
        if self._panning is not None:
            start, pan0, _ = self._panning
            d = event.position() - start
            self.pan = QPointF(pan0.x() + d.x(), pan0.y() + d.y())
            self._clamp_pan()
        elif self._down:
            self.dragged.emit(*self._to_image(event.position()))
        self.update()

    def mouseReleaseEvent(self, event):
        if self._panning is not None and event.button() == self._panning[2]:
            self._panning = None
            self._update_cursor()
        elif self._down and event.button() == Qt.MouseButton.LeftButton:
            self._down = False
            self.released.emit()

    def leaveEvent(self, event):
        self._hover = None
        self.update()


class PhotoEditDialog(ModalDialog):
    movable = True
    fill = True
    def __init__(self, parent, pil_img, state=None, box=None, max_px=photofx.MAX_EXPORT_PX):
        """box = (x0, y0, x1, y1) in pil_img pixels: only this part (the head crop) is edited and returned,
        so the lighting and cutout are judged on the person, not on the whole picture.
        max_px = most pixels the returned picture may have (None = keep the original size)."""
        super().__init__(parent)
        self.max_px = max_px
        self.setWindowTitle("Edit photo")
        self.box = tuple(int(v) for v in box) if box is not None else None
        if self.box is not None:
            pil_img = pil_img.crop(self.box)
        work = pil_img.copy()
        work.thumbnail((photofx.WORK_PX, photofx.WORK_PX), Image.LANCZOS)
        has_alpha = pil_img.mode == "RGBA"
        self.full = pil_img.convert("RGB")
        self.work_img = work.convert("RGB")
        self.work = np.asarray(self.work_img, dtype=np.uint8).copy()
        self.alpha = np.asarray(work.getchannel("A")).copy() if has_alpha else None
        self.bg = photofx.TRANSPARENT if has_alpha else photofx.BACKGROUNDS[0][1]
        self.bg_image = None
        self._bg_cache = {}
        self.feather, self.shrink, self.sharp = 10, 1, photofx.DEFAULT_SHARP
        self.light = photofx.default_light()
        self.result_image = None
        self.result_state = None
        self._undo = []
        self._last = None
        self._lit = self.work           # global lighting + light brush
        self._lit_global = self.work    # global lighting only
        self._lit_dirty = True
        self.light_map = np.zeros(self.work.shape[:2], np.int8)    # light brush: + brighter, - darker
        self.edge_map = np.zeros(self.work.shape[:2], np.int8)     # edge brush: + sharper, - smoother
        self.light_strength, self.edge_strength = 35, 60
        self.mode_buttons = {}
        self._protect = None        # unsure areas inside the person, recomputed when the cutout changes
        self._parts = None          # (portrait matte, big-model matte) of the last high quality run
        self._hq_shape = None       # the big model's silhouette, used by the Accessory brush
        self._even_map = None       # light-evening map, computed on demand and when the cutout changes
        self._showing_original = False
        self._dirty = None          # image box changed by the brush since the last repaint
        self._brush_timer = QTimer(self)
        self._brush_timer.setSingleShot(True)
        self._brush_timer.setInterval(16)
        self._brush_timer.timeout.connect(self._flush_brush)
        self._worker = None
        self._progress = None
        self._creep = QTimer(self)
        self._creep.setInterval(500)
        self._creep.timeout.connect(self._creep_step)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._render)
        self._restore_state(state)
        self._build()
        QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), self).activated.connect(self._undo_stroke)
        self._sync_controls()
        self._render()
        self.resize(1040, 740)

    def _restore_state(self, state):
        if not state:
            return
        self.light = dict(state["light"])
        if state.get("alpha") is not None and state["alpha"].shape == self.work.shape[:2]:
            self.alpha = state["alpha"].copy()
            self.bg = state["bg"]
            self.bg_image = state.get("bg_image")
            if self.bg == IMAGE_BG and self.bg_image is None:
                self.bg = photofx.BACKGROUNDS[0][1]
            self.feather, self.shrink = state["feather"], state["shrink"]
            self.sharp = state.get("sharp", photofx.DEFAULT_SHARP)
            em = state.get("edge_map")
            if em is not None and em.shape == self.work.shape[:2]:
                self.edge_map = em.copy()
        lm = state.get("light_map")
        if lm is not None and lm.shape == self.work.shape[:2]:
            self.light_map = lm.copy()

    # ---------------- layout ----------------

    def _build(self):
        root = QHBoxLayout(self)
        root.setSpacing(16)
        left = QVBoxLayout()
        root.addLayout(left, 1)
        self.canvas = PhotoCanvas()
        self.canvas.pressed.connect(self._press)
        self.canvas.dragged.connect(self._drag)
        self.canvas.released.connect(self._release)
        left.addWidget(self.canvas, 1)
        bar = QHBoxLayout()
        self.compare_btn = QPushButton("Hold to see original")
        self.compare_btn.setObjectName("secondary")
        self.compare_btn.setToolTip("Hold this button, or hold the Shift key, to see the original photo")
        self.compare_btn.pressed.connect(lambda: self._compare(True))
        self.compare_btn.released.connect(lambda: self._compare(False))
        bar.addWidget(self.compare_btn)
        bar.addStretch()
        self.zoom_label = QLabel("1.0×")
        self.zoom_label.setObjectName("muted")
        self.zoom_label.setFixedWidth(42)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_out, zoom_in, zoom_fit = QPushButton("−"), QPushButton("+"), QPushButton("Fit")
        for b, tip in ((zoom_out, "Zoom out"), (zoom_in, "Zoom in"), (zoom_fit, "Show the whole photo")):
            b.setObjectName("secondary")
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        zoom_out.setFixedWidth(34)
        zoom_in.setFixedWidth(34)
        zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.4))
        zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.4))
        zoom_fit.clicked.connect(self.canvas.fit)
        self.canvas.zoom_changed.connect(lambda z: self.zoom_label.setText(f"{z:.1f}×"))
        for w in (zoom_out, self.zoom_label, zoom_in, zoom_fit):
            bar.addWidget(w)
        left.addLayout(bar)
        tips = QLabel("Scroll to zoom · two-finger swipe (or drag with the middle / right button) to move around · "
                      "Ctrl+Z to undo a stroke · hold Shift to see the original")
        tips.setObjectName("muted")
        left.addWidget(tips)

        right = QVBoxLayout()
        root.addLayout(right, 0)
        scroll = FitWidthScroll()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(390)
        holder = QWidget()
        body = QVBoxLayout(holder)
        body.setContentsMargins(4, 4, 10, 4)
        body.setSpacing(12)
        scroll.setWidget(holder)
        right.addWidget(scroll, 1)

        def section(title):
            head = QLabel(title)
            head.setObjectName("cardHeader")
            head.setContentsMargins(0, 18 if body.count() else 0, 0, 0)
            body.addWidget(head)
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            body.addWidget(line)
            v = QVBoxLayout()
            v.setSpacing(10)
            body.addLayout(v)
            return v

        v = section("Background")
        row = QHBoxLayout()
        self.remove_btn = QPushButton("Remove background")
        self.remove_btn.setObjectName("primary")
        self.remove_btn.clicked.connect(self._remove_bg)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.setToolTip("Go back to the original background")
        self.clear_btn.clicked.connect(self._clear_cutout)
        row.addWidget(self.remove_btn, 1)
        row.addWidget(self.clear_btn)
        v.addLayout(row)
        self.hq_check = QCheckBox("High quality (slower)")
        self.hq_check.setToolTip("Better for hats, headwear, veils and other accessories")
        v.addWidget(self.hq_check)
        self.detail_slider = self._slider(v, "Accessory detail", 0, 100, 0, self._set_detail)
        self.detail_slider.setToolTip("Higher fills in accessory shapes and edges everywhere, but can make hair edges harder. "
                                      "To fix one spot only, use the Accessory brush.")
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(250)
        self._detail_timer.timeout.connect(self._remerge)
        hint = QLabel("Runs on this PC. The photo never leaves it.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        sw = QHBoxLayout()
        sw.setSpacing(6)
        self.swatches = []
        for name, color in photofx.BACKGROUNDS:
            b = QPushButton(name)
            b.setFixedHeight(32)
            b.clicked.connect(lambda _, c=color: self._set_bg(c))
            self.swatches.append((b, color))
            sw.addWidget(b)
        self.custom_btn = QPushButton("Custom…")
        self.custom_btn.setFixedHeight(32)
        self.custom_btn.clicked.connect(self._pick_bg_color)
        sw.addWidget(self.custom_btn)
        v.addLayout(sw)
        self.transparent_btn = QPushButton("Transparent")
        self.transparent_btn.setFixedHeight(32)
        self.transparent_btn.setToolTip("Keep the background empty (prints as the paper color)")
        self.transparent_btn.clicked.connect(lambda: self._set_bg(photofx.TRANSPARENT))
        v.addWidget(self.transparent_btn)
        self.image_btn = QPushButton("Import background…")
        self.image_btn.setFixedHeight(32)
        self.image_btn.setToolTip("Use a picture as the background")
        self.image_btn.clicked.connect(self._import_bg)
        v.addWidget(self.image_btn)
        self.feather_slider = self._slider(v, "Feather", 0, 60, self.feather, self._set_feather,
                                           lambda x: f"{x / 10:.1f}")
        self.shrink_slider = self._slider(v, "Shrink edge", 0, 5, self.shrink, self._set_shrink)
        self.sharp_slider = self._slider(v, "Edge sharpness", 0, 100, self.sharp, self._set_sharp)

        v = section("Brush")
        row = QHBoxLayout()
        row.setSpacing(6)
        self.brush_combo = NoWheelCombo()
        for label, mode in BRUSH_TYPES:
            self.brush_combo.addItem(label, mode)
        self.brush_combo.currentIndexChanged.connect(lambda i: self._set_mode(self.brush_combo.itemData(i)))
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setObjectName("secondary")
        self.undo_btn.setToolTip("Undo the last brush stroke (Ctrl+Z)")
        self.undo_btn.clicked.connect(self._undo_stroke)
        row.addWidget(self.brush_combo, 1)
        row.addWidget(self.undo_btn)
        v.addLayout(row)
        self.brush_slider = self._slider(v, "Brush size", 3, 60, 12, self._set_brush)
        self.strength_slider = self._slider(v, "Strength", 5, 100, self.light_strength, self._set_strength)
        self.brush_hint = QLabel("")
        self.brush_hint.setObjectName("muted")
        self.brush_hint.setWordWrap(True)
        v.addWidget(self.brush_hint)

        v = section("Lighting")
        self.light_sliders = {}
        for key, label in LIGHT_SLIDERS:
            self.light_sliders[key] = self._slider(v, label, -100, 100, self.light.get(key, 0),
                                                   lambda x, k=key: self._set_light(k, x))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.auto_btn = QPushButton("Auto")
        self.auto_btn.setObjectName("secondary")
        self.auto_btn.clicked.connect(self._auto)
        self.wp_btn = self._mode_button("White point", "pick")
        self.wp_btn.setToolTip("Click something that should be white (shirt, wall) to fix the color cast")
        self.light_reset = QPushButton("Reset")
        self.light_reset.setObjectName("secondary")
        self.light_reset.clicked.connect(self._reset_light)
        for b in (self.auto_btn, self.wp_btn, self.light_reset):
            row.addWidget(b)
        v.addLayout(row)
        self.light_sliders["even"].setToolTip("Lifts the shadow side of the person (adapts to the photo, row by row)")
        self.light_sliders["balance"].setToolTip(
            "Drag right if the right side of the face is too dark (left is too bright), left for the opposite")
        self.balance_btn = QPushButton("Even out left / right light")
        self.balance_btn.setObjectName("secondary")
        self.balance_btn.setToolTip("Lifts the darker side of the face and body so the light looks even")
        self.balance_btn.clicked.connect(self._auto_balance)
        v.addWidget(self.balance_btn)

        body.addStretch()

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Use this photo")
        ok.setObjectName("cta")
        ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addStretch()
        buttons.addWidget(ok)
        right.addLayout(buttons)

    def _slider(self, layout, label, lo, hi, value, on_change, fmt=str):
        row = QHBoxLayout()
        name = QLabel(label)
        name.setFixedWidth(116)
        s = NoWheelSlider()
        s.setRange(lo, hi)
        s.setValue(value)
        val = QLabel(fmt(value))
        val.setObjectName("muted")
        val.setFixedWidth(34)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        s.valueChanged.connect(lambda x: (val.setText(fmt(x)), on_change(x)))
        step = max(1, round((hi - lo) / 40))

        def stepper(text, sign):
            b = QPushButton(text)
            b.setFixedSize(24, 24)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(60)
            b.setStyleSheet("QPushButton { padding: 0; font-size: 15px; }")
            b.clicked.connect(lambda: s.setValue(s.value() + sign * step))
            return b

        minus, plus = stepper("−", -1), stepper("+", 1)
        row.addWidget(name)
        row.addWidget(minus)
        row.addWidget(s, 1)
        row.addWidget(plus)
        row.addWidget(val)
        layout.addLayout(row)
        s.group = (name, minus, plus, val)
        return s

    @staticmethod
    def _enable_slider(s, on):
        s.setEnabled(on)
        for w in s.group:
            w.setEnabled(on)

    def _sync_brush_sliders(self):
        mode = self.canvas.mode
        self._enable_slider(self.strength_slider, mode in ("lighten", "darken", "sharpen", "smooth"))
        self._enable_slider(self.brush_slider, mode in BRUSH_COLORS)

    def _mode_button(self, text, mode):
        b = QPushButton(text)
        b.setObjectName("secondary")
        b.setCheckable(True)
        b.toggled.connect(lambda on, m=mode: self._set_mode(m if on else "view"))
        self.mode_buttons[mode] = b
        return b

    def _sync_controls(self):
        has = self.alpha is not None
        hq_ready = has and self._hq_shape is not None
        for w in (self.clear_btn, self.custom_btn, self.transparent_btn, self.image_btn, *[b for b, _ in self.swatches]):
            w.setEnabled(has)
        for s in (self.feather_slider, self.shrink_slider, self.sharp_slider):
            self._enable_slider(s, has)
        self._enable_slider(self.detail_slider, has and self._parts is not None)
        model = self.brush_combo.model()
        for i in range(self.brush_combo.count()):
            m = self.brush_combo.itemData(i)
            if m in CUTOUT_MODES:
                model.item(i).setEnabled(hq_ready if m == "accessory" else has)
        self.undo_btn.setEnabled(bool(self._undo))
        self.remove_btn.setText("Redo cutout" if has else "Remove background")
        mode = self.canvas.mode
        if (mode in CUTOUT_MODES and not has) or (mode == "accessory" and not hq_ready):
            self._set_mode("view")
        self._sync_brush_sliders()
        presets = {c.lower() for _, c in photofx.BACKGROUNDS}
        for b, color in self.swatches:
            self._style_swatch(b, color, color.lower() == self.bg.lower())
        clear = self.bg == photofx.TRANSPARENT
        custom = self.bg.lower() not in presets and not clear and self.bg != IMAGE_BG
        self._style_swatch(self.custom_btn, self.bg if custom else "#ffffff", custom)
        self._style_swatch(self.transparent_btn, "#eef1f5", clear)
        self._style_swatch(self.image_btn, "#eef1f5", self.bg == IMAGE_BG)

    @staticmethod
    def _style_swatch(button, color, selected):
        r, g, b = photofx.hex_to_rgb(color)
        text = "#ffffff" if (r * 0.299 + g * 0.587 + b * 0.114) < 150 else "#0f1b2d"
        button.setStyleSheet(
            f"QPushButton {{ background: {color}; color: {text}; padding: 4px 8px; "
            f"border: {3 if selected else 1}px solid {PRIMARY if selected else BORDER}; }}"
            f"QPushButton:hover {{ background: {color}; border-color: {PRIMARY}; }}"
            f"QPushButton:disabled {{ background: {color}; color: {text}; border-color: {BORDER}; }}")

    # ---------------- rendering ----------------

    @guarded
    def _render(self, fast=False):
        if self._lit_dirty:
            self._lit_global = photofx.apply_lighting(self.work, self.light, self._get_even_map())
            self._lit = photofx.apply_local_light(self._lit_global, self.light_map)
            self._lit_dirty = False
        if self._showing_original:
            view = self.work
        elif self.alpha is None:
            view = self._lit
        else:
            a = photofx.finish_alpha(self.alpha, self.feather / 10, self.shrink, self.sharp, 1.0,
                                     self._get_protect(), self.edge_map)
            if self.bg == photofx.TRANSPARENT:
                view = photofx.over_checker(photofx.compose_transparent(self._lit, a, clean=not fast))
            else:
                view = photofx.compose(self._lit, a, self._bg_value(*self.work.shape[:2]), clean=not fast)
        self.canvas.set_array(view)

    def _get_even_map(self):
        if not self.light.get("even"):
            return None
        if self._even_map is None:
            self._even_map = photofx.make_even_map(self.work, self.alpha)
        return self._even_map

    def _get_protect(self):
        if self._protect is None and self.alpha is not None:
            self._protect = photofx.interior_mask(self.alpha)
        return self._protect

    def _alpha_changed(self):
        self._protect = None
        self._even_map = None
        if self.light.get("even"):
            self._lit_dirty = True

    def _report_error(self, e, where=""):
        if getattr(self, "_reporting", False):
            return
        self._reporting = True
        try:
            QMessageBox.warning(self, "Something went wrong",
                                f"{type(e).__name__}: {e}\n\nThe photo editor is still open, so your work is "
                                f"kept. If it keeps happening, tell us this message (step: {where}).")
        finally:
            self._reporting = False

    def _compare(self, on):
        self._showing_original = on
        self._render()

    # ---------------- background ----------------

    def _set_bg(self, color):
        self.bg = color
        self._sync_controls()
        self._render()

    def _pick_bg_color(self):
        start = "#ffffff" if self.bg in (photofx.TRANSPARENT, IMAGE_BG) else self.bg
        color = QColorDialog.getColor(QColor(start), self, "Background color")
        if color.isValid():
            self._set_bg(color.name())

    def _import_bg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import background", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.avif *.jfif)")
        if not path:
            return
        try:
            with Image.open(path) as im:
                img = ImageOps.exif_transpose(im).convert("RGB")
        except Exception as e:
            QMessageBox.warning(self, "Import background", f"Couldn't open that picture.\n\n{e}")
            return
        img.thumbnail((4000, 4000), Image.LANCZOS)
        self.bg_image = img
        self._bg_cache = {}
        self._set_bg(IMAGE_BG)

    def _bg_value(self, h, w, box=None, cache=True):
        if self.bg != IMAGE_BG:
            return photofx.hex_to_rgb(self.bg)
        arr = self._bg_cache.get((w, h))
        if arr is None:
            src = self.bg_image
            k = max(w / src.width, h / src.height)
            r = src.resize((max(w, round(src.width * k)), max(h, round(src.height * k))), Image.LANCZOS)
            x, y = (r.width - w) // 2, (r.height - h) // 2
            arr = np.asarray(r.crop((x, y, x + w, y + h)), dtype=np.uint8)
            if cache:
                self._bg_cache[(w, h)] = arr
        return arr if box is None else arr[box[1]:box[3], box[0]:box[2]]

    def _set_feather(self, v):
        self.feather = v
        self._timer.start()

    def _set_shrink(self, v):
        self.shrink = v
        self._timer.start()

    def _set_sharp(self, v):
        self.sharp = v
        self._timer.start()

    def _clear_cutout(self):
        self.alpha = None
        self._parts = None
        self._hq_shape = None
        self.edge_map[:] = 0
        self._alpha_changed()
        self._undo.clear()
        self._sync_controls()
        self._render()

    def _remove_bg(self):
        err = matting.runtime_error()
        if err:
            QMessageBox.warning(self, "Background remover", err)
            return
        if matting.is_installed():
            if self.hq_check.isChecked() and not matting.hq_installed():
                self._get_hq_pack()
            else:
                self._run_matte(self.hq_check.isChecked())
            return
        ask = QMessageBox.question(
            self, "One-time download",
            f"The background remover needs a small model (about {matting.MODEL_SIZE_MB} MB). "
            "It is saved on this PC and works offline afterwards.\n\nDownload it now?")
        if ask != QMessageBox.StandardButton.Yes:
            return
        self._progress = DownloadDialog(self, "Downloading", "Downloading the background remover...")
        worker = matting.ModelDownloadWorker(self)
        worker.progress.connect(self._download_progress)
        worker.done.connect(self._download_done)
        worker.failed.connect(self._download_failed)
        self._progress.canceled.connect(worker.cancel)
        self._worker = worker
        worker.start()

    def _download_progress(self, done, total):
        if self._progress is not None:
            self._progress.update_progress(done, total)

    def _download_done(self):
        self._close_progress()
        self._remove_bg()

    def _download_failed(self, msg):
        self._close_progress()
        if msg != "Cancelled":
            QMessageBox.warning(self, "Download failed", f"{msg}\n\nCheck your internet connection and try again.")

    def _get_hq_pack(self):
        box = QMessageBox(self)
        box.setWindowTitle("High quality pack")
        box.setText(f"High quality cutouts need an extra model (about {matting.HQ_SIZE_MB} MB). "
                    "It is saved on this PC and works offline afterwards.")
        download = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        have_file = box.addButton("I have the file…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is download:
            self._download_hq()
        elif box.clickedButton() is have_file:
            self._import_hq()

    def _download_hq(self):
        self._progress = DownloadDialog(self, "Downloading", "Downloading the high quality pack...")
        worker = matting.ModelDownloadWorker(self, kind="hq")
        worker.progress.connect(self._download_progress)
        worker.done.connect(self._hq_download_done)
        worker.failed.connect(self._download_failed)
        self._progress.canceled.connect(worker.cancel)
        self._worker = worker
        worker.start()

    def _hq_download_done(self):
        self._close_progress()
        self._run_matte(True)

    def _import_hq(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the high quality model file", "", "ONNX model (*.onnx)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            matting.install_hq(path)
        except Exception as e:
            QMessageBox.warning(self, "Can't use this file", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._run_matte(True)

    def _run_matte(self, hq=False):
        self._progress = QProgressDialog("Removing the background...", None, 0, 100 if hq else 0, self)
        bar = QProgressBar(self._progress)
        bar.setTextVisible(False)
        self._progress.setBar(bar)
        self._progress.setWindowTitle("Please wait")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        if hq:
            self._progress.setValue(0)
        self._progress.setFixedSize(440, 110)
        self._progress.canceled.connect(self._cancel_matte)
        self._progress.show()
        worker = matting.MatteWorker(self.work_img, self, hq=hq)
        if hq:
            worker.stage.connect(self._matte_stage)
        worker.done.connect(self._matte_done)
        worker.failed.connect(self._matte_failed)
        self._worker = worker
        worker.start(worker.Priority.LowPriority)

    def _matte_stage(self, value, text):
        if self._progress is None:
            return
        self._progress.setValue(value)
        self._progress.setLabelText(text)
        if value == 20:
            self._creep.start()
        else:
            self._creep.stop()

    def _creep_step(self):
        if self._progress is None or self._progress.value() >= 90:
            self._creep.stop()
            return
        v = self._progress.value()
        self._progress.setValue(v + max(1, (90 - v) // 20))

    def _cancel_matte(self):
        w = self._worker
        if not isinstance(w, matting.MatteWorker) or not w.isRunning():
            return
        w.cancel()
        for sig in (w.done, w.failed, w.stage):
            try:
                sig.disconnect()
            except TypeError:
                pass
        w.setParent(None)
        _orphans.append(w)
        w.finished.connect(lambda: _orphans.remove(w))
        self._worker = None
        self._close_progress()

    def _matte_done(self, alpha):
        self._creep.stop()
        if self._progress is not None:
            self._progress.setLabelText("Finishing the cutout...")
            QApplication.processEvents()
        try:
            self._matte_apply(alpha)
        finally:
            self._close_progress()

    @guarded
    def _matte_apply(self, alpha):
        self._parts = getattr(self._worker, "parts", None)
        self._hq_shape = matting.hq_shape(self._parts[1]) if self._parts else None
        if self._parts:
            self._apply_merge()
        else:
            self.alpha = photofx.refine_matte(self.work, alpha)    # snap the soft model output to the real edges
            self.edge_map[:] = 0
            self._alpha_changed()
        self._undo.clear()
        self._sync_controls()
        self._render()

    def _apply_merge(self):
        merged, added = matting.merge_parts(*self._parts, self.detail_slider.value())
        self.alpha = np.maximum(photofx.refine_matte(self.work, merged), added)
        self.edge_map[:] = 0
        self._alpha_changed()

    def _set_detail(self, v):
        self._detail_timer.start()

    def _remerge(self):
        if self._parts is None or self.alpha is None:
            return
        if self._undo:
            QMessageBox.information(self, "Accessory detail",
                                    "Set this before using the brush. Click Remove background again to redo the cutout.")
            return
        self._apply_merge()
        self._render()

    def _matte_failed(self, msg):
        self._close_progress()
        QMessageBox.warning(self, "Couldn't remove the background", msg)

    def _close_progress(self):
        self._creep.stop()
        if self._progress is not None:
            try:
                self._progress.canceled.disconnect()
            except TypeError:
                pass
            self._progress.close()
            self._progress = None

    # ---------------- brush ----------------

    def _set_mode(self, mode):
        self.canvas.set_mode(mode)
        for m, btn in self.mode_buttons.items():          # the White point button
            btn.blockSignals(True)
            btn.setChecked(m == mode)
            btn.blockSignals(False)
        idx = self.brush_combo.findData(mode)
        self.brush_combo.blockSignals(True)
        self.brush_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.brush_combo.blockSignals(False)
        self._sync_brush_sliders()
        if mode in ("lighten", "darken"):
            self.strength_slider.setValue(self.light_strength)
        elif mode in ("sharpen", "smooth"):
            self.strength_slider.setValue(self.edge_strength)
        self.brush_hint.setText(BRUSH_HINTS.get(mode, ""))

    def _set_brush(self, v):
        self.canvas.radius = v
        self.canvas.update()

    def _set_strength(self, v):
        if self.canvas.mode in ("lighten", "darken"):
            self.light_strength = v
        elif self.canvas.mode in ("sharpen", "smooth"):
            self.edge_strength = v

    def _dab(self, x, y):
        mode, r = self.canvas.mode, self.canvas.brush_radius_image()
        if mode in ("restore", "erase"):
            box = photofx.paint(self.alpha, x, y, r, 255 if mode == "restore" else 0)
        elif mode == "accessory":
            box = photofx.paint_from(self.alpha, self._hq_shape, x, y, r)
        elif mode in LIGHT_BRUSHES:
            value = {"lighten": self.light_strength, "darken": -self.light_strength, "light_clear": 0}[mode]
            box = photofx.paint_map(self.light_map, x, y, r, value, softness=1.0)
        else:
            value = {"sharpen": self.edge_strength, "smooth": -self.edge_strength, "edge_clear": 0}[mode]
            box = photofx.paint_map(self.edge_map, x, y, r, value, softness=0.6)
        if box is not None:
            d = self._dirty
            self._dirty = box if d is None else (min(d[0], box[0]), min(d[1], box[1]),
                                                 max(d[2], box[2]), max(d[3], box[3]))

    @guarded
    def _flush_brush(self):
        """Repaint only the part of the picture the brush touched (at most once per frame)."""
        box, self._dirty = self._dirty, None
        if box is None or self._showing_original or self.canvas.array is None:
            return
        h, w = self.work.shape[:2]
        x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
        if self.canvas.mode in LIGHT_BRUSHES:
            if self._lit is self._lit_global:       # never paint into the shared global image
                self._lit = self._lit_global.copy()
            self._lit[y0:y1, x0:x1] = photofx.apply_local_light(self._lit_global[y0:y1, x0:x1],
                                                                self.light_map[y0:y1, x0:x1])
        if self.alpha is None:
            self.canvas.array[y0:y1, x0:x1] = self._lit[y0:y1, x0:x1]
        else:
            m = int(3 * self.feather / 10) + 14          # extra border so the edge blur matches the rest
            X0, Y0, X1, Y1 = max(0, box[0] - m), max(0, box[1] - m), min(w, box[2] + m), min(h, box[3] + m)
            prot = self._get_protect()
            a = photofx.finish_alpha(self.alpha[Y0:Y1, X0:X1], self.feather / 10, self.shrink, self.sharp, 1.0,
                                     None if prot is None else prot[Y0:Y1, X0:X1], self.edge_map[Y0:Y1, X0:X1])
            if self.bg == photofx.TRANSPARENT:
                piece = photofx.over_checker(photofx.compose_transparent(self._lit[Y0:Y1, X0:X1], a, clean=False),
                                             origin=(X0, Y0))
            else:
                piece = photofx.compose(self._lit[Y0:Y1, X0:X1], a, self._bg_value(h, w, (X0, Y0, X1, Y1)), clean=False)
            self.canvas.array[y0:y1, x0:x1] = piece[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0]
        self.canvas.update()

    def _snapshot(self):
        return (None if self.alpha is None else self.alpha.copy(), self.light_map.copy(), self.edge_map.copy())

    @guarded
    def _press(self, x, y):
        mode = self.canvas.mode
        if mode == "pick":
            self._pick(x, y)
            return
        if mode in LIGHT_BRUSHES:
            if self._lit_dirty:
                self._render()
        elif self.alpha is None or (mode == "accessory" and self._hq_shape is None):
            return
        self._undo.append(self._snapshot())
        del self._undo[:-UNDO_LIMIT]
        self.undo_btn.setEnabled(True)
        self._last = (x, y)
        self._dab(x, y)
        self._brush_timer.start()

    @guarded
    def _drag(self, x, y):
        if self._last is None:
            return
        lx, ly = self._last
        steps = max(1, int(math.hypot(x - lx, y - ly) / max(1.0, self.canvas.brush_radius_image() * 0.25)))
        for i in range(1, steps + 1):
            t = i / steps
            self._dab(lx + (x - lx) * t, ly + (y - ly) * t)
        self._last = (x, y)
        self._brush_timer.start()

    @guarded
    def _release(self):
        if self._last is not None:
            self._last = None
            self._brush_timer.stop()
            self._dirty = None
            if self.canvas.mode in ("restore", "erase", "accessory"):
                self._alpha_changed()
            self._render()          # one full-quality pass (with edge color cleanup) when the stroke ends

    def _undo_stroke(self):
        if self._undo and self._last is None:
            alpha, self.light_map, self.edge_map = self._undo.pop()
            if alpha is not None:
                self.alpha = alpha
            self._alpha_changed()
            self._lit_dirty = True
            self.undo_btn.setEnabled(bool(self._undo))
            self._render()

    # ---------------- lighting ----------------

    def _set_light(self, key, v):
        self.light[key] = v
        self._lit_dirty = True
        self._timer.start()

    def _auto(self):
        self.light.update(photofx.auto_lighting(self.work, self.alpha))
        self.light["even"] = 70
        for key, s in self.light_sliders.items():
            s.setValue(int(self.light[key]))
        self._lit_dirty = True
        self._render()

    def _auto_balance(self):
        self.light["even"] = 80
        self.light_sliders["even"].setValue(80)
        self._lit_dirty = True
        self._render()

    def _reset_light(self):
        self.light = photofx.default_light()
        self.light_map[:] = 0
        for s in self.light_sliders.values():
            s.setValue(0)
        self._lit_dirty = True
        self._render()

    def _pick(self, x, y):
        h, w = self.work.shape[:2]
        xi, yi = int(min(max(x, 0), w - 1)), int(min(max(y, 0), h - 1))
        patch = self.work[max(0, yi - 3):yi + 4, max(0, xi - 3):xi + 4].reshape(-1, 3).mean(axis=0)
        self.light["gain"] = photofx.white_gain(patch)
        self._lit_dirty = True
        self._set_mode("view")
        self._render()

    # ---------------- hold Shift to see the original ----------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Shift and not event.isAutoRepeat():
            self._compare(True)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Shift and not event.isAutoRepeat():
            self._compare(False)
            return
        super().keyReleaseEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self._showing_original:
            self._compare(False)          # the key release would go to another window
        super().changeEvent(event)

    # ---------------- result ----------------

    def _export(self, max_px="default"):
        """The finished picture. max_px = most pixels it may have (None = the original size)."""
        if max_px == "default":
            max_px = self.max_px
        full = self.full
        k = math.sqrt(max_px / (full.width * full.height)) if max_px else 1.0
        if k < 1:
            full = full.resize((round(full.width * k), round(full.height * k)), Image.LANCZOS)
        out = photofx.light_full(np.asarray(full, dtype=np.uint8), self.light, self._get_even_map(), self.light_map)
        if self.alpha is not None:
            a = photofx.export_alpha(out, self.alpha, self.feather / 10, self.shrink, self.sharp, self.edge_map)
            if self.bg == photofx.TRANSPARENT:
                out = photofx.compose_transparent(out, a, clean=True)
            else:
                out = photofx.compose(out, a, self._bg_value(*out.shape[:2], cache=False), clean=True)
            del a
        return Image.fromarray(out)

    def _export_safely(self):
        """Returns (picture, note) or (None, None) when it failed. A big picture can run out of memory:
        then it is retried at half the pixels, up to three times, and the person is told."""
        px = self.full.width * self.full.height
        if self.max_px:
            px = min(px, self.max_px)
        first = px
        error = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for attempt in range(4):
                try:
                    img = self._export(None if attempt == 0 and not self.max_px else px)
                    note = None if px == first else (
                        f"The computer ran out of memory at full size, so the edited photo was made smaller "
                        f"({img.width} x {img.height} px). Choose a lower resolution next time for big photos.")
                    return img, note
                except MemoryError as e:
                    error = e
                    gc.collect()
                    px = int(px / 2)
                    if px < 1_500_000:
                        break
                except Exception as e:
                    traceback.print_exc()
                    error = e
                    break
        finally:
            QApplication.restoreOverrideCursor()
        QMessageBox.warning(self, "Couldn't finish the photo",
                            f"{type(error).__name__}: {error}\n\nTry again with a lower resolution (the "
                            "picture size question when you press Edit photo).")
        return None, None

    def _accept(self):
        changed = self.alpha is not None or not photofx.is_default_light(self.light) or bool(self.light_map.any())
        if changed:
            image, note = self._export_safely()
            if image is None:
                return                     # stay in the editor; nothing is lost
            self.result_image = image
            self.result_state = {
                "alpha": None if self.alpha is None else self.alpha.copy(),
                "bg": self.bg, "bg_image": self.bg_image, "feather": self.feather, "shrink": self.shrink, "sharp": self.sharp,
                "box": self.box,
                "light": dict(self.light),
                "light_map": self.light_map.copy(), "edge_map": self.edge_map.copy(),
            }
            if note:
                QMessageBox.information(self, "Photo made smaller", note)
        self.accept()