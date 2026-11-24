import io
import os
import tempfile
import time

import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:
    import pymupdf as fitz
except ImportError:
    import fitz

LOAD_MAX_SIDE = 3200
EXPORT_MAX_SIDE = 2600
PREVIEW_MAX_SIDE = 1100
DETECT_SIDE = 256
DEFAULT_INSET = 0.04
EDGE_CONTRAST = 20.0

FILTERS = [
    ("original", "Original"),
    ("enhance", "Enhance"),
    ("lighten", "Lighten"),
    ("gray", "Grayscale"),
    ("bw", "Black & white"),
]

PAGE_SIZES = [
    ("auto", "Same as the scan", None),
    ("a4", "A4", (595.28, 841.89)),
    ("letter", "Letter", (612.0, 792.0)),
    ("long", "Long (8.5 x 13 in)", (612.0, 936.0)),
    ("a5", "A5", (419.53, 595.28)),
]


def load_image(path, max_side=LOAD_MAX_SIDE):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        base = Image.new("RGB", img.size, "white")
        base.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = base
    else:
        img = img.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def render_pdf_page(path, index, dpi=150):
    doc = fitz.open(path)
    try:
        pix = doc[index].get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), colorspace=fitz.csRGB, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    if max(img.size) > LOAD_MAX_SIDE:
        img.thumbnail((LOAD_MAX_SIDE, LOAD_MAX_SIDE), Image.LANCZOS)
    return img


def default_corners(inset=DEFAULT_INSET):
    a, b = inset, 1.0 - inset
    return [(a, a), (b, a), (b, b), (a, b)]


def _otsu(values):
    hist, _ = np.histogram(values, bins=256, range=(0, 256))
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256)
    cum_w = np.cumsum(hist)
    cum_s = np.cumsum(hist * levels)
    rest = total - cum_w
    mean_b = cum_s / np.maximum(cum_w, 1)
    mean_f = (cum_s[-1] - cum_s) / np.maximum(rest, 1)
    var = cum_w * rest * (mean_b - mean_f) ** 2
    var[(cum_w == 0) | (rest == 0)] = 0
    return int(np.argmax(var))


def _largest_component(mask):
    h, w = mask.shape
    cells = bytearray(mask.astype(np.uint8).ravel().tobytes())
    best = []
    for start in np.flatnonzero(mask.ravel()).tolist():
        if not cells[start]:
            continue
        cells[start] = 0
        stack = [start]
        found = []
        while stack:
            i = stack.pop()
            found.append(i)
            x = i % w
            if x > 0 and cells[i - 1]:
                cells[i - 1] = 0
                stack.append(i - 1)
            if x < w - 1 and cells[i + 1]:
                cells[i + 1] = 0
                stack.append(i + 1)
            if i >= w and cells[i - w]:
                cells[i - w] = 0
                stack.append(i - w)
            if i < w * (h - 1) and cells[i + w]:
                cells[i + w] = 0
                stack.append(i + w)
        if len(found) > len(best):
            best = found
    return np.array(best, dtype=np.int64)


def _quad_area(pts):
    x = [p[0] for p in pts]
    y = [p[1] for p in pts]
    return abs(sum(x[i] * y[(i + 1) % 4] - x[(i + 1) % 4] * y[i] for i in range(4))) / 2.0


def _convex(pts):
    signs = []
    for i in range(4):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % 4]
        cx, cy = pts[(i + 2) % 4]
        signs.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    return all(s > 0 for s in signs) or all(s < 0 for s in signs)


def is_convex(corners):
    return _convex(corners)


