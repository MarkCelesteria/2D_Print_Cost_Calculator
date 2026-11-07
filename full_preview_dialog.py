from PyQt6.QtWidgets import QDialog, QScrollArea, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from analyzer import render_pdf_page
from preview_widget import pil_to_qpixmap
from style import PREVIEW_BG, MUTED

STRIP_PAGE_SIZE = (700, 900)


class RenderAllPagesWorker(QThread):
    page_ready = pyqtSignal(int, object)

    def __init__(self, pdf_path, page_count, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.page_count = page_count

    def run(self):
        for i in range(self.page_count):
            img = render_pdf_page(self.pdf_path, i, max_size=STRIP_PAGE_SIZE)
            self.page_ready.emit(i, img)


class FullPreviewDialog(QDialog):
    def __init__(self, pdf_path, page_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Full Preview")
        self.setObjectName("fullPreview")
        self.setStyleSheet(f"""
            #fullPreview, #stripContainer {{ background: {PREVIEW_BG}; }}
            #stripContainer QLabel {{ color: {MUTED}; }}
        """)

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addStretch()
        close_btn = QPushButton("Close (Esc)")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.close)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
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

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        self.worker = RenderAllPagesWorker(pdf_path, page_count)
        self.worker.page_ready.connect(self._on_page_ready)
        self.worker.start()

        self.showFullScreen()

    def _on_page_ready(self, index, img):
        lbl = self.page_labels[index]
        if img is None:
            lbl.setText(f"Page {index + 1}: preview unavailable")
        else:
            lbl.setPixmap(pil_to_qpixmap(img))
            lbl.setFixedSize(img.width, img.height)
            lbl.setText("")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.worker.quit()
        self.worker.wait(1000)
        super().closeEvent(event)