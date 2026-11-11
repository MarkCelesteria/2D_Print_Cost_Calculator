import os

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QComboBox, QLineEdit, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea
)
from PyQt6.QtCore import Qt

from preview_widget import PreviewWidget
from worker import AnalyzeWorker, LoadDocumentWorker
from analyzer import (render_pdf_page, apply_preview_mode, compute_cost, cleanup_temp_dirs,
                      parse_page_ranges, format_page_ranges, get_document_page_size, closest_paper,
                      get_pdf_page_inches, fit_page_to_paper, SUPPORTED_EXTS, is_image)
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


QUALITY_LABELS = [("draft", "Draft"), ("standard", "Standard"), ("high", "High")]


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
        self._analysis_cache = {}
        self._calc_load_id = 0
        self._loading = False
        self._workers = []
        self.worker = None
        self.load_worker = None

        self.resolved_pdf_path = None
        self.page_count = 1
        self.current_page = 0
        self.is_approx = False

        self.pct_inputs = {}
        self.quality_inputs = {}
        self.setAcceptDrops(True)
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
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

        self.pages_caption = QLabel("")
        self.pages_caption.setObjectName("muted")
        self.pages_caption.setWordWrap(True)
        self.pages_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.pages_caption)

        self.calc_btn = QPushButton("Calculate Cost")
        self.calc_btn.setObjectName("primary")
        self.calc_btn.clicked.connect(self._calculate)
        left_layout.addWidget(self.calc_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.clicked.connect(self._cancel_calculation)
        self.cancel_btn.setVisible(False)
        left_layout.addWidget(self.cancel_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.status_label)

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

        self.refresh_btn = QPushButton("⟳ Refresh")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)

        opts_card, opts_layout = card(QGridLayout)
        opts_layout.setHorizontalSpacing(10)
        opts_layout.setVerticalSpacing(8)
        right_col.addWidget(opts_card)

        opts_layout.addWidget(QLabel("Paper size"), 0, 0)
        self.paper_combo = QComboBox()
        self.paper_note = QLabel("")
        self.paper_note.setWordWrap(True)
        self.paper_note.hide()
        paper_box = QVBoxLayout()
        paper_box.setSpacing(2)
        paper_box.addWidget(self.paper_combo)
        paper_box.addWidget(self.paper_note)
        opts_layout.addLayout(paper_box, 0, 1, 1, 5)
        self._refresh_paper_options()

        opts_layout.addWidget(QLabel("Print mode"), 1, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Color", "Grayscale", "Black & White"])
        self.mode_combo.setCurrentText("Color")
        self.mode_combo.currentIndexChanged.connect(self._refresh_preview_mode)
        opts_layout.addWidget(self.mode_combo, 1, 1)

        opts_layout.addWidget(QLabel("Quality"), 1, 2)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Draft", "Standard", "High"])
        self.quality_combo.setCurrentText("Standard")
        opts_layout.addWidget(self.quality_combo, 1, 3)

        opts_layout.addWidget(QLabel("Image fit"), 2, 0)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["Fit (with margins)", "Fill (edge to edge)"])
        opts_layout.addWidget(self.fit_combo, 2, 1)
        opts_layout.addWidget(QLabel("Per page"), 2, 2)
        self.per_page_combo = QComboBox()
        self.per_page_combo.addItems(["1", "2", "4"])
        opts_layout.addWidget(self.per_page_combo, 2, 3)

        self.paper_combo.currentIndexChanged.connect(self._reload_layout)
        self.paper_combo.currentIndexChanged.connect(self._on_paper_changed)
        self.fit_combo.currentIndexChanged.connect(self._reload_layout)
        self.per_page_combo.currentIndexChanged.connect(self._reload_layout)

        for i, (key, label) in enumerate(PCT_LABELS):
            r, c = 4 + i // 3, (i % 3) * 2
            opts_layout.addWidget(QLabel(label), r, c)
            field = QLineEdit(str(self.settings["default_percentages"][key]))
            field.setFixedWidth(60)
            self.pct_inputs[key] = field
            opts_layout.addWidget(field, r, c + 1)

        opts_layout.addWidget(QLabel("Discount %"), 6, 0)
        self.discount_input = QLineEdit(str(self.settings.get("default_discount", 0.0)))
        self.discount_input.setFixedWidth(60)
        opts_layout.addWidget(self.discount_input, 6, 1)

        opts_layout.addWidget(QLabel("Print pages"), 3, 0)
        self.pages_input = QLineEdit()
        self.pages_input.setPlaceholderText("All pages (or e.g. 1-6, 8-9, 11, 12)")
        self.pages_input.textChanged.connect(self._on_pages_changed)
        opts_layout.addWidget(self.pages_input, 3, 1, 1, 5)

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
        self._analysis_cache = {}
        self.doc_size = None
        self.paper_note.hide()
        self.pages_input.clear()
        self.doc_size = None
        self.paper_note.hide()
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
        self._update_paper_note()

    def load_file(self, path, auto_calculate=False):
        self.load_files([path], auto_calculate)

    def load_files(self, paths, auto_calculate=False):
        paths = [p for p in paths if p.lower().endswith(SUPPORTED_EXTS)]
        if not paths:
            return
        self.sources = paths
        self.file_path = paths[0]
        self._new_document = True
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

    def _detect_paper(self):
        """Read the document's page size, pre-select the closest paper and flag a mismatch."""
        is_new = getattr(self, "_new_document", False)
        self._new_document = False
        self.doc_size = None
        self._paper_auto_note = ""
        if self.resolved_pdf_path and not any(is_image(p) for p in self.sources):
            try:
                self.doc_size = get_document_page_size(self.resolved_pdf_path)
            except Exception:
                self.doc_size = None
        if self.doc_size and is_new:  # only for a newly chosen file, never override a manual choice later
            idx = closest_paper(self.doc_size, self.settings["paper_sizes"])
            if idx is not None and idx != self.paper_combo.currentIndex():
                self.paper_combo.blockSignals(True)
                self.paper_combo.setCurrentIndex(idx)
                self.paper_combo.blockSignals(False)
                self._paper_auto_note = f"Paper set to {self.paper_combo.currentText()} to match the document."
        self._update_paper_note()

    def _on_paper_changed(self):
        self._paper_auto_note = ""  # a manual choice: drop the "paper set to..." message
        self._update_paper_note()
        if self.resolved_pdf_path:
            self._show_current_page()

    def _update_paper_note(self):
        size = getattr(self, "doc_size", None)
        paper = self._current_paper() if self.paper_combo.count() else None
        if not size or paper is None:
            self.paper_note.hide()
            return
        ps = sorted((paper["width_in"], paper["height_in"]))
        label = f"{size[0]:g}×{size[1]:g} in"
        if max(abs(ps[0] - size[0]), abs(ps[1] - size[1])) <= 0.15:
            text, color = getattr(self, "_paper_auto_note", ""), "#64748b"  # matches: only say what the app did
        else:
            scale = min(ps[0] / size[0], ps[1] / size[1])
            if scale < 0.98:
                text = f"Document is {label}; pages shrink to {scale * 100:.0f}% on this paper."
            elif scale > 1.02:
                text = f"Document is {label}; pages are enlarged to {scale * 100:.0f}% to fill this paper."
            else:
                text = f"Document is {label}; pages leave blank space on this paper."
            text += " The preview shows how it prints."
            if closest_paper(size, self.settings["paper_sizes"]) is None:
                text += " No paper in Settings matches it."
            color = "#b45309"
        self.paper_note.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.paper_note.setText(text)
        self.paper_note.setVisible(bool(text))

    def _fit_paper(self):
        """The selected paper when the document doesn't match it (previews then show how it prints on it)."""
        size = getattr(self, "doc_size", None)
        paper = self._current_paper() if self.paper_combo.count() else None
        if not size or paper is None:
            return None
        ps = sorted((paper["width_in"], paper["height_in"]))
        return None if max(abs(ps[0] - size[0]), abs(ps[1] - size[1])) <= 0.15 else paper

    def _prepare_preview(self, img, page_index):
        img = apply_preview_mode(img, self._current_mode())
        paper = self._fit_paper()
        if paper is not None and img is not None:
            size = get_pdf_page_inches(self.resolved_pdf_path, page_index)
            if size:
                img = fit_page_to_paper(img, size, paper["width_in"], paper["height_in"], max_size=(256, 336))
        return img

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
        self._analysis_cache = {}
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
        if self.worker is None or not self.worker.isRunning():
            cleanup_temp_dirs(keep_path=pdf_path)
        self._detect_paper()
        self.page_count = page_count
        self.current_page = 0
        self.is_approx = is_approx

        if first_page_img is not None:
            self.preview.show_image(self._prepare_preview(first_page_img, 0))
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

    def _current_mode(self):
        return {"Color": "color", "Grayscale": "grayscale", "Black & White": "bw"}[self.mode_combo.currentText()]

    def _refresh_preview_mode(self):
        if self.resolved_pdf_path:
            self._show_current_page()

    def _update_page_label(self):
        if self.resolved_pdf_path is None:
            self.page_label.setText("No page preview")
        else:
            self.page_label.setText(f"Page {self.current_page + 1} of {self.page_count}")

    def _selected_pages(self):
        """1-based pages from the Print pages field, or None for all (also while the text is incomplete)."""
        if not self.resolved_pdf_path:
            return None
        try:
            return parse_page_ranges(self.pages_input.text(), self.page_count)
        except ValueError:
            return None

    def _included_indexes(self):
        sel = self._selected_pages()
        return [n - 1 for n in sel] if sel else list(range(self.page_count))

    def _update_nav_buttons(self):
        inc = self._included_indexes()
        self.prev_btn.setEnabled(any(i < self.current_page for i in inc))
        self.next_btn.setEnabled(any(i > self.current_page for i in inc))

    def _update_pages_caption(self):
        text = self.pages_input.text().strip()
        if not text or not self.resolved_pdf_path:
            self.pages_caption.setText("")
            return
        try:
            pages = parse_page_ranges(text, self.page_count)
        except ValueError:
            self.pages_caption.setText("Can't read the page list")
            return
        self.pages_caption.setText(f"Printing pages {format_page_ranges(pages)} ({len(pages)} of {self.page_count})")

    def _on_pages_changed(self):
        if not self.resolved_pdf_path:
            return
        self._update_pages_caption()
        included = self._included_indexes()
        if self.current_page not in included:
            later = [i for i in included if i >= self.current_page]
            self.current_page = later[0] if later else included[-1]
            self._show_current_page()
        else:
            self._update_nav_buttons()

    def _go_prev(self):
        prev = [i for i in self._included_indexes() if i < self.current_page]
        if prev:
            self.current_page = prev[-1]
            self._show_current_page()

    def _go_next(self):
        nxt = [i for i in self._included_indexes() if i > self.current_page]
        if nxt:
            self.current_page = nxt[0]
            self._show_current_page()

    def _show_current_page(self):
        img = render_pdf_page(self.resolved_pdf_path, self.current_page)
        self.preview.show_image(self._prepare_preview(img, self.current_page))
        self._update_page_label()
        self._update_nav_buttons()

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
        dialog = FullPreviewDialog(self.resolved_pdf_path, self.page_count, parent=self,
                                   mode=self._current_mode(), included=self._selected_pages(),
                                   fit_paper=self._fit_paper())
        dialog.exec()
        dialog.deleteLater()

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

        try:
            page_text = self.pages_input.text() if self.resolved_pdf_path else ""
            selected_pages = parse_page_ranges(page_text, self.page_count)
        except ValueError as e:
            QMessageBox.critical(self, "Invalid pages", str(e))
            return
        self.print_pages = selected_pages

        paper_name = self.paper_combo.currentText()
        self.paper = next(p for p in self.settings["paper_sizes"] if p["name"] == paper_name)

        self._clear_results()
        self._set_calculating(True)
        self.status_label.setText("Analyzing document...")

        mode_map = {"Color": "color", "Grayscale": "grayscale", "Black & White": "bw"}
        mode = mode_map[self.mode_combo.currentText()]
        quality = self.quality_combo.currentText().lower()
        ink_multiplier = self.settings["quality_multipliers"].get(quality, 1.0)
        self.print_mode = mode
        self.print_quality = quality
        self._calc_load_id = self._load_id

        cached = self._analysis_cache.get(mode)
        if cached is not None:
            self._set_calculating(False)
            coverage, cached_approx = cached
            chosen = coverage if not selected_pages else [coverage[n - 1] for n in selected_pages]
            result = compute_cost(
                chosen, self.settings["ink_prices"], self.paper["price"], percentages,
                page_numbers=selected_pages,
                min_price_per_page=self.settings.get("min_price_per_page"),
                discount_pct=discount_pct, ink_multiplier=ink_multiplier,
                min_price_color=self.settings.get("min_price_color"),
                color_threshold_pct=self.settings.get("color_threshold_pct", 0.5),
            )
            self.status_label.setText("Done (reused the earlier scan)." + (" (approximate estimate)" if cached_approx else ""))
            self._render_results(result, self.paper, cached_approx)
            return

        self.worker = AnalyzeWorker(
            self.resolved_pdf_path, self.file_path,
            self.settings["ink_prices"], self.paper["price"], percentages, self.is_approx, mode,
            min_price=self.settings.get("min_price_per_page"), discount_pct=discount_pct,
            ink_multiplier=ink_multiplier,
            min_price_color=self.settings.get("min_price_color"),
            color_threshold_pct=self.settings.get("color_threshold_pct", 0.5),
            pages=selected_pages,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.cancelled.connect(self._on_cancelled)
        from PyQt6.QtCore import QThread
        self.worker.start(QThread.Priority.LowPriority)

    def _set_calculating(self, on):
        self.calc_btn.setVisible(not on)
        self.cancel_btn.setVisible(on)
        self.cancel_btn.setEnabled(True)
        if on:
            self.preview.start_scan()
        else:
            self.preview.stop_scan()

    def _cancel_calculation(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.status_label.setText("Cancelling...")

    def _on_cancelled(self):
        self._set_calculating(False)
        self.status_label.setText("Cancelled.")

    def _on_progress(self, cur, total):
        self.status_label.setText(f"Analyzing page {cur} of {total}...")

    def _on_failure(self, msg):
        self._set_calculating(False)
        self.status_label.setText("")
        QMessageBox.critical(self, "Error", msg)

    def _on_success(self, result, is_approx):
        self._set_calculating(False)
        if self._calc_load_id == self._load_id:
            self._analysis_cache[self.print_mode] = (result["all_coverage"], is_approx)
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
        cb = getattr(self, "on_calculated", None)
        if cb:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(800, cb)
        summary_card, summary_layout = card()
        header_row = QHBoxLayout()
        header = QLabel(f"{result['num_pages']} page(s) — {paper['name']}")
        header.setObjectName("cardHeader")
        header_total = QLabel(f"{result['total']:.2f}")
        header_total.setObjectName("total")
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(header_total)
        summary_layout.addLayout(header_row)
        shown = [p["page"] for p in result["per_page"]]
        prefix = "" if len(shown) == self.page_count else f"Pages {format_page_ranges(shown)} · "
        counts = QLabel(prefix + f"{result['color_pages']} color, {result['num_pages'] - result['color_pages']} black & white")
        counts.setWordWrap(True)
        counts.setObjectName("muted")
        summary_layout.addWidget(counts)

        ink_label = "Ink cost"
        if result["ink_multiplier"] != 1:
            ink_label += f" (×{result['ink_multiplier']:g})"
        self._row(summary_layout, ink_label, f"{result['ink_total']:.2f}")
        self._row(summary_layout, "Paper cost", f"{result['paper_total']:.2f}")
        self._row(summary_layout, "Subtotal", f"{result['subtotal']:.2f}")

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        summary_layout.addWidget(line)

        for key, label in PCT_LABELS:
            pct = result["percentages"].get(key, 0)
            amt = result["category_amounts"].get(key, 0)
            self._row(summary_layout, f"{label.replace(' %','')} ({pct:g}%)", f"{amt:.2f}", muted=True)

        if result["min_topup"] > 0:
            self._row(summary_layout, "Minimum price top-up", f"{result['min_topup']:.2f}", muted=True)

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

        table = QTableWidget(len(result["per_page"]), 7)
        table.setHorizontalHeaderLabels(["Page", "Cyan %", "Magenta %", "Yellow %", "Black %", "Ink cost", "Price"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        for i, p in enumerate(result["per_page"]):
            cov = p["coverage"]
            values = [
                str(p["page"]),
                f"{cov['C']*100:.1f}%",
                f"{cov['M']*100:.1f}%",
                f"{cov['Y']*100:.1f}%",
                f"{cov['K']*100:.1f}%",
                f"{p['ink_cost']:.2f}",
                f"{p['page_cost']:.2f}",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 6:
                    item.setToolTip(f"Color area {cov['color_frac']*100:.2f}% ({'Color' if p['is_color'] else 'B&W'})")
                table.setItem(i, col, item)

        table.setMinimumHeight(min(220, 40 + 28 * len(result["per_page"])))
        table_layout.addWidget(table)

        self.results_layout.addWidget(table_card)
        self.results_layout.addStretch()

    def _on_print_clicked(self):
        if not self.resolved_pdf_path:
            QMessageBox.information(self, "Can't print", "No real PDF available for this document (install LibreOffice for .docx printing).")
            return
        if self._selected_pages() != getattr(self, "print_pages", None):
            QMessageBox.information(
                self, "Pages changed",
                "The page list changed since the last calculation. Press Calculate first so the price matches, then print.",
            )
            return
        names = [os.path.basename(p) for p in self.sources]
        job_name = names[0] if len(names) == 1 else f"{len(names)} files"
        print_pdf(
            self.resolved_pdf_path, parent=self,
            paper=getattr(self, "paper", None),
            color_mode=getattr(self, "print_mode", "color"),
            quality=getattr(self, "print_quality", "standard"),
            job_name=job_name,
            pages=getattr(self, "print_pages", None),
        )