def _candidate_masks(small):
    rgb = np.asarray(small, dtype=np.float32)
    h, w = rgb.shape[:2]
    gray = np.asarray(small.convert("L").filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(7)))
    yield gray > _otsu(gray)
    ring = max(2, int(min(h, w) * 0.04))
    edge = np.concatenate([rgb[:ring].reshape(-1, 3), rgb[-ring:].reshape(-1, 3),
                           rgb[:, :ring].reshape(-1, 3), rgb[:, -ring:].reshape(-1, 3)])
    bg = np.median(edge, axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    top = float(dist.max())
    if top > 1e-3:
        scaled = (dist / top * 255.0).astype(np.uint8)
        yield scaled > _otsu(scaled)


def _fill_holes(mask):
    h, w = mask.shape
    outside = bytearray((~mask).astype(np.uint8).ravel().tobytes())
    stack = []
    for x in range(w):
        for i in (x, (h - 1) * w + x):
            if outside[i]:
                outside[i] = 2
                stack.append(i)
    for y in range(h):
        for i in (y * w, y * w + w - 1):
            if outside[i]:
                outside[i] = 2
                stack.append(i)
    while stack:
        i = stack.pop()
        x = i % w
        for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, i >= w), (i + w, i < w * (h - 1))):
            if ok and outside[j] == 1:
                outside[j] = 2
                stack.append(j)
    return np.frombuffer(bytes(outside), dtype=np.uint8).reshape(h, w) != 2


def _clean(mask):
    img = Image.fromarray(_fill_holes(mask).astype(np.uint8) * 255)
    img = img.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    return np.asarray(img) > 127


def _edge_support(rgb, quad, reach=3.0):
    """Share of points along the quad's edges where the color changes across the edge (either direction)."""
    h, w = rgb.shape[:2]
    cx = sum(p[0] for p in quad) / 4.0
    cy = sum(p[1] for p in quad) / 4.0
    hits, total = 0, 0
    for i in range(4):
        (x0, y0), (x1, y1) = quad[i], quad[(i + 1) % 4]
        length = float(np.hypot(x1 - x0, y1 - y0))
        if length < 4:
            return 0.0
        nx, ny = (y1 - y0) / length, -(x1 - x0) / length
        if nx * ((x0 + x1) / 2 - cx) + ny * ((y0 + y1) / 2 - cy) < 0:
            nx, ny = -nx, -ny
        for t in np.linspace(0.1, 0.9, 17):
            px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            ox, oy = int(round(px + nx * reach)), int(round(py + ny * reach))
            ix, iy = int(round(px - nx * reach)), int(round(py - ny * reach))
            total += 1
            if not (0 <= ox < w and 0 <= oy < h and 0 <= ix < w and 0 <= iy < h):
                continue
            if float(np.sqrt(((rgb[iy, ix] - rgb[oy, ox]) ** 2).sum())) > EDGE_CONTRAST:
                hits += 1
    return hits / float(total)


def detect_corners(img):
    """(corners, found). corners are 4 (x, y) pairs in 0..1: top-left, top-right, bottom-right, bottom-left."""
    scale = DETECT_SIDE / float(max(img.size))
    small = img.resize((max(8, round(img.width * scale)), max(8, round(img.height * scale))), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(1.2))
    h, w = small.height, small.width
    rgb = np.asarray(small, dtype=np.float32)
    best, best_score = None, 0.0
    for mask in _candidate_masks(small):
        idx = _largest_component(_clean(mask))
        if idx.size < 0.1 * h * w:
            continue
        ys, xs = idx // w, idx % w
        s, d = xs + ys, xs - ys
        picks = [int(np.argmin(s)), int(np.argmax(d)), int(np.argmax(s)), int(np.argmin(d))]
        quad = [(float(xs[i]), float(ys[i])) for i in picks]
        quad = [(quad[0][0], quad[0][1]), (quad[1][0] + 1, quad[1][1]), (quad[2][0] + 1, quad[2][1] + 1),
                (quad[3][0], quad[3][1] + 1)]
        area = _quad_area(quad)
        frac = area / float(w * h)
        if not _convex(quad) or frac < 0.12 or frac > 0.985:
            continue
        support = _edge_support(rgb, quad)
        if support < 0.7:
            continue
        score = min(idx.size / area, area / idx.size) * (0.6 + 0.4 * min(1.0, frac / 0.5)) * support
        if score > best_score:
            best, best_score = quad, score
    if best is None or best_score < 0.55:
        return default_corners(), False
    return [(min(1.0, max(0.0, x / w)), min(1.0, max(0.0, y / h))) for x, y in best], True


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _perspective_coeffs(dst, src):
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    return np.linalg.solve(np.array(rows, dtype=np.float64), np.array(rhs, dtype=np.float64)).tolist()


