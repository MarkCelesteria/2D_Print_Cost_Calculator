from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialog, QDialogButtonBox, QFormLayout, QScrollArea, QCheckBox, QTextEdit
)
from PyQt6.QtCore import Qt, QRectF, QSize, QVariantAnimation
from PyQt6.QtGui import QPainter, QColor, QPen
import sys

from printcost.core import pricing
from printcost.core import autostart
from printcost.core import updater
from printcost.ui.style import PRIMARY, TEXT, CARD_BG, BORDER, YELLOW, YELLOW_DARK, CmykBar
from printcost.ui.notes import notice

QUALITY_LABELS = [("draft", "Draft"), ("standard", "Standard"), ("high", "High")]

PCT_LABELS = [
    ("labor", "Labor %"),
    ("maintenance", "Maintenance %"),
    ("electricity", "Electricity %"),
    ("spoilage", "Spoilage %"),
    ("printer", "Printer %"),
    ("profit", "Profit %"),
]


class ToggleSwitch(QCheckBox):
    TRACK_W, TRACK_H = 42, 22

    def __init__(self, text="", parent=None, extra=False):
        """extra=True: yellow when on, for options that cost extra (rush fee, double-sided...)."""
        super().__init__(text, parent)
        self._on_color = YELLOW if extra else PRIMARY
        self._pos = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(120)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _set_pos(self, v):
        self._pos = v
        self.update()

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on):  # programmatic changes snap instead of sliding
        super().setChecked(on)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def hitButton(self, pos):
        return self.rect().contains(pos)  # whole row is clickable, not just the switch

    def sizeHint(self):
        text_w = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(self.TRACK_W + (10 + text_w if self.text() else 0), self.TRACK_H + 4)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor("#cbd5e1"), QColor(self._on_color)
        t = self._pos
        track_color = QColor(*(int(off.getRgb()[i] + (on.getRgb()[i] - off.getRgb()[i]) * t) for i in range(3)))

        y = (self.height() - self.TRACK_H) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(QRectF(0, y, self.TRACK_W, self.TRACK_H), self.TRACK_H / 2, self.TRACK_H / 2)

        d = self.TRACK_H - 4
        x = 2 + (self.TRACK_W - d - 4) * t
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, y + 2, d, d))

        if self.text():
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(self.TRACK_W + 10, 0, self.width() - self.TRACK_W - 10, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class DirtyButton(QPushButton):
    """A button that shows a small yellow dot while there is something unsaved."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.dirty = False

    def set_dirty(self, on):
        self.dirty = on
        self.setToolTip("You have unsaved changes" if on else "")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.dirty:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor(YELLOW))
            p.drawEllipse(self.width() - 17, 5, 11, 11)
            p.end()


def card(layout_cls=QVBoxLayout):
    frame = QFrame()
    frame.setObjectName("card")
    layout = layout_cls(frame)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(6)
    return frame, layout


def _opt_str(v):
    return "" if v is None else str(v)


class PaperSizeDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Paper Size")
        form = QFormLayout(self)

        self.name_input = QLineEdit(existing["name"] if existing else "")
        self.width_input = QLineEdit(str(existing["width_in"]) if existing else "")
        self.height_input = QLineEdit(str(existing["height_in"]) if existing else "")
        self.price_input = QLineEdit(str(existing["price"]) if existing else "")

        form.addRow("Name:", self.name_input)
        form.addRow("Width (in):", self.width_input)
        form.addRow("Height (in):", self.height_input)
        form.addRow("Price per sheet:", self.price_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.result_data = None

    def _on_accept(self):
        try:
            name = self.name_input.text().strip()
            if not name:
                raise ValueError
            self.result_data = {
                "name": name,
                "width_in": float(self.width_input.text()),
                "height_in": float(self.height_input.text()),
                "price": float(self.price_input.text()),
            }
            self.accept()
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Please fill all fields correctly.")


class DiscountDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Discount")
        form = QFormLayout(self)
        self.name_input = QLineEdit(existing["name"] if existing else "")
        self.pct_input = QLineEdit(f"{existing['pct']:g}" if existing else "")
        form.addRow("Name:", self.name_input)
        form.addRow("Discount %:", self.pct_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.result_data = None

    def _on_accept(self):
        try:
            name = self.name_input.text().strip()
            pct = float(self.pct_input.text())
            if not name or not 0 < pct <= 100:
                raise ValueError
            self.result_data = {"name": name, "pct": pct}
            self.accept()
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Enter a name and a percentage between 0 and 100.")


class BulkDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Bulk discount")
        form = QFormLayout(self)
        self.min_input = QLineEdit(str(existing["min"]) if existing else "")
        self.max_input = QLineEdit(str(existing["max"] or "") if existing else "")
        self.max_input.setPlaceholderText("no limit")
        self.pct_input = QLineEdit(f"{existing['pct']:g}" if existing else "")
        form.addRow("From (pages):", self.min_input)
        form.addRow("To (pages):", self.max_input)
        form.addRow("Discount %:", self.pct_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.result_data = None

    def _on_accept(self):
        try:
            lo = int(self.min_input.text())
            raw = self.max_input.text().strip()
            hi = int(raw) if raw else None
            pct = float(self.pct_input.text())
            if lo < 1 or (hi is not None and hi < lo) or not 0 < pct <= 100:
                raise ValueError
            self.result_data = {"min": lo, "max": hi, "pct": pct}
            self.accept()
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Check the page range and a percentage between 0 and 100.")


class PermitRulesDialog(QDialog):
    RULES = (
        "Read before turning this on:\n\n"
        "1. Only turn this on if your business has a valid business permit (Mayor's permit) "
        "and is registered with the BIR (you have a TIN).\n\n"
        "2. Your receipts will then be labeled \"Official Receipt\" and will show your TIN.\n\n"
        "3. A real Official Receipt must follow BIR rules (for example an Authority to Print, "
        "or an approved receipting system). Ask your BIR RDO whether receipts from this app are "
        "acceptable before you use them as your official receipt.\n\n"
        "4. You are responsible for using this correctly. Do not turn it on if you have no permit.\n\n"
        "If you leave it off, receipts say they are NOT official and are only a price breakdown."
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Business permit rules")
        self.resize(460, 420)
        lay = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.RULES)
        lay.addWidget(text, 1)

        self.agree_row = QFrame()
        self.agree_row.setObjectName("flash")
        row = QHBoxLayout(self.agree_row)
        row.setContentsMargins(8, 6, 8, 6)
        self.agree = ToggleSwitch("I have read the rules above")
        row.addWidget(self.agree)
        row.addStretch()
        lay.addWidget(self.agree_row)

        # light red flash, twice, then back to normal
        self._flash = QVariantAnimation(self)
        self._flash.setDuration(400)
        self._flash.setStartValue(0)
        self._flash.setKeyValueAt(0.5, 110)
        self._flash.setEndValue(0)
        self._flash.setLoopCount(2)
        self._flash.valueChanged.connect(self._paint_flash)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _paint_flash(self, alpha):
        self.agree_row.setStyleSheet(
            f"QFrame#flash {{ background: rgba(239, 68, 68, {int(alpha)}); border-radius: 8px; }}")

    def _try_accept(self):
        if self.agree.isChecked():
            self.accept()
        else:
            self._flash.stop()
            self._flash.start()


class PaperTypeDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Paper type")
        form = QFormLayout(self)
        e = existing or {}
        self.name_input = QLineEdit(e.get("name", ""))
        self.price_input = QLineEdit(f"{e['price_factor']:g}" if e else "1")
        self.ink_input = QLineEdit(f"{e['ink_factor']:g}" if e else "1")
        self.driver_input = QLineEdit(e.get("driver", ""))
        self.driver_input.setPlaceholderText("e.g. Glossy Photo Paper")
        form.addRow("Name:", self.name_input)
        form.addRow("Sheet price ×:", self.price_input)
        form.addRow("Ink cost ×:", self.ink_input)
        form.addRow("Printer setting:", self.driver_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.result_data = None

    def _on_accept(self):
        try:
            name = self.name_input.text().strip()
            pf, inkf = float(self.price_input.text()), float(self.ink_input.text())
            if not name or pf <= 0 or inkf <= 0:
                raise ValueError
            self.result_data = {"name": name, "price_factor": pf, "ink_factor": inkf,
                                "driver": self.driver_input.text().strip()}
            self.accept()
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Enter a name and numbers above 0.")


class SettingsTab(QWidget):
    def __init__(self, settings, on_save, on_check_update=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.on_save = on_save
        self.on_check_update = on_check_update
        self.ink_inputs = {}
        self.pct_inputs = {}
        self.quality_inputs = {}
        self.setAcceptDrops(True)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        # ---- General ----
        gen_card, gen_layout = card()
        root.addWidget(gen_card)

        gen_header = QLabel("General")
        gen_header.setObjectName("cardHeader")
        gen_layout.addWidget(gen_header)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Receipt name"))
        self.shop_input = QLineEdit(self.settings.get("shop_name", "Print Cost Calculator"))
        self.shop_input.setPlaceholderText("Name shown at the top of receipts")
        self.shop_input.setMaxLength(40)
        name_row.addWidget(self.shop_input, 1)
        gen_layout.addLayout(name_row)

        self.permit_box = ToggleSwitch("I have a business permit / BIR registration")
        self.permit_box.setChecked(bool(self.settings.get("has_permit")))
        self.permit_box.toggled.connect(self._toggle_permit)
        gen_layout.addWidget(self.permit_box)

        tin_row = QHBoxLayout()
        self.tin_label = QLabel("TIN")
        self.tin_input = QLineEdit(self.settings.get("tin", ""))
        self.tin_input.setPlaceholderText("000-000-000-00000")
        self.tin_input.setMaxLength(20)
        tin_row.addWidget(self.tin_label)
        tin_row.addWidget(self.tin_input, 1)
        gen_layout.addLayout(tin_row)
        self.tin_label.setVisible(self.permit_box.isChecked())
        self.tin_input.setVisible(self.permit_box.isChecked())

        if sys.platform.startswith("win"):
            self.autostart_box = ToggleSwitch("Open automatically when my PC turns on")
            self.autostart_box.setChecked(autostart.is_autostart_enabled())
            self.autostart_box.toggled.connect(self._toggle_autostart)
            gen_layout.addWidget(self.autostart_box)

        update_row = QHBoxLayout()
        update_row.addWidget(QLabel(f"Version {updater.APP_VERSION}"))
        update_row.addStretch()
        check_btn = QPushButton("Check for updates")
        check_btn.setObjectName("secondary")
        check_btn.clicked.connect(lambda: self.on_check_update and self.on_check_update())
        update_row.addWidget(check_btn)
        gen_layout.addLayout(update_row)

        # ---- Paper sizes ----
        paper_card, paper_layout = card()
        root.addWidget(paper_card)

        header = QLabel("Bond Paper Prices")
        header.setObjectName("cardHeader")
        paper_layout.addWidget(header)

        self.paper_table = QTableWidget(0, 4)
        self.paper_table.setHorizontalHeaderLabels(["Name", "Width (in)", "Height (in)", "Price / sheet"])
        self.paper_table.verticalHeader().setVisible(False)
        self.paper_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.paper_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.paper_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        paper_layout.addWidget(self.paper_table)
        self._refresh_paper_table()

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add")
        add_btn.setObjectName("secondary")
        add_btn.clicked.connect(self._add_paper)
        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("secondary")
        edit_btn.clicked.connect(self._edit_paper)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("secondary")
        del_btn.clicked.connect(self._delete_paper)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        paper_layout.addLayout(btn_row)

        # ---- Paper types ----
        type_card, type_layout = card()
        root.addWidget(type_card)
        th = QLabel("Paper Types")
        th.setObjectName("cardHeader")
        type_layout.addWidget(th)
        tsub = QLabel("Sheet price = paper size price × Sheet price ×. Ink cost × is multiplied on top of the quality. "
                      "Printer setting is shown before printing.")
        tsub.setObjectName("muted")
        tsub.setWordWrap(True)
        type_layout.addWidget(tsub)
        self.type_table = self._make_table(["Name", "Sheet price ×", "Ink cost ×", "Printer setting"])
        type_layout.addWidget(self.type_table)
        type_layout.addLayout(self._button_row(
            lambda: self._add_item("paper_types", PaperTypeDialog),
            lambda: self._edit_item("paper_types", PaperTypeDialog, self.type_table),
            lambda: self._delete_item("paper_types", self.type_table)))

        # ---- Ink prices ----
        ink_card, ink_layout = card(QGridLayout)
        root.addWidget(ink_card)

        ink_header = QLabel("Ink Prices")
        ink_header.setObjectName("cardHeader")
        ink_layout.addWidget(ink_header, 0, 0, 1, 4)
        ink_sub = QLabel("Cost to print ONE full page at 100% of that color")
        ink_sub.setObjectName("muted")
        ink_layout.addWidget(ink_sub, 1, 0, 1, 4)

        labels = {"C": "Cyan", "M": "Magenta", "Y": "Yellow", "K": "Black (K)"}
        for i, ch in enumerate(["C", "M", "Y", "K"]):
            r, c = 2 + i // 2, (i % 2) * 2
            ink_layout.addWidget(QLabel(labels[ch]), r, c)
            field = QLineEdit(str(self.settings["ink_prices"][ch]))
            field.setFixedWidth(80)
            self.ink_inputs[ch] = field
            ink_layout.addWidget(field, r, c + 1)

        # ---- Percentages ----
        pct_card, pct_layout = card(QGridLayout)
        root.addWidget(pct_card)

        pct_header = QLabel("Default Cost Percentages")
        pct_header.setObjectName("cardHeader")
        pct_layout.addWidget(pct_header, 0, 0, 1, 6)
        pct_sub = QLabel("Each is applied separately on top of ink + paper cost")
        pct_sub.setObjectName("muted")
        pct_layout.addWidget(pct_sub, 1, 0, 1, 6)

        for i, (key, label) in enumerate(PCT_LABELS):
            r, c = 2 + i // 3, (i % 3) * 2
            pct_layout.addWidget(QLabel(label), r, c)
            field = QLineEdit(str(self.settings["default_percentages"][key]))
            field.setFixedWidth(70)
            self.pct_inputs[key] = field
            pct_layout.addWidget(field, r, c + 1)

        self.pct_inputs["printer"].setToolTip("Savings for buying a new printer when this one wears out")

        # ---- Print quality ----
        q_card, q_layout = card(QGridLayout)
        root.addWidget(q_card)

        q_header = QLabel("Print Quality")
        q_header.setObjectName("cardHeader")
        q_layout.addWidget(q_header, 0, 0, 1, 6)
        q_sub = QLabel("Ink cost multiplier per quality level (paper cost is not affected)")
        q_sub.setObjectName("muted")
        q_layout.addWidget(q_sub, 1, 0, 1, 6)

        for i, (key, label) in enumerate(QUALITY_LABELS):
            q_layout.addWidget(QLabel(f"{label} ×"), 2, i * 2)
            field = QLineEdit(str(self.settings["quality_multipliers"][key]))
            field.setFixedWidth(70)
            self.quality_inputs[key] = field
            q_layout.addWidget(field, 2, i * 2 + 1)

        q_layout.addWidget(QLabel("High: black mix %"), 3, 0)
        self.black_mix_input = QLineEdit(str(self.settings.get("high_black_mix", 60.0)))
        self.black_mix_input.setFixedWidth(70)
        self.black_mix_input.setToolTip(
            "On High quality, this % of each page's black is also charged as Cyan, Magenta and Yellow ink "
            "(0 = plain black).")
        q_layout.addWidget(self.black_mix_input, 3, 1)

        # ---- Pricing rules ----
        rules_card, rules_layout = card(QGridLayout)
        root.addWidget(rules_card)

        rules_header = QLabel("Pricing Rules")
        rules_header.setObjectName("cardHeader")
        rules_layout.addWidget(rules_header, 0, 0, 1, 4)
        rules_sub = QLabel("Each page has a minimum, added up across the document (e.g. 2 color at 5 + 1 B&W at 3 = 13). "
                           "If the total after all percentages is below that sum, it is topped up to it. Leave blank to turn off. "
                           "If only B&W is set, it also applies to color pages.")
        rules_sub.setWordWrap(True)
        rules_sub.setObjectName("muted")
        rules_layout.addWidget(rules_sub, 1, 0, 1, 4)

        rules_layout.addWidget(QLabel("Min price / page (B&W)"), 2, 0)
        self.min_price_input = QLineEdit(_opt_str(self.settings.get("min_price_per_page")))
        self.min_price_input.setPlaceholderText("off")
        self.min_price_input.setFixedWidth(80)
        rules_layout.addWidget(self.min_price_input, 2, 1)

        rules_layout.addWidget(QLabel("Min price / page (Color)"), 2, 2)
        self.min_price_color_input = QLineEdit(_opt_str(self.settings.get("min_price_color")))
        self.min_price_color_input.setPlaceholderText("off")
        self.min_price_color_input.setFixedWidth(80)
        rules_layout.addWidget(self.min_price_color_input, 2, 3)

        rules_layout.addWidget(QLabel("Max price / page"), 3, 0)
        self.max_price_input = QLineEdit(_opt_str(self.settings.get("max_price_per_page")))
        self.max_price_input.setPlaceholderText("off")
        self.max_price_input.setFixedWidth(80)
        self.max_price_input.setToolTip("The job total can't go above this price × number of pages. Leave blank for no max.")
        rules_layout.addWidget(self.max_price_input, 3, 1)

        rules_layout.addWidget(QLabel("Color page threshold %"), 3, 2)
        self.color_threshold_input = QLineEdit(str(self.settings.get("color_threshold_pct", 0.5)))
        self.color_threshold_input.setFixedWidth(80)
        self.color_threshold_input.setToolTip(
            "A page counts as Color only if at least this % of it is colored. "
            "Raise it to ignore tiny colored items.")
        rules_layout.addWidget(self.color_threshold_input, 3, 3)

        # ---- Discounts ----
        disc_card, disc_layout = card()
        root.addWidget(disc_card)
        h = QLabel("Discounts")
        h.setObjectName("cardHeader")
        disc_layout.addWidget(h)
        sub = QLabel("Named discounts. After calculating, click Apply Discount on the results to use one.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        disc_layout.addWidget(sub)
        self.discount_table = self._make_table(["Name", "Discount %"])
        disc_layout.addWidget(self.discount_table)
        disc_layout.addLayout(self._button_row(
            lambda: self._add_item("discounts", DiscountDialog),
            lambda: self._edit_item("discounts", DiscountDialog, self.discount_table),
            lambda: self._delete_item("discounts", self.discount_table)))

        bh = QLabel("Bulk discount (automatic)")
        bh.setObjectName("cardHeader")
        disc_layout.addWidget(bh)
        bsub = QLabel("Applied automatically by the number of pages printed. Leave To blank for no upper limit. "
                      "It adds to a named discount if you apply one.")
        bsub.setObjectName("muted")
        bsub.setWordWrap(True)
        disc_layout.addWidget(bsub)
        self.bulk_table = self._make_table(["From (pages)", "To (pages)", "Discount %"])
        disc_layout.addWidget(self.bulk_table)
        disc_layout.addLayout(self._button_row(
            lambda: self._add_item("bulk_discounts", BulkDialog),
            lambda: self._edit_item("bulk_discounts", BulkDialog, self.bulk_table),
            lambda: self._delete_item("bulk_discounts", self.bulk_table)))
        self._refresh_discount_tables()

        save_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to Default")
        reset_btn.setObjectName("secondary")
        reset_btn.clicked.connect(self._reset_to_default)
        save_row.addWidget(reset_btn)
        save_row.addStretch()
        save_btn = DirtyButton("Save Settings")
        self.save_btn = save_btn
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        root.addStretch()
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setStyleSheet(f"QFrame#footer {{ background: {CARD_BG}; border-top: 1px solid {BORDER}; }}")
        save_row.setContentsMargins(16, 10, 16, 10)
        footer.setLayout(save_row)
        outer.addWidget(footer)
        self._add_header_strips()
        self._wire_dirty()

    def _add_header_strips(self):
        """A short CMYK strip under every section title, like the bar at the top of the window."""
        for h in self.findChildren(QLabel):
            if h.objectName() != "cardHeader":
                continue
            lay = h.parentWidget().layout() if h.parentWidget() else None
            i = lay.indexOf(h) if lay is not None else -1
            if i >= 0 and isinstance(lay, QVBoxLayout):
                lay.insertWidget(i + 1, CmykBar(3, 44), 0, Qt.AlignmentFlag.AlignLeft)

    def _wire_dirty(self):
        for w in self.findChildren(QLineEdit):
            w.textChanged.connect(self._mark_dirty)
        for w in self.findChildren(QCheckBox):
            if w is not getattr(self, "autostart_box", None):  # that one applies at once, it is not saved here
                w.toggled.connect(self._mark_dirty)
        self._set_dirty(False)

    def _mark_dirty(self, *_):
        self._set_dirty(True)

    def _set_dirty(self, on):
        self.save_btn.set_dirty(on)

    def _toggle_permit(self, on):
        if on and not PermitRulesDialog(self).exec():
            self.permit_box.blockSignals(True)
            self.permit_box.setChecked(False)
            self.permit_box.blockSignals(False)
            on = False
        self.tin_label.setVisible(on)
        self.tin_input.setVisible(on)

    def _toggle_autostart(self, checked):
        try:
            if checked:
                autostart.enable_autostart()
            else:
                autostart.disable_autostart()
        except OSError as e:
            QMessageBox.warning(self, "Autostart", f"Couldn't change the startup setting:\n{e}")
            self.autostart_box.blockSignals(True)
            self.autostart_box.setChecked(autostart.is_autostart_enabled())
            self.autostart_box.blockSignals(False)

    def _make_table(self, headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return t

    def _button_row(self, add, edit, delete):
        row = QHBoxLayout()
        for text, fn in (("+ Add", add), ("Edit", edit), ("Delete", delete)):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        return row

    def _refresh_discount_tables(self):
        self.settings["bulk_discounts"].sort(key=lambda b: b["min"])
        rows = [(self.type_table, [[t["name"], f"{t['price_factor']:g}", f"{t['ink_factor']:g}", t["driver"]]
                                   for t in self.settings["paper_types"]]),
                (self.discount_table, [[d["name"], f"{d['pct']:g}%"] for d in self.settings["discounts"]]),
                (self.bulk_table, [[str(b["min"]), str(b["max"]) if b["max"] else "and up", f"{b['pct']:g}%"]
                                   for b in self.settings["bulk_discounts"]])]
        for table, data in rows:
            table.setRowCount(len(data))
            for i, values in enumerate(data):
                for col, val in enumerate(values):
                    item = QTableWidgetItem(val)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(i, col, item)
            self._fit_table(table)

    def _fit_table(self, table, max_rows=6):
        rows = min(table.rowCount(), max_rows)
        body = sum(table.rowHeight(i) for i in range(rows)) or table.verticalHeader().defaultSectionSize()
        table.setFixedHeight(table.horizontalHeader().sizeHint().height() + body + 2 * table.frameWidth())

    def _add_item(self, key, dlg_cls):
        dlg = dlg_cls(self)
        if dlg.exec() and dlg.result_data:
            self.settings[key].append(dlg.result_data)
            self._refresh_discount_tables()
            self._set_dirty(True)

    def _edit_item(self, key, dlg_cls, table):
        row = table.currentRow()
        if row < 0:
            return
        dlg = dlg_cls(self, existing=self.settings[key][row])
        if dlg.exec() and dlg.result_data:
            self.settings[key][row] = dlg.result_data
            self._refresh_discount_tables()
            self._set_dirty(True)

    def _delete_item(self, key, table):
        row = table.currentRow()
        if row < 0:
            return
        del self.settings[key][row]
        self._refresh_discount_tables()
        self._set_dirty(True)

    def _refresh_paper_table(self):
        self.paper_table.setRowCount(len(self.settings["paper_sizes"]))
        for i, p in enumerate(self.settings["paper_sizes"]):
            values = [p["name"], str(p["width_in"]), str(p["height_in"]), str(p["price"])]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.paper_table.setItem(i, col, item)
        self._fit_table(self.paper_table)

    def _add_paper(self):
        dlg = PaperSizeDialog(self)
        if dlg.exec() and dlg.result_data:
            self.settings["paper_sizes"].append(dlg.result_data)
            self._refresh_paper_table()
            self._set_dirty(True)

    def _edit_paper(self):
        row = self.paper_table.currentRow()
        if row < 0:
            return
        dlg = PaperSizeDialog(self, existing=self.settings["paper_sizes"][row])
        if dlg.exec() and dlg.result_data:
            self.settings["paper_sizes"][row] = dlg.result_data
            self._refresh_paper_table()
            self._set_dirty(True)

    def _delete_paper(self):
        row = self.paper_table.currentRow()
        if row < 0:
            return
        del self.settings["paper_sizes"][row]
        self._refresh_paper_table()
        self._set_dirty(True)

    def _reset_to_default(self):
        if notice(self, "danger", "Reset to Default",
                  "This resets everything in Settings: paper, ink, percentages, receipt details and discounts.",
                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        defaults = pricing.load_default_settings()
        keep = ("asked_about_autostart", "update_remind_after")
        for key, value in defaults.items():
            if key not in keep:
                self.settings[key] = value

        pricing.save_settings(self.settings)
        self.on_save()

        self._refresh_paper_table()
        self._refresh_discount_tables()
        for ch, field in self.ink_inputs.items():
            field.setText(str(self.settings["ink_prices"][ch]))
        for key, field in self.pct_inputs.items():
            field.setText(str(self.settings["default_percentages"][key]))
        self.min_price_input.setText(_opt_str(self.settings["min_price_per_page"]))
        self.min_price_color_input.setText(_opt_str(self.settings["min_price_color"]))
        self.max_price_input.setText(_opt_str(self.settings["max_price_per_page"]))
        self.color_threshold_input.setText(str(self.settings["color_threshold_pct"]))
        self.black_mix_input.setText(str(self.settings["high_black_mix"]))
        for key, field in self.quality_inputs.items():
            field.setText(str(self.settings["quality_multipliers"][key]))
        self.shop_input.setText(self.settings["shop_name"])
        self.permit_box.blockSignals(True)
        self.permit_box.setChecked(False)
        self.permit_box.blockSignals(False)
        self.tin_input.setText("")
        self.tin_label.setVisible(False)
        self.tin_input.setVisible(False)

        self._set_dirty(False)  # reset is saved straight away
        QMessageBox.information(self, "Reset", "Settings reset to default.")
        
    def _save(self):
        try:
            for ch, field in self.ink_inputs.items():
                self.settings["ink_prices"][ch] = float(field.text())
            for key, field in self.pct_inputs.items():
                self.settings["default_percentages"][key] = float(field.text())
            raw = self.min_price_input.text().strip()
            self.settings["min_price_per_page"] = float(raw) if raw else None
            raw_c = self.min_price_color_input.text().strip()
            self.settings["min_price_color"] = float(raw_c) if raw_c else None
            raw_max = self.max_price_input.text().strip()
            self.settings["max_price_per_page"] = float(raw_max) if raw_max else None
            threshold = float(self.color_threshold_input.text().strip() or 0)
            if not 0 <= threshold <= 100:
                raise ValueError
            self.settings["color_threshold_pct"] = threshold
            self.settings["shop_name"] = self.shop_input.text().strip() or "Print Cost Calculator"
            mix = float(self.black_mix_input.text().strip() or 0)
            if not 0 <= mix <= 100:
                raise ValueError
            self.settings["high_black_mix"] = mix
            for key, field in self.quality_inputs.items():
                mult = float(field.text())
                if mult <= 0:
                    raise ValueError
                self.settings["quality_multipliers"][key] = mult
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Ink prices and percentages must be numbers.")
            return
        tin = self.tin_input.text().strip()
        if self.permit_box.isChecked() and not tin:
            QMessageBox.critical(self, "TIN needed", "Enter your TIN, or turn off the permit option.")
            return
        self.settings["has_permit"] = self.permit_box.isChecked()
        self.settings["tin"] = tin
        pricing.save_settings(self.settings)
        self._set_dirty(False)
        self.on_save()
        QMessageBox.information(self, "Saved", "Settings saved.")