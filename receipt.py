import datetime
import os

import fitz
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFileDialog, QMessageBox, QLineEdit,
)

from analyzer import format_page_ranges

W, M = 288, 20  # page width (4 in) and side margin, in points
INK = (0.06, 0.11, 0.18)
MUTED = (0.39, 0.45, 0.55)
LOGO = [(0.0, 0.68, 0.94), (0.93, 0.0, 0.55), (1.0, 0.95, 0.0), (0.14, 0.12, 0.13)]
MODES = {"color": "Color", "grayscale": "Grayscale", "bw": "Black & White"}
DETAIL_MAX = 50


def receipt_number(when):
    return "PCC-" + when.strftime("%Y%m%d-%H%M%S")


def _fit(text, size, width, font="helv"):
    if fitz.get_text_length(text, fontname=font, fontsize=size) <= width:
        return text
    while text and fitz.get_text_length(text + "...", fontname=font, fontsize=size) > width:
        text = text[:-1]
    return text + "..."


def _draw(page, d):
    """Draws the receipt top to bottom and returns the height it needs."""
    r = d["result"]
    y = M

    def center(text, size, font="helv", color=INK, gap=1.5):
        nonlocal y
        w = fitz.get_text_length(text, fontname=font, fontsize=size)
        page.insert_text(((W - w) / 2, y), text, fontname=font, fontsize=size, color=color)
        y += size * gap

    def row(label, value, size=9, font="helv", color=INK, label_color=MUTED):
        nonlocal y
        value = _fit(value, size, W - 2 * M - 60, font)
        vw = fitz.get_text_length(value, fontname=font, fontsize=size)
        page.insert_text((M, y), _fit(label, size, W - 2 * M - vw - 8, font),
                         fontname=font, fontsize=size, color=label_color)
        page.insert_text((W - M - vw, y), value, fontname=font, fontsize=size, color=color)
        y += size * 1.7

    def rule(dashed=True):
        nonlocal y
        y += 2
        page.draw_line((M, y), (W - M, y), color=MUTED, width=0.6, dashes="[2 2] 0" if dashed else None)
        y += 13

    # logo: the four ink dots
    s, gap = 14, 3
    x0 = (W - (2 * s + gap)) / 2
    for i, col in enumerate(LOGO):
        c = (x0 + (i % 2) * (s + gap) + s / 2, y + (i // 2) * (s + gap) + s / 2)
        page.draw_circle(c, s / 2, color=None, fill=col)
    y += 2 * s + gap + 16

    center(d["shop"], 13, "hebo", gap=1.4)
    tin = d.get("tin")
    center("OFFICIAL RECEIPT" if tin else "PRINT RECEIPT", 8, color=MUTED, gap=1.2)
    if tin:
        center(f"TIN: {tin}", 8, color=MUTED, gap=1.2)
    y += 6
    rule()

    when = d["when"]
    names = d["files"]
    pages = d.get("pages")
    row("OR no." if tin else "Receipt no.", receipt_number(when))
    row("Date", when.strftime("%b %d, %Y  %I:%M %p"))
    if d.get("customer_tin"):
        row("Customer TIN", d["customer_tin"])
    row("File", names[0] if len(names) == 1 else f"{len(names)} files")
    if d.get("poster"):
        row("Poster", f"{d['poster']['grid']} = {d['poster']['sheets']} sheets")
        row("Poster size", d["poster"]["size"])
    row("Paper", d["paper"])
    row("Paper type", d.get("paper_type", "Plain paper"))
    row("Mode / Quality", f"{MODES.get(d['mode'], 'Color')} / {d['quality'].title()}")
    row("Pages", f"{format_page_ranges(pages)} ({len(pages)} of {d['page_count']})" if pages
        else f"All ({r['num_pages']})")
    row("Color / B&W", f"{r['color_pages']} / {r['num_pages'] - r['color_pages']}")
    rule()

    # per-page color use (% of each ink) and price (ink + paper)
    edges = [M + 78, M + 116, M + 154, M + 192, W - M]

    def cells(vals, font="helv", color=INK, size=8):
        nonlocal y
        page.insert_text((M, y), vals[0], fontname=font, fontsize=size, color=color)
        for x, v in zip(edges, vals[1:]):
            w = fitz.get_text_length(v, fontname=font, fontsize=size)
            page.insert_text((x - w, y), v, fontname=font, fontsize=size, color=color)
        y += size * 1.65

    per = r["per_page"]
    if len(per) <= DETAIL_MAX:
        center("Color use per page (%)", 9, "hebo", gap=1.7)
        cells(["Page", "C", "M", "Y", "K", "Price"], "hebo", MUTED)
        for p in per:
            c = p["coverage"]
            cells([str(p["page"])] + [f"{c[k] * 100:.1f}" for k in "CMYK"] + [f"{p['page_cost']:.2f}"])
    else:
        center("Color use summary (average %)", 9, "hebo", gap=1.7)
        cells(["Type", "C", "M", "Y", "K", "Price"], "hebo", MUTED)
        for label, group in (("Color", [p for p in per if p["is_color"]]),
                             ("B&W", [p for p in per if not p["is_color"]])):
            if not group:
                continue
            avg = [sum(p["coverage"][k] for p in group) / len(group) * 100 for k in "CMYK"]
            cells([f"{label} x{len(group)}"] + [f"{a:.1f}" for a in avg]
                  + [f"{sum(p['page_cost'] for p in group):.2f}"])
    rule(dashed=False)
    if r["min_topup"] > 0:
        cells(["Min. mark-up", "", "", "", "", f"{r['min_topup']:.2f}"])
    max_cut = r.get("max_cut", 0)
    if max_cut > 0:
        cells(["Max price cap", "", "", "", "", f"-{max_cut:.2f}"])
    page_total = sum(p["page_cost"] for p in r["per_page"]) + r["min_topup"] - max_cut
    cells(["Total", "", "", "", "", f"{page_total:.2f}"], "hebo")
    y -= 4
    rule()

    if r.get("poster_fee") is not None:
        row(r["poster_fee_label"], f"{r['poster_fee']:.2f}", font="hebo", label_color=INK)
        y += 3
    else:
        row("Ink", f"{r['ink_total']:.2f}", label_color=INK)
        row("Paper", f"{r['paper_total']:.2f}", label_color=INK)
        row("Subtotal", f"{r['subtotal']:.2f}", font="hebo", label_color=INK)
        y += 3
        handling = sum(r["category_amounts"].values())
        if handling > 0:
            row("Service & production handling", f"{handling:.2f}")
        if r["min_topup"] > 0:
            row("Minimum price mark-up", f"{r['min_topup']:.2f}")
        if max_cut > 0:
            row("Max price cap", f"-{max_cut:.2f}")
    for label, amount in r.get("discount_lines", []):
        row(label, f"-{amount:.2f}")
    rule(dashed=False)

    row("TOTAL", f"{r['total']:.2f}", size=13, font="hebo", label_color=INK)
    y += 4
    rule()
    center("Thank you!", 10, "hebo", gap=1.6)
    if tin:
        center("This serves as an Official Receipt.", 7, color=MUTED)
        center("Issued by a registered business.", 7, color=MUTED)
    else:
        center("THIS IS NOT AN OFFICIAL RECEIPT.", 7, "hebo", color=MUTED)
        center("It is only a breakdown of the price.", 7, color=MUTED)
    center("Keep this receipt for your records.", 7, color=MUTED)
    return y + M


def build_receipt_pdf(d):
    probe = fitz.open()
    height = _draw(probe.new_page(width=W, height=14400), d)  # first pass: measure
    probe.close()
    doc = fitz.open()
    _draw(doc.new_page(width=W, height=height), d)  # second pass: exact height
    data = doc.tobytes()
    doc.close()
    return data


class ReceiptDialog(QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        data.setdefault("when", datetime.datetime.now())
        self.number = receipt_number(data["when"])
        self.data = data
        self.pdf = build_receipt_pdf(data)
        self.setWindowTitle(f"Receipt {self.number}")
        self.resize(400, 680)

        layout = QVBoxLayout(self)
        if data.get("tin"):
            tin_row = QHBoxLayout()
            tin_row.addWidget(QLabel("Customer TIN"))
            tin_input = QLineEdit()
            tin_input.setPlaceholderText("optional, 000-000-000-00000")
            tin_input.setMaxLength(20)
            tin_input.textChanged.connect(self._customer_tin_changed)
            tin_row.addWidget(tin_input, 1)
            layout.addLayout(tin_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.label.setPixmap(self._pixmap(2.0))
        scroll.setWidget(self.label)
        layout.addWidget(scroll, 1)

        row = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        save_btn = QPushButton("Download receipt")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._download)
        row.addWidget(close_btn)
        row.addStretch()
        row.addWidget(save_btn)
        layout.addLayout(row)

    def _customer_tin_changed(self, text):
        self.data["customer_tin"] = text.strip()
        self.pdf = build_receipt_pdf(self.data)
        self.label.setPixmap(self._pixmap(2.0))

    def _pixmap(self, scale):
        with fitz.open("pdf", self.pdf) as doc:
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(scale)
        return pm

    def _download(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Download receipt", f"{self.number}.png", "PNG image (*.png);;PDF (*.pdf)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".png"
        try:
            if path.lower().endswith(".png"):
                self._pixmap(4.0).save(path, "PNG")
            else:
                with open(path, "wb") as f:
                    f.write(self.pdf)
        except OSError as e:
            QMessageBox.warning(self, "Couldn't save", str(e))
            return
        QMessageBox.information(self, "Saved", f"Receipt saved to:\n{path}")