def _true_aspect(src, size):
    """Width / height the page really has, undoing the perspective (Zhang & He). None if it can't be worked out."""
    u0, v0 = size[0] / 2.0, size[1] / 2.0
    m1, m2, m3, m4 = (np.array([x, y, 1.0]) for x, y in (src[0], src[1], src[3], src[2]))
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    except (FloatingPointError, ZeroDivisionError):
        return None
    if min(abs(k2 - 1.0), abs(k3 - 1.0)) < 0.05:
        return None
    n2, n3 = k2 * m2 - m1, k3 * m3 - m1
    if abs(n2[2]) < 1e-6 or abs(n3[2]) < 1e-6:
        return None
    f2 = -((n2[0] * n3[0] - (n2[0] * n3[2] + n2[2] * n3[0]) * u0 + n2[2] * n3[2] * u0 ** 2)
           + (n2[1] * n3[1] - (n2[1] * n3[2] + n2[2] * n3[1]) * v0 + n2[2] * n3[2] * v0 ** 2)) / (n2[2] * n3[2])
    if not np.isfinite(f2) or f2 <= 0:
        return None
    inv = np.linalg.inv(np.array([[np.sqrt(f2), 0, u0], [0, np.sqrt(f2), v0], [0, 0, 1.0]]))
    q = inv.T @ inv
    a, b = float(n2 @ q @ n2), float(n3 @ q @ n3)
    if a <= 0 or b <= 0:
        return None
    ratio = float(np.sqrt(a / b))
    return ratio if 0.2 < ratio < 5.0 else None


PAPER_RATIOS = (1.4142, 1.2941, 1.5294, 1.6471)


def _snap_to_paper(w, h, tolerance=0.025):
    long_side, short_side = max(w, h), min(w, h)
    for r in PAPER_RATIOS:
        if abs(long_side / short_side / r - 1.0) <= tolerance:
            long_side = short_side * r
            break
    return (short_side, long_side) if w < h else (long_side, short_side)


