import math

import numpy as np
from PIL import Image
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressDialog,
    QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from printcost.core import matting, photofx
from printcost.ui.style import BORDER, MAGENTA, PREVIEW_BG, PRIMARY

LIGHT_SLIDERS = [("exposure", "Exposure"), ("shadows", "Shadows"), ("highlights", "Highlights"),
                 ("contrast", "Contrast"), ("warmth", "Warmth")]
UNDO_LIMIT = 15


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

    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 560)
        self.setMouseTracking(True)
        self.pix = None
        self.mode = "view"
        self.radius = 18
        self._hover = None
        self._down = False

    def set_array(self, arr):
        arr = np.ascontiguousarray(arr)
        h, w = arr.shape[:2]
        self.pix = QPixmap.fromImage(QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy())
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode != "view" else Qt.CursorShape.ArrowCursor)
        self.update()

    def _rect(self):
        pad = 12
        s = min((self.width() - 2 * pad) / self.pix.width(), (self.height() - 2 * pad) / self.pix.height())
        w, h = self.pix.width() * s, self.pix.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_image(self, pos):
        r = self._rect()
        k = self.pix.width() / r.width()
        return (pos.x() - r.x()) * k, (pos.y() - r.y()) * k

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PREVIEW_BG))
        if self.pix is None:
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self._rect()
        p.drawPixmap(r, self.pix, QRectF(self.pix.rect()))
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if self._hover is not None and self.mode in ("restore", "erase"):
            rad = self.radius * r.width() / self.pix.width()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor("white"), 3))
            p.drawEllipse(self._hover, rad, rad)
            p.setPen(QPen(QColor(MAGENTA if self.mode == "erase" else PRIMARY), 1.5))
            p.drawEllipse(self._hover, rad, rad)

    def mousePressEvent(self, event):
        if self.pix is None or event.button() != Qt.MouseButton.LeftButton or self.mode == "view":
            return
        self._down = True
        self.pressed.emit(*self._to_image(event.position()))

    def mouseMoveEvent(self, event):
        self._hover = event.position()
        if self._down:
            self.dragged.emit(*self._to_image(event.position()))
        self.update()

    def mouseReleaseEvent(self, event):
        if self._down:
            self._down = False
            self.released.emit()

    def leaveEvent(self, event):
        self._hover = None
        self.update()


