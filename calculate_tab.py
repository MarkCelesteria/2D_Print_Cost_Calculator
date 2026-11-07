import os

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QComboBox, QLineEdit, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea
)
from PyQt6.QtCore import Qt

from preview_widget import PreviewWidget
from worker import AnalyzeWorker, LoadDocumentWorker
from analyzer import render_pdf_page, SUPPORTED_EXTS, is_image
from full_preview_dialog import FullPreviewDialog
from print_helper import print_pdf

PCT_LABELS = [
    ("labor", "Labor %"),
    ("maintenance", "Maintenance %"),
    ("electricity", "Electricity %"),
    ("spoilage", "Spoilage %"),
    ("printer", "Printer %"),
    ("profit", "Profit %"),
]


def card(layout_cls=QVBoxLayout):
    frame = QFrame()
    frame.setObjectName("card")
    layout = layout_cls(frame)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(6)
    return frame, layout


class CalculateTab(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.file_path = None
        self.sources = []
        self._load_id = 0
        self._loading = False
        self._workers = []
        self.worker = None
        self.load_worker = None

        self.resolved_pdf_path = None
        self.page_count = 1
        self.current_page = 0
        self.is_approx = False

        self.pct_inputs = {}
        self.setAcceptDrops(True)
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setSpacing(14)

        left_card, left_layout = card()
        root.addWidget(left_card, 0)

        self.preview = PreviewWidget()
        left_layout.addWidget(self.preview)

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton("< Prev")
        self.prev_btn.setObjectName("secondary")
        self.prev_btn.clicked.connect(self._go_prev)
        self.prev_btn.setEnabled(False)

        self.page_label = QLabel("No file selected")
        self.page_label.setObjectName("muted")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.next_btn = QPushButton("Next >")
        self.next_btn.setObjectName("secondary")
        self.next_btn.clicked.connect(self._go_next)
        self.next_btn.setEnabled(False)

        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.page_label, 1)
        nav_row.addWidget(self.next_btn)
        left_layout.addLayout(nav_row)

        self.full_preview_btn = QPushButton("⛶ Full Preview")
        self.full_preview_btn.setObjectName("secondary")
        self.full_preview_btn.clicked.connect(self._open_full_preview)
        self.full_preview_btn.setEnabled(False)
        left_layout.addWidget(self.full_preview_btn)

        browse_btn = QPushButton("Browse (PDF / Word / images)")
        browse_btn.setObjectName("secondary")
        browse_btn.clicked.connect(self._browse)
        left_layout.addWidget(browse_btn)

        drop_hint = QLabel("or drag & drop files here")
        drop_hint.setObjectName("muted")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(drop_hint)

        self.file_label = QLabel("No file selected")
        self.file_label.setObjectName("muted")
        self.file_label.setWordWrap(True)
        left_layout.addWidget(self.file_label)
        left_layout.addStretch()

        right_col = QVBoxLayout()
        root.addLayout(right_col, 1)

        top_row = QHBoxLayout()
        top_row.addStretch()
        self.refresh_btn = QPushButton("⟳ Refresh")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        top_row.addWidget(self.refresh_btn)
        right_col.addLayout(top_row)

        opts_card, opts_layout = card(QGridLayout)
        opts_layout.setHorizontalSpacing(10)
        opts_layout.setVerticalSpacing(8)
        right_col.addWidget(opts_card)

        opts_layout.addWidget(QLabel("Paper size"), 0, 0)
        self.paper_combo = QComboBox()
        opts_layout.addWidget(self.paper_combo, 0, 1, 1, 3)
        self._refresh_paper_options()

        opts_layout.addWidget(QLabel("Print mode"), 1, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Color", "Grayscale", "Black & White"])
        self.mode_combo.setCurrentText("Color")
        opts_layout.addWidget(self.mode_combo, 1, 1, 1, 3)

        opts_layout.addWidget(QLabel("Image fit"), 2, 0)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["Fit (with margins)", "Fill (edge to edge)"])
        opts_layout.addWidget(self.fit_combo, 2, 1)
        opts_layout.addWidget(QLabel("Per page"), 2, 2)
        self.per_page_combo = QComboBox()
        self.per_page_combo.addItems(["1", "2", "4"])
        opts_layout.addWidget(self.per_page_combo, 2, 3)

        self.paper_combo.currentIndexChanged.connect(self._reload_layout)
        self.fit_combo.currentIndexChanged.connect(self._reload_layout)
        self.per_page_combo.currentIndexChanged.connect(self._reload_layout)

        for i, (key, label) in enumerate(PCT_LABELS):
            r, c = 3 + i // 3, (i % 3) * 2
            opts_layout.addWidget(QLabel(label), r, c)
            field = QLineEdit(str(self.settings["default_percentages"][key]))
            field.setFixedWidth(60)
            self.pct_inputs[key] = field
            opts_layout.addWidget(field, r, c + 1)

        opts_layout.addWidget(QLabel("Discount %"), 5, 0)
        self.discount_input = QLineEdit(str(self.settings.get("default_discount", 0.0)))
        self.discount_input.setFixedWidth(60)
        opts_layout.addWidget(self.discount_input, 5, 1)

        calc_row = QHBoxLayout()
        self.calc_btn = QPushButton("Calculate Cost")
        self.calc_btn.setObjectName("primary")
        self.calc_btn.clicked.connect(self._calculate)
        calc_row.addWidget(self.calc_btn)
        calc_row.addStretch()
        right_col.addLayout(calc_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        right_col.addWidget(self.status_label)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setSpacing(10)
        self.results_scroll.setWidget(self.results_container)
        right_col.addWidget(self.results_scroll, 1)

    def refresh_settings(self, settings):
        self.settings = settings
        self._refresh_paper_options()
        for key, field in self.pct_inputs.items():
            field.setText(str(self.settings["default_percentages"][key]))
        self.discount_input.setText(str(self.settings.get("default_discount", 0.0)))

    def _on_refresh_clicked(self):
        for key, field in self.pct_inputs.items():
            field.setText(str(self.settings["default_percentages"][key]))
        self.discount_input.setText(str(self.settings.get("default_discount", 0.0)))
        self._refresh_paper_options()

        self.file_path = None
        self.sources = []
        self._load_id += 1
        self._loading = False
        self.resolved_pdf_path = None
        self.page_count = 1
        self.current_page = 0
        self.is_approx = False

        self.preview.show_placeholder("No file selected")
        self.file_label.setText("No file selected")
        self.page_label.setText("No file selected")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.full_preview_btn.setEnabled(False)
        self.status_label.setText("")
        self._clear_results()

    def _refresh_paper_options(self):
        current = self.paper_combo.currentText()
        self.paper_combo.blockSignals(True)
        self.paper_combo.clear()
        names = [p["name"] for p in self.settings["paper_sizes"]]
        self.paper_combo.addItems(names)
        if current in names:
            self.paper_combo.setCurrentText(current)
        self.paper_combo.blockSignals(False)

    def load_file(self, path, auto_calculate=False):
        self.load_files([path], auto_calculate)

    def load_files(self, paths, auto_calculate=False):
        paths = [p for p in paths if p.lower().endswith(SUPPORTED_EXTS)]
        if not paths:
            return
        self.sources = paths
        self.file_path = paths[0]
        if len(paths) == 1:
            self.file_label.setText(os.path.basename(paths[0]))
        else:
            self.file_label.setText(f"{len(paths)} files selected")
        self._start_load(auto_calculate)

    def _reload_layout(self):
        # paper / fit / per-page changed: rebuild the merged PDF
        if len(self.sources) > 1 or any(is_image(p) for p in self.sources):
            self._clear_results()
            self._start_load()

    def _current_paper(self):
        name = self.paper_combo.currentText()
        return next((p for p in self.settings["paper_sizes"] if p["name"] == name), None)

    def _start_load(self, auto_calculate=False):
        paper = self._current_paper()
        if paper is None:
            QMessageBox.warning(self, "No paper size", "Please add a paper size in Settings first.")
            return
        self._load_id += 1
        load_id = self._load_id
        self._loading = True
        self.preview.show_placeholder("Loading preview...")
        self.page_label.setText("Loading...")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.full_preview_btn.setEnabled(False)
        self.resolved_pdf_path = None
        self.page_count = 1
        self.current_page = 0

        worker = LoadDocumentWorker(
            self.sources, paper,
            fill=self.fit_combo.currentIndex() == 1,
            per_page=int(self.per_page_combo.currentText()),
        )
        worker.ready.connect(lambda *args: self._on_document_loaded(*args, auto_calculate=auto_calculate, load_id=load_id))
        worker.warning.connect(lambda msg: QMessageBox.warning(self, "Heads up", msg))
        worker.finished.connect(lambda: self._workers.remove(worker))
        self._workers.append(worker)
        self.load_worker = worker
        worker.start()

    def _on_document_loaded(self, pdf_path, page_count, first_page_img, is_approx, auto_calculate=False, load_id=None):
        if load_id != self._load_id:
            return
        self._loading = False
        self.resolved_pdf_path = pdf_path
        self.page_count = page_count
        self.current_page = 0
        self.is_approx = is_approx

        if first_page_img is not None:
            self.preview.show_image(first_page_img)
        else:
            self.preview.show_placeholder(
                "No preview available\n(install LibreOffice for .docx previews)"
            )

        self._update_page_label()
        can_navigate = pdf_path is not None and page_count > 1
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(can_navigate)
        self.full_preview_btn.setEnabled(pdf_path is not None)

        if auto_calculate:
            self._calculate()

    def _update_page_label(self):
        if self.resolved_pdf_path is None:
            self.page_label.setText("No page preview")
        else:
            self.page_label.setText(f"Page {self.current_page + 1} of {self.page_count}")

    def _go_prev(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._show_current_page()

    def _go_next(self):
        if self.current_page < self.page_count - 1:
            self.current_page += 1
            self._show_current_page()

    def _show_current_page(self):
        img = render_pdf_page(self.resolved_pdf_path, self.current_page)
        self.preview.show_image(img)
        self._update_page_label()
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < self.page_count - 1)

    def _browse(self):
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTS)
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files", "", f"Documents and images ({exts})")
        if paths:
            self.load_files(paths)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(
            u.toLocalFile().lower().endswith(SUPPORTED_EXTS) for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.load_files([u.toLocalFile() for u in event.mimeData().urls()])

    def _open_full_preview(self):
        if not self.resolved_pdf_path:
            QMessageBox.information(self, "No preview", "Load a PDF (or a .docx with LibreOffice) first.")
            return
        dialog = FullPreviewDialog(self.resolved_pdf_path, self.page_count, parent=self)
        dialog.exec()

    def _calculate(self):
        if not self.file_path:
            QMessageBox.warning(self, "No file", "Please select a file first.")
            return
        if self._loading:
            self.status_label.setText("Still preparing files, try again in a moment...")
            return
        single_docx = len(self.sources) == 1 and self.file_path.lower().endswith(".docx")
        if not self.resolved_pdf_path and not single_docx:
            QMessageBox.warning(self, "Can't read files", "The selected files couldn't be prepared.")
            return
        if self.paper_combo.count() == 0:
            QMessageBox.warning(self, "No paper size", "Please add a paper size in Settings first.")
            return

        try:
            percentages = {key: float(field.text()) for key, field in self.pct_inputs.items()}
            discount_pct = float(self.discount_input.text().strip() or 0)
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Percentages and discount must be numbers.")
            return

        paper_name = self.paper_combo.currentText()
        self.paper = next(p for p in self.settings["paper_sizes"] if p["name"] == paper_name)

        self._clear_results()
        self.calc_btn.setEnabled(False)
        self.status_label.setText("Analyzing document...")
        self.preview.start_scan()

        mode_map = {"Color": "color", "Grayscale": "grayscale", "Black & White": "bw"}
        mode = mode_map[self.mode_combo.currentText()]

        self.worker = AnalyzeWorker(
            self.resolved_pdf_path, self.file_path,
            self.settings["ink_prices"], self.paper["price"], percentages, self.is_approx, mode,
            min_price=self.settings.get("min_price_per_page"), discount_pct=discount_pct,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.start()

    def _on_progress(self, cur, total):
        self.status_label.setText(f"Analyzing page {cur} of {total}...")

    def _on_failure(self, msg):
        self.preview.stop_scan()
        self.calc_btn.setEnabled(True)
        self.status_label.setText("")
        QMessageBox.critical(self, "Error", msg)

    def _on_success(self, result, is_approx):
        self.preview.stop_scan()
        self.calc_btn.setEnabled(True)
        self.status_label.setText("Done." + (" (approximate estimate)" if is_approx else ""))
        self._render_results(result, self.paper, is_approx)

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _row(self, layout, label, value, muted=False):
        r = QHBoxLayout()
        lbl = QLabel(label)
        val = QLabel(value)
        if muted:
            lbl.setObjectName("muted")
            val.setObjectName("muted")
        r.addWidget(lbl)
        r.addStretch()
        r.addWidget(val)
        layout.addLayout(r)

    def _render_results(self, result, paper, is_approx):
        summary_card, summary_layout = card()
        header = QLabel(f"{result['num_pages']} page(s) — {paper['name']}")
        header.setObjectName("cardHeader")
        summary_layout.addWidget(header)

        self._row(summary_layout, "Ink cost", f"{result['ink_total']:.2f}")
        self._row(summary_layout, "Paper cost", f"{result['paper_total']:.2f}")
        if result["min_topup"] > 0:
            self._row(summary_layout, "Minimum price top-up", f"{result['min_topup']:.2f}", muted=True)
        self._row(summary_layout, "Subtotal", f"{result['subtotal']:.2f}")

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        summary_layout.addWidget(line)

        for key, label in PCT_LABELS:
            pct = result["percentages"].get(key, 0)
            amt = result["category_amounts"].get(key, 0)
            self._row(summary_layout, f"{label.replace(' %','')} ({pct:g}%)", f"{amt:.2f}", muted=True)

        if result["discount_amount"] > 0:
            self._row(summary_layout, f"Discount ({result['discount_pct']:g}%)",
                      f"-{result['discount_amount']:.2f}", muted=True)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        summary_layout.addWidget(line2)

        total_row = QHBoxLayout()
        total_label = QLabel("TOTAL")
        total_label.setObjectName("total")
        total_value = QLabel(f"{result['total']:.2f}")
        total_value.setObjectName("total")
        total_row.addWidget(total_label)
        total_row.addStretch()
        total_row.addWidget(total_value)
        summary_layout.addLayout(total_row)

        print_btn = QPushButton("🖨 Print")
        print_btn.setObjectName("secondary")
        print_btn.clicked.connect(self._on_print_clicked)
        summary_layout.addWidget(print_btn)

        if is_approx:
            note = QLabel("Approximate: .docx text coverage is estimated (install LibreOffice for exact results).")
            note.setObjectName("muted")
            note.setWordWrap(True)
            summary_layout.addWidget(note)

        self.results_layout.addWidget(summary_card)

        table_card, table_layout = card()
        table_header = QLabel("Color use per page")
        table_header.setObjectName("cardHeader")
        table_layout.addWidget(table_header)

        note2 = QLabel("White space is automatically excluded — it converts to 0% on every channel.")
        note2.setObjectName("muted")
        note2.setWordWrap(True)
        table_layout.addWidget(note2)

        table = QTableWidget(len(result["per_page"]), 6)
        table.setHorizontalHeaderLabels(["Page", "Cyan %", "Magenta %", "Yellow %", "Black %", "Ink cost"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        for i, p in enumerate(result["per_page"]):
            cov = p["coverage"]
            values = [
                str(i + 1),
                f"{cov['C']*100:.1f}%",
                f"{cov['M']*100:.1f}%",
                f"{cov['Y']*100:.1f}%",
                f"{cov['K']*100:.1f}%",
                f"{p['ink_cost']:.2f}",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(i, col, item)

        table.setMinimumHeight(min(220, 40 + 28 * len(result["per_page"])))
        table_layout.addWidget(table)

        self.results_layout.addWidget(table_card)
        self.results_layout.addStretch()

    def _on_print_clicked(self):
        if not self.resolved_pdf_path:
            QMessageBox.information(self, "Can't print", "No real PDF available for this document (install LibreOffice for .docx printing).")
            return
        print_pdf(self.resolved_pdf_path, parent=self)