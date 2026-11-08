from PyQt6.QtWidgets import (
    QDialog, QScrollArea, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QStackedWidget, QProgressBar
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QEvent, QRectF, QPoint, QSize, QEasingCurve, QVariantAnimation,
    QCoreApplication
)
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap

from analyzer import (
    render_pdf_page, apply_preview_mode, format_page_ranges, get_pdf_page_inches, fit_page_to_paper
)
from preview_widget import pil_to_qpixmap
from style import PREVIEW_BG, MUTED

STRIP_PAGE_SIZE = (700, 900)   # page size at 100% zoom
ZOOM_MIN = 0.5
ZOOM_STEP = 1.25          # buttons / keyboard
WHEEL_SENSITIVITY = 1.0015  # per wheel unit: one mouse notch (120) is about x1.2, touchpads zoom smoothly
ZOOM_ANIM_MS = 140


class RenderAllPagesWorker(QThread):
    page_ready = pyqtSignal(int, object, float)  # index, image, zoom it was rendered at

    def __init__(self, pdf_path, page_count, zoom=1.0, mode="color", order=None, fit_paper=None, parent=None):
        super().__init__(parent)
        self.fit_paper = fit_paper
        self.order = order or list(range(page_count))  # pages near the viewport go first
        self.pdf_path = pdf_path
        self.mode = mode
        self.page_count = page_count
        self.zoom = zoom
        self.abort = False

    def run(self):
        size = (int(STRIP_PAGE_SIZE[0] * self.zoom), int(STRIP_PAGE_SIZE[1] * self.zoom))
        for i in self.order:
            if self.abort:
                return
            img = render_pdf_page(self.pdf_path, i, max_size=size, scale=1.2 * self.zoom)
            if self.abort:
                return
            img = apply_preview_mode(img, self.mode)
            if self.fit_paper:
                img = fit_page_to_paper(img, get_pdf_page_inches(self.pdf_path, i),
                                        self.fit_paper["width_in"], self.fit_paper["height_in"], max_size=size)
            self.page_ready.emit(i, img, self.zoom)


