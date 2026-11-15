import io
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import fitz
except ImportError:
    fitz = None

from printcost.core.analyzer import AnalysisError, IMAGE_DPI, load_image_rgb, make_temp_dir

MM = 1 / 25.4
MAX_ROWS = 8
MAX_QTY = 500
MIN_OK_DPI = 150

# name -> (width_in, height_in)
SIZES = [
    ("1×1 in", (1.0, 1.0)),
    ("1.5×1.5 in", (1.5, 1.5)),
    ("2×2 in", (2.0, 2.0)),
    ("Passport 35×45 mm", (35 * MM, 45 * MM)),
    ("Stamp 20×25 mm", (20 * MM, 25 * MM)),
    ("Wallet 2×3 in", (2.0, 3.0)),
]
CUSTOM = "Custom"
SIZE_MAP = dict(SIZES)

# ready-made sets: name -> [(size name, qty), ...]
SETS = [
    ("6 pcs 2×2", [("2×2 in", 6)]),
    ("8 pcs 1×1", [("1×1 in", 8)]),
    ("6 pcs Passport", [("Passport 35×45 mm", 6)]),
    ("4 pcs 2×2 + 8 pcs 1×1", [("2×2 in", 4), ("1×1 in", 8)]),
    ("3 pcs 2×2 + 3 pcs Passport", [("2×2 in", 3), ("Passport 35×45 mm", 3)]),
]

DEFAULTS = {
    "entries": [{"name": "2×2 in", "w": 2.0, "h": 2.0, "qty": 6}],
    "orientation": "auto",      # auto | portrait | landscape
    "gap_in": 0.1, "margin_in": 0.25, "lines": True,
    "focus": (0.5, 0.5, 1.0),   # centre x, centre y (fractions of the picture), height (fraction)
    "easy": False,              # Easy cutting: every cut runs straight across the sheet
    "sig_pct": 20.0,            # signature strip height, % of the photo height
    "signature_path": None,
    "sig_name": "",             # optional name printed under the signature
    "pricing_mode": "calculated", "fee": 0.0,
}


# ---------------- small helpers ----------------

