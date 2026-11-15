import math

import fitz
from PyQt6.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from printcost.ui.full_preview_dialog import (
    RenderAllPagesWorker, WHEEL_SENSITIVITY, ZOOM_ANIM_MS, ZOOM_MIN, ZOOM_STEP,
)
from printcost.ui.preview_widget import pil_to_qpixmap
from printcost.ui.style import BORDER, MUTED, PREVIEW_BG

NOTHING = -1  # no leaf at all (outside the covers); None is a blank page


class LayoutPreviewBase(QDialog):
    """Full screen preview like Full Preview: loading screen, zoom, Ctrl + scroll, Esc to close."""

    def __init__(self, pdf_path, render_pages, mode, hint, info="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Full Preview")
        self.setObjectName("fullPreview")
        self.setStyleSheet(f"""
            #fullPreview, #stripContainer {{ background: {PREVIEW_BG}; }}
            #stripContainer QLabel {{ color: {MUTED}; }}
        """)
        self.pdf_path, self.mode = pdf_path, mode
        self.render_pages = list(render_pages)
        with fitz.open(pdf_path) as doc:
            self.pdf_pages = len(doc)
        self.zoom = 1.0
        self.max_zoom = 2.0 if len(self.render_pages) <= 40 else 1.5
        self._pixmaps = {}
        self._seen = set()
        self._workers = []
        self.worker = None
        self._anim = None
        self._anim_target = 1.0

        layout = QVBoxLayout(self)
        top_row = QHBoxLayout()
        self.hint = QLabel(hint)
        self.hint.setObjectName("muted")
        top_row.addWidget(self.hint)
        top_row.addStretch()
        out_btn = QPushButton("−")
        out_btn.setObjectName("secondary")
        out_btn.setFixedWidth(40)
        out_btn.setToolTip("Zoom out (Ctrl + -)")
        out_btn.clicked.connect(lambda: self._zoom_by(1 / ZOOM_STEP))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setMinimumWidth(50)
        in_btn = QPushButton("+")
        in_btn.setObjectName("secondary")
        in_btn.setFixedWidth(40)
        in_btn.setToolTip("Zoom in (Ctrl + +)")
        in_btn.clicked.connect(lambda: self._zoom_by(ZOOM_STEP))
        reset_btn = QPushButton("100%")
        reset_btn.setObjectName("secondary")
        reset_btn.setToolTip("Reset zoom (Ctrl + 0)")
        reset_btn.clicked.connect(lambda: self._animate_zoom(1.0))
        close_btn = QPushButton("Close (Esc)")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.close)
        for w in (out_btn, self.zoom_label, in_btn, reset_btn):
            top_row.addWidget(w)
        top_row.addSpacing(12)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)
        if info:
            info_label = QLabel(info)
            info_label.setObjectName("muted")
            layout.addWidget(info_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setWidget(self.build_body())

        loading = QWidget()
        loading.setObjectName("stripContainer")
        loading_layout = QVBoxLayout(loading)
        loading_layout.addStretch()
        total = len(self.render_pages)
        self.loading_label = QLabel(f"Preparing preview... 0 of {total}")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        loading_layout.addWidget(self.loading_label)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, total)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedWidth(320)
        loading_layout.addWidget(self.loading_bar, 0, Qt.AlignmentFlag.AlignHCenter)
        loading_layout.addStretch()

        self.stack = QStackedWidget()
        self.stack.addWidget(loading)
        self.stack.addWidget(self.scroll)
        layout.addWidget(self.stack, 1)
        self.footer = QHBoxLayout()
        layout.addLayout(self.footer)

        self._rerender_timer = QTimer(self)
        self._rerender_timer.setSingleShot(True)
        self._rerender_timer.timeout.connect(self._start_worker)
        self._start_worker()
        self.showFullScreen()

    # subclasses
    def build_body(self):
        raise NotImplementedError

    def page_rendered(self, index):
        pass

    def zoom_changed(self):
        pass

    def ready(self):
        pass

    # rendering
    def _start_worker(self):
        if self.worker is not None:
            self.worker.abort = True
            self.worker.page_ready.disconnect(self._on_worker_page)
        self.worker = RenderAllPagesWorker(self.pdf_path, self.pdf_pages, self.zoom, self.mode,
                                           order=self.render_pages)
        self.worker.page_ready.connect(self._on_worker_page)
        self._workers.append(self.worker)
        self.worker.start()

    def _on_worker_page(self, index, img, zoom):
        if self.sender() is not self.worker:
            return
        if img is not None:
            self._pixmaps[index] = (pil_to_qpixmap(img), zoom)
            self.page_rendered(index)
        self._seen.add(index)
        self.loading_bar.setValue(len(self._seen))
        self.loading_label.setText(f"Preparing preview... {len(self._seen)} of {len(self.render_pages)}")
        if len(self._seen) == len(self.render_pages) and self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.ready()

    # zoom
    def _zoom_by(self, factor):
        base = self._anim_target if self._anim is not None else self.zoom
        self._animate_zoom(base * factor)

    def _animate_zoom(self, target):
        target = max(ZOOM_MIN, min(self.max_zoom, target))
        if self.stack.currentIndex() == 0 or abs(target - self.zoom) < 1e-4:
            return
        if self._anim is not None:
            self._anim.stop()
        start = self.zoom
        self._anim_target = target
        anim = QVariantAnimation(self)
        anim.setDuration(ZOOM_ANIM_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(lambda t: self._apply_zoom(start * (target / start) ** t))
        anim.finished.connect(lambda: setattr(self, "_anim", None))
        self._anim = anim
        anim.start()

    def _apply_zoom(self, z):
        z = max(ZOOM_MIN, min(self.max_zoom, z))
        if abs(z - self.zoom) < 1e-4 or self.stack.currentIndex() == 0:
            return
        self.zoom = z
        self.zoom_label.setText(f"{round(z * 100)}%")
        self.zoom_changed()
        self._rerender_timer.start(250)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self._anim is not None:
                self._anim.stop()
                self._anim = None
            self._apply_zoom(self.zoom * WHEEL_SENSITIVITY ** event.angleDelta().y())
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_by(ZOOM_STEP)
        elif ctrl and key == Qt.Key.Key_Minus:
            self._zoom_by(1 / ZOOM_STEP)
        elif ctrl and key == Qt.Key.Key_0:
            self._animate_zoom(1.0)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._rerender_timer.stop()
        if self._anim is not None:
            self._anim.stop()
        for w in self._workers:
            w.abort = True
        for w in self._workers:
            w.quit()
            w.wait(1000)
        super().closeEvent(event)


class SheetsPreviewDialog(LayoutPreviewBase):
    """Back-to-back: every sheet is one row, the front on the left and its back on the right."""

    def __init__(self, pdf_path, order, mode="color", flip="long", parent=None):
        self.order = order
        self._fit = 1.0
        info = (f"Each row is one sheet: the front on the left, its back on the right. "
                f"After the fronts print, flip the whole stack on the {flip} edge.")
        super().__init__(pdf_path, range(len(order)), mode, "Back-to-back preview  ·  Ctrl + scroll to zoom",
                         info, parent)

    def build_body(self):
        container = QWidget()
        container.setObjectName("stripContainer")
        rows = QVBoxLayout(container)
        rows.setSpacing(16)
        rows.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.labels = []
        for sheet in range((len(self.order) + 1) // 2):
            row = QHBoxLayout()
            row.setSpacing(24)
            row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            for side in (0, 1):
                i = sheet * 2 + side
                if i >= len(self.order):
                    continue
                cell = QVBoxLayout()
                page = QLabel(f"Loading page {i + 1}...")
                page.setAlignment(Qt.AlignmentFlag.AlignCenter)
                page.setScaledContents(True)
                page.setMinimumHeight(200)
                what = "blank" if self.order[i] is None else f"page {self.order[i] + 1}"
                caption = QLabel(f"Sheet {sheet + 1}  ·  {'Front' if side == 0 else 'Back'}  ·  {what}")
                caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                cell.addWidget(page, 0, Qt.AlignmentFlag.AlignHCenter)
                cell.addWidget(caption)
                row.addLayout(cell)
                self.labels.append(page)
            rows.addLayout(row)
        return container

    def page_rendered(self, index):
        pm, _ = self._pixmaps[index]
        self.labels[index].setPixmap(pm)
        self.labels[index].setText("")
        self._refit()

    def _size_page(self, index):
        pm, rendered = self._pixmaps[index]
        ratio = self.zoom / rendered * self._fit
        self.labels[index].setFixedSize(QSize(round(pm.width() * ratio), round(pm.height() * ratio)))

    def _refit(self):
        widest = max((pm.width() / z for pm, z in self._pixmaps.values()), default=0)
        if widest:
            self._fit = min(1.0, (self.scroll.viewport().width() - 100) / 2 / widest)
        for i in self._pixmaps:
            self._size_page(i)
        body = self.scroll.widget()
        body.layout().invalidate()
        body.layout().activate()
        body.updateGeometry()

    def zoom_changed(self):
        self._refit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "labels"):
            self._refit()


class BookView(QWidget):
    changed = pyqtSignal()

    def __init__(self, pages, pixmaps, parent=None):
        super().__init__(parent)
        self.pages, self.pixmaps = pages, pixmaps
        self.cw, self.ch = 300, 400
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.spreads = len(pages) // 2 + 1
        self.spread = 0
        self._flip = None
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(420)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self._done)

    def set_cell(self, cw, ch):
        self.cw, self.ch = cw, ch
        self.setFixedSize(2 * cw + 80, ch + 60)
        self.update()

    def leaf(self, spread, side):
        slot = spread * 2 - 1 if side == "L" else spread * 2
        return self.pages[slot] if 0 <= slot < len(self.pages) else NOTHING

    def caption(self):
        names = []
        for side in "LR":
            leaf = self.leaf(self.spread, side)
            if leaf != NOTHING:
                names.append("blank" if leaf is None else f"page {leaf + 1}")
        return f"{' and '.join(names)}  ({self.spread + 1} of {self.spreads})"

    def go(self, delta):
        target = self.spread + delta
        if self._flip is not None or not 0 <= target < self.spreads:
            return
        self._flip = (self.spread, target, 0.0)
        self._anim.start()

    def _tick(self, value):
        self._flip = (self._flip[0], self._flip[1], value)
        self.update()

    def _done(self):
        self.spread = self._flip[1]
        self._flip = None
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event):
        self.go(-1 if event.position().x() < self.width() / 2 else 1)

    def _draw_leaf(self, p, leaf, side, sx=1.0, shade=0):
        if leaf == NOTHING or sx <= 0.001:
            return
        p.save()
        p.translate(self.width() / 2, 30)
        p.scale(sx, 1.0)
        x = 0 if side == "R" else -self.cw
        rect = QRectF(x, 0, self.cw, self.ch)
        p.fillRect(rect, QColor("white"))
        if leaf is not None and leaf in self.pixmaps:
            pm, _ = self.pixmaps[leaf]
            scale = min(self.cw / pm.width(), self.ch / pm.height())
            w, h = pm.width() * scale, pm.height() * scale
            p.drawPixmap(QRectF(x + (self.cw - w) / 2, (self.ch - h) / 2, w, h), pm, QRectF(pm.rect()))
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        if shade:
            p.fillRect(rect, QColor(0, 0, 0, shade))
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._flip is None:
            self._draw_leaf(p, self.leaf(self.spread, "L"), "L")
            self._draw_leaf(p, self.leaf(self.spread, "R"), "R")
        else:
            src, dst, t = self._flip
            shade = int(70 * math.sin(math.pi * t))
            if dst > src:
                self._draw_leaf(p, self.leaf(src, "L"), "L")
                self._draw_leaf(p, self.leaf(dst, "R"), "R")
                if t < 0.5:
                    self._draw_leaf(p, self.leaf(src, "R"), "R", 1 - 2 * t, shade)
                else:
                    self._draw_leaf(p, self.leaf(dst, "L"), "L", 2 * t - 1, shade)
            else:
                self._draw_leaf(p, self.leaf(dst, "L"), "L")
                self._draw_leaf(p, self.leaf(src, "R"), "R")
                if t < 0.5:
                    self._draw_leaf(p, self.leaf(src, "L"), "L", 1 - 2 * t, shade)
                else:
                    self._draw_leaf(p, self.leaf(dst, "R"), "R", 2 * t - 1, shade)
        p.setPen(QPen(QColor(0, 0, 0, 70), 2))
        p.drawLine(self.width() // 2, 30, self.width() // 2, 30 + self.ch)


class BookPreviewDialog(LayoutPreviewBase):
    """Booklet: the pages in reading order as an open book. Click a page or use the arrow keys to flip."""

    def __init__(self, pdf_path, pages, mode="color", parent=None):
        self.pages = pages
        with fitz.open(pdf_path) as doc:
            first = doc[next(p for p in pages if p is not None)].rect
        self.aspect = first.width / first.height
        info = (f"{len(pages) // 4} sheets, folded in half and stapled. "
                "Click the left or right page, or use the arrow keys, to flip.")
        super().__init__(pdf_path, sorted({p for p in pages if p is not None}), mode,
                         "Booklet preview  ·  Ctrl + scroll to zoom", info, parent)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prev_btn = QPushButton("< Previous")
        self.prev_btn.setObjectName("secondary")
        self.next_btn = QPushButton("Next >")
        self.next_btn.setObjectName("secondary")
        self.prev_btn.clicked.connect(lambda: self.book.go(-1))
        self.next_btn.clicked.connect(lambda: self.book.go(1))
        self.book.changed.connect(self._refresh)
        self.footer.addWidget(self.prev_btn)
        self.footer.addWidget(self.caption, 1)
        self.footer.addWidget(self.next_btn)
        self._refresh()

    def build_body(self):
        container = QWidget()
        container.setObjectName("stripContainer")
        box = QVBoxLayout(container)
        self.book = BookView(self.pages, self._pixmaps)
        box.addWidget(self.book, 0, Qt.AlignmentFlag.AlignCenter)
        return container

    def _refit(self):
        vp = self.scroll.viewport()
        cw = min((vp.width() - 100) / 2, (vp.height() - 80) * self.aspect) * self.zoom
        cw = max(120, cw)
        self.book.set_cell(round(cw), round(cw / self.aspect))

    def page_rendered(self, index):
        self.book.update()

    def zoom_changed(self):
        self._refit()

    def ready(self):
        self._refit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.stack.currentIndex() == 1:
            self._refit()

    def _refresh(self):
        self.caption.setText(self.book.caption())
        self.prev_btn.setEnabled(self.book.spread > 0)
        self.next_btn.setEnabled(self.book.spread < self.book.spreads - 1)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.book.go(-1)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.book.go(1)
        else:
            super().keyPressEvent(event)