def _mark_not_included(pm):
    """Dim a page and stamp NOT INCLUDED across it (for pages that won't be printed)."""
    out = QPixmap(pm)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.fillRect(out.rect(), QColor(255, 255, 255, 150))  # fade the page

    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(max(12, int(out.width() * 0.10)))
    p.setFont(font)
    text = "NOT INCLUDED"
    tw = p.fontMetrics().horizontalAdvance(text)
    th = p.fontMetrics().height()

    p.translate(out.width() / 2, out.height() / 2)
    p.rotate(-35)
    red = QColor(200, 30, 30, 210)
    pen = p.pen()
    pen.setColor(red)
    pen.setWidth(max(2, int(out.width() * 0.008)))
    p.setPen(pen)
    pad = int(th * 0.35)
    p.drawRoundedRect(QRectF(-tw / 2 - pad, -th / 2 - pad / 2, tw + 2 * pad, th + pad), 10, 10)
    p.drawText(QRectF(-tw / 2 - pad, -th / 2 - pad / 2, tw + 2 * pad, th + pad), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return out


class FullPreviewDialog(QDialog):
    def __init__(self, pdf_path, page_count, parent=None, mode="color", included=None, fit_paper=None):
        super().__init__(parent)
        self.mode = mode
        self.fit_paper = fit_paper
        self.included = set(included) if included else None  # 1-based pages that will print (None = all)
        self.setWindowTitle("Full Preview")
        self.setObjectName("fullPreview")
        self.setStyleSheet(f"""
            #fullPreview, #stripContainer {{ background: {PREVIEW_BG}; }}
            #stripContainer QLabel {{ color: {MUTED}; }}
        """)

        self.pdf_path = pdf_path
        self.page_count = page_count
        self.zoom = 1.0
        self.max_zoom = 2.0 if page_count <= 40 else 1.5  # keep memory reasonable on long documents
        self._pixmaps = {}      # page index -> (QPixmap, zoom it was rendered at)
        self._workers = []
        self.worker = None
        self._seen = set()  # pages rendered at least once (drives the loading screen)
        self._anim = None
        self._anim_target = 1.0

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        mode_names = {"color": "Color", "grayscale": "Grayscale", "bw": "Black & White"}
        hint = QLabel(f"{mode_names.get(mode, 'Color')} preview  ·  Ctrl + scroll to zoom")
        if self.included:
            hint.setText(hint.text() + f"  ·  Printing pages {format_page_ranges(self.included)}")
        if self.fit_paper:
            hint.setText(hint.text() + f"  ·  Shown on {self.fit_paper['name']}")
        hint.setObjectName("muted")
        top_row.addWidget(hint)
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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().installEventFilter(self)  # Ctrl + wheel zoom
        container = QWidget()
        container.setObjectName("stripContainer")
        strip_layout = QVBoxLayout(container)
        strip_layout.setSpacing(16)
        strip_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self.page_labels = []
        for i in range(page_count):
            lbl = QLabel(f"Loading page {i + 1}...")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setScaledContents(True)  # Qt stretches the page smoothly, and only for pages on screen
            lbl.setMinimumHeight(200)
            self.page_labels.append(lbl)
            strip_layout.addWidget(lbl)

        self.scroll.setWidget(container)

        # loading screen (shown until every page is rendered), then the pages
        loading = QWidget()
        loading.setObjectName("stripContainer")
        loading_layout = QVBoxLayout(loading)
        loading_layout.addStretch()
        self.loading_label = QLabel(f"Preparing preview... 0 of {page_count}")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        loading_layout.addWidget(self.loading_label)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, page_count)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedWidth(320)
        loading_layout.addWidget(self.loading_bar, 0, Qt.AlignmentFlag.AlignHCenter)
        loading_layout.addStretch()

        self.stack = QStackedWidget()
        self.stack.addWidget(loading)      # index 0
        self.stack.addWidget(self.scroll)  # index 1
        layout.addWidget(self.stack, 1)

        self._rerender_timer = QTimer(self)
        self._rerender_timer.setSingleShot(True)
        self._rerender_timer.timeout.connect(self._start_worker)

        self._start_worker()
        self.showFullScreen()

    # ---------------- rendering ----------------

    def _start_worker(self):
        if self.worker is not None:
            self.worker.abort = True
            self.worker.page_ready.disconnect(self._on_page_ready)
        self.worker = RenderAllPagesWorker(self.pdf_path, self.page_count, self.zoom, self.mode,
                                           order=self._render_order(), fit_paper=self.fit_paper)
        self.worker.page_ready.connect(self._on_page_ready)
        self._workers.append(self.worker)  # keep a reference until the dialog closes
        self.worker.start()

    def _on_page_ready(self, index, img, zoom):
        if img is None:
            self.page_labels[index].setText(f"Page {index + 1}: preview unavailable")
        else:
            pm = pil_to_qpixmap(img)
            if self.included is not None and (index + 1) not in self.included:
                pm = _mark_not_included(pm)
            self._pixmaps[index] = (pm, zoom)
            self._show_page(index)

        self._seen.add(index)
        self.loading_bar.setValue(len(self._seen))
        self.loading_label.setText(f"Preparing preview... {len(self._seen)} of {self.page_count}")
        if len(self._seen) == self.page_count and self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)

    def _render_order(self):
        """Pages closest to the middle of the screen first, so what you're looking at sharpens first."""
        if self.stack.currentIndex() == 0:
            return None
        y = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() / 2
        centre = min(range(self.page_count),
                     key=lambda i: abs(self.page_labels[i].y() + self.page_labels[i].height() / 2 - y))
        return sorted(range(self.page_count), key=lambda i: abs(i - centre))

    def _page_at(self, y):
        """The page under content row y, and how far down it (0 = top, 1 = bottom)."""
        best, best_d = 0, None
        for i, lbl in enumerate(self.page_labels):
            top, bottom = lbl.y(), lbl.y() + lbl.height()
            if top <= y < bottom + 16:  # include the gap below the page
                return i, (y - top) / max(1, lbl.height())
            d = min(abs(y - top), abs(y - bottom))
            if best_d is None or d < best_d:
                best, best_d = i, d
        lbl = self.page_labels[best]
        return best, (y - lbl.y()) / max(1, lbl.height())

    def _show_page(self, index):
        pm, _ = self._pixmaps[index]
        lbl = self.page_labels[index]
        lbl.setPixmap(pm)
        lbl.setText("")
        self._size_page(index)

    def _size_page(self, index):
        pm, rendered_zoom = self._pixmaps[index]
        ratio = self.zoom / rendered_zoom  # old image is stretched until the sharp one arrives
        self.page_labels[index].setFixedSize(QSize(round(pm.width() * ratio), round(pm.height() * ratio)))

    # ---------------- zoom ----------------

    def _zoom_by(self, factor):
        base = self._anim_target if self._anim is not None else self.zoom
        self._animate_zoom(base * factor)

    def _animate_zoom(self, target):
        """Glide to the target zoom (buttons and keyboard)."""
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
        # interpolate in log space so zooming in and out feel the same speed
        anim.valueChanged.connect(lambda t: self._apply_zoom(start * (target / start) ** t))
        anim.finished.connect(lambda: setattr(self, "_anim", None))
        self._anim = anim
        anim.start()

    def _set_zoom(self, z):
        self._apply_zoom(z)

    def _apply_zoom(self, z, anchor=None):
        """Zoom immediately, keeping the point under `anchor` (viewport coordinates) where it is."""
        z = max(ZOOM_MIN, min(self.max_zoom, z))
        if abs(z - self.zoom) < 1e-4 or self.stack.currentIndex() == 0:
            return
        vp = self.scroll.viewport()
        bv, bh = self.scroll.verticalScrollBar(), self.scroll.horizontalScrollBar()
        if anchor is None:
            anchor = QPoint(vp.width() // 2, vp.height() // 2)
        ratio = z / self.zoom
        cx = bh.value() + anchor.x()
        idx, frac = self._page_at(bv.value() + anchor.y())  # the page spot under the anchor
        had_h_scroll = bh.maximum() > 0

        self.zoom = z
        self.zoom_label.setText(f"{round(z * 100)}%")
        for i in self._pixmaps:
            self._size_page(i)

        container = self.scroll.widget()
        container.layout().activate()  # update the scroll range right now, not on the next event loop turn
        QCoreApplication.sendPostedEvents(container, QEvent.Type.LayoutRequest)
        QCoreApplication.sendPostedEvents(vp, QEvent.Type.LayoutRequest)

        lbl = self.page_labels[idx]
        bv.setValue(round(lbl.y() + frac * lbl.height() - anchor.y()))
        if had_h_scroll:
            bh.setValue(round(cx * ratio - anchor.x()))
        else:
            bh.setValue(bh.maximum() // 2)  # pages are centred, so stay centred
        self._rerender_timer.start(250)  # sharp re-render once the user stops zooming

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self._anim is not None:
                self._anim.stop()
                self._anim = None
            vp = self.scroll.viewport()
            anchor = vp.mapFromGlobal(event.globalPosition().toPoint())  # zoom around the mouse pointer
            self._apply_zoom(self.zoom * WHEEL_SENSITIVITY ** event.angleDelta().y(), anchor)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif ctrl and event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_by(ZOOM_STEP)
        elif ctrl and event.key() == Qt.Key.Key_Minus:
            self._zoom_by(1 / ZOOM_STEP)
        elif ctrl and event.key() == Qt.Key.Key_0:
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