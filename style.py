BG = "#f3f6fa"
CARD_BG = "#ffffff"
PRIMARY = "#0891b2"        # cyan
PRIMARY_DARK = "#0e7490"
SOFT = "#e6f6fa"           # light cyan tint (hover / selection)
TEXT = "#0f1b2d"           # ink navy
MUTED = "#64748b"
BORDER = "#dde3ec"
ACCENT_LINE = "#e91e8c"    # magenta scanning bar
PREVIEW_BG = "#e8edf4"

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI";
    font-size: 13px;
}}
QLabel, QCheckBox {{ background: transparent; }}
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
    color: {PRIMARY_DARK};
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
QTabBar::tab:hover {{ color: {TEXT}; }}
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
QPushButton#primary:disabled {{ background: #a5d3df; }}

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
QPushButton#secondary:disabled {{ color: #9fb0bf; border-color: {BORDER}; background: {CARD_BG}; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: {PRIMARY};
    selection-color: white;
}}
QLineEdit:hover, QComboBox:hover {{ border-color: #b8c4d3; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {PRIMARY}; }}
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
    selection-background-color: {SOFT};
    selection-color: {TEXT};
}}
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
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 4px; }}

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