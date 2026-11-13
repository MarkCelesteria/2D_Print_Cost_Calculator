from PyQt6.QtWidgets import QDialog, QFormLayout, QComboBox, QLabel, QDialogButtonBox


class PageSetupDialog(QDialog):
    """Paper type and orientation for one page or the whole document."""

    def __init__(self, parent, page_number, size_pts, paper_sizes, page_count):
        super().__init__(parent)
        self.setWindowTitle("Page setup")
        self.result_data = None
        w_in, h_in = size_pts[0] / 72, size_pts[1] / 72
        form = QFormLayout(self)

        now = QLabel(f"Page {page_number}: {w_in:.2f} × {h_in:.2f} in, {'landscape' if w_in > h_in else 'portrait'}")
        now.setObjectName("muted")
        form.addRow(now)

        self.paper = QComboBox()
        self.paper.addItem("Keep current size", None)
        for p in paper_sizes:
            self.paper.addItem(f"{p['name']}", (p["width_in"] * 72, p["height_in"] * 72))
        form.addRow("Paper:", self.paper)

        self.orientation = QComboBox()
        self.orientation.addItem("Keep as is", None)
        self.orientation.addItem("Portrait", "portrait")
        self.orientation.addItem("Landscape", "landscape")
        form.addRow("Orientation:", self.orientation)

        self.content = QComboBox()
        self.content.addItem("Scale to fit the new page", True)
        self.content.addItem("Keep size and position (may be cut off)", False)
        form.addRow("Content:", self.content)

        self.scope = QComboBox()
        self.scope.addItem(f"This page ({page_number})", False)
        if page_count > 1:
            self.scope.addItem(f"All pages ({page_count})", True)
        form.addRow("Apply to:", self.scope)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        for box in (self.paper, self.orientation):
            box.currentIndexChanged.connect(self._update_ok)
        self._update_ok()

    def _update_ok(self):
        changed = self.paper.currentData() is not None or self.orientation.currentData() is not None
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(changed)

    def _accept(self):
        self.result_data = {
            "paper": self.paper.currentData(),
            "orientation": self.orientation.currentData(),
            "scale": self.content.currentData(),
            "all": self.scope.currentData(),
        }
        self.accept()