def fmt_in(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def size_name(w, h):
    """Readable name for a size: known presets first, else inches."""
    for name, (sw, sh) in SIZES:
        if abs(sw - w) < 0.005 and abs(sh - h) < 0.005:
            return name
    return f"{fmt_in(w)}×{fmt_in(h)} in"


def make_entry(name, qty, w=None, h=None, sig=False):
    if name in SIZE_MAP:
        w, h = SIZE_MAP[name]
    return {"name": name, "w": float(w), "h": float(h), "qty": int(qty), "sig": bool(sig)}


def sig_strip_h(entry, spec):
    """Height of the white strip at the bottom INSIDE the photo that holds the signature and name."""
    return entry["h"] * spec.get("sig_pct", 20.0) / 100 if entry.get("sig") else 0.0


def photo_h(entry, spec):
    """Height of the part of the frame that shows the face."""
    return entry["h"] - sig_strip_h(entry, spec)


def photo_aspect(entry, spec):
    return entry["w"] / photo_h(entry, spec)


def cell_h(entry, spec):
    """Space on the sheet. The photo keeps its exact size: the strip is inside it, never added to it."""
    return entry["h"]


def short_paper_name(paper):
    return paper["name"].split(" (")[0]


def sheet_size(paper, landscape):
    short, long_ = sorted((paper["width_in"], paper["height_in"]))
    return (long_, short) if landscape else (short, long_)


def total_photos(spec):
    return sum(e["qty"] for e in spec["entries"])


# ---------------- packing ----------------

class Sheet(list):
    """The photos on one sheet as (x, y, w, h, entry_index) in inches from the top-left corner.
    `cuts` holds ready-made straight cut guides when the sheet was laid out for Easy cutting."""
    cuts = None


def _expand(spec):
    """One item per wanted photo: (w, cell height, entry index). Tallest first so shelves stay tidy."""
    items = [(e["w"], cell_h(e, spec), i) for i, e in enumerate(spec["entries"]) for _ in range(e["qty"])]
    return sorted(items, key=lambda it: (-it[1], -it[0], it[2]))


def _too_big(w, h):
    return AnalysisError(f"A {fmt_in(w)}×{fmt_in(h)} in photo doesn't fit on this paper.")


def pack(spec, landscape):
    """Returns a list of sheets (see Sheet). Normal mode: shelf packing, every shelf centred."""
    if spec.get("easy"):
        return pack_easy(spec, landscape)
    sw, sh = sheet_size(spec["paper"], landscape)
    m, g = spec["margin_in"], spec["gap_in"]
    pw, ph = sw - 2 * m, sh - 2 * m
    sheets, shelves, used_h = [], [], 0.0   # shelves: list of (height, [(w, h, idx)])

    def close_sheet():
        nonlocal shelves, used_h
        if not shelves:
            return
        block_h = sum(s[0] for s in shelves) + g * (len(shelves) - 1)
        y = m + (ph - block_h) / 2
        placed = Sheet()
        for height, row in shelves:
            row_w = sum(it[0] for it in row) + g * (len(row) - 1)
            x = m + (pw - row_w) / 2
            for w, h, idx in row:
                placed.append((x, y, w, h, idx))
                x += w + g
            y += height + g
        sheets.append(placed)
        shelves, used_h = [], 0.0

    for w, h, idx in _expand(spec):
        if w > pw + 1e-9 or h > ph + 1e-9:
            raise _too_big(w, h)
        placed_item = False
        if shelves:
            height, row = shelves[-1]
            row_w = sum(it[0] for it in row) + g * len(row)
            if row_w + w <= pw + 1e-9 and h <= height + 1e-9:
                row.append((w, h, idx))
                placed_item = True
        if not placed_item:
            extra = h + (g if shelves else 0)
            if shelves and used_h + extra > ph + 1e-9:
                close_sheet()
                extra = h
            shelves.append((h, [(w, h, idx)]))
            used_h += extra
    close_sheet()
    return sheets


def pack_easy(spec, landscape):
    """Easy cutting. The sheet is split into full-width bands; every band is as tall as its tallest photo.
    Smaller photos fill the rest of a band in stacked columns (with no gap, 4 × 1×1 take exactly the room
    of one 2×2). Every horizontal cut runs across the whole block and every vertical cut runs the whole
    band, so a paper cutter does all of it."""
    sw, sh = sheet_size(spec["paper"], landscape)
    m, g = spec["margin_in"], spec["gap_in"]
    pw, ph = sw - 2 * m, sh - 2 * m
    entries = spec["entries"]
    cells = [(e["w"], cell_h(e, spec)) for e in entries]
    for w, h in cells:
        if w > pw + 1e-9 or h > ph + 1e-9:
            raise _too_big(w, h)
    remaining = [e["qty"] for e in entries]
    order = sorted(range(len(entries)), key=lambda i: (-cells[i][1], -cells[i][0], i))

    bands = []   # (height, columns, width); a column is {"w": width, "items": [(h, entry_index), ...]}
    while any(remaining):
        i = next(j for j in order if remaining[j] > 0)   # the tallest photo still to place sets the band
        band_h = cells[i][1]
        cols, used = [], 0.0

        def add(j, k):
            nonlocal used
            w, h = cells[j]
            n = min(k, remaining[j])
            cols.append({"w": w, "items": [(h, j)] * n})
            used += (g if len(cols) > 1 else 0) + w
            remaining[j] -= n

        while remaining[i] > 0 and used + (g if cols else 0) + cells[i][0] <= pw + 1e-9:
            add(i, 1)
        for j in order:
            if j == i or remaining[j] == 0:
                continue
            wj, hj = cells[j]
            k = max(1, int((band_h + g + 1e-9) // (hj + g)))   # how many stack up inside the band
            while remaining[j] > 0 and used + g + wj <= pw + 1e-9:
                add(j, k)
        bands.append((band_h, cols, used))

    groups, cur, used_h = [], [], 0.0
    for band in bands:
        extra = band[0] + (g if cur else 0)
        if cur and used_h + extra > ph + 1e-9:
            groups.append(cur)
            cur, used_h, extra = [], 0.0, band[0]
        cur.append(band)
        used_h += extra
    if cur:
        groups.append(cur)
    return [_layout_easy(group, spec, sw, sh) for group in groups]


def _layout_easy(bands, spec, sw, sh):
    m, g = spec["margin_in"], spec["gap_in"]
    pw, ph = sw - 2 * m, sh - 2 * m
    pad = g / 2
    block_h = sum(b[0] for b in bands) + g * (len(bands) - 1)
    block_w = max(b[2] for b in bands)
    x0, y = m + (pw - block_w) / 2, m + (ph - block_h) / 2
    ext = 0.15                                   # cut lines stick out a little so the cutter can be lined up
    left, right = max(m, x0 - ext), min(sw - m, x0 + block_w + ext)
    sheet, cuts = Sheet(), []
    cuts.append(((left, y - pad), (right, y - pad)))
    for band_h, cols, _ in bands:
        x = x0
        for ci, col in enumerate(cols):
            cy = y
            for k, (h, idx) in enumerate(col["items"]):
                sheet.append((x, cy, col["w"], h, idx))
                if k:
                    cuts.append(((x - pad, cy - pad), (x + col["w"] + pad, cy - pad)))
                cy += h + g
            if ci == 0:
                cuts.append(((x - pad, y - pad), (x - pad, y + band_h + pad)))
            cuts.append(((x + col["w"] + pad, y - pad), (x + col["w"] + pad, y + band_h + pad)))
            x += col["w"] + g
        cuts.append(((left, y + band_h + pad), (right, y + band_h + pad)))
        y += band_h + g
    sheet.cuts = cuts
    return sheet


def cut_segments(spec, sheet):
    """Cut guide lines for one sheet as ((x1, y1), (x2, y2)) in inches."""
    if getattr(sheet, "cuts", None) is not None:
        return sheet.cuts
    pad = spec["gap_in"] / 2
    segs = []
    for x, y, w, h, idx in sheet:
        a, b, c, d = x - pad, y - pad, x + w + pad, y + h + pad
        segs += [((a, b), (c, b)), ((c, b), (c, d)), ((c, d), (a, d)), ((a, d), (a, b))]
    return segs


def best_plan(spec):
    """(landscape, sheets) using the chosen orientation, or whichever needs fewer sheets when set to auto.
    A hand layout (spec["manual"]) is used instead while it still matches the paper and the photos."""
    manual = manual_plan(spec)
    if manual:
        return manual
    mode = spec.get("orientation", "auto")
    options = [mode == "landscape"] if mode in ("portrait", "landscape") else [False, True]
    best = None
    for landscape in options:
        sheets = pack(spec, landscape)
        sw, sh = sheet_size(spec["paper"], landscape)
        key = (len(sheets), -sum(1 for _ in sheets[0]) if sheets else 0)
        if best is None or key < best[0]:
            best = (key, landscape, sheets)
    return best[1], best[2]


def capacity(spec, index):
    """How many photos of entry `index` fit on one empty sheet (best orientation)."""
    e = spec["entries"][index]
    probe = dict(spec, entries=[dict(e, qty=MAX_QTY)], manual=None)
    try:
        landscape, sheets = best_plan(probe)
    except AnalysisError:
        return 0
    return len(sheets[0]) if sheets else 0


def validate(spec):
    """None when the sheet can be built, else a short message for the user."""
    if not spec["entries"] or total_photos(spec) < 1:
        return "Add at least one photo."
    if len(spec["entries"]) > MAX_ROWS:
        return f"Use at most {MAX_ROWS} different sizes."
    for e in spec["entries"]:
        if e["w"] < 0.3 or e["h"] < 0.3:
            return "A photo can't be smaller than 0.3 in."
    if total_photos(spec) > MAX_QTY:
        return f"That's too many photos (max {MAX_QTY})."
    if spec["margin_in"] < 0 or spec["gap_in"] < 0:
        return "Margin and gap can't be negative."
    try:
        landscape, sheets = best_plan(spec)
    except AnalysisError as e:
        return str(e)
    if not sheets:
        return "Nothing to print."
    return None


def describe(spec, sheets=None):
    """Texts for the UI and the receipt."""
    if sheets is None:
        _, sheets = best_plan(spec)
    parts = [f"{e['qty']} × {size_name(e['w'], e['h'])}" + (" + signature" if e.get("sig") else "")
             for e in spec["entries"] if e["qty"] > 0]
    counts = [len(s) for s in sheets]
    return {
        "photos": total_photos(spec),
        "sheets": len(sheets),
        "paper": short_paper_name(spec["paper"]),
        "summary": " + ".join(parts),
        "counts": counts,
    }


def receipt_info(spec, sheets=None):
    d = describe(spec, sheets)
    return {"photos": d["photos"], "sheets": d["sheets"], "summary": d["summary"]}


# ---------------- hand layout ----------------

EPS = 1e-6


def printable(spec, landscape):
    """(left, top, right, bottom) of the area photos may use, in inches."""
    sw, sh = sheet_size(spec["paper"], landscape)
    m = spec["margin_in"]
    return (m, m, sw - m, sh - m)


def make_manual(spec, landscape, sheets):
    """Store a layout (list of sheets of (x, y, w, h, entry_index)) so it can be kept in the spec."""
    return {"paper": spec["paper"]["name"], "landscape": bool(landscape),
            "sheets": [[tuple(it) for it in sheet] for sheet in sheets if sheet]}


def manual_plan(spec):
    """(landscape, sheets) of the hand layout, or None when there is none or it no longer fits
    (other paper, other photos, or a photo outside the printable area)."""
    man = spec.get("manual")
    if not man or man.get("paper") != spec["paper"]["name"]:
        return None
    L, T, R, B = printable(spec, man["landscape"])
    counts = [0] * len(spec["entries"])
    sheets = []
    for items in man["sheets"]:
        sheet = Sheet()
        for x, y, w, h, idx in items:
            if idx >= len(counts) or x < L - 1e-6 or y < T - 1e-6 or x + w > R + 1e-6 or y + h > B + 1e-6:
                return None
            counts[idx] += 1
            sheet.append((x, y, w, h, idx))
        if sheet:
            sheets.append(sheet)
    if not sheets or counts != [e["qty"] for e in spec["entries"]]:
        return None
    return man["landscape"], sheets


def too_close(a, b, gap):
    """True when two photos (x, y, w, h) overlap or are closer than the gap."""
    return (a[0] < b[0] + b[2] + gap - EPS and b[0] < a[0] + a[2] + gap - EPS and
            a[1] < b[1] + b[3] + gap - EPS and b[1] < a[1] + a[3] + gap - EPS)


def clamp_rect(x, y, w, h, bounds):
    L, T, R, B = bounds
    return min(max(L, x), max(L, R - w)), min(max(T, y), max(T, B - h))


def resolve(items, moved, bounds, gap):
    """items: list of [x, y, w, h, idx] with item `moved` already at the spot it should take.
    Photos in its way are pushed aside (and push the ones behind them). Returns the new list, or None
    when there is no room."""
    items = [list(it) for it in items]
    L, T, R, B = bounds
    items[moved][0], items[moved][1] = clamp_rect(*items[moved][:4], bounds)
    queue, steps = [moved], 0
    while queue:
        a = items[queue.pop(0)]
        for j, b in enumerate(items):
            if b is a or j == moved or not too_close(a[:4], b[:4], gap):
                continue
            steps += 1
            if steps > 400:
                return None
            shifts = [("x", a[0] + a[2] + gap - b[0]), ("x", a[0] - gap - b[2] - b[0]),
                      ("y", a[1] + a[3] + gap - b[1]), ("y", a[1] - gap - b[3] - b[1])]
            for axis, d in sorted(shifts, key=lambda t: abs(t[1])):
                nx, ny = (b[0] + d, b[1]) if axis == "x" else (b[0], b[1] + d)
                if nx >= L - EPS and ny >= T - EPS and nx + b[2] <= R + EPS and ny + b[3] <= B + EPS:
                    b[0], b[1] = nx, ny
                    queue.append(j)
                    break
            else:
                return None
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if too_close(a[:4], b[:4], gap):
                return None
    return items


def snap_move(rect, others, bounds, gap, thr):
    """Snap a photo that is being moved. It only snaps to photos right next to it (never to far-away spots):
    beside one it sits exactly one gap away, and it lines up with the neighbour's top/bottom or left/right
    edge. The printable edges snap too. Returns (x, y, guides); guides = [("v", x, y0, y1) | ("h", y, x0, x1)]."""
    x, y, w, h = rect
    L, T, R, B = bounds
    bx, by = [(abs(L - x), L, None), (abs(R - w - x), R - w, None)], [(abs(T - y), T, None), (abs(B - h - y), B - h, None)]
    for o in others:
        ox, oy, ow, oh = o[:4]
        gx = max(ox - (x + w), x - (ox + ow))      # clear distance sideways (negative: shadows overlap)
        gy = max(oy - (y + h), y - (oy + oh))
        if (gx < -EPS and gy < -EPS) or max(gx, gy) > thr + gap:
            continue                                # on top of it, or not next to it
        side, stacked = gy < 0 <= gx, gx < 0 <= gy
        corner = gx >= 0 and gy >= 0
        if side or corner:
            bx += [(abs(ox + ow + gap - x), ox + ow + gap, o), (abs(ox - gap - w - x), ox - gap - w, o)]
        if stacked or corner:
            by += [(abs(oy + oh + gap - y), oy + oh + gap, o), (abs(oy - gap - h - y), oy - gap - h, o)]
        if side:
            by += [(abs(oy - y), oy, o), (abs(oy + oh - h - y), oy + oh - h, o)]
        if stacked:
            bx += [(abs(ox - x), ox, o), (abs(ox + ow - w - x), ox + ow - w, o)]
    bx = [c for c in bx if c[0] <= thr]
    by = [c for c in by if c[0] <= thr]
    nx = min(bx, key=lambda c: c[0])[1] if bx else x
    ny = min(by, key=lambda c: c[0])[1] if by else y
    guides = []
    for _, v, o in ([min(bx, key=lambda c: c[0])] if bx else []):
        if o is not None:
            edge = nx if abs(v - nx) < EPS and (abs(nx - (o[0] + o[2] + gap)) < EPS or abs(nx - o[0]) < EPS) else nx + w
            guides.append(("v", edge, min(ny, o[1]), max(ny + h, o[1] + o[3])))
    for _, v, o in ([min(by, key=lambda c: c[0])] if by else []):
        if o is not None:
            edge = ny if abs(ny - (o[1] + o[3] + gap)) < EPS or abs(ny - o[1]) < EPS else ny + h
            guides.append(("h", edge, min(nx, o[0]), max(nx + w, o[0] + o[2])))
    return nx, ny, guides


def find_spot(items, w, h, bounds, gap):
    """First free place (top to bottom, left to right) for a new photo next to what is already there."""
    L, T, R, B = bounds
    xs = {L} | {it[0] for it in items} | {it[0] + it[2] + gap for it in items}
    ys = {T} | {it[1] for it in items} | {it[1] + it[3] + gap for it in items}
    for y in sorted(ys):
        for x in sorted(xs):
            if x + w <= R + EPS and y + h <= B + EPS and not any(too_close((x, y, w, h), it[:4], gap) for it in items):
                return x, y
    return None


# ---------------- cropping ----------------

def focus_box(img_w, img_h, aspect, focus):
    """Crop box (x0, y0, x1, y1) in picture pixels with width/height == aspect, centred on the focus point.
    focus = (cx, cy, hfrac): hfrac is the box height as a fraction of the picture height (clamped to fit)."""
    cx, cy, hf = focus
    h = img_h * max(0.05, min(1.0, hf))
    w = h * aspect
    if w > img_w:
        w = img_w
        h = w / aspect
    x0 = min(max(0.0, cx * img_w - w / 2), img_w - w)
    y0 = min(max(0.0, cy * img_h - h / 2), img_h - h)
    return (x0, y0, x0 + w, y0 + h)


def _font(size):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _draw_strip(cell, rect, sig, name):
    """Signature (and name under it) centred in the white strip `rect` = (x0, y0, x1, y1) of the photo."""
    x0, y0, x1, y1 = rect
    bw, bh = x1 - x0, y1 - y0
    name_h = int(bh * 0.34) if name else 0
    if sig is not None:
        aw, ah = bw * 0.9, max(1, (bh - name_h) * 0.88)
        k = min(aw / sig.width, ah / sig.height)
        w, h = max(1, round(sig.width * k)), max(1, round(sig.height * k))
        scaled = sig.resize((w, h), Image.LANCZOS if k < 1 else Image.BICUBIC)
        cell.paste(scaled, (x0 + (bw - w) // 2, y0 + (bh - name_h - h) // 2))
    if name:
        draw = ImageDraw.Draw(cell)
        size = max(6, int(name_h * 0.8))
        font = _font(size)
        while size > 6 and draw.textlength(name, font=font) > bw * 0.92:
            size -= 1
            font = _font(size)
        tw = draw.textlength(name, font=font)
        draw.text((x0 + (bw - tw) / 2, y1 - name_h + name_h * 0.05), name, fill=(0, 0, 0), font=font)


def render_cell(img, entry, focus, spec, sig, ppi, resample=None):
    """The photo at exactly its size (`ppi` pixels per inch). With a signature strip, the face is cut to the
    upper part of the frame and the bottom part is white, holding the signature and name."""
    w_px, h_px = max(2, round(entry["w"] * ppi)), max(2, round(entry["h"] * ppi))
    strip_px = round(sig_strip_h(entry, spec) * ppi) if entry.get("sig") else 0
    strip_px = min(strip_px, h_px - 1)
    top_px = h_px - strip_px
    box = focus_box(img.width, img.height, w_px / top_px, focus)
    if resample is None:   # upscaling: LANCZOS only adds ringing
        small = (box[2] - box[0]) < w_px or (box[3] - box[1]) < top_px
        resample = Image.BICUBIC if small else Image.LANCZOS
    photo = img.resize((w_px, top_px), resample, box=box)
    if strip_px <= 0:
        return photo
    cell = Image.new("RGB", (w_px, h_px), "white")
    cell.paste(photo, (0, 0))
    _draw_strip(cell, (0, top_px, w_px, h_px), sig, spec.get("sig_name", ""))
    return cell


def effective_dpi(img, spec):
    """Lowest pixels-per-inch any photo in the sheet gets."""
    low = None
    for e in spec["entries"]:
        if e["qty"] <= 0:
            continue
        box = focus_box(img.width, img.height, photo_aspect(e, spec), spec["focus"])
        dpi = min((box[2] - box[0]) / e["w"], (box[3] - box[1]) / photo_h(e, spec))
        low = dpi if low is None else min(low, dpi)
    return low


# ---------------- preview ----------------

def preview_sheet(img, spec, landscape, sheets, index, max_px=520, sig=None):
    """One sheet as a PIL picture. Returns (image, pixels per inch)."""
    sw, sh = sheet_size(spec["paper"], landscape)
    s = max_px / max(sw, sh)
    canvas = Image.new("RGB", (max(1, round(sw * s)), max(1, round(sh * s))), "white")
    draw = ImageDraw.Draw(canvas)
    cache = {}
    for x, y, w, h, idx in sheets[index]:
        if idx not in cache:
            cache[idx] = render_cell(img, spec["entries"][idx], spec["focus"], spec, sig, s, Image.BILINEAR)
        canvas.paste(cache[idx], (round(x * s), round(y * s)))
    if spec.get("lines"):
        for (x1, y1), (x2, y2) in cut_segments(spec, sheets[index]):
            draw.line([round(x1 * s), round(y1 * s), round(x2 * s), round(y2 * s)], fill=(190, 190, 190))
    return canvas, s


# ---------------- signature ----------------

def prepare_signature(im):
    """Ink on white, cropped to the writing. Works on a transparent PNG, a photo or a scan on white paper."""
    rgba = np.asarray(im.convert("RGBA")).astype(np.float32)
    alpha = rgba[..., 3] / 255.0
    lum = rgba[..., :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    if alpha.min() < 0.98:                       # a cut-out signature: the transparency is the ink
        ink = alpha
    else:                                        # ink on paper: dark = ink, paper white = nothing
        ink = np.clip((235.0 - lum) / 140.0, 0.0, 1.0)
    rows, cols = np.where(ink > 0.15)
    if rows.size == 0:
        raise AnalysisError("Couldn't find a signature in this picture. Use dark ink on white paper.")
    pad = max(2, int(0.03 * max(rows.max() - rows.min(), cols.max() - cols.min())))
    y0, y1 = max(0, rows.min() - pad), min(ink.shape[0], rows.max() + pad + 1)
    x0, x1 = max(0, cols.min() - pad), min(ink.shape[1], cols.max() + pad + 1)
    gray = (255 * (1 - ink[y0:y1, x0:x1])).astype(np.uint8)
    out = Image.fromarray(gray, "L").convert("RGB")
    if max(out.size) > 1600:
        out.thumbnail((1600, 1600), Image.LANCZOS)
    return out


def save_signature(sig, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sig.save(path, "PNG")
    return path


def load_signature(path):
    try:
        with Image.open(path) as im:
            return im.convert("RGB")
    except Exception:
        return None


# ---------------- building the PDF ----------------

def build_pdf(source_path, spec):
    """Returns (pdf_path, info). One PDF page per sheet, at the real paper size, so it prints at 100%.
    info = {"sheets", "photos", "counts", "landscape"}"""
    if fitz is None:
        raise AnalysisError("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")
    err = validate(spec)
    if err:
        raise AnalysisError(err)
    img = load_image_rgb(source_path)
    sig = load_signature(spec["signature_path"]) if spec.get("signature_path") else None
    landscape, sheets = best_plan(spec)
    sw, sh = sheet_size(spec["paper"], landscape)
    streams = {}
    out = fitz.Document()
    try:
        for placed in sheets:
            pg = out.new_page(width=sw * 72, height=sh * 72)
            for x, y, w, h, idx in placed:
                if idx not in streams:
                    buf = io.BytesIO()
                    render_cell(img, spec["entries"][idx], spec["focus"], spec, sig, IMAGE_DPI).save(
                        buf, "JPEG", quality=95, dpi=(300, 300))
                    streams[idx] = buf.getvalue()
                pg.insert_image(fitz.Rect(x * 72, y * 72, (x + w) * 72, (y + h) * 72), stream=streams[idx])
            if spec.get("lines"):
                shape = pg.new_shape()
                for (x1, y1), (x2, y2) in cut_segments(spec, placed):
                    shape.draw_line((x1 * 72, y1 * 72), (x2 * 72, y2 * 72))
                shape.finish(color=(0.75, 0.75, 0.75), width=0.25)
                shape.commit()
        path = os.path.join(make_temp_dir(), "id_photos.pdf")
        out.save(path)
    finally:
        out.close()
    return path, {"sheets": len(sheets), "photos": total_photos(spec),
                  "counts": [len(s) for s in sheets], "landscape": landscape}