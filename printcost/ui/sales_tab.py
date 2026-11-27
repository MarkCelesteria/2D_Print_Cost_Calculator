from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QComboBox, QProgressBar,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox, QLineEdit,
)

from printcost.core import saleslog
from printcost.ui.receipt import ReceiptDialog, receipt_number

MONTHS_SHOWN = 36
PERIODS = ["This month", "Last month", "Last 12 months", "All time"]
NEGATIVE = "#b91c1c"


def _card():
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 14, 14, 14)
    lay.setSpacing(6)
    return frame, lay


def _stat(title, text, sub="", big=False):
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("muted")
    v = QLabel(text)
    v.setObjectName("total")
    if big:
        v.setStyleSheet("font-size: 28px;")
    lay.addWidget(t)
    lay.addWidget(v)
    s = QLabel(sub or " ")
    s.setObjectName("muted")
    s.setWordWrap(True)
    lay.addWidget(s)
    return frame


def _bar_row(label, amount, share):
    """One line of a breakdown: name, amount and share of revenue, with a bar under it."""
    row = QWidget()
    row.setObjectName("barRow")
    row.setStyleSheet("QWidget#barRow { background: transparent; }")
    lay = QVBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    top = QHBoxLayout()
    top.addWidget(QLabel(label))
    top.addStretch()
    value = QLabel(f"{amount:,.2f}")
    value.setStyleSheet("font-weight: 700;")
    top.addWidget(value)
    pct = QLabel(f"{share * 100:.0f}%")
    pct.setObjectName("muted")
    pct.setMinimumWidth(34)
    pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    top.addWidget(pct)
    lay.addLayout(top)
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setValue(int(max(0.0, min(1.0, share)) * 1000))
    bar.setFixedHeight(8)
    lay.addWidget(bar)
    return row


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return t


def _fit_height(t, max_rows):
    """Shrinks the table to its rows (at least one row high); past max_rows it scrolls."""
    rows = max(1, min(t.rowCount(), max_rows))
    t.setFixedHeight(t.horizontalHeader().sizeHint().height()
                     + rows * t.verticalHeader().defaultSectionSize() + 2 * t.frameWidth() + 2)


def _cell(text, color=None, bold=False):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    if color:
        item.setForeground(QColor(color))
    if bold:
        f = item.font()
        f.setBold(True)
        item.setFont(f)
    return item


def _sum(rows):
    t = {"total": 0.0, "jobs": 0, "pages": 0, "ink": 0.0, "paper": 0.0, "fee": 0.0, "discount": 0.0, "cats": {}}
    for v in rows:
        for k in ("total", "jobs", "pages", "ink", "paper", "fee", "discount"):
            t[k] += v.get(k, 0)
        for c, a in (v.get("cats") or {}).items():
            t["cats"][c] = t["cats"].get(c, 0.0) + a
    return t


