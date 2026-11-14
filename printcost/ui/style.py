from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

BG = "#f3f6fa"
CARD_BG = "#ffffff"
PRIMARY = "#0891b2"        # cyan
PRIMARY_DARK = "#0e7490"
SOFT = "#e6f6fa"           # light cyan tint (hover / selection)
TEXT = "#0f1b2d"           # ink navy
MUTED = "#64748b"
BORDER = "#dde3ec"
MAGENTA = "#e91e8c"        # magenta: guides, outlines, live things
ACCENT_LINE = MAGENTA      # old name, same color
PREVIEW_BG = "#e8edf4"
YELLOW = "#f5c400"         # yellow accent (hover underline on tabs)
MAGENTA_DARK = "#c4137a"   # magenta for the price total and final buttons (white text passes on it)
MAGENTA_DEEP = "#a30f66"   # magenta hover
YELLOW_SOFT = "#fff3b8"    # soft yellow: warning boxes, selected rows
YELLOW_DARK = "#c9a000"    # yellow outline / dot ring
NAVY = TEXT                  # all text
DISABLED_TEXT = "#7a8799"  # disabled text (darker than before, so it stays readable)
CMYK_COLORS = ["#00aeef", "#ec008c", "#fff200", "#231f20"]


class CmykBar(QWidget):
    """The four-color strip. Full width under the window top; short (`width`) under section titles."""

    def __init__(self, height=4, width=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        if width:
            self.setFixedWidth(width)

    def paintEvent(self, event):
        painter = QPainter(self)
        seg = self.width() / len(CMYK_COLORS)
        for i, color in enumerate(CMYK_COLORS):
            painter.fillRect(int(i * seg), 0, int(seg) + 1, self.height(), QColor(color))

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI";
    font-size: 13px;
}}
QLabel, QCheckBox {{ background: transparent; }}
QLabel:disabled, QCheckBox:disabled {{ color: {DISABLED_TEXT}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid #b8c4d3; border-radius: 4px; background: white;
}}
QCheckBox::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}
QCheckBox::indicator:disabled {{ background: {BG}; border-color: {BORDER}; }}
/* options that cost extra (rush fee, double-sided...): setProperty("extra", True) */
QCheckBox[extra="true"]::indicator:checked {{ background: {YELLOW}; border-color: {YELLOW_DARK}; }}
QCheckBox[extra="true"]::indicator:hover {{ border-color: {YELLOW_DARK}; }}
QToolTip {{
    background: {TEXT}; color: white; border: none; padding: 4px 8px;
}}

/* ---------- cards & text ---------- */
QFrame#card {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame[frameShape="4"] {{
    background: {BORDER};
    border: none;
    max-height: 1px;
}}
QLabel#cardHeader {{
    font-size: 15px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#muted {{
    color: {MUTED};
    font-size: 11px;
}}
QLabel#total {{
    font-size: 20px;
    font-weight: 800;
    color: {MAGENTA_DARK};
}}
QLabel#badge {{
    background: {MAGENTA_DARK};
    color: white;
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 10px;
    font-weight: 700;
}}

/* ---------- tabs ---------- */
QTabWidget::pane {{
    border: none;
    background: {BG};
}}
QTabBar {{ background: {CARD_BG}; }}
QTabBar::tab {{
    background: transparent;
    padding: 11px 24px;
    font-weight: 600;
    color: {MUTED};
    border-bottom: 3px solid transparent;
}}
QTabBar::tab:hover {{ color: {TEXT}; border-bottom: 3px solid {YELLOW}; }}
QTabBar::tab:selected {{
    color: {PRIMARY_DARK};
    border-bottom: 3px solid {PRIMARY};
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: {CARD_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: {SOFT}; border-color: {PRIMARY}; }}

QPushButton#primary {{
    background: {PRIMARY};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 11px 24px;
    font-weight: 700;
}}
QPushButton#primary:hover {{ background: {PRIMARY_DARK}; }}
QPushButton#primary:pressed {{ background: #155e75; }}
QPushButton:disabled {{ background: #eef1f5; color: {DISABLED_TEXT}; border: 1px solid {BORDER}; }}
QPushButton#primary:disabled {{ background: #dbe2ea; color: #5f6b7d; border: none; }}

/* the final "do it" buttons (Print, Create poster) */
QPushButton#cta {{
    background: {MAGENTA_DARK};
    color: white;
    border: 1px solid {MAGENTA_DARK};
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 700;
}}
QPushButton#cta:hover {{ background: {MAGENTA_DEEP}; border-color: {MAGENTA_DEEP}; }}
QPushButton#cta:pressed {{ background: #8a0d56; }}
QPushButton#cta:disabled {{ background: #dbe2ea; color: #5f6b7d; border: 1px solid #dbe2ea; }}

QPushButton#secondary {{
    background: {CARD_BG};
    color: {PRIMARY_DARK};
    border: 1px solid {PRIMARY};
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton#secondary:hover {{ background: {SOFT}; }}
QPushButton#secondary:pressed {{ background: #cdeef5; }}
QPushButton#secondary:checked {{ background: {PRIMARY}; color: white; }}
QPushButton#secondary:disabled {{ color: {DISABLED_TEXT}; border: 1px solid {BORDER}; background: #eef1f5; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: {PRIMARY};
    selection-color: white;
}}
QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover {{ border-color: #b8c4d3; }}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border: 1px solid {PRIMARY}; }}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {{
    background: #eef1f5;
    color: {DISABLED_TEXT};
    border: 1px solid {BORDER};
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button, QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: white;
    border: 1px solid {BORDER};
    selection-background-color: {SOFT};
    selection-color: {TEXT};
    outline: 0;
    padding: 2px;
}}

/* ---------- tables ---------- */
QTableWidget {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: #edf1f6;
    selection-background-color: {YELLOW_SOFT};
    selection-color: {TEXT};
}}
QTableWidget::item:hover {{ background: {SOFT}; }}
QTableWidget::item:selected, QTableWidget::item:selected:hover {{ background: {YELLOW_SOFT}; color: {TEXT}; }}
QHeaderView::section {{
    background: #f1f5f9;
    color: {MUTED};
    padding: 8px 6px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 700;
    font-size: 12px;
}}

/* ---------- progress ---------- */
QProgressBar {{ background: {BORDER}; border: none; border-radius: 4px; max-height: 8px; }}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00aeef, stop:0.55 #ec008c, stop:1 #f5c400);
    border-radius: 4px;
}}

/* ---------- scrolling ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #94a3b8; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #cbd5e1; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #94a3b8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""