class PhotoEditDialog(QDialog):
    def __init__(self, parent, pil_img, state=None):
        super().__init__(parent)
        self.setWindowTitle("Edit photo")
        self.full = pil_img
        work = pil_img.copy()
        work.thumbnail((photofx.WORK_PX, photofx.WORK_PX), Image.LANCZOS)
        self.work_img = work
        self.work = np.asarray(work, dtype=np.uint8).copy()
        self.alpha = None
        self.bg = photofx.BACKGROUNDS[0][1]
        self.feather, self.shrink = 10, 1
        self.light = photofx.default_light()
        self.result_image = None
        self.result_state = None
        self._undo = []
        self._last = None
        self._lit = self.work
        self._lit_dirty = True
        self._showing_original = False
        self._worker = None
        self._progress = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._render)
        self._restore_state(state)
        self._build()
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
            self.feather, self.shrink = state["feather"], state["shrink"]

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
        self.compare_btn = QPushButton("Hold to compare")
        self.compare_btn.setObjectName("secondary")
        self.compare_btn.pressed.connect(lambda: self._compare(True))
        self.compare_btn.released.connect(lambda: self._compare(False))
        bar.addWidget(self.compare_btn)
        bar.addStretch()
        left.addLayout(bar)

        right = QVBoxLayout()
        root.addLayout(right, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(340)
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
        self.feather_slider = self._slider(v, "Feather", 0, 60, self.feather, self._set_feather,
                                           lambda x: f"{x / 10:.1f}")
        self.shrink_slider = self._slider(v, "Shrink edge", 0, 5, self.shrink, self._set_shrink)

        v = section("Refine edges")
        row = QHBoxLayout()
        row.setSpacing(6)
        self.restore_btn = self._mode_button("Restore", "restore")
        self.erase_btn = self._mode_button("Erase", "erase")
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setObjectName("secondary")
        self.undo_btn.clicked.connect(self._undo_stroke)
        for b in (self.restore_btn, self.erase_btn, self.undo_btn):
            row.addWidget(b)
        v.addLayout(row)
        self.brush_slider = self._slider(v, "Brush size", 4, 80, 18, self._set_brush)
        hint = QLabel("Restore brings back parts the cutout removed (hair, shoulders, glasses). "
                      "Erase removes leftover background.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        v.addWidget(hint)

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
        name.setFixedWidth(84)
        s = NoWheelSlider()
        s.setRange(lo, hi)
        s.setValue(value)
        val = QLabel(fmt(value))
        val.setObjectName("muted")
        val.setFixedWidth(34)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        s.valueChanged.connect(lambda x: (val.setText(fmt(x)), on_change(x)))
        row.addWidget(name)
        row.addWidget(s, 1)
        row.addWidget(val)
        layout.addLayout(row)
        return s

    def _mode_button(self, text, mode):
        b = QPushButton(text)
        b.setObjectName("secondary")
        b.setCheckable(True)
        b.toggled.connect(lambda on, m=mode: self._set_mode(m if on else "view"))
        return b

    def _sync_controls(self):
        has = self.alpha is not None
        for w in (self.clear_btn, self.restore_btn, self.erase_btn, self.brush_slider, self.feather_slider,
                  self.shrink_slider, self.custom_btn, self.compare_btn, *[b for b, _ in self.swatches]):
            w.setEnabled(has)
        self.undo_btn.setEnabled(bool(self._undo))
        self.remove_btn.setText("Redo cutout" if has else "Remove background")
        if not has and self.canvas.mode in ("restore", "erase"):
            self._set_mode("view")
        presets = {c.lower() for _, c in photofx.BACKGROUNDS}
        for b, color in self.swatches:
            self._style_swatch(b, color, color.lower() == self.bg.lower())
        custom = self.bg.lower() not in presets
        self._style_swatch(self.custom_btn, self.bg if custom else "#ffffff", custom)

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

    def _render(self, fast=False):
        if self._lit_dirty:
            self._lit = photofx.apply_lighting(self.work, self.light)
            self._lit_dirty = False
        if self._showing_original:
            view = self.work
        elif self.alpha is None:
            view = self._lit
        else:
            a = photofx.finish_alpha(self.alpha, self.feather / 10, self.shrink)
            view = photofx.compose(self._lit, a, photofx.hex_to_rgb(self.bg), clean=not fast)
        self.canvas.set_array(view)

    def _compare(self, on):
        self._showing_original = on
        self._render()

    # ---------------- background ----------------

    def _set_bg(self, color):
        self.bg = color
        self._sync_controls()
        self._render()

    def _pick_bg_color(self):
        color = QColorDialog.getColor(QColor(self.bg), self, "Background color")
        if color.isValid():
            self._set_bg(color.name())

    def _set_feather(self, v):
        self.feather = v
        self._timer.start()

    def _set_shrink(self, v):
        self.shrink = v
        self._timer.start()

    def _clear_cutout(self):
        self.alpha = None
        self._undo.clear()
        self._sync_controls()
        self._render()

    def _remove_bg(self):
        err = matting.runtime_error()
        if err:
            QMessageBox.warning(self, "Background remover", err)
            return
        if matting.is_installed():
            self._run_matte()
            return
        ask = QMessageBox.question(
            self, "One-time download",
            f"The background remover needs a small model (about {matting.MODEL_SIZE_MB} MB). "
            "It is saved on this PC and works offline afterwards.\n\nDownload it now?")
        if ask != QMessageBox.StandardButton.Yes:
            return
        self._progress = QProgressDialog("Downloading the background remover...", "Cancel", 0, 100, self)
        self._progress.setWindowTitle("Downloading")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        self._progress.setValue(0)
        worker = matting.ModelDownloadWorker(self)
        worker.progress.connect(self._download_progress)
        worker.done.connect(self._download_done)
        worker.failed.connect(self._download_failed)
        self._progress.canceled.connect(worker.cancel)
        self._worker = worker
        worker.start()

    def _download_progress(self, done, total):
        if total and self._progress is not None:
            self._progress.setValue(int(done * 100 / total))

    def _download_done(self):
        self._close_progress()
        self._run_matte()

    def _download_failed(self, msg):
        self._close_progress()
        if msg != "Cancelled":
            QMessageBox.warning(self, "Download failed", f"{msg}\n\nCheck your internet connection and try again.")

    def _run_matte(self):
        self._progress = QProgressDialog("Removing the background...", None, 0, 0, self)
        self._progress.setWindowTitle("Please wait")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.show()
        worker = matting.MatteWorker(self.work_img, self)
        worker.done.connect(self._matte_done)
        worker.failed.connect(self._matte_failed)
        self._worker = worker
        worker.start()

    def _matte_done(self, alpha):
        self._close_progress()
        self.alpha = alpha
        self._undo.clear()
        self._sync_controls()
        self._render()

    def _matte_failed(self, msg):
        self._close_progress()
        QMessageBox.warning(self, "Couldn't remove the background", msg)

    def _close_progress(self):
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
        for btn, m in ((self.restore_btn, "restore"), (self.erase_btn, "erase"), (self.wp_btn, "pick")):
            btn.blockSignals(True)
            btn.setChecked(m == mode)
            btn.blockSignals(False)

    def _set_brush(self, v):
        self.canvas.radius = v
        self.canvas.update()

    def _dab(self, x, y):
        photofx.paint(self.alpha, x, y, self.brush_slider.value(), 255 if self.canvas.mode == "restore" else 0)

    def _press(self, x, y):
        if self.canvas.mode == "pick":
            self._pick(x, y)
            return
        if self.alpha is None:
            return
        self._undo.append(self.alpha.copy())
        del self._undo[:-UNDO_LIMIT]
        self.undo_btn.setEnabled(True)
        self._last = (x, y)
        self._dab(x, y)
        self._render(fast=True)

    def _drag(self, x, y):
        if self._last is None:
            return
        lx, ly = self._last
        steps = max(1, int(math.hypot(x - lx, y - ly) / max(1.0, self.brush_slider.value() * 0.25)))
        for i in range(1, steps + 1):
            t = i / steps
            self._dab(lx + (x - lx) * t, ly + (y - ly) * t)
        self._last = (x, y)
        self._render(fast=True)

    def _release(self):
        if self._last is not None:
            self._last = None
            self._render()

    def _undo_stroke(self):
        if self._undo:
            self.alpha = self._undo.pop()
            self.undo_btn.setEnabled(bool(self._undo))
            self._render()

    # ---------------- lighting ----------------

    def _set_light(self, key, v):
        self.light[key] = v
        self._lit_dirty = True
        self._timer.start()

    def _auto(self):
        self.light.update(photofx.auto_lighting(self.work, self.alpha))
        for key, s in self.light_sliders.items():
            s.setValue(int(self.light[key]))
        self._lit_dirty = True
        self._render()

    def _reset_light(self):
        self.light = photofx.default_light()
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

    # ---------------- result ----------------

    def _export(self):
        full = self.full
        k = photofx.MAX_EXPORT / max(full.size)
        if k < 1:
            full = full.resize((round(full.width * k), round(full.height * k)), Image.LANCZOS)
        out = photofx.apply_lighting(np.asarray(full, dtype=np.uint8), self.light)
        if self.alpha is not None:
            a = photofx.finish_alpha(self.alpha, self.feather / 10, self.shrink)
            out = photofx.compose(out, a, photofx.hex_to_rgb(self.bg), clean=True)
        return Image.fromarray(out)

    def _accept(self):
        changed = self.alpha is not None or not photofx.is_default_light(self.light)
        if changed:
            self.result_state = {
                "alpha": None if self.alpha is None else self.alpha.copy(),
                "bg": self.bg, "feather": self.feather, "shrink": self.shrink, "light": dict(self.light),
            }
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.result_image = self._export()
            finally:
                QApplication.restoreOverrideCursor()
        self.accept()