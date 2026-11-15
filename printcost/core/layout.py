import os

try:
    import fitz
except ImportError:
    fitz = None

from printcost.core.analyzer import AnalysisError, make_temp_dir

MODES = ["normal", "duplex", "booklet"]
MODE_NAMES = {"normal": "Normal", "duplex": "Back-to-back", "booklet": "Booklet"}
BADGES = {"duplex": "Double-sided", "booklet": "Booklet"}
FLIP_DEFAULTS = {
    "duplex": {"flip": "long", "output": "up", "reverse": False},
    "booklet": {"flip": "long", "output": "up", "reverse": False},
}


def pad(pages, multiple):
    return pages + [None] * (-len(pages) % multiple)


def arrange(n, multiple, blank_covers=False, back_cover=True):
    """Page indexes in order; None is a blank page. With blank_covers the back of the front cover stays empty.
    With back_cover too, the inside of the back cover stays empty and the padding goes before it."""
    if not blank_covers or n < 2:
        return pad(list(range(n)), multiple)
    if not back_cover:
        return pad([0, None] + list(range(1, n)), multiple)
    head, tail = [0, None], [None, n - 1]
    middle = list(range(1, n - 1))
    middle += [None] * (-(len(head) + len(middle) + len(tail)) % multiple)
    return head + middle + tail


def duplex_order(n, blank_covers=False):
    return arrange(n, 2, blank_covers, back_cover=False)


def booklet_sides(n, blank_covers=False):
    """(left, right) page for every side, in print order: front, back, front, back..."""
    p = arrange(n, 4, blank_covers)
    total = len(p)
    sides = []
    for k in range(total // 4):
        sides.append((p[total - 1 - 2 * k], p[2 * k]))
        sides.append((p[2 * k + 1], p[total - 2 - 2 * k]))
    return sides


def sheet_count(sides):
    return (sides + 1) // 2


def sheet_pages(n, mode, blank_covers=False):
    """For every sheet, the original page numbers (1-based) printed on it, blanks left out."""
    if mode == "duplex":
        sides = [[p] for p in duplex_order(n, blank_covers)]
    else:
        sides = [list(s) for s in booklet_sides(n, blank_covers)]
    return [sorted(p + 1 for side in sides[i:i + 2] for p in side if p is not None)
            for i in range(0, len(sides), 2)]


def needs_rotation(flip, landscape):
    """The back pass is turned 180 degrees when the stack is flipped over the page's top edge."""
    return (flip == "long") == landscape


def printer_prefs(settings, printer, mode, saved_only=False):
    saved = settings.get("duplex", {}).get("printers", {}).get(printer, {}).get(mode)
    if saved:
        return {**FLIP_DEFAULTS[mode], **saved}
    return None if saved_only else dict(FLIP_DEFAULTS[mode])


def save_printer_prefs(settings, printer, mode, prefs):
    settings["duplex"].setdefault("printers", {}).setdefault(printer, {})[mode] = dict(prefs)


def pass_orders(total, output="up", opposite_back=False):
    """1-based sides for the front pass and the back pass.
    output "up": the printer drops sheets printed side up, so you flip the whole stack to reload it.
    output "down": sheets come out printed side down, so the stack goes back as it is.
    Either way the finished stack ends up front side up with page 1 on top."""
    sheets = list(range(1, total + 1, 2))
    back = sheets if output == "up" else sheets[::-1]
    front = back if output == "up" else back[::-1]
    if opposite_back:
        back = back[::-1]
    return [n for n in front], [n + 1 for n in back if n + 1 <= total]


def _show(page, rect, src, index, align="center"):
    sp = src[index]
    scale = min(rect.width / sp.rect.width, rect.height / sp.rect.height)
    w, h = sp.rect.width * scale, sp.rect.height * scale
    x = rect.x0 + {"left": 0, "right": rect.width - w}.get(align, (rect.width - w) / 2)
    y = rect.y0 + (rect.height - h) / 2
    target = fitz.Rect(x, y, x + w, y + h)
    if sp.rotation:  # vector placement can't handle /Rotate safely
        page.insert_image(target, pixmap=sp.get_pixmap(dpi=200, alpha=False))
    else:
        page.show_pdf_page(target, src, index)


def build_layout_pdf(source_pdf, mode, paper, blank_covers=False, margin_in=0.25, connect=False):
    """Returns (pdf_path, number_of_sides). Sides are printed front, back, front, back...
    connect=True removes the middle margin of a booklet so the two pages touch at the fold."""
    if fitz is None:
        raise AnalysisError("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")
    src = fitz.open(source_pdf)
    out = fitz.open()
    try:
        n = len(src)
        if n == 0:
            raise AnalysisError("The document has no pages.")
        if mode == "duplex":
            for index in duplex_order(n, blank_covers):
                if index is None:
                    size = out[-1].rect if len(out) else src[0].rect
                    out.new_page(width=size.width, height=size.height)
                else:
                    out.insert_pdf(src, from_page=index, to_page=index)
        else:
            short, long_ = sorted((paper["width_in"] * 72, paper["height_in"] * 72))
            w, h, m = long_, short, margin_in * 72
            fold = 0 if connect else m
            for left, right in booklet_sides(n, blank_covers):
                page = out.new_page(width=w, height=h)
                cells = ((left, fitz.Rect(m, m, w / 2 - fold, h - m), "right" if connect else "center"),
                         (right, fitz.Rect(w / 2 + fold, m, w - m, h - m), "left" if connect else "center"))
                for index, rect, align in cells:
                    if index is not None:
                        _show(page, rect, src, index, align)
        path = os.path.join(make_temp_dir(), f"{mode}.pdf")
        out.save(path)
        return path, len(out)
    finally:
        src.close()
        out.close()


def build_test_pdf(paper, landscape):
    """Two sheets, four sides, so both the flip direction and the back-pass order show up."""
    if fitz is None:
        raise AnalysisError("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")
    short, long_ = sorted((paper["width_in"] * 72, paper["height_in"] * 72))
    w, h = (long_, short) if landscape else (short, long_)
    out = fitz.open()
    for sheet in (1, 2):
        for side in ("FRONT", "BACK"):
            page = out.new_page(width=w, height=h)
            page.draw_rect(fitz.Rect(18, 18, w - 18, h - 18), color=(0, 0, 0), width=1)
            page.draw_polyline([(w / 2, 28), (w / 2 - 14, 54), (w / 2 + 14, 54)], color=(0, 0, 0), fill=(0, 0, 0), closePath=True)
            page.insert_textbox(fitz.Rect(0, 60, w, 90), "TOP", fontsize=16, fontname="hebo", align=1)
            page.insert_textbox(fitz.Rect(0, h / 2 - 80, w, h / 2 + 80), f"SHEET {sheet}\n{side}",
                                fontsize=40, fontname="hebo", align=1)
            page.insert_textbox(fitz.Rect(0, h - 60, w, h - 30), "Alignment test", fontsize=11, align=1)
    path = os.path.join(make_temp_dir(), "alignment_test.pdf")
    out.save(path)
    out.close()
    return path