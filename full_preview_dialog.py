from PyQt6.QtWidgets import (
    QDialog, QScrollArea, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QStackedWidget, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QEvent

from analyzer import render_pdf_page, apply_preview_mode
from preview_widget import pil_to_qpixmap
from style import PREVIEW_BG, MUTED

STRIP_PAGE_SIZE = (700, 900)   # page size at 100% zoom
ZOOM_MIN = 0.5
ZOOM_STEP = 1.25


class RenderAllPagesWorker(QThread):
    page_ready = pyqtSignal(int, object, float)  # index, image, zoom it was rendered at

    def __init__(self, pdf_path, page_count, zoom=1.0, mode="color", parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.page_count = page_count
        self.zoom = zoom
        self.mode = mode
        self.abort = False

    def run(self):
        size = (int(STRIP_PAGE_SIZE[0] * self.zoom), int(STRIP_PAGE_SIZE[1] * self.zoom))
        for i in range(self.page_count):
            if self.abort:
                return
            img = render_pdf_page(self.pdf_path, i, max_size=size, scale=1.2 * self.zoom)
            if self.abort:
                return
            img = apply_preview_mode(img, self.mode)
            self.page_ready.emit(i, img, self.zoom)


class FullPreviewDialog(QDialog):
    def __init__(self, pdf_path, page_count, parent=None, mode="color"):
        super().__init__(parent)
        self.setWindowTitle("Full Preview")
        self.setObjectName("fullPreview")
        self.setStyleSheet(f"""
            #fullPreview, #stripContainer {{ background: {PREVIEW_BG}; }}
            #stripContainer QLabel {{ color: {MUTED}; }}
        """)

        self.pdf_path = pdf_path
        self.page_count = page_count
        self.mode = mode
        self.zoom = 1.0
        self.max_zoom = 2.0 if page_count <= 40 else 1.5  # keep memory reasonable on long documents
        self._pixmaps = {}      # page index -> (QPixmap, zoom it was rendered at)
        self._workers = []
        self.worker = None
        self._seen = set()  # pages rendered at least once (drives the loading screen)

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        mode_names = {"color": "Color", "grayscale": "Grayscale", "bw": "Black & White"}
        hint = QLabel(f"{mode_names.get(mode, 'Color')} preview  ·  Ctrl + scroll to zoom")
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
        reset_btn.clicked.connect(lambda: self._set_zoom(1.0))
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
            lbl.setMinimumHeight(200)
            self.page_labels.append(lbl)
            strip_layout.addWidget(lbl)

        self.scroll.setWidget(container)

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
        self.worker = RenderAllPagesWorker(self.pdf_path, self.page_count, self.zoom, self.mode)
        self.worker.page_ready.connect(self._on_page_ready)
        self._workers.append(self.worker)  # keep a reference until the dialog closes
        self.worker.start()

    def _on_page_ready(self, index, img, zoom):
        if img is None:
            self.page_labels[index].setText(f"Page {index + 1}: preview unavailable")
        else:
            self._pixmaps[index] = (pil_to_qpixmap(img), zoom)
            self._show_page(index)

        self._seen.add(index)
        self.loading_bar.setValue(len(self._seen))
        self.loading_label.setText(f"Preparing preview... {len(self._seen)} of {self.page_count}")
        if len(self._seen) == self.page_count and self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)

    def _show_page(self, index):
        pm, rendered_zoom = self._pixmaps[index]
        ratio = self.zoom / rendered_zoom
        if abs(ratio - 1) > 1e-3:  # not re-rendered yet: stretch the old image for instant feedback
            pm = pm.scaled(int(pm.width() * ratio), int(pm.height() * ratio),
                           Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        lbl = self.page_labels[index]
        lbl.setPixmap(pm)
        lbl.setFixedSize(pm.size())
        lbl.setText("")

    # ---------------- zoom ----------------

    def _zoom_by(self, factor):
        self._set_zoom(self.zoom * factor)

    def _set_zoom(self, z):
        z = max(ZOOM_MIN, min(self.max_zoom, z))
        if abs(z - self.zoom) < 1e-3 or self.stack.currentIndex() == 0:
            return
        bv, bh = self.scroll.verticalScrollBar(), self.scroll.horizontalScrollBar()
        fv = bv.value() / bv.maximum() if bv.maximum() else 0
        fh = bh.value() / bh.maximum() if bh.maximum() else 0

        self.zoom = z
        self.zoom_label.setText(f"{round(z * 100)}%")
        for i in self._pixmaps:
            self._show_page(i)
        QTimer.singleShot(50, lambda: self._restore_scroll(fv, fh))
        self._rerender_timer.start(250)  # sharp re-render once the user stops zooming

    def _restore_scroll(self, fv, fh):
        bv, bh = self.scroll.verticalScrollBar(), self.scroll.horizontalScrollBar()
        bv.setValue(int(fv * bv.maximum()))
        bh.setValue(int(fh * bh.maximum()))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._zoom_by(ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP)
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
            self._set_zoom(1.0)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._rerender_timer.stop()
        for w in self._workers:
            w.abort = True
        for w in self._workers:
            w.quit()
            w.wait(1000)
        super().closeEvent(event)