def warp(img, corners, max_side=EXPORT_MAX_SIDE):
    """Cut the quad out of `img` and flatten it into a straight rectangle."""
    w, h = img.size
    src = [(x * w, y * h) for x, y in corners]
    out_w = max(_dist(src[0], src[1]), _dist(src[3], src[2]))
    out_h = max(_dist(src[0], src[3]), _dist(src[1], src[2]))
    if out_w < 8 or out_h < 8:
        return img.copy()
    with np.errstate(all="ignore"):
        ratio = _true_aspect(src, (w, h))
    if ratio is not None and 0.75 < ratio / (out_w / out_h) < 1.33:
        out_h = (_dist(src[0], src[3]) + _dist(src[1], src[2])) / 2.0
        out_w = out_h * ratio
    out_w, out_h = _snap_to_paper(out_w, out_h)
    k = min(1.0, max_side / max(out_w, out_h))
    out_w, out_h = max(8, int(round(out_w * k))), max(8, int(round(out_h * k)))
    dst = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    coeffs = _perspective_coeffs(dst, src)
    return img.transform((out_w, out_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(255, 255, 255))


def rotate(img, turns):
    turns %= 4
    if turns == 1:
        return img.transpose(Image.ROTATE_270)
    if turns == 2:
        return img.transpose(Image.ROTATE_180)
    if turns == 3:
        return img.transpose(Image.ROTATE_90)
    return img


def _background(img):
    w, h = img.size
    k = max(1, min(w, h) // 160)
    small = img.resize((max(1, w // k), max(1, h // k)), Image.BOX)
    small = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(6))
    return np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32)


def _flatten(img):
    arr = np.asarray(img, dtype=np.float32)
    bg = np.maximum(_background(img), 24.0)
    return np.clip(arr / bg, 0.0, 1.6)


def _flatten_lum(img):
    """Remove uneven light using the brightness only, so colors keep their hue (no rainbow tints)."""
    bg = np.maximum(_background(img.convert("L")), 24.0)
    return np.clip(np.asarray(img, dtype=np.float32) / bg[:, :, None], 0.0, 1.6)


def _stretch(arr, low=0.5, high=99.5):
    lo, hi = np.percentile(arr, [low, high])
    if hi - lo < 1e-3:
        return arr
    return (arr - lo) / (hi - lo)


def _to_image(arr):
    return Image.fromarray((np.clip(arr, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))


def apply_filter(img, name, brightness=0, contrast=0, strength=50):
    """brightness / contrast: -100..100. strength: 0..100, how much Enhance changes the photo. Returns an RGB image ('L' converted back) the same size as `img`."""
    if name == "original":
        arr = np.asarray(img, dtype=np.float32) / 255.0
    elif name == "enhance":
        orig = np.asarray(img, dtype=np.float32) / 255.0
        flat = np.clip(_flatten_lum(img), 0.0, 1.0)
        lo, hi = np.percentile(flat.mean(axis=2), [1.0, 99.0])
        if hi - lo > 1e-3:
            flat = np.clip((flat - lo) / (hi - lo), 0.0, 1.0)
        k = min(1.0, max(0.0, strength / 100.0))
        arr = orig * (1.0 - k) + flat * k
    elif name == "lighten":
        arr = np.clip(_flatten_lum(img), 0.0, 1.0)
        lo, hi = np.percentile(arr.mean(axis=2), [1.0, 97.0])
        if hi - lo > 1e-3:
            arr = (arr - lo) / (hi - lo)
        arr = np.clip(arr, 0.0, 1.0) ** 0.62
    elif name == "gray":
        lum = np.asarray(img.convert("L"), dtype=np.float32)
        flat = np.clip(_flatten(Image.fromarray(lum.astype(np.uint8))), 0.0, 1.0)
        arr = np.clip(_stretch(flat, 1.0, 99.0), 0.0, 1.0)[:, :, None].repeat(3, axis=2)
    elif name == "bw":
        lum = Image.fromarray(np.asarray(img.convert("L")))
        flat = _flatten(lum)
        cut = 0.80 - brightness / 250.0
        soft = np.clip((flat - (cut - 0.07)) / 0.14, 0.0, 1.0)
        arr = soft[:, :, None].repeat(3, axis=2)
        return _to_image(arr)
    else:
        arr = np.asarray(img, dtype=np.float32) / 255.0
    if brightness:
        arr = arr + brightness / 250.0
    if contrast:
        arr = (arr - 0.5) * (1.0 + contrast / 100.0) + 0.5
    return _to_image(arr)


def process(src, corners, turns=0, filter_name="original", brightness=0, contrast=0, strength=50,
            max_side=EXPORT_MAX_SIDE):
    flat = warp(src, corners, max_side)
    flat = rotate(flat, turns)
    return apply_filter(flat, filter_name, brightness, contrast, strength)


def preview_source(img):
    if max(img.size) <= PREVIEW_MAX_SIDE:
        return img
    k = PREVIEW_MAX_SIDE / float(max(img.size))
    return img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)


def _encode(img, filter_name):
    buf = io.BytesIO()
    if filter_name == "bw":
        img.convert("L").save(buf, "PNG", optimize=True)
    else:
        img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def build_pdf(pages, dest, page_size="auto", dpi=200):
    """pages: [(PIL image, filter name)]. page_size: key of PAGE_SIZES, or a (width, height) in points. Returns dest."""
    if isinstance(page_size, (tuple, list)):
        paper = (min(page_size), max(page_size))
    else:
        paper = next((p[2] for p in PAGE_SIZES if p[0] == page_size), None)
    doc = fitz.open()
    try:
        for img, filter_name in pages:
            if paper is None:
                pw, ph = img.width * 72.0 / dpi, img.height * 72.0 / dpi
                rect = fitz.Rect(0, 0, pw, ph)
            else:
                pw, ph = paper if img.width <= img.height else (paper[1], paper[0])
                k = min(pw / img.width, ph / img.height)
                iw, ih = img.width * k, img.height * k
                rect = fitz.Rect((pw - iw) / 2, (ph - ih) / 2, (pw + iw) / 2, (ph + ih) / 2)
            page = doc.new_page(width=pw, height=ph)
            page.insert_image(rect, stream=_encode(img, filter_name))
        doc.save(dest, garbage=3, deflate=True)
    finally:
        doc.close()
    return dest


def new_scan_path():
    folder = tempfile.mkdtemp(prefix="printcost_scan_")
    return os.path.join(folder, "Scan " + time.strftime("%Y-%m-%d %H-%M-%S") + ".pdf")