class SpendDialog(QDialog):
    """Buy supplies: takes money out of one bank."""

    def __init__(self, banks, current_key, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Spend from bank")
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.bank = QComboBox()
        for key, label, _earned, _spent, balance in banks:
            self.bank.addItem(f"{label}  (bank {balance:,.2f})", key)
        idx = self.bank.findData(current_key)
        if idx >= 0:
            self.bank.setCurrentIndex(idx)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.0, 10_000_000)
        self.amount.setDecimals(2)
        self.note = QLineEdit()
        self.note.setPlaceholderText("e.g. A4 ream, 500 sheets")
        form.addRow("Bank", self.bank)
        form.addRow("Amount spent", self.amount)
        form.addRow("Note", self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _ok(self):
        if self.amount.value() > 0:
            self.accept()

    def values(self):
        return self.bank.currentData(), self.amount.value(), self.note.text().strip()


class SalesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._recent = []
        self._months = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)  # the tab scrolls on small screens
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        top = QHBoxLayout()
        title = QLabel("Sales overview")
        title.setObjectName("cardHeader")
        top.addWidget(title)
        top.addStretch()
        self.period = QComboBox()
        self.period.addItems(PERIODS)
        self.period.currentIndexChanged.connect(self._fill_cards)
        top.addWidget(self.period)
        root.addLayout(top)
        note = QLabel("What customers paid, split by the percentages in Settings (after discounts). "
                      "Electricity, labor and the rest are your markup shares, not your real bills.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)
        self.cards = QVBoxLayout()
        self.cards.setSpacing(10)
        root.addLayout(self.cards)

        bank_card, bk = _card()
        bank_head = QHBoxLayout()
        bank_title = QLabel("Banks")
        bank_title.setObjectName("cardHeader")
        bank_head.addWidget(bank_title)
        bank_head.addStretch()
        spend_btn = QPushButton("Spend from bank")
        spend_btn.setObjectName("cta")
        spend_btn.clicked.connect(self._spend)
        bank_head.addWidget(spend_btn)
        bk.addLayout(bank_head)
        self.bank_sub = QLabel()
        self.bank_sub.setObjectName("muted")
        self.bank_sub.setWordWrap(True)
        bk.addWidget(self.bank_sub)
        self.bank_table = _table(["Bank", "Earned", "Spent", "Balance"])
        bk.addWidget(self.bank_table)
        root.addWidget(bank_card)

        spend_card, sk = _card()
        spend_head = QHBoxLayout()
        spend_title = QLabel("Spending history")
        spend_title.setObjectName("cardHeader")
        spend_head.addWidget(spend_title)
        spend_head.addStretch()
        self.remove_spend_btn = QPushButton("Remove selected")
        self.remove_spend_btn.setObjectName("cta")
        self.remove_spend_btn.setVisible(False)
        self.remove_spend_btn.clicked.connect(self._remove_spends)
        spend_head.addWidget(self.remove_spend_btn)
        edit_spend_btn = QPushButton("Edit")
        edit_spend_btn.setObjectName("secondary")
        edit_spend_btn.setCheckable(True)
        spend_head.addWidget(edit_spend_btn)
        sk.addLayout(spend_head)
        spend_sub = QLabel("Money taken out of a bank. Remove one to put the money back.")
        spend_sub.setObjectName("muted")
        sk.addWidget(spend_sub)
        self.spend_table = _table(["Date", "Bank", "Note", "Amount"])
        sk.addWidget(self.spend_table)
        edit_spend_btn.toggled.connect(lambda on: self._set_edit(self.spend_table, self.remove_spend_btn, on))
        root.addWidget(spend_card)

        month_card, ml = _card()
        header = QLabel("Monthly sales")
        header.setObjectName("cardHeader")
        ml.addWidget(header)
        self.month_table = _table(["Month", "Jobs", "Pages", "Revenue", "Paper", "Ink"])
        ml.addWidget(self.month_table)
        root.addWidget(month_card)

        recent_card, rl = _card()
        header2 = QLabel("Recent receipts")
        header2.setObjectName("cardHeader")
        rl.addWidget(header2)
        sub = QLabel(f"The last {saleslog.MAX_RECENT} printed jobs. Double-click one to open its receipt, or press Edit to remove some.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        rl.addWidget(sub)
        self.recent_table = _table(["When", "Receipt no.", "Job", "Total"])
        self.recent_table.cellDoubleClicked.connect(lambda row, _c: self._open(row))
        rl.addWidget(self.recent_table)
        row = QHBoxLayout()
        row.addStretch()
        self.remove_sale_btn = QPushButton("Remove selected")
        self.remove_sale_btn.setObjectName("cta")
        self.remove_sale_btn.setVisible(False)
        self.remove_sale_btn.clicked.connect(self._remove_sales)
        row.addWidget(self.remove_sale_btn)
        edit_sale_btn = QPushButton("Edit")
        edit_sale_btn.setObjectName("secondary")
        edit_sale_btn.setCheckable(True)
        edit_sale_btn.toggled.connect(lambda on: self._set_edit(self.recent_table, self.remove_sale_btn, on))
        row.addWidget(edit_sale_btn)
        open_btn = QPushButton("View Receipt")
        open_btn.setObjectName("secondary")
        open_btn.clicked.connect(lambda: self._open(self.recent_table.currentRow()))
        row.addWidget(open_btn)
        rl.addLayout(row)
        root.addWidget(recent_card)

        self.refresh()

    # ---------------- data ----------------

    def refresh(self):
        months = saleslog.months()
        self._months = dict(months)
        self._fill_cards()
        self._fill_banks()
        self._fill_spends()

        shown = months[:MONTHS_SHOWN]
        self.month_table.setRowCount(len(shown))
        for i, (key, v) in enumerate(shown):
            label = datetime.strptime(key, "%Y-%m").strftime("%B %Y")
            values = (label, str(v["jobs"]), str(v["pages"]), f"{v['total']:,.2f}",
                      f"{v.get('paper', 0):,.2f}", f"{v.get('ink', 0):,.2f}")
            for col, text in enumerate(values):
                self.month_table.setItem(i, col, _cell(text))
        _fit_height(self.month_table, 12)

        self._recent = saleslog.recent()
        self.recent_table.setRowCount(len(self._recent))
        for i, e in enumerate(self._recent):
            try:
                dt = datetime.fromisoformat(e["ts"])
                when, number = dt.strftime("%b %d, %I:%M %p"), receipt_number(dt)
            except (KeyError, ValueError):
                when, number = e.get("ts", ""), ""
            job = e.get("files") or e.get("kind", "")
            if e.get("kind") and e["kind"] != "Print":
                job = f"{e['kind']}: {job}" if e.get("files") else e["kind"]
            for col, text in enumerate((when, number, job, f"{e.get('total', 0):,.2f}")):
                item = _cell(text)
                if col == 2:
                    item.setToolTip(job)
                self.recent_table.setItem(i, col, item)
        _fit_height(self.recent_table, 10)

    def _period_keys(self):
        today = date.today()
        this = today.strftime("%Y-%m")
        i = self.period.currentIndex()
        if i == 0:
            return [this]
        if i == 1:
            return [(today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")]
        if i == 2:
            y, m = today.year - 1, today.month + 1  # the last 12 months, this one included
            if m == 13:
                y, m = today.year, 1
            return [k for k in self._months if k >= f"{y:04d}-{m:02d}"]
        return list(self._months)

    def _fill_cards(self):
        t = _sum(self._months[k] for k in self._period_keys() if k in self._months)
        rev, jobs = t["total"], t["jobs"]

        def share(v):
            return v / rev if rev > 0 else 0.0

        markup = sum(t["cats"].values())
        other = rev - t["ink"] - t["paper"] - t["fee"] - markup

        _clear(self.cards)

        kpis = QHBoxLayout()
        kpis.setSpacing(10)
        kpis.addWidget(_stat("Revenue", f"{rev:,.2f}", f"{jobs} job(s)  ·  {t['pages']:,} page(s)", big=True), 2)
        kpis.addWidget(_stat("Average per job", f"{rev / jobs:,.2f}" if jobs else "0.00", "revenue ÷ jobs"), 1)
        kpis.addWidget(_stat("Discounts given", f"{t['discount']:,.2f}", "already taken off revenue"), 1)
        self.cards.addLayout(kpis)

        split = QHBoxLayout()
        split.setSpacing(10)

        src_card, sl = _card()
        h = QLabel("Where the money comes from")
        h.setObjectName("cardHeader")
        sl.addWidget(h)
        sub = QLabel("Share of revenue, after discounts")
        sub.setObjectName("muted")
        sl.addWidget(sub)
        sl.addSpacing(4)
        sources = [("Paper", t["paper"]), ("Ink", t["ink"]), ("All percentages", markup)]
        if t["fee"]:
            sources.append(("Poster / ID flat fees", t["fee"]))
        if abs(other) >= 0.5:
            sources.append(("Other / not itemized", other))
        for label, v in sources:
            sl.addWidget(_bar_row(label, v, share(v)))
        sl.addStretch()
        split.addWidget(src_card, 1)

        pct_card, pl = _card()
        head = QHBoxLayout()
        h2 = QLabel("Percentages")
        h2.setObjectName("cardHeader")
        head.addWidget(h2)
        head.addStretch()
        total_lbl = QLabel(f"{markup:,.2f}")
        total_lbl.setObjectName("total")
        head.addWidget(total_lbl)
        pl.addLayout(head)
        sub2 = QLabel("Total earned from the Settings percentages")
        sub2.setObjectName("muted")
        pl.addWidget(sub2)
        pl.addSpacing(4)
        shown = False
        for key, label in saleslog.CATEGORIES:
            v = t["cats"].get(key, 0.0)
            if abs(v) >= 0.005:
                pl.addWidget(_bar_row(label, v, share(v)))
                shown = True
        if not shown:
            empty = QLabel("Nothing yet")
            empty.setObjectName("muted")
            pl.addWidget(empty)
        pl.addStretch()
        split.addWidget(pct_card, 1)

        self.cards.addLayout(split)

    def _fill_banks(self):
        self._banks = saleslog.banks()
        earned = sum(b[2] for b in self._banks)
        spent = sum(b[3] for b in self._banks)
        balance = sum(b[4] for b in self._banks)
        self.bank_sub.setText("All time. Paper, ink and each percentage add to their bank with every sale; "
                              f"buying supplies takes money out.  Total in banks: {balance:,.2f}")
        rows = [(b[1], b[2], b[3], b[4], False) for b in self._banks] + [("Total", earned, spent, balance, True)]
        self.bank_table.setRowCount(len(rows))
        for i, (label, e, sp, bal, bold) in enumerate(rows):
            self.bank_table.setItem(i, 0, _cell(label, bold=bold))
            self.bank_table.setItem(i, 1, _cell(f"{e:,.2f}", bold=bold))
            self.bank_table.setItem(i, 2, _cell(f"{sp:,.2f}", bold=bold))
            self.bank_table.setItem(i, 3, _cell(f"{bal:,.2f}", NEGATIVE if bal < -0.004 else None, bold=True))
        _fit_height(self.bank_table, len(rows))

    def _fill_spends(self):
        self._spends = saleslog.spends()
        names = dict(saleslog.BANKS)
        self.spend_table.setRowCount(len(self._spends))
        for i, sp in enumerate(self._spends):
            try:
                when = datetime.fromisoformat(sp["ts"]).strftime("%b %d, %I:%M %p")
            except (KeyError, ValueError):
                when = sp.get("ts", "")
            note = sp.get("note", "")
            for col, text in enumerate((when, names.get(sp.get("bank"), sp.get("bank", "")), note,
                                        f"{sp.get('amount', 0):,.2f}")):
                item = _cell(text)
                if col == 2:
                    item.setToolTip(note)
                self.spend_table.setItem(i, col, item)
        _fit_height(self.spend_table, 8)

    def _spend(self):
        row = self.bank_table.currentRow()
        key = self._banks[row][0] if 0 <= row < len(self._banks) else None
        dlg = SpendDialog(self._banks, key, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        key, amount, note = dlg.values()
        label, balance = next((b[1], b[4]) for b in self._banks if b[0] == key)
        if amount > balance + 0.004:
            ask = QMessageBox.question(
                self, "Not enough in the bank",
                f"The {label} bank only has {balance:,.2f}. Spend {amount:,.2f} anyway?\n"
                "The bank will go below zero.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ask != QMessageBox.StandardButton.Yes:
                return
        saleslog.add_spend(key, amount, note)
        self.refresh()

    def _set_edit(self, table, remove_btn, on):
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection if on
                               else QTableWidget.SelectionMode.SingleSelection)
        remove_btn.setVisible(on)
        if not on:
            table.clearSelection()

    @staticmethod
    def _selected_rows(table):
        return sorted({i.row() for i in table.selectedIndexes()})

    def _remove_sales(self):
        ids = [self._recent[r]["id"] for r in self._selected_rows(self.recent_table) if 0 <= r < len(self._recent)]
        if not ids:
            return
        ask = QMessageBox.question(
            self, "Remove receipts",
            f"Remove {len(ids)} receipt(s) from the sales log?\n\n"
            "Monthly sales, revenue and the banks go down by their amounts. "
            "This doesn't cancel the receipt the customer already has.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ask == QMessageBox.StandardButton.Yes:
            saleslog.delete_sales(ids)
            self.refresh()

    def _remove_spends(self):
        ids = [self._spends[r]["id"] for r in self._selected_rows(self.spend_table) if 0 <= r < len(self._spends)]
        if not ids:
            return
        ask = QMessageBox.question(
            self, "Remove spending",
            f"Remove {len(ids)} spending record(s)? The money goes back into its bank.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ask == QMessageBox.StandardButton.Yes:
            saleslog.delete_spends(ids)
            self.refresh()

    def _open(self, row):
        if not 0 <= row < len(self._recent):
            return
        entry = self._recent[row]
        data = dict(entry["receipt"])
        try:  # same date and number as the receipt the customer got (older entries fall back to the log time)
            data["when"] = datetime.fromisoformat(data.get("when") or entry["ts"])
            ReceiptDialog(data, self).exec()
        except Exception:
            QMessageBox.warning(self, "Receipt", "This receipt couldn't be opened.")