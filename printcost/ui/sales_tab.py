from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QComboBox, QProgressBar,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)

from printcost.core import saleslog
from printcost.ui.receipt import ReceiptDialog, receipt_number

MONTHS_SHOWN = 36
PERIODS = ["This month", "Last month", "Last 12 months", "All time"]


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


def _cell(text):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def _sum(rows):
    t = {"total": 0.0, "jobs": 0, "pages": 0, "ink": 0.0, "paper": 0.0, "fee": 0.0, "discount": 0.0, "cats": {}}
    for v in rows:
        for k in ("total", "jobs", "pages", "ink", "paper", "fee", "discount"):
            t[k] += v.get(k, 0)
        for c, a in (v.get("cats") or {}).items():
            t["cats"][c] = t["cats"].get(c, 0.0) + a
    return t


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
        sub = QLabel(f"The last {saleslog.MAX_RECENT} printed jobs. Double-click one to open its receipt.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        rl.addWidget(sub)
        self.recent_table = _table(["When", "Receipt no.", "Job", "Total"])
        self.recent_table.cellDoubleClicked.connect(lambda row, _c: self._open(row))
        rl.addWidget(self.recent_table)
        row = QHBoxLayout()
        row.addStretch()
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