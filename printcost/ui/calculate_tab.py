import os

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QComboBox, QLineEdit, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea, QInputDialog, QDoubleSpinBox, QApplication, QCheckBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtPrintSupport import QPrinterInfo

from printcost.core import pricing
from printcost.ui.icons import make_icon
from printcost.ui.preview_widget import PreviewWidget
from printcost.core.worker import AnalyzeWorker, LoadDocumentWorker, LayoutWorker
from printcost.core.analyzer import (render_pdf_page, apply_preview_mode, compute_cost, cleanup_temp_dirs,
                      parse_page_ranges, format_page_ranges, get_document_page_size, closest_paper,
                      get_pdf_page_inches, fit_page_to_paper, SUPPORTED_EXTS, IMAGE_EXTS, is_image,
                      get_pdf_page_count)
from printcost.ui.full_preview_dialog import FullPreviewDialog
from printcost.ui.print_helper import print_pdf, print_duplex
from printcost.ui.receipt import ReceiptDialog
from printcost.core import poster
from printcost.core import idphoto
from printcost.core import layout as page_layout
from printcost.ui.poster_dialog import PosterDialog
from printcost.ui.idphoto_dialog import IdPhotoDialog
from printcost.ui.layout_preview_dialog import SheetsPreviewDialog, BookPreviewDialog
from printcost.ui.help import HelpButton, with_help
from printcost.ui.notes import label_style, notice
from printcost.ui.style import MAGENTA_DARK, ElidedLabel

