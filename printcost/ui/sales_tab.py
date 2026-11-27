from datetime import date, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)

from printcost.core import saleslog
from printcost.ui.receipt import ReceiptDialog

MONTHS_SHOWN = 36


def _card():
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 14, 14, 14)
    lay.setSpacing(6)
    return frame, lay


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return t


def _cell(text):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


class SalesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._recent = []
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        month_card, ml = _card()
        header = QLabel("Monthly sales")
        header.setObjectName("cardHeader")
        ml.addWidget(header)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        ml.addWidget(self.summary)
        self.month_table = _table(["Month", "Jobs", "Pages", "Total"])
        ml.addWidget(self.month_table)
        root.addWidget(month_card, 1)

        recent_card, rl = _card()
        header2 = QLabel("Recent receipts")
        header2.setObjectName("cardHeader")
        rl.addWidget(header2)
        sub = QLabel(f"The last {saleslog.MAX_RECENT} printed jobs. Double-click one to open its receipt.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        rl.addWidget(sub)
        self.recent_table = _table(["When", "Job", "Total"])
        self.recent_table.cellDoubleClicked.connect(lambda row, _c: self._open(row))
        rl.addWidget(self.recent_table)
        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("View Receipt")
        open_btn.setObjectName("secondary")
        open_btn.clicked.connect(lambda: self._open(self.recent_table.currentRow()))
        row.addWidget(open_btn)
        rl.addLayout(row)
        root.addWidget(recent_card, 1)

        self.refresh()

    def refresh(self):
        months = saleslog.months()
        today = date.today()
        y, m = today.year - 1, today.month + 1  # the last 12 months, this one included
        if m == 13:
            y, m = today.year, 1
        start = f"{y:04d}-{m:02d}"
        this = dict(months).get(today.strftime("%Y-%m"), {"total": 0, "jobs": 0})
        year_total = sum(v["total"] for k, v in months if k >= start)
        year_jobs = sum(v["jobs"] for k, v in months if k >= start)
        self.summary.setText(
            f"This month: {this['total']:,.2f}  ·  {this['jobs']} job(s)        "
            f"Last 12 months: {year_total:,.2f}  ·  {year_jobs} job(s)"
            if months else "No sales yet. A job is added here when you press Print.")

        shown = months[:MONTHS_SHOWN]
        self.month_table.setRowCount(len(shown))
        for i, (key, v) in enumerate(shown):
            label = datetime.strptime(key, "%Y-%m").strftime("%B %Y")
            for col, text in enumerate((label, str(v["jobs"]), str(v["pages"]), f"{v['total']:,.2f}")):
                self.month_table.setItem(i, col, _cell(text))

        self._recent = saleslog.recent()
        self.recent_table.setRowCount(len(self._recent))
        for i, e in enumerate(self._recent):
            try:
                when = datetime.fromisoformat(e["ts"]).strftime("%b %d, %I:%M %p")
            except ValueError:
                when = e.get("ts", "")
            job = e.get("files") or e.get("kind", "")
            if e.get("kind") and e["kind"] != "Print":
                job = f"{e['kind']}: {job}" if e.get("files") else e["kind"]
            for col, text in enumerate((when, job, f"{e.get('total', 0):,.2f}")):
                item = _cell(text)
                if col == 1:
                    item.setToolTip(job)
                self.recent_table.setItem(i, col, item)

    def _open(self, row):
        if not 0 <= row < len(self._recent):
            return
        try:
            ReceiptDialog(self._recent[row]["receipt"], self).exec()
        except Exception:
            QMessageBox.warning(self, "Receipt", "This receipt couldn't be opened.")
