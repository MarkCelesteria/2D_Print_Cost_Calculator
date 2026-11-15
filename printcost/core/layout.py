import os

try:
    import fitz
except ImportError:
    fitz = None

from printcost.core.analyzer import AnalysisError, make_temp_dir

MODES = ["normal", "duplex", "booklet"]
MODE_NAMES = {"normal": "Normal", "duplex": "Back-to-back", "booklet": "Booklet"}
BADGES = {"duplex": "Double-sided", "booklet": "Booklet"}
FLIP_DEFAULTS = {"duplex": ("long", False), "booklet": ("short", False)}  # (flip edge, reverse back pass)


def pad(pages, multiple):
    return pages + [None] * (-len(pages) % multiple)


def arrange(n, multiple, blank_covers=False):
    """Page indexes in order; None is a blank page. With blank_covers the back of the front cover and the
    inside of the back cover stay empty, and the padding goes before the back cover."""
    if not blank_covers or n < 2:
        return pad(list(range(n)), multiple)
    head, tail = [0, None], [None, n - 1]
    middle = list(range(1, n - 1))
    middle += [None] * (-(len(head) + len(middle) + len(tail)) % multiple)
    return head + middle + tail


def duplex_order(n, blank_covers=False):
    return arrange(n, 2, blank_covers)


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


def needs_rotation(flip, landscape):
    """The back pass is turned 180 degrees when the stack is flipped over the page's top edge."""
    return (flip == "long") == landscape


def printer_prefs(settings, printer, mode, saved_only=False):
    saved = settings.get("duplex", {}).get("printers", {}).get(printer, {}).get(mode)
    if saved:
        return saved["flip"], saved["reverse"]
    return None if saved_only else FLIP_DEFAULTS[mode]


def save_printer_prefs(settings, printer, mode, flip, reverse):
    entry = settings["duplex"].setdefault("printers", {}).setdefault(printer, {})
    entry[mode] = {"flip": flip, "reverse": reverse}


def _show(page, rect, src, index):
    if src[index].rotation:  # vector placement can't handle /Rotate safely
        page.insert_image(rect, pixmap=src[index].get_pixmap(dpi=200, alpha=False))
    else:
        page.show_pdf_page(rect, src, index)


def build_layout_pdf(source_pdf, mode, paper, blank_covers=False, margin_in=0.25):
    """Returns (pdf_path, number_of_sides). Sides are printed front, back, front, back..."""
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
            for left, right in booklet_sides(n, blank_covers):
                page = out.new_page(width=w, height=h)
                for index, x in ((left, 0), (right, w / 2)):
                    if index is not None:
                        _show(page, fitz.Rect(x + m, m, x + w / 2 - m, h - m), src, index)
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