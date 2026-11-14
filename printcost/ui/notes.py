import html

from PyQt6.QtWidgets import QMessageBox

# level: (text color, background, icon)
LEVELS = {
    "info": ("#0369a1", "#e0f2fe", QMessageBox.Icon.Information),
    "warn": ("#b45309", "#fef3c7", QMessageBox.Icon.Warning),
    "danger": ("#b91c1c", "#fee2e2", QMessageBox.Icon.Critical),
}
_RANK = {"info": 0, "warn": 1, "danger": 2}


def label_style(level, size=11):
    fg, bg, _ = LEVELS[level]
    return f"color: {fg}; background: {bg}; border-radius: 6px; padding: 4px 8px; font-size: {size}px;"


def notice(parent, level, title, lines, buttons=QMessageBox.StandardButton.Ok):
    """lines: one string, or a list of (level, text). The box takes the highest level. Returns the clicked button."""
    if isinstance(lines, str):
        lines = [(level, lines)]
    top = max((l for l, _ in lines), key=_RANK.get)
    body = "".join(
        f'<p style="margin:3px 0; color:{LEVELS[l][0]};"><b>●</b> {html.escape(t)}</p>' for l, t in lines)
    box = QMessageBox(parent)
    box.setIcon(LEVELS[top][2])
    box.setWindowTitle(title)
    box.setTextFormat(box.textFormat().RichText)
    box.setText(body)
    box.setStandardButtons(buttons)
    return box.exec()