INK_TINTS = ["#e0f4fb", "#fde4f2", "#fff7c9", "#e9edf2"]

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
    _layout_token = 0
    _layout_busy = False
    _calc_after_layout = False

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
        self.original_pdf_path = None 
        self.page_count = 1
        self.current_page = 0
        self.is_approx = False

        self.poster_job = None  # set while the loaded document is a tiled poster
        self.pct_inputs = {}
        self.quality_inputs = {}
        self.manual_discount = None
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

        tools_row = QHBoxLayout()
        self.poster_btn = QPushButton("Poster")
        self.poster_btn.setObjectName("secondary")
        self.poster_btn.setIcon(make_icon("poster"))
        self.poster_btn.setIconSize(QSize(18, 18))
        self.poster_btn.setToolTip("Slice one picture or PDF page into a grid of sheets.\n"
                                   "Uses the file already loaded, or asks you to pick one.")
        self.poster_btn.clicked.connect(self._open_poster)
        tools_row.addWidget(self.poster_btn, 1)
        self.id_btn = QPushButton("ID photos")
        self.id_btn.setObjectName("secondary")
        self.id_btn.setIcon(make_icon("id-photo"))
        self.id_btn.setIconSize(QSize(18, 18))
        self.id_btn.setToolTip("Print 1x1, 2x2, passport size and other ID photos, as many copies as you need.\n"
                               "Uses the picture already loaded, or asks you to pick one.")
        self.id_btn.clicked.connect(self._open_id)
        tools_row.addWidget(self.id_btn, 1)
        left_layout.addLayout(tools_row)

        browse_btn = QPushButton("Browse files")
        browse_btn.setObjectName("secondary")
        browse_btn.setToolTip("PDF, Word or images")
        browse_btn.clicked.connect(self._browse)
        left_layout.addWidget(browse_btn)

        drop_hint = QLabel("or drag & drop files here")
        drop_hint.setObjectName("muted")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(drop_hint)

        self.file_label = ElidedLabel("No file selected")
        self.file_label.setObjectName("muted")
        self.file_label.setWordWrap(True)
        left_layout.addWidget(self.file_label)
        self._badges = []
        self.badge_row = QHBoxLayout()
        self.badge_row.setSpacing(6)
        self.badge_row.addStretch()
        left_layout.addLayout(self.badge_row)

        self.poster_box = QFrame()
        self.poster_box.setObjectName("posterBox")
        self.poster_box.setStyleSheet("QFrame#posterBox { background: transparent; border: none; }")
        pb = QVBoxLayout(self.poster_box)
        pb.setContentsMargins(0, 4, 0, 0)
        pb.setSpacing(6)
        self.poster_label = QLabel("")
        self.poster_label.setWordWrap(True)
        self.poster_label.setStyleSheet(label_style("info"))
        pb.addWidget(self.poster_label)
        tile_row = QHBoxLayout()
        tile_row.addWidget(QLabel("Print"))
        self.tile_combo = QComboBox()
        self.tile_combo.setToolTip("Choose one sheet to reprint a bad one")
        self.tile_combo.currentIndexChanged.connect(self._on_tile_chosen)
        tile_row.addWidget(self.tile_combo, 1)
        pb.addLayout(tile_row)
        poster_btns = QHBoxLayout()
        self.edit_poster_btn = edit_poster = QPushButton("Edit poster")
        edit_poster.setObjectName("secondary")
        edit_poster.clicked.connect(self._edit_job)
        remove_poster = QPushButton("Remove")
        remove_poster.setObjectName("secondary")
        remove_poster.setToolTip("Go back to the normal document")
        remove_poster.clicked.connect(self._remove_poster)
        poster_btns.addWidget(edit_poster, 1)
        poster_btns.addWidget(remove_poster, 1)
        pb.addLayout(poster_btns)
        self.poster_box.hide()
        left_layout.addWidget(self.poster_box)
        left_layout.addStretch()

        right_col = QVBoxLayout()
        root.addLayout(right_col, 1)

        self.refresh_btn = QPushButton("⟳ Refresh")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)

        opts_card, opts_layout = card(QGridLayout)
        opts_layout.setHorizontalSpacing(10)
        opts_layout.setVerticalSpacing(8)
        opts_layout.setColumnMinimumWidth(0, 80)
        right_col.addWidget(opts_card)

        # top block: two equal columns (size / type, mode / quality, fit / per page)
        top = QGridLayout()
        top.setHorizontalSpacing(10)
        top.setVerticalSpacing(8)
        top.setColumnMinimumWidth(0, 80)
        top.setColumnStretch(1, 1)
        top.setColumnStretch(3, 1)
        opts_layout.addLayout(top, 0, 0, 4, 6)

        top.addWidget(QLabel("Paper size"), 0, 0)
        self.paper_combo = QComboBox()
        top.addWidget(self.paper_combo, 0, 1)
        top.addWidget(QLabel("Paper type"), 0, 2)
        self.type_combo = QComboBox()
        top.addWidget(self.type_combo, 0, 3)
        self.paper_note = QLabel("")
        self.paper_note.setWordWrap(True)
        self.paper_note.hide()
        top.addWidget(self.paper_note, 1, 0, 1, 4)
        self._refresh_paper_options()
        self._refresh_type_options()

        top.addWidget(QLabel("Print mode"), 2, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Color", "Grayscale", "Black & White"])
        self.mode_combo.setCurrentText("Color")
        self.mode_combo.currentIndexChanged.connect(self._refresh_preview_mode)
        top.addWidget(self.mode_combo, 2, 1)

        top.addWidget(QLabel("Quality"), 2, 2)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Draft", "Standard", "High"])
        self.quality_combo.setCurrentText("Standard")
        top.addWidget(self.quality_combo, 2, 3)

        self.fit_label = QLabel("Image fit")
        top.addWidget(self.fit_label, 3, 0)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["Fit (with margins)", "Fill (edge to edge)"])
        top.addWidget(self.fit_combo, 3, 1)
        self.per_page_label = QLabel("Per page")
        top.addWidget(self.per_page_label, 3, 2)
        self.per_page_combo = QComboBox()
        self.per_page_combo.addItems(["1", "2", "4"])
        top.addWidget(self.per_page_combo, 3, 3)

        self.layout_label = QLabel("Print layout")
        top.addLayout(with_help(self.layout_label, "Normal prints every page on its own side. Back-to-back prints on both sides of each sheet. Booklet puts 2 pages on each side so the sheets can be folded and stapled into a small book."), 4, 0)
        self.layout_combo = QComboBox()
        self.layout_combo.addItems([page_layout.MODE_NAMES[m] for m in page_layout.MODES])
        top.addWidget(self.layout_combo, 4, 1)
        self.blank_covers_box = QCheckBox("Blank back on covers")
        self.blank_covers_box.setToolTip("Keeps the back of the first page empty. In a booklet, the inside of the last page too.")
        blank_row = with_help(self.blank_covers_box, "Keeps the back of the first page empty, so the front cover is printed on one side only. In a booklet, the inside of the back cover stays empty too.")
        self.blank_help = blank_row.help
        top.addLayout(blank_row, 4, 2, 1, 2)

        self.duplex_frame = QFrame()
        self.duplex_frame.setObjectName("sub")
        sub = QGridLayout(self.duplex_frame)
        sub.setContentsMargins(12, 10, 12, 10)
        sub.setHorizontalSpacing(12)
        sub.setVerticalSpacing(8)
        self.flip_combo = QComboBox()
        self.flip_combo.addItems(["Long edge", "Short edge"])
        self.flip_combo.setToolTip("How you turn the stack over. Pick the one where the backs come out the right "
                                   "way up. The alignment test finds it for you.")
        self.output_combo = QComboBox()
        self.output_combo.addItems(["Printed side up", "Printed side down"])
        self.output_combo.setToolTip("How your printer drops the sheets. most inkjets printers drop them "
                                     "printed side up. Most laser printers drop them printed side down.")
        self.back_combo = QComboBox()
        self.back_combo.addItems(["Automatic", "Reversed"])
        self.back_combo.setToolTip("Automatic is right when you follow the prompts. Pick Reversed only if the "
                                   "backs land on the wrong sheets.")
        self.test_btn = QPushButton("Alignment test")
        self.test_btn.setObjectName("secondary")
        self.test_btn.clicked.connect(self._print_alignment_test)
        sub.addLayout(with_help(QLabel("Flip stack on"), "How you turn the printed stack over before the backs print. printers with a rear tray use Long edge. If the backs come out upside down, switch it, or run the Alignment test."), 0, 0)
        sub.addWidget(self.flip_combo, 0, 1)
        sub.addLayout(with_help(QLabel("Paper comes out"), "Print one page and look at where it lands. If you can see the print, choose Printed side up (Most inkjets printers). If you see the blank back, choose Printed side down (most laser printers). This decides whether you flip the stack when reloading."), 0, 2)
        sub.addWidget(self.output_combo, 0, 3)
        sub.addLayout(with_help(QLabel("Back order"), "Automatic prints the backs in the right order for your printer. Pick Reversed only if the backs end up on the wrong sheets."), 1, 0)
        sub.addWidget(self.back_combo, 1, 1)
        test_row = with_help(self.test_btn, "Prints 2 test sheets so you can check the backs once. The app then fixes the settings above and remembers them for this printer.")
        sub.addLayout(test_row, 1, 2, 1, 2)
        self.fold_label = QLabel("Middle margin")
        self.fold_input = QDoubleSpinBox()
        self.fold_input.setRange(0.0, 2.0)
        self.fold_input.setDecimals(2)
        self.fold_input.setSingleStep(0.05)
        self.fold_input.setSuffix(" in")
        self.fold_input.setFixedWidth(90)
        self.fold_input.setKeyboardTracking(False)  # rebuild once you press Enter or leave the box
        self.fold_input.setToolTip("Booklet only. 0 = the two pages touch at the fold. Any number sets the space between the fold and each page.")
        self.fold_label.setVisible(False)
        self.fold_input.setVisible(False)
        connect_row = with_help(self.fold_input, "0 makes the two pages touch at the fold, so a picture can run across both. Type a number (in inches) to leave that much space between the fold and each page. Your pages need to be designed to reach the edge.")
        connect_row.insertWidget(0, self.fold_label)
        self.connect_help = connect_row.help
        self.connect_help.setVisible(False)
        sub.addLayout(connect_row, 2, 0, 1, 4)
        sub.setColumnStretch(1, 1)
        sub.setColumnStretch(3, 1)
        top.addWidget(self.duplex_frame, 5, 0, 1, 4)
        self._duplex_widgets = [self.blank_covers_box, self.blank_help, self.duplex_frame]
        for w in self._duplex_widgets:
            w.setVisible(False)
        self._duplex_touched = False
        self._duplex_printer = None
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        self.blank_covers_box.toggled.connect(self._apply_layout)
        self.fold_input.valueChanged.connect(self._apply_layout)
        for box in (self.flip_combo, self.output_combo, self.back_combo):
            box.currentIndexChanged.connect(self._duplex_edited)

        self.paper_combo.currentIndexChanged.connect(self._reload_layout)
        self.paper_combo.currentIndexChanged.connect(self._on_paper_changed)
        self.fit_combo.currentIndexChanged.connect(self._reload_layout)
        self.per_page_combo.currentIndexChanged.connect(self._reload_layout)

        self.pages_label = QLabel("Print pages")
        opts_layout.addWidget(self.pages_label, 4, 0)
        self.pages_input = QLineEdit()
        self.pages_input.setPlaceholderText("All pages (or e.g. 1-6, 8-9, 11, 12)")
        self.pages_input.textChanged.connect(self._on_pages_changed)
        opts_layout.addWidget(self.pages_input, 4, 1, 1, 5)

        self.pct_combo = QComboBox()
        self.pct_edit = QLineEdit()
        self.pct_edit.setFixedWidth(60)
        for key, label in PCT_LABELS:
            field = QLineEdit(str(self.settings["default_percentages"][key]), self)  # hidden: holds the value
            field.hide()
            self.pct_inputs[key] = field
            self.pct_combo.addItem(label)
        for field in self.pct_inputs.values():
            field.textChanged.connect(self._sync_pct)
        self.pct_combo.currentIndexChanged.connect(self._sync_pct)
        self.pct_edit.textChanged.connect(self._pct_edited)
        self._sync_pct()
        opts_layout.addWidget(QLabel("Percentages"), 5, 0)
        opts_layout.addWidget(self.pct_combo, 5, 1, 1, 3)
        opts_layout.addWidget(self.pct_edit, 5, 4)
        opts_layout.addWidget(QLabel("%"), 5, 5)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setSpacing(10)
        self.results_scroll.setWidget(self.results_container)
        right_col.addWidget(self.results_scroll, 1)
        self._update_input_states()

    def _sync_pct(self, *_):
        for i, (key, label) in enumerate(PCT_LABELS):
            self.pct_combo.setItemText(i, f"{label.replace(' %', '')}:  {self.pct_inputs[key].text()}%")
        key = PCT_LABELS[self.pct_combo.currentIndex()][0]
        if self.pct_edit.text() != self.pct_inputs[key].text():
            self.pct_edit.setText(self.pct_inputs[key].text())

    def _pct_edited(self, text):
        self.pct_inputs[PCT_LABELS[self.pct_combo.currentIndex()][0]].setText(text)

    def refresh_settings(self, settings):
        self.settings = settings
        self._refresh_paper_options()
        self._refresh_type_options()
        for key, field in self.pct_inputs.items():
            field.setText(str(self.settings["default_percentages"][key]))

    def _on_refresh_clicked(self):
        for key, field in self.pct_inputs.items():
            field.setText(str(self.settings["default_percentages"][key]))
        self._refresh_paper_options()
        self._refresh_type_options(reset=True)
        for combo, default in ((self.mode_combo, "Color"), (self.quality_combo, "Standard")):
            combo.blockSignals(True)
            combo.setCurrentText(default)
            combo.blockSignals(False)

        self.file_path = None
        self.poster_job = None
        self._update_poster_box()
        self.sources = []
        self.manual_discount = None
        self._load_id += 1
        self._analysis_cache = {}
        self.doc_size = None
        self.paper_note.hide()
        self.pages_input.clear()
        self.doc_size = None
        self.paper_note.hide()
        self._loading = False
        self.resolved_pdf_path = None
        self.original_pdf_path = None
        self._layout_base = None
        self.page_count = 1
        self.current_page = 0
        self.is_approx = False

        self.preview.show_placeholder("No file selected")
        self.file_label.setText("No file selected")
        self._set_badges()
        self.page_label.setText("No file selected")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.full_preview_btn.setEnabled(False)
        self.status_label.setText("")
        self._clear_results()

    def _set_badges(self, *names):
        """Small magenta tags next to the file name: Poster, Edited, Double-sided..."""
        self._badges = list(names)
        while self.badge_row.count():
            w = self.badge_row.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for n in self._badges:
            b = QLabel(n)
            b.setObjectName("badge")
            self.badge_row.addWidget(b)
        self.badge_row.addStretch()

    def _add_badge(self, name):
        if name not in self._badges:
            self._set_badges(*self._badges, name)

    def _refresh_type_options(self, reset=False):
        current = "" if reset else self.type_combo.currentText()
        self.type_combo.clear()
        self.type_combo.addItems([t["name"] for t in self.settings["paper_types"]])
        if current:
            self.type_combo.setCurrentText(current)

    def _refresh_paper_options(self):
        current = self.paper_combo.currentText()
        self.paper_combo.blockSignals(True)
        self.paper_combo.clear()
        names = [p["name"] for p in self.settings["paper_sizes"]]
        self.paper_combo.addItems(names)
        default = next((n for n in names if n.startswith("Short")), None)
        if current in names:
            self.paper_combo.setCurrentText(current)
        elif default:
            self.paper_combo.setCurrentText(default)
        self.paper_combo.blockSignals(False)
        self._update_paper_note()

    def load_file(self, path, auto_calculate=False):
        self.load_files([path], auto_calculate)

    def load_files(self, paths, auto_calculate=False):
        paths = [p for p in paths if p.lower().endswith(SUPPORTED_EXTS)]
        if not paths:
            return
        self.poster_job = None
        self._update_poster_box()
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
        if self.poster_job:  # posters are rebuilt by _on_paper_changed; fit / per-page don't apply
            return
        if len(self.sources) > 1 or any(is_image(p) for p in self.sources):
            self._clear_results()
            self._start_load()

    def _detect_paper(self):
        """Read the document's page size, pre-select the closest paper and flag a mismatch."""
        is_new = getattr(self, "_new_document", False)
        self._new_document = False
        self.doc_size = None
        self._paper_auto_note = ""
        if self.resolved_pdf_path and (self.poster_job or not any(is_image(p) for p in self.sources)):
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
        if self.poster_job:  # the sheets changed size, so the tiles have to be cut again
            self._rebuild_poster()
        self._paper_auto_note = ""  # a manual choice: drop the "paper set to..." message
        self._update_paper_note()
        if self.layout_mode == "booklet":
            self._apply_layout()
        elif self.resolved_pdf_path:
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
            text, level = getattr(self, "_paper_auto_note", ""), "info"  # matches: only say what the app did
        else:
            scale = min(ps[0] / size[0], ps[1] / size[1])
            if scale < 0.98:
                text = f"{label} document: shrinks to {scale * 100:.0f}% on this paper."
            elif scale > 1.02:
                text = f"{label} document: enlarged to {scale * 100:.0f}% on this paper."
            else:
                text = f"{label} document: blank space on this paper."
            if closest_paper(size, self.settings["paper_sizes"]) is None:
                text += " No matching paper in Settings."
            level = "warn"
        self.paper_note.setStyleSheet(label_style(level))
        self.paper_note.setText(text)
        self.paper_note.setVisible(bool(text))

    def _fit_paper(self):
        """The selected paper when the document doesn't match it (previews then show how it prints on it)."""
        if self.layout_mode != "normal":
            return None
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
        self._set_badges()
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
        self.original_pdf_path = pdf_path
        self._layout_base = pdf_path
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
        self._update_input_states()
        if pdf_path is not None and self.layout_mode != "normal" and not self.poster_job:
            self._apply_layout()

        if auto_calculate:
            self._calculate()

    def _update_input_states(self):
        """Gray out the boxes that can't change anything for the current file."""
        loaded = bool(self.sources) and not self.poster_job
        images = sum(1 for p in self.sources if is_image(p))
        pages_on = self.resolved_pdf_path is not None and self.page_count > 1 and self.layout_mode == "normal"
        can_layout = not self.poster_job and (not self.sources or getattr(self, "_layout_base", None) is not None)
        for box, label, on, tip in (
            (self.layout_combo, self.layout_label, can_layout,
             "Not available for posters, or when the file can't be turned into a PDF."),
            (self.fit_combo, self.fit_label, loaded and images > 0,
             "Only used for pictures. PDF and Word pages keep their own layout."),
            (self.per_page_combo, self.per_page_label, loaded and images > 1,
             "Only used when you add more than one picture."),
            (self.pages_input, self.pages_label, pages_on,
             "Only used when the document has more than one page."),
        ):
            box.setEnabled(on)
            label.setEnabled(on)
            box.setToolTip("" if on else tip)
        if not pages_on and self.pages_input.text():
            self.pages_input.clear()

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
        if self.layout_mode != "normal":
            self._open_layout_preview()
            return
        dialog = FullPreviewDialog(self.resolved_pdf_path, self.page_count, parent=self,
                                   mode=self._current_mode(), included=self._selected_pages(),
                                   fit_paper=self._fit_paper(),
                                   original_path=self.original_pdf_path, doc_name=self._doc_name(),
                                   paper_sizes=self.settings["paper_sizes"])
        dialog.exec()
        edited = dialog.edited_path()
        pages, included = dialog.page_count, dialog.included
        dialog.deleteLater()
        if edited:
            self._adopt_edited_pdf(edited, pages, included)

    def _open_layout_preview(self):
        count = get_pdf_page_count(self._layout_base)
        blank = self.blank_covers_box.isChecked()
        if self.layout_mode == "duplex":
            dialog = SheetsPreviewDialog(self.resolved_pdf_path, page_layout.duplex_order(count, blank),
                                         self._current_mode(), self._duplex_values()["flip"], self,
                                         paper=self._current_paper())
        else:
            dialog = BookPreviewDialog(self._layout_base, page_layout.arrange(count, 4, blank),
                                       self._current_mode(), self, sheets_cb=self._open_booklet_sheets)
        dialog.exec()
        dialog.deleteLater()

    def _open_booklet_sheets(self):
        """Booklet as printed: every sheet's front and back on bond paper."""
        count = get_pdf_page_count(self._layout_base)
        sides = page_layout.booklet_sides(count, self.blank_covers_box.isChecked())
        dialog = SheetsPreviewDialog(self.resolved_pdf_path, sides, self._current_mode(),
                                     self._duplex_values()["flip"], self, booklet=True)
        dialog.exec()
        dialog.deleteLater()

    def _doc_name(self):
        return os.path.splitext(os.path.basename(self.sources[0]))[0] if self.sources else "document"

    def _adopt_edited_pdf(self, path, page_count, included):
        """Use the PDF edited in Full Preview for the preview, the price and printing."""
        if not os.path.exists(path):
            QMessageBox.warning(self, "Edits not applied",
                                "The edited file is no longer available, so your changes could not be applied. "
                                "The document is unchanged.")
            return
        try:
            before = get_document_page_size(self.resolved_pdf_path)
            after = get_document_page_size(path)
        except Exception:
            before = after = None
        self.resolved_pdf_path = path
        self._layout_base = path
        self._analysis_cache = {}
        self.page_count = page_count
        self.current_page = min(self.current_page, page_count - 1)
        self.pages_input.setText(format_page_ranges(included) if included else "")
        self._update_input_states()
        if before and after and max(abs(a - b) for a, b in zip(before, after)) > 0.05:
            idx = closest_paper(after, self.settings["paper_sizes"])
            if idx is not None and idx != self.paper_combo.currentIndex():
                self.paper_combo.blockSignals(True)
                self.paper_combo.setCurrentIndex(idx)
                self.paper_combo.blockSignals(False)
        self._new_document = False
        self._detect_paper()
        self._clear_results()
        self._add_badge("Edited")
        self.status_label.setText("Document edited. Press Calculate to update the price.")
        self._show_current_page()

    # ---------------- print layout ----------------

    @property
    def layout_mode(self):
        return page_layout.MODES[self.layout_combo.currentIndex()]

    def _set_layout_mode(self, mode):
        self.layout_combo.blockSignals(True)
        self.layout_combo.setCurrentIndex(page_layout.MODES.index(mode))
        self.layout_combo.blockSignals(False)
        for w in self._duplex_widgets:
            w.setVisible(mode != "normal")
        self.fold_label.setVisible(mode == "booklet")
        self.fold_input.setVisible(mode == "booklet")
        self.connect_help.setVisible(mode == "booklet")

    def _on_layout_changed(self):
        mode = self.layout_mode
        self._set_layout_mode(mode)
        if mode != "normal":
            self._load_duplex_prefs()
        self._apply_layout()

    def _load_duplex_prefs(self):
        name = self._duplex_printer or QPrinterInfo.defaultPrinterName()
        prefs = page_layout.printer_prefs(self.settings, name, self.layout_mode)
        for box, index in ((self.flip_combo, 0 if prefs["flip"] == "long" else 1),
                           (self.output_combo, 0 if prefs["output"] == "up" else 1),
                           (self.back_combo, 1 if prefs["reverse"] else 0)):
            box.blockSignals(True)
            box.setCurrentIndex(index)
            box.blockSignals(False)
        self._duplex_touched = False

    def _duplex_edited(self):
        self._duplex_touched = True

    def _duplex_values(self):
        return {"flip": "long" if self.flip_combo.currentIndex() == 0 else "short",
                "output": "up" if self.output_combo.currentIndex() == 0 else "down",
                "reverse": self.back_combo.currentIndex() == 1}

    def _prefs_for_printer(self, name):
        if self._duplex_touched:
            return None
        return page_layout.printer_prefs(self.settings, name, self.layout_mode, saved_only=True)

    def _remember_duplex(self, result):
        self._duplex_printer = result["printer"]
        page_layout.save_printer_prefs(self.settings, result["printer"], self.layout_mode, result["prefs"])
        pricing.save_settings(self.settings)
        self._load_duplex_prefs()

    def _sheet_count(self):
        return page_layout.sheet_count(self.page_count) if self.layout_mode != "normal" else None

    def _sheet_price(self, price):
        if self.layout_mode == "normal":
            return price
        duplex = self.settings["duplex"]
        return price * (1 - duplex["discount_pct"] / 100) + duplex["extra_per_sheet"]

    def _apply_layout(self):
        base = getattr(self, "_layout_base", None)
        if base is None or self.poster_job:
            return
        mode = self.layout_mode
        if mode == "normal":
            self._layout_token += 1  # a build still running is now out of date
            self._layout_busy = False
            self.preview.stop_scan()
            self._finish_layout(mode, base, get_pdf_page_count(base))
            return
        paper = self._current_paper()
        if paper is None:
            return
        self._layout_token += 1
        token = self._layout_token
        self._layout_busy = True
        worker = LayoutWorker(base, mode, paper, self.blank_covers_box.isChecked(),
                              self.settings["duplex"]["margin_in"], self.fold_input.value(), self)
        worker.done.connect(lambda path, count: self._on_layout_built(token, mode, path, count))
        worker.failed.connect(lambda msg: self._on_layout_failed(token, msg))
        worker.finished.connect(worker.deleteLater)
        self.preview.start_scan()
        self.status_label.setText("Arranging pages...")
        worker.start()

    def _on_layout_built(self, token, mode, path, count):
        if token != self._layout_token:  # the layout changed again while this one was building
            return
        self._layout_busy = False
        self.preview.stop_scan()
        self._finish_layout(mode, path, count)
        if self._calc_after_layout:
            self._calc_after_layout = False
            self._calculate()

    def _on_layout_failed(self, token, msg):
        if token != self._layout_token:
            return
        self._layout_busy = False
        self._calc_after_layout = False
        self.preview.stop_scan()
        QMessageBox.warning(self, "Couldn't arrange the pages", msg)
        self._set_layout_mode("normal")
        self._apply_layout()

    def _finish_layout(self, mode, path, count):
        self.resolved_pdf_path = path
        self.page_count = count
        self.current_page = 0
        self._analysis_cache = {}
        self.manual_discount = None
        self._clear_results()
        self._update_input_states()
        badges = [b for b in self._badges if b not in page_layout.BADGES.values()]
        self._set_badges(*badges, *([page_layout.BADGES[mode]] if mode != "normal" else []))
        self._show_current_page()
        if mode != "normal":
            self.status_label.setText(
                f"{count} sides on {page_layout.sheet_count(count)} sheets. Press Calculate for the price.")

    def _print_alignment_test(self):
        paper = self._current_paper()
        if paper is None:
            QMessageBox.warning(self, "No paper size", "Please add a paper size in Settings first.")
            return
        try:
            pdf = page_layout.build_test_pdf(paper, landscape=self.layout_mode == "booklet")
        except Exception as e:
            QMessageBox.warning(self, "Couldn't make the test sheets", str(e))
            return
        result = print_duplex(pdf, parent=self, paper=paper, color_mode="bw", job_name="Alignment test",
                              prefs=self._duplex_values())
        if not result:
            return
        box = QMessageBox(self)
        box.setWindowTitle("How did the back sides come out?")
        box.setText("Sheet 1 should read FRONT on one side and BACK on the other, both the right way up, "
                    "and sheet 2 should be the next sheet.")
        right = box.addButton("They look right", QMessageBox.ButtonRole.AcceptRole)
        upside = box.addButton("The back is upside down", QMessageBox.ButtonRole.ActionRole)
        order = box.addButton("The backs are on the wrong sheets", QMessageBox.ButtonRole.ActionRole)
        both = box.addButton("Both are wrong", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (right, upside, order, both):
            return
        prefs = dict(result["prefs"])
        if clicked in (upside, both):
            prefs["flip"] = "short" if prefs["flip"] == "long" else "long"
        if clicked in (order, both):
            prefs["reverse"] = not prefs["reverse"]
        self._remember_duplex(dict(result, prefs=prefs))
        if clicked is not right:
            notice(self, "info", "Settings changed", "Saved for this printer. Run the test again to check it.")

    # ---------------- poster ----------------

    def _open_poster(self):
        job = self.poster_job
        if job and job.get("kind") == "id":  # an ID sheet is loaded: start a poster from the same picture
            path, job = job["source"], None
        else:
            path = job["source"] if job else None
        if path is None and len(self.sources) == 1 and not self.sources[0].lower().endswith(".docx"):
            path = self.sources[0]
        if path is None:
            exts = " ".join(f"*{e}" for e in IMAGE_EXTS + (".pdf",))
            path, _ = QFileDialog.getOpenFileName(self, "Choose a picture or PDF for the poster", "",
                                                  f"Pictures and PDFs ({exts})")
            if not path:
                return
        if self.paper_combo.count() == 0:
            QMessageBox.warning(self, "No paper size", "Please add a paper size in Settings first.")
            return
        dialog = PosterDialog(self, path, self.settings, self._current_paper(), job=job)
        if dialog.src is None:  # the file couldn't be opened (the dialog already said why)
            dialog.deleteLater()
            return
        accepted = dialog.exec() == dialog.DialogCode.Accepted
        result = dialog.result_data
        dialog.deleteLater()
        if accepted and result:
            self._adopt_poster(result["pdf"], result["job"], result["tile"])

    def _edit_job(self):
        """The Edit button: reopen whichever dialog made the current sheets."""
        if self.poster_job and self.poster_job.get("kind") == "id":
            self._open_id()
        else:
            self._open_poster()

    def _open_id(self):
        job = self.poster_job if self.poster_job and self.poster_job.get("kind") == "id" else None
        path = job["source"] if job else None
        if path is None and self.poster_job and is_image(self.poster_job["source"]):
            path = self.poster_job["source"]
        if path is None and len(self.sources) == 1 and is_image(self.sources[0]):
            path = self.sources[0]
        if path is None:
            exts = " ".join(f"*{e}" for e in IMAGE_EXTS)
            path, _ = QFileDialog.getOpenFileName(self, "Choose a photo for the ID sheets", "", f"Pictures ({exts})")
            if not path:
                return
        if self.paper_combo.count() == 0:
            QMessageBox.warning(self, "No paper size", "Please add a paper size in Settings first.")
            return
        dialog = IdPhotoDialog(self, path, self.settings, self._current_paper(), job=job)
        if dialog.src is None:  # the file couldn't be opened (the dialog already said why)
            dialog.deleteLater()
            return
        accepted = dialog.exec() == dialog.DialogCode.Accepted
        result = dialog.result_data
        dialog.deleteLater()
        if accepted and result:
            self._adopt_poster(result["pdf"], result["job"], result["tile"])

    def _adopt_poster(self, pdf_path, job, tile=0):
        """Use the tiled PDF like any loaded document: preview, price, print and receipt all work on it."""
        try:
            count = get_pdf_page_count(pdf_path)
            first = render_pdf_page(pdf_path, 0)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't open the poster", str(e))
            return
        self._set_layout_mode("normal")
        self.poster_job = job
        self.sources = [job["source"]]
        self.file_path = job["source"]
        self.manual_discount = None
        self._clear_results()
        self._load_id += 1
        idx = self.paper_combo.findText(job["spec"]["paper"]["name"])
        if idx >= 0 and idx != self.paper_combo.currentIndex():
            self.paper_combo.blockSignals(True)
            self.paper_combo.setCurrentIndex(idx)
            self.paper_combo.blockSignals(False)
        self._new_document = False  # the poster's paper was chosen on purpose: don't auto-switch it
        self._on_document_loaded(pdf_path, count, first, False, load_id=self._load_id)
        self.pages_input.clear()
        self.file_label.setText(os.path.basename(job["source"]))
        self._set_badges("ID photos" if job.get("kind") == "id" else "Poster")
        self._update_poster_box()
        if tile:
            self.current_page = min(tile, count - 1)
            self._show_current_page()
        self.status_label.setText(("ID sheets" if job.get("kind") == "id" else "Poster")
                                  + " ready. Press Calculate for the price.")

    def _rebuild_poster(self):
        job = self.poster_job
        paper = self._current_paper()
        if job is None or paper is None:
            return
        spec = dict(job["spec"], paper={"name": paper["name"], "width_in": paper["width_in"],
                                        "height_in": paper["height_in"]})
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        extra = {}
        try:
            if job.get("kind") == "id":
                pdf, info = idphoto.build_pdf(job["source"], spec)
                extra = {"photos": info["photos"], "sheet_counts": info["counts"]}
            else:
                pdf = poster.build_poster_pdf(job["source"], spec, job["page"])
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Couldn't rebuild the sheets", str(e))
            return
        QApplication.restoreOverrideCursor()
        self._adopt_poster(pdf, dict(job, spec=spec, **extra), tile=self.current_page)

    def _remove_poster(self):
        sources = list(self.sources)
        self.poster_job = None
        self._update_poster_box()
        self.load_files(sources)

    def _update_poster_box(self):
        job = self.poster_job
        self.poster_box.setVisible(job is not None)
        if job is None:
            return
        is_id = job.get("kind") == "id"
        self.edit_poster_btn.setText("Edit ID photos" if is_id else "Edit poster")
        self.tile_combo.blockSignals(True)
        self.tile_combo.clear()
        self.tile_combo.addItem("All sheets")
        if is_id:
            d = idphoto.describe(job["spec"])
            self.poster_label.setText(f"ID photos · {d['summary']} · {d['sheets']} sheet(s) of {d['paper']}")
            for i, n in enumerate(job["sheet_counts"]):
                self.tile_combo.addItem(f"Sheet {i + 1}  ({n} photos)")
        else:
            d = poster.describe(job["spec"])
            self.poster_label.setText(f"Poster {d['grid']} of {d['paper']} · {d['sheets']} sheets · {d['size']}")
            cols = job["spec"]["cols"]
            for i in range(d["sheets"]):
                self.tile_combo.addItem(f"{poster.tile_label(i, cols)}  (sheet {i + 1})")
        self.tile_combo.blockSignals(False)

    def _on_tile_chosen(self, index):
        """One sheet = a one-page selection, so price, print and receipt follow the normal page logic."""
        self.pages_input.setText("" if index <= 0 else str(index))

    def _id_receipt(self):
        job = self.poster_job
        if not job or job.get("kind") != "id":
            return None
        return idphoto.receipt_info(job["spec"])

    def _poster_receipt(self):
        if not self.poster_job or self.poster_job.get("kind") == "id":
            return None
        d = poster.describe(self.poster_job["spec"])
        return {"grid": d["grid"], "sheets": d["sheets"], "size": d["size"]}

    def _layout_receipt(self, result):
        mode = getattr(self, "print_layout", "normal")
        if mode == "normal" or self._layout_base is None:
            return None
        count = get_pdf_page_count(self._layout_base)
        return {"name": page_layout.BADGES[mode], "sheets": result["sheets"], "pages": count,
                "sheet_pages": page_layout.sheet_pages(count, mode, self.blank_covers_box.isChecked())}

    def _calculate(self):
        if self._layout_busy:
            self._calc_after_layout = True
            return
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
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Percentages must be numbers.")
            return

        try:
            page_text = self.pages_input.text() if self.resolved_pdf_path else ""
            selected_pages = parse_page_ranges(page_text, self.page_count)
        except ValueError as e:
            QMessageBox.critical(self, "Invalid pages", str(e))
            return
        self.print_pages = selected_pages

        paper_name = self.paper_combo.currentText()
        size = next(p for p in self.settings["paper_sizes"] if p["name"] == paper_name)
        ptype = next((t for t in self.settings["paper_types"] if t["name"] == self.type_combo.currentText()),
                     {"name": "Plain paper", "price_factor": 1.0, "ink_factor": 1.0, "driver": ""})
        self.paper = dict(size, price=size["price"] * ptype["price_factor"])  # sheet price = size x type factor
        self.print_paper_type = ptype["name"]
        self.print_media = ptype.get("driver", "")
        self.print_layout = self.layout_mode
        sheets, sheet_price = self._sheet_count(), self._sheet_price(self.paper["price"])

        self.manual_discount = None
        self._clear_results()
        self._set_calculating(True)
        self.status_label.setText("Analyzing document...")

        mode_map = {"Color": "color", "Grayscale": "grayscale", "Black & White": "bw"}
        mode = mode_map[self.mode_combo.currentText()]
        quality = self.quality_combo.currentText().lower()
        ink_multiplier = self.settings["quality_multipliers"].get(quality, 1.0) * ptype["ink_factor"]
        black_mix = self.settings.get("high_black_mix", 0.0) / 100 if quality == "high" else 0.0
        self.print_mode = mode
        self.print_quality = quality
        self._calc_load_id = self._load_id

        cached = self._analysis_cache.get(mode)
        if cached is not None:
            self._set_calculating(False)
            coverage, cached_approx = cached
            chosen = coverage if not selected_pages else [coverage[n - 1] for n in selected_pages]
            result = compute_cost(
                chosen, self.settings["ink_prices"], sheet_price, percentages,
                page_numbers=selected_pages,
                min_price_per_page=self.settings.get("min_price_per_page"),
                min_price_color=self.settings.get("min_price_color"),
                color_threshold_pct=self.settings.get("color_threshold_pct", 0.5),
                black_mix=black_mix, ink_multiplier=ink_multiplier,
                max_price_per_page=self.settings.get("max_price_per_page"), sheets=sheets,
            )
            self.status_label.setText("Done (reused the earlier scan)." + (" (approximate estimate)" if cached_approx else ""))
            self._render_results(result, self.paper, cached_approx)
            return

        self.worker = AnalyzeWorker(
            self.resolved_pdf_path, self.file_path,
            self.settings["ink_prices"], sheet_price, percentages, self.is_approx, mode,
            min_price=self.settings.get("min_price_per_page"),
            ink_multiplier=ink_multiplier,
            min_price_color=self.settings.get("min_price_color"),
            color_threshold_pct=self.settings.get("color_threshold_pct", 0.5),
            pages=selected_pages, black_mix=black_mix,
            max_price=self.settings.get("max_price_per_page"), sheets=sheets,
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

    def _render_results(self, result, paper, is_approx, notify=True):
        self._result, self._result_paper, self._result_approx = result, paper, is_approx
        poster.apply_pricing(result, self.poster_job, self.page_count)
        self._apply_discounts(result)
        cb = getattr(self, "on_calculated", None)
        if cb and notify:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(800, cb)
        summary_card, summary_layout = card()
        header_row = QHBoxLayout()
        if self.poster_job and self.poster_job.get("kind") == "id":
            header = QLabel(f"ID photos · {result['num_pages']} sheet(s) — {paper['name']}")
        elif self.poster_job:
            d = poster.describe(self.poster_job["spec"])
            header = QLabel(f"Poster {d['grid']} · {result['num_pages']} sheet(s) — {paper['name']}")
        elif self.print_layout != "normal":
            header = QLabel(f"{result['sheets']} sheet(s) · {self._layout_receipt(result)['pages']} pages — {paper['name']}")
        else:
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
        if self.poster_job:
            if self.poster_job.get("kind") == "id":
                d = idphoto.describe(self.poster_job["spec"])
                text = f"{d['photos']} photos: {d['summary']}"
            else:
                d = poster.describe(self.poster_job["spec"])
                text = f"Final poster size: {d['size']}"
            pr = self.poster_job["pricing"]
            if pr["mode"] == "flat" and pr["fee"] > 0 and result.get("poster_fee") is None:
                text += ". The flat fee applies when all sheets are printed; this is the calculated price."
            size_note = QLabel(text)
            size_note.setObjectName("muted")
            size_note.setWordWrap(True)
            summary_layout.addWidget(size_note)

        if result.get("poster_fee") is not None:
            self._row(summary_layout, result["poster_fee_label"], f"{result['poster_fee']:.2f}")
            self._row(summary_layout, "Ink, paper and handling are included", "", muted=True)
        else:
            ink_label = "Ink cost"
            if result.get("black_mix"):
                ink_label += " (rich black)"
            if result["ink_multiplier"] != 1:
                ink_label += f" (×{result['ink_multiplier']:g})"
            self._row(summary_layout, ink_label, f"{result['ink_total']:.2f}")
            paper_label = "Paper cost" if self.print_layout == "normal" else f"Paper cost ({result['sheets']} sheets)"
            self._row(summary_layout, paper_label, f"{result['paper_total']:.2f}")
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

            if result.get("max_cut", 0) > 0:
                self._row(summary_layout, "Max price cap", f"-{result['max_cut']:.2f}", muted=True)

        for label, amount in result["discount_lines"]:
            self._row(summary_layout, label, f"-{amount:.2f}", muted=True)

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

        if is_approx:
            note = QLabel("Approximate: .docx text coverage is estimated (install LibreOffice for exact results).")
            note.setObjectName("muted")
            note.setWordWrap(True)
            summary_layout.addWidget(note)

        summary_layout.addSpacing(4)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        if self.settings.get("discounts"):
            md = self.manual_discount
            name = md["name"] if md and len(md["name"]) <= 14 else (md["name"][:13] + "…" if md else "")
            disc_btn = self._action_button(name or "Apply Discount", "tag", self._choose_discount)
            disc_btn.setToolTip("Choose a discount")
            actions.addWidget(disc_btn, 1)
        actions.addWidget(self._action_button("Print", "printer", self._on_print_clicked, cta=True), 1)
        receipt_data = self._receipt_data(result, paper)
        actions.addWidget(self._action_button(
            "View Receipt", "receipt", lambda: ReceiptDialog(receipt_data, self).exec()), 1)
        summary_layout.addLayout(actions)

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
                if 1 <= col <= 4:
                    item.setBackground(QColor(INK_TINTS[col - 1]))
                if col == 6:
                    item.setToolTip(f"Color area {cov['color_frac']*100:.2f}% ({'Color' if p['is_color'] else 'B&W'})")
                table.setItem(i, col, item)

        rows = min(table.rowCount(), 8)
        table.setFixedHeight(table.horizontalHeader().sizeHint().height()
                             + sum(table.rowHeight(i) for i in range(rows)) + 2 * table.frameWidth())
        table_layout.addWidget(table)

        self.results_layout.addWidget(table_card)
        self.results_layout.addStretch()

    def _action_button(self, text, icon, slot, cta=False):
        btn = QPushButton(text)
        btn.setObjectName("cta" if cta else "secondary")
        btn.setIcon(make_icon(icon, "white", MAGENTA_DARK) if cta else make_icon(icon))
        btn.setIconSize(QSize(18, 18))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: slot())
        return btn

    def _apply_discounts(self, result):
        """Bulk (automatic) + the chosen named discount, both taken off the gross total."""
        gross = result["gross"]
        bulk = min(100.0, pricing.bulk_pct(self.settings.get("bulk_discounts"), result["num_pages"]))
        lines = []
        if bulk:
            lines.append((f"Bulk discount ({bulk:g}%)", gross * bulk / 100))
        md = self.manual_discount
        if md:
            pct = min(md["pct"], 100.0 - bulk)
            lines.append((f"{md['name']} ({pct:g}%)", gross * pct / 100))
        amount = sum(a for _, a in lines)
        result.update(discount_lines=lines, discount_amount=amount,
                      discount_pct=amount / gross * 100 if gross else 0.0, total=gross - amount)

    def _choose_discount(self):
        discounts = self.settings.get("discounts", [])
        items = ["No discount"] + [f"{d['name']} ({d['pct']:g}%)" for d in discounts]
        names = [d["name"] for d in discounts]
        current = names.index(self.manual_discount["name"]) + 1 if self.manual_discount and \
            self.manual_discount["name"] in names else 0
        item, ok = QInputDialog.getItem(self, "Apply discount", "Choose a discount:", items, current, False)
        if not ok:
            return
        i = items.index(item)
        self.manual_discount = dict(discounts[i - 1]) if i else None
        self._clear_results()
        self._render_results(self._result, self._result_paper, self._result_approx, notify=False)

    def _receipt_data(self, result, paper):
        return {
            "shop": self.settings.get("shop_name") or "Print Cost Calculator",
            "files": [os.path.basename(p) for p in self.sources],
            "paper": paper["name"],
            "paper_type": getattr(self, "print_paper_type", "Plain paper"),
            "mode": getattr(self, "print_mode", "color"),
            "quality": getattr(self, "print_quality", "standard"),
            "pages": getattr(self, "print_pages", None),
            "page_count": self.page_count,
            "tin": (self.settings.get("tin") or None) if self.settings.get("has_permit") else None,
            "poster": self._poster_receipt(),
            "idphoto": self._id_receipt(),
            "layout": self._layout_receipt(result),
            "result": result,
        }

    def _on_print_clicked(self):
        if not self.resolved_pdf_path:
            notice(self, "warn", "Can't print", "No PDF to print. Install LibreOffice for .docx.")
            return
        if self._selected_pages() != getattr(self, "print_pages", None):
            notice(self, "warn", "Pages changed", "Pages changed. Press Calculate again, then print.")
            return
        names = [os.path.basename(p) for p in self.sources]
        job_name = names[0] if len(names) == 1 else f"{len(names)} files"
        if self.layout_mode != "normal":
            result = print_duplex(
                self.resolved_pdf_path, parent=self, paper=getattr(self, "paper", None),
                color_mode=getattr(self, "print_mode", "color"),
                quality=getattr(self, "print_quality", "standard"),
                job_name=job_name, media=getattr(self, "print_media", ""),
                prefs=self._duplex_values(), prefs_for=self._prefs_for_printer,
                finish_note="Fold the stack in half and staple the middle." if self.layout_mode == "booklet" else "",
            )
            if result:
                self._remember_duplex(result)
            return
        print_pdf(
            self.resolved_pdf_path, parent=self,
            paper=getattr(self, "paper", None),
            color_mode=getattr(self, "print_mode", "color"),
            quality=getattr(self, "print_quality", "standard"),
            job_name=job_name,
            pages=getattr(self, "print_pages", None),
            media=getattr(self, "print_media", ""),
            actual_size=self.poster_job is not None,
        )