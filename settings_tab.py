from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialog, QDialogButtonBox, QFormLayout, QScrollArea
)
from PyQt6.QtCore import Qt

import pricing

QUALITY_LABELS = [("draft", "Draft"), ("standard", "Standard"), ("high", "High")]

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


class SettingsTab(QWidget):
    def __init__(self, settings, on_save, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.on_save = on_save
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
        self.paper_table.setMinimumHeight(190)
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

        # ---- Pricing rules ----
        rules_card, rules_layout = card(QGridLayout)
        root.addWidget(rules_card)

        rules_header = QLabel("Pricing Rules")
        rules_header.setObjectName("cardHeader")
        rules_layout.addWidget(rules_header, 0, 0, 1, 4)
        rules_sub = QLabel("Each page has a minimum, added up across the document (e.g. 2 color at 5 + 1 B&W at 3 = 13). "
                           "If the total after all percentages is below that sum, it is topped up to it. Leave blank to turn off. "
                           "If only B&W is set, it also applies to color pages. Discount is taken off the final total.")
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

        rules_layout.addWidget(QLabel("Discount %"), 3, 0)
        self.discount_input = QLineEdit(str(self.settings.get("default_discount", 0.0)))
        self.discount_input.setFixedWidth(80)
        rules_layout.addWidget(self.discount_input, 3, 1)

        rules_layout.addWidget(QLabel("Color page threshold %"), 3, 2)
        self.color_threshold_input = QLineEdit(str(self.settings.get("color_threshold_pct", 0.5)))
        self.color_threshold_input.setFixedWidth(80)
        self.color_threshold_input.setToolTip(
            "A page counts as Color only if at least this % of it is colored. "
            "Raise it to ignore tiny colored items.")
        rules_layout.addWidget(self.color_threshold_input, 3, 3)

        save_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to Default")
        reset_btn.setObjectName("secondary")
        reset_btn.clicked.connect(self._reset_to_default)
        save_row.addWidget(reset_btn)
        save_row.addStretch()
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        root.addLayout(save_row)
        root.addStretch()

    def _refresh_paper_table(self):
        self.paper_table.setRowCount(len(self.settings["paper_sizes"]))
        for i, p in enumerate(self.settings["paper_sizes"]):
            values = [p["name"], str(p["width_in"]), str(p["height_in"]), str(p["price"])]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.paper_table.setItem(i, col, item)

    def _add_paper(self):
        dlg = PaperSizeDialog(self)
        if dlg.exec() and dlg.result_data:
            self.settings["paper_sizes"].append(dlg.result_data)
            self._refresh_paper_table()

    def _edit_paper(self):
        row = self.paper_table.currentRow()
        if row < 0:
            return
        dlg = PaperSizeDialog(self, existing=self.settings["paper_sizes"][row])
        if dlg.exec() and dlg.result_data:
            self.settings["paper_sizes"][row] = dlg.result_data
            self._refresh_paper_table()

    def _delete_paper(self):
        row = self.paper_table.currentRow()
        if row < 0:
            return
        del self.settings["paper_sizes"][row]
        self._refresh_paper_table()

    def _reset_to_default(self):
        reply = QMessageBox.question(
            self, "Reset to Default",
            "This will reset all paper sizes, ink prices, and percentages "
            "back to the built-in defaults. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        defaults = pricing.load_default_settings()
        self.settings["paper_sizes"] = defaults["paper_sizes"]
        self.settings["ink_prices"] = defaults["ink_prices"]
        self.settings["default_percentages"] = defaults["default_percentages"]
        self.settings["min_price_per_page"] = defaults["min_price_per_page"]
        self.settings["min_price_color"] = defaults["min_price_color"]
        self.settings["color_threshold_pct"] = defaults["color_threshold_pct"]
        self.settings["default_discount"] = defaults["default_discount"]
        self.settings["quality_multipliers"] = defaults["quality_multipliers"]

        pricing.save_settings(self.settings)
        self.on_save()

        self._refresh_paper_table()
        for ch, field in self.ink_inputs.items():
            field.setText(str(self.settings["ink_prices"][ch]))
        for key, field in self.pct_inputs.items():
            field.setText(str(self.settings["default_percentages"][key]))
        self.min_price_input.setText(_opt_str(self.settings["min_price_per_page"]))
        self.min_price_color_input.setText(_opt_str(self.settings["min_price_color"]))
        self.color_threshold_input.setText(str(self.settings["color_threshold_pct"]))
        self.discount_input.setText(str(self.settings["default_discount"]))
        for key, field in self.quality_inputs.items():
            field.setText(str(self.settings["quality_multipliers"][key]))

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
            threshold = float(self.color_threshold_input.text().strip() or 0)
            if not 0 <= threshold <= 100:
                raise ValueError
            self.settings["color_threshold_pct"] = threshold
            discount = float(self.discount_input.text().strip() or 0)
            if not 0 <= discount <= 100:
                raise ValueError
            self.settings["default_discount"] = discount
            for key, field in self.quality_inputs.items():
                mult = float(field.text())
                if mult <= 0:
                    raise ValueError
                self.settings["quality_multipliers"][key] = mult
        except ValueError:
            QMessageBox.critical(self, "Invalid input", "Ink prices and percentages must be numbers.")
            return
        pricing.save_settings(self.settings)
        self.on_save()
        QMessageBox.information(self, "Saved", "Settings saved.")