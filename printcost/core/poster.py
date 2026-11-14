import io
import math
import os

from PIL import Image

try:
    import fitz
except ImportError:
    fitz = None

from printcost.core.analyzer import AnalysisError, IMAGE_DPI, is_image, load_image_rgb, make_temp_dir

MAX_GRID = 50
MARK_LEN = 14.4          # 0.2 in, length of crop / alignment marks
PDF_RASTER_MAX_PX = 5000  # only used for rotated PDF pages (vector placement can't handle /Rotate safely)

DEFAULTS = {
    "cols": 3, "rows": 2, "landscape": False, "fill": False, "target": None,
    "crop": (0.0, 0.0, 0.0, 0.0),
    "overlap_in": 0.0, "margin_in": 0.25,
    "marks": False, "labels": False,
    "pricing_mode": "calculated", "fee": 0.0,
}


# ---------------- small helpers ----------------

def fmt_in(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def row_letters(r):
    """0 -> A, 25 -> Z, 26 -> AA."""
    s, r = "", r + 1
    while r:
        r, rem = divmod(r - 1, 26)
        s = chr(65 + rem) + s
    return s


def tile_label(index, cols):
    """0-based print-order index -> 'A1'. Rows are letters (top = A), columns are numbers."""
    r, c = divmod(index, cols)
    return f"{row_letters(r)}{c + 1}"


def short_paper_name(paper):
    return paper["name"].split(" (")[0]


def sheet_size(paper, landscape):
    short, long_ = sorted((paper["width_in"], paper["height_in"]))
    return (long_, short) if landscape else (short, long_)


# ---------------- geometry ----------------

def layout(spec):
    sw, sh = sheet_size(spec["paper"], spec["landscape"])
    m, o = spec["margin_in"], spec["overlap_in"]
    pw, ph = sw - 2 * m, sh - 2 * m
    cols, rows = spec["cols"], spec["rows"]
    return {
        "sw": sw, "sh": sh, "m": m, "o": o, "pw": pw, "ph": ph,
        "sx": pw - o, "sy": ph - o, "cols": cols, "rows": rows,
        "W": cols * pw - (cols - 1) * o, "H": rows * ph - (rows - 1) * o,
    }


def validate(spec):
    """None when the settings can be built, else a short message for the user."""
    if not (1 <= spec["cols"] <= MAX_GRID and 1 <= spec["rows"] <= MAX_GRID):
        return f"Columns and rows must be between 1 and {MAX_GRID}."
    if spec["margin_in"] < 0 or spec["overlap_in"] < 0:
        return "Margin and overlap can't be negative."
    l, t, r, b = spec.get("crop") or (0, 0, 0, 0)
    if l + r > 0.9 or t + b > 0.9:
        return "That much cropping leaves almost nothing of the picture."
    L = layout(spec)
    if L["pw"] < 1 or L["ph"] < 1:
        return "That margin leaves no printable area on this paper."
    if L["o"] >= min(L["pw"], L["ph"]) / 2:
        return "The overlap is too large for this paper."
    return None


def describe(spec):
    """Texts for the UI and the receipt: grid, sheet count and final size."""
    L = layout(spec)
    n = spec["cols"] * spec["rows"]
    return {
        "grid": f"{spec['cols']}×{spec['rows']}",
        "sheets": n,
        "paper": short_paper_name(spec["paper"]),
        "size": (f"{fmt_in(spec['target'][0])} × {fmt_in(spec['target'][1])} in" if spec.get("target")
                 else f"{fmt_in(L['W'])} × {fmt_in(L['H'])} in"),
        "area": f"{fmt_in(L['W'])} × {fmt_in(L['H'])} in",
        "W": L["W"], "H": L["H"],
    }


def tile_box(L, c, r):
    """Tile (column c, row r) as (x0, y0, x1, y1) in poster inches (the part of the poster its sheet prints)."""
    x0, y0 = c * L["sx"], r * L["sy"]
    return (x0, y0, x0 + L["pw"], y0 + L["ph"])


def fit_rect(src_w, src_h, W, H, fill):
    """Where the picture sits on the W x H poster. Fit: whole picture, blank bands. Fill: covers it, edges cropped."""
    s = max(W / src_w, H / src_h) if fill else min(W / src_w, H / src_h)
    w, h = src_w * s, src_h * s
    x0, y0 = (W - w) / 2, (H - h) / 2
    return (x0, y0, x0 + w, y0 + h)


def placement(src_w, src_h, L, spec):
    """(placed, frame): where the picture sits, and the area it may cover.
    The frame is the wanted image size centred on the sheets, or the whole grid."""
    t = spec.get("target")
    fw, fh = (min(t[0], L["W"]), min(t[1], L["H"])) if t else (L["W"], L["H"])
    fx, fy = (L["W"] - fw) / 2, (L["H"] - fh) / 2
    if t and spec.get("stretch"):
        return (fx, fy, fx + fw, fy + fh), (fx, fy, fx + fw, fy + fh)
    s = max(fw / src_w, fh / src_h) if spec["fill"] else min(fw / src_w, fh / src_h)
    w, h = src_w * s, src_h * s
    x0, y0 = fx + (fw - w) / 2, fy + (fh - h) / 2
    return (x0, y0, x0 + w, y0 + h), (fx, fy, fx + fw, fy + fh)


def crop_box(src, spec):
    """The kept part of the picture, in source units (pixels, or points for a PDF)."""
    l, t, r, b = spec.get("crop") or (0, 0, 0, 0)
    return (src.w * l, src.h * t, src.w * (1 - r), src.h * (1 - b))


def cropped_size(src, spec):
    x0, y0, x1, y1 = crop_box(src, spec)
    return x1 - x0, y1 - y0


def _scales(src, spec, placed):
    """Source units per poster inch, separately for x and y (they differ when the picture is stretched)."""
    cx0, cy0, cx1, cy1 = crop_box(src, spec)
    return cx0, cy0, (cx1 - cx0) / (placed[2] - placed[0]), (cy1 - cy0) / (placed[3] - placed[1])


def source_picture(src, max_px=900):
    """The whole uncropped picture, small, for the crop window."""
    if not src.vector:
        im = src.thumb().copy()
        im.thumbnail((max_px, max_px))
        return im
    z = max_px / max(src.w, src.h)
    pix = src.doc[src.page].get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def grid_for_size(paper, tw, th, margin, overlap):
    """Fewest sheets that cover a tw x th inch picture (tries portrait and landscape sheets)."""
    best = None
    for landscape in (False, True):
        sw, sh = sheet_size(paper, landscape)
        pw, ph = sw - 2 * margin, sh - 2 * margin
        if pw - overlap <= 0 or ph - overlap <= 0:
            continue
        cols = max(1, math.ceil((tw - overlap) / (pw - overlap) - 1e-9))
        rows = max(1, math.ceil((th - overlap) / (ph - overlap) - 1e-9))
        area = (cols * pw - (cols - 1) * overlap) * (rows * ph - (rows - 1) * overlap)
        key = (cols * rows, area)  # fewest sheets first, then least blank paper
        if best is None or key < best[0]:
            best = (key, {"cols": cols, "rows": rows, "landscape": landscape})
    return best[1] if best else {"cols": 1, "rows": 1, "landscape": False}


def _intersect(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 - x0 > 1e-6 and y1 - y0 > 1e-6 else None


def suggest_grid(aspect, paper, min_sheets=4, max_sheets=12, max_side=8):
    """Grid (and sheet orientation) whose poster shape is closest to the picture's width/height."""
    best = None
    for landscape in (False, True):
        sw, sh = sheet_size(paper, landscape)
        for cols in range(1, max_side + 1):
            for rows in range(1, max_side + 1):
                n = cols * rows
                if not (min_sheets <= n <= max_sheets):
                    continue
                err = abs(math.log((cols * sw / (rows * sh)) / aspect))
                key = (round(err / 0.04), n, err)  # near-ties (within ~4%) go to fewer sheets
                if best is None or key < best[0]:
                    best = (key, {"cols": cols, "rows": rows, "landscape": landscape})
    return best[1]


# ---------------- source (image or one PDF page) ----------------

class Source:
    def __init__(self, path, page=0):
        self.path = path
        self.img = None
        self.doc = None
        self.vector = False
        self._thumb = None
        self.page = 0
        if is_image(path):
            self.img = load_image_rgb(path)
            self.w, self.h = self.img.size
        elif path.lower().endswith(".pdf"):
            if fitz is None:
                raise AnalysisError("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")
            self.doc = fitz.open(path)
            if len(self.doc) == 0:
                raise AnalysisError("The PDF has no pages.")
            self.set_page(page)
        else:
            raise AnalysisError("Pick an image or a PDF for the poster.")

    @property
    def page_count(self):
        return len(self.doc) if self.doc else 1

    def set_page(self, page):
        self.page = min(max(page, 0), len(self.doc) - 1)
        pg = self.doc[self.page]
        self._thumb = None
        if pg.rotation == 0:
            self.vector, self.img = True, None
            self.w, self.h = pg.rect.width, pg.rect.height  # points
        else:  # rotated pages: use a picture of the page (keeps the maths simple and correct)
            z = PDF_RASTER_MAX_PX / max(pg.rect.width, pg.rect.height)
            pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csRGB, alpha=False)
            self.vector = False
            self.img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            self.w, self.h = self.img.size

    def thumb(self):
        if self._thumb is None:
            self._thumb = self.img.copy()
            self._thumb.thumbnail((1600, 1600), Image.LANCZOS)
        return self._thumb

    def close(self):
        if self.doc is not None:
            self.doc.close()
            self.doc = None


def effective_dpi(src, spec):
    """Pixels per inch the picture gets at this poster size (None for vector PDFs: they stay sharp)."""
    if src.vector:
        return None
    L = layout(spec)
    cw, ch = cropped_size(src, spec)
    (x0, y0, x1, y1), _ = placement(cw, ch, L, spec)
    return min(cw / (x1 - x0), ch / (y1 - y0))


# ---------------- preview (whole poster as one picture) ----------------

def preview_image(src, spec, max_px=560):
    """The assembled poster (grid is drawn by the UI on top). Returns (PIL image, pixels per inch)."""
    L = layout(spec)
    s = max_px / max(L["W"], L["H"])
    canvas = Image.new("RGB", (max(1, round(L["W"] * s)), max(1, round(L["H"] * s))), "white")
    placed, frame = placement(*cropped_size(src, spec), L, spec)
    vis = _intersect(placed, frame)
    if vis is None:
        return canvas, s
    size = (max(1, round((vis[2] - vis[0]) * s)), max(1, round((vis[3] - vis[1]) * s)))
    cx0, cy0, kx, ky = _scales(src, spec, placed)
    sx0, sy0 = cx0 + (vis[0] - placed[0]) * kx, cy0 + (vis[1] - placed[1]) * ky
    sx1, sy1 = cx0 + (vis[2] - placed[0]) * kx, cy0 + (vis[3] - placed[1]) * ky
    if src.vector:
        pix = src.doc[src.page].get_pixmap(matrix=fitz.Matrix(s / kx, s / ky), clip=fitz.Rect(sx0, sy0, sx1, sy1),
                                           colorspace=fitz.csRGB, alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if im.size != size:
            im = im.resize(size, Image.BILINEAR)
    else:
        th = src.thumb()
        ts = th.width / src.w  # thumbnail pixels per source pixel
        box = _clamp_box((sx0 * ts, sy0 * ts, sx1 * ts, sy1 * ts), th.size)
        im = th.resize(size, Image.BILINEAR, box=box)
    canvas.paste(im, (round(vis[0] * s), round(vis[1] * s)))
    return canvas, s


def _clamp_box(box, size):
    w, h = size
    x0, y0 = max(0.0, box[0]), max(0.0, box[1])
    x1, y1 = min(float(w), box[2]), min(float(h), box[3])
    return (x0, y0, max(x0 + 1e-3, x1), max(y0 + 1e-3, y1))


# ---------------- building the PDF ----------------

def _draw_marks(page, L, c, r, rect):
    """Corner marks on the printable area, plus overlap guides where a neighbour's edge falls on this sheet."""
    m = L["m"] * 72
    ticks = []
    for (x, dx) in ((rect.x0 + 1, 1), (rect.x1 - 1, -1)):
        for (y, dy) in ((rect.y0 + 1, 1), (rect.y1 - 1, -1)):
            ticks += [((x, y), (x + dx * MARK_LEN, y)), ((x, y), (x, y + dy * MARK_LEN))]
    if L["o"] > 0:  # where the next / previous sheet starts: line the sheets up on these
        xs = ([L["sx"]] if c < L["cols"] - 1 else []) + ([L["o"]] if c > 0 else [])
        ys = ([L["sy"]] if r < L["rows"] - 1 else []) + ([L["o"]] if r > 0 else [])
        for xi in xs:
            x = m + xi * 72
            ticks += [((x, rect.y0 + 1), (x, rect.y0 + 1 + MARK_LEN)), ((x, rect.y1 - 1), (x, rect.y1 - 1 - MARK_LEN))]
        for yi in ys:
            y = m + yi * 72
            ticks += [((rect.x0 + 1, y), (rect.x0 + 1 + MARK_LEN, y)), ((rect.x1 - 1, y), (rect.x1 - 1 - MARK_LEN, y))]
    for width, color in ((2.6, (1, 1, 1)), (0.8, (0, 0, 0))):  # white underlay so marks show on dark pictures
        for p1, p2 in ticks:
            page.draw_line(p1, p2, color=color, width=width)


def _draw_label(page, L, text):
    x, y = L["m"] * 72 + 8, L["m"] * 72 + 16
    page.insert_text((x, y), text, fontname="hebo", fontsize=9, color=(0.55, 0.55, 0.55))


def build_poster_pdf(source_path, spec, page=0, src=None):
    """Cut the picture into tiles and return the path of the multi-page PDF (one tile per page, row by row)."""
    if fitz is None:
        raise AnalysisError("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")
    err = validate(spec)
    if err:
        raise AnalysisError(err)
    own = src is None
    if own:
        src = Source(source_path, page)
    elif src.doc is not None:
        src.set_page(page)
    try:
        L = layout(spec)
        placed, frame = placement(*cropped_size(src, spec), L, spec)
        m = L["m"] * 72
        out = fitz.Document()
        for r in range(L["rows"]):
            for c in range(L["cols"]):
                pg = out.new_page(width=L["sw"] * 72, height=L["sh"] * 72)
                rect = fitz.Rect(m, m, m + L["pw"] * 72, m + L["ph"] * 72)
                tb = tile_box(L, c, r)
                vis = _intersect(tb, placed)
                vis = _intersect(vis, frame) if vis else None
                if vis is not None:
                    _draw_tile(pg, src, spec, placed, tb, vis, m)
                if spec["marks"]:
                    _draw_marks(pg, L, c, r, rect)
                if spec["labels"]:
                    _draw_label(pg, L, tile_label(r * L["cols"] + c, L["cols"]))
        path = os.path.join(make_temp_dir(), "poster.pdf")
        out.save(path)
        out.close()
        return path
    finally:
        if own:
            src.close()


def _draw_tile(pg, src, spec, placed, tb, vis, m):
    """Put the part of the picture that falls inside this tile onto the sheet."""
    dest = fitz.Rect(m + (vis[0] - tb[0]) * 72, m + (vis[1] - tb[1]) * 72,
                     m + (vis[2] - tb[0]) * 72, m + (vis[3] - tb[1]) * 72)
    cx0, cy0, kx, ky = _scales(src, spec, placed)
    sx0, sy0 = cx0 + (vis[0] - placed[0]) * kx, cy0 + (vis[1] - placed[1]) * ky
    sx1, sy1 = cx0 + (vis[2] - placed[0]) * kx, cy0 + (vis[3] - placed[1]) * ky
    if src.vector:
        pg.show_pdf_page(dest, src.doc, src.page, clip=fitz.Rect(sx0, sy0, sx1, sy1), keep_proportion=False)
        return
    box = _clamp_box((sx0, sy0, sx1, sy1), src.img.size)
    size = (max(1, round((vis[2] - vis[0]) * min(IMAGE_DPI, kx))),
            max(1, round((vis[3] - vis[1]) * min(IMAGE_DPI, ky))))
    tile = src.img.resize(size, Image.LANCZOS, box=box)
    buf = io.BytesIO()
    tile.save(buf, "JPEG", quality=92)
    pg.insert_image(dest, stream=buf.getvalue())


# ---------------- pricing ----------------

def apply_pricing(result, job, total_sheets):
    """Optional poster pricing on top of compute_cost's result (in place). Idempotent.

    per_sheet: every printed sheet costs `fee`.  flat: the whole poster costs `fee`, but only when all
    sheets are printed (a single-tile reprint keeps the calculated price).
    """
    if not job or result.get("poster_fee") is not None:
        return
    pr = job.get("pricing") or {}
    mode, fee = pr.get("mode", "calculated"), float(pr.get("fee") or 0.0)
    n = result["num_pages"]
    if fee <= 0 or n == 0:
        return
    if mode == "per_sheet":
        total, label = fee * n, f"Poster price ({n} × {fee:.2f})"
    elif mode == "flat" and n == total_sheets:
        total, label = fee, "Poster price (flat)"
    else:
        return
    for p in result["per_page"]:
        p["page_cost"] = p["total"] = total / n
    result.update(poster_fee=total, poster_fee_label=label, gross=total, min_topup=0.0, max_cut=0.0)