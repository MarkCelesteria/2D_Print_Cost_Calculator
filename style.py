BG = "#f4f6fb"
CARD_BG = "#ffffff"
PRIMARY = "#2f6fed"
PRIMARY_DARK = "#2555c7"
TEXT = "#1f2430"
MUTED = "#707a8c"
BORDER = "#e1e5ee"
ACCENT_LINE = "#ff6b57"  # scanning bar color

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI";
    font-size: 13px;
}}

QFrame#card {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QLabel#cardHeader {{
    font-size: 14px;
    font-weight: 600;
    color: {TEXT};
}}

QLabel#muted {{
    color: {MUTED};
    font-size: 11px;
}}

QLabel#total {{
    font-size: 18px;
    font-weight: 700;
    color: {PRIMARY_DARK};
}}

QTabWidget::pane {{
    border: none;
    background: {BG};
}}
QTabBar::tab {{
    background: transparent;
    padding: 10px 18px;
    font-weight: 600;
    color: {MUTED};
}}
QTabBar::tab:selected {{
    color: {PRIMARY};
    border-bottom: 3px solid {PRIMARY};
}}

QPushButton#primary {{
    background: {PRIMARY};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 18px;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {PRIMARY_DARK}; }}
QPushButton#primary:disabled {{ background: #a9b7d8; }}

QPushButton#secondary {{
    background: {CARD_BG};
    color: {PRIMARY};
    border: 1px solid {PRIMARY};
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
}}
QPushButton#secondary:hover {{ background: #eef3ff; }}

QLineEdit, QComboBox {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 8px;
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {PRIMARY}; }}

QTableWidget {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
}}
QHeaderView::section {{
    background: #f0f3fa;
    padding: 6px;
    border: none;
    font-weight: 600;
    color: {TEXT};
}}
"""
