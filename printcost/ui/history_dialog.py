from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QCheckBox, QMessageBox,
)

from printcost.core import history, pricing
from printcost.ui.modal import ModalDialog
from printcost.ui.style import BORDER, SOFT, TEXT, YELLOW_SOFT


class HistoryDialog(ModalDialog):
    dismiss_outside = True

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.chosen = None
        self.setWindowTitle("Recent jobs")
        self.resize(460, 540)

        layout = QVBoxLayout(self)
        title = QLabel("Recent jobs")
        title.setObjectName("cardHeader")
        layout.addWidget(title)
        sub = QLabel(f"The last {history.MAX_ENTRIES} jobs are kept for {history.MAX_AGE_DAYS} days, "
                     "with a copy of their files. Double-click one to open it again.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self.list = QListWidget()
        self.list.setStyleSheet(
            f"QListWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; outline: 0; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-bottom: 1px solid #edf1f6; color: {TEXT}; }}"
            f"QListWidget::item:hover {{ background: {SOFT}; }}"
            f"QListWidget::item:selected {{ background: {YELLOW_SOFT}; color: {TEXT}; }}"
        )
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.itemDoubleClicked.connect(lambda _item: self._open())
        layout.addWidget(self.list, 1)

        self.keep_box = QCheckBox("Keep a history of my jobs")
        self.keep_box.setChecked(bool(settings.get("history_enabled", True)))
        self.keep_box.toggled.connect(self._toggle_keep)
        layout.addWidget(self.keep_box)

        row = QHBoxLayout()
        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("secondary")
        delete_btn.clicked.connect(self._delete)
        clear_btn = QPushButton("Clear all")
        clear_btn.setObjectName("secondary")
        clear_btn.clicked.connect(self._clear)
        open_btn = QPushButton("Open")
        open_btn.setObjectName("primary")
        open_btn.clicked.connect(self._open)
        row.addWidget(delete_btn)
        row.addWidget(clear_btn)
        row.addStretch()
        row.addWidget(open_btn)
        layout.addLayout(row)

        self._fill()

    def _fill(self):
        self.list.clear()
        entries = history.entries()
        if not entries:
            item = QListWidgetItem("No recent jobs yet")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return
        for e in entries:
            files = e.get("files", [])
            name = files[0]["name"] if len(files) == 1 else f"{len(files)} files"
            o = e.get("options", {})
            when = datetime.fromtimestamp(e.get("ts", 0)).strftime("%b %d, %I:%M %p")
            printed = e.get("printed")
            stats = [when, f"{printed} page(s)" if printed else "", f"was {e.get('total', 0):,.2f}"]
            setup = [o.get("paper"), o.get("mode")]
            lines = [name, "  ·  ".join(p for p in stats if p), "  ·  ".join(p for p in setup if p)]
            item = QListWidgetItem("\n".join(line for line in lines if line))
            item.setData(Qt.ItemDataRole.UserRole, e)
            self.list.addItem(item)
        self.list.setCurrentRow(0)

    def _current(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _open(self):
        entry = self._current()
        if entry:
            self.chosen = entry
            self.accept()

    def _delete(self):
        entry = self._current()
        if entry:
            history.delete(entry["id"])
            self._fill()

    def _clear(self):
        ask = QMessageBox.question(self, "Clear history", "Delete all saved jobs and their file copies?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ask == QMessageBox.StandardButton.Yes:
            history.clear()
            self._fill()

    def _toggle_keep(self, on):
        self.settings["history_enabled"] = on
        try:
            disk = pricing.load_settings()
            disk["history_enabled"] = on
            pricing.save_settings(disk)
        except Exception:
            pass