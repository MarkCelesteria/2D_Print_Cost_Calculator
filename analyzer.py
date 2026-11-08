import io
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image

try:
    import fitz 
except ImportError:
    fitz = None

try:
    import docx
except ImportError:
    docx = None


RENDER_DPI = 150
COLOR_SPREAD = 0.12


class AnalysisError(Exception):
    pass


def rgb_array_to_cmyk_coverage(rgb_array: np.ndarray, mode: str = "color"):
    rgb = rgb_array.astype(np.float64) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    if mode == "grayscale":
        gray = 0.299 * r + 0.587 * g + 0.114 * b
        k = 1.0 - gray
        return {"C": 0.0, "M": 0.0, "Y": 0.0, "K": float(np.clip(k, 0, 1).mean()), "color_frac": 0.0}

    if mode == "bw":
        gray = 0.299 * r + 0.587 * g + 0.114 * b
        k = (gray < 0.5).astype(np.float64)
        return {"C": 0.0, "M": 0.0, "Y": 0.0, "K": float(k.mean()), "color_frac": 0.0}

    k = 1.0 - np.maximum(np.maximum(r, g), b)
    safe_denom = np.where(k < 1.0, 1.0 - k, 1.0)

    c = (1.0 - r - k) / safe_denom
    m = (1.0 - g - k) / safe_denom
    y = (1.0 - b - k) / safe_denom

    c = np.where(k >= 1.0, 0.0, c)
    m = np.where(k >= 1.0, 0.0, m)
    y = np.where(k >= 1.0, 0.0, y)

    spread = rgb.max(axis=-1) - rgb.min(axis=-1)
    color_frac = float((spread > COLOR_SPREAD).mean())

    return {
        "color_frac": color_frac,
        "C": float(np.clip(c, 0, 1).mean()),
        "M": float(np.clip(m, 0, 1).mean()),
        "Y": float(np.clip(y, 0, 1).mean()),
        "K": float(np.clip(k, 0, 1).mean()),
    }


def _pixmap_to_rgb_array(pix) -> np.ndarray:
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return np.array(img)


def analyze_pdf(path: str, progress_cb=None, mode: str = "color"):
    if fitz is None:
        raise AnalysisError("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")

    pages_coverage = []
    doc = fitz.open(path)
    total = len(doc)
    if total == 0:
        raise AnalysisError("The PDF has no pages.")

    zoom = RENDER_DPI / 72.0
    mat = fitz.Matrix(zoom, zoom)

    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
        rgb_array = _pixmap_to_rgb_array(pix)
        pages_coverage.append(rgb_array_to_cmyk_coverage(rgb_array, mode=mode))
        if progress_cb:
            progress_cb(i + 1, total)

    doc.close()
    return pages_coverage


def _soffice_path():
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    common_paths = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/lib/libreoffice/program/soffice",
    ]
    for path in common_paths:
        if os.path.exists(path):
            return path
    return None

def is_libreoffice_available() -> bool:
    return _soffice_path() is not None


def convert_docx_to_pdf(docx_path: str, out_dir: str) -> str:
    soffice = _soffice_path()
    if not soffice:
        raise AnalysisError("LIBREOFFICE_MISSING")

    result = subprocess.run(
        [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", out_dir, docx_path],
        capture_output=True, text=True, timeout=120,
    )
    base = os.path.splitext(os.path.basename(docx_path))[0]
    out_pdf = os.path.join(out_dir, base + ".pdf")
    if result.returncode != 0 or not os.path.exists(out_pdf):
        raise AnalysisError(f"LibreOffice conversion failed: {result.stderr.strip()}")
    return out_pdf


def analyze_docx_fallback(path: str, mode: str = "color"):
    if docx is None:
        raise AnalysisError("python-docx is not installed. Run: pip install python-docx")

    document = docx.Document(path)

    # crude page count estimate: ~500 words per page
    word_count = sum(len(p.text.split()) for p in document.paragraphs)
    est_pages = max(1, round(word_count / 500))

    # flat assumption: typical text-only page ~= 5% black coverage
    TEXT_K_COVERAGE = 0.05
    pages_coverage = [{"C": 0.0, "M": 0.0, "Y": 0.0, "K": TEXT_K_COVERAGE, "color_frac": 0.0} for _ in range(est_pages)]

    # fold in embedded images, spread across the estimated pages
    image_parts = [rel.target_part for rel in document.part.rels.values()
                   if "image" in rel.reltype]
    for idx, part in enumerate(image_parts):
        try:
            img = Image.open(io.BytesIO(part.blob)).convert("RGB")
            cov = rgb_array_to_cmyk_coverage(np.array(img), mode=mode)
            target_page = idx % est_pages
            for ch in "CMYK":
                pages_coverage[target_page][ch] = max(pages_coverage[target_page][ch], cov[ch])
            pages_coverage[target_page]["color_frac"] = max(pages_coverage[target_page]["color_frac"], cov["color_frac"])
        except Exception:
            continue

    return pages_coverage, True  # True = approximate


def analyze_document(path: str, progress_cb=None):
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        return analyze_pdf(path, progress_cb), False

    if ext == ".docx":
        soffice = _soffice_path()
        if soffice:
            with tempfile.TemporaryDirectory() as tmp:
                pdf_path = convert_docx_to_pdf(path, tmp)
                return analyze_pdf(pdf_path, progress_cb), False
        else:
            return analyze_docx_fallback(path)

    raise AnalysisError(f"Unsupported file type: {ext}. Use a .pdf or .docx file.")


def compute_cost(pages_coverage, ink_prices: dict, paper_price_per_sheet: float, percentages: dict,
                 min_price_per_page=None, discount_pct: float = 0.0, ink_multiplier: float = 1.0,
                 min_price_color=None, color_threshold_pct: float = 0.5):
    per_page = []
    ink_total = 0.0
    min_topup = 0.0
    pct_factor = 1.0 + sum(percentages.values()) / 100.0

    for cov in pages_coverage:
        page_ink_cost = sum(cov[ch] * ink_prices.get(ch, 0.0) for ch in "CMYK") * ink_multiplier
        is_color = cov.get("color_frac", 0.0) * 100.0 >= color_threshold_pct
        floor = min_price_color if (is_color and min_price_color) else min_price_per_page

        # page price after all percentages, then the minimum is applied to that
        page_total = (page_ink_cost + paper_price_per_sheet) * pct_factor
        topup = max(0.0, floor - page_total) if floor else 0.0

        ink_total += page_ink_cost
        min_topup += topup
        per_page.append({"coverage": cov, "ink_cost": page_ink_cost, "is_color": is_color,
                         "topup": topup, "total": page_total + topup})

    num_pages = len(pages_coverage)
    paper_total = paper_price_per_sheet * num_pages
    subtotal = ink_total + paper_total

    category_amounts = {name: subtotal * (pct / 100.0) for name, pct in percentages.items()}
    gross = subtotal + sum(category_amounts.values()) + min_topup
    discount_amount = gross * (discount_pct / 100.0)

    return {
        "num_pages": num_pages,
        "color_pages": sum(1 for p in per_page if p["is_color"]),
        "per_page": per_page,
        "ink_total": ink_total,
        "ink_multiplier": ink_multiplier,
        "paper_total": paper_total,
        "min_topup": min_topup,
        "subtotal": subtotal,
        "percentages": percentages,
        "category_amounts": category_amounts,
        "gross": gross,
        "discount_pct": discount_pct,
        "discount_amount": discount_amount,
        "total": gross - discount_amount,
    }


def generate_preview(path: str, max_size=(280, 360)):
    ext = os.path.splitext(path)[1].lower()

    try:
        if ext == ".pdf":
            if fitz is None:
                return None
            doc = fitz.open(path)
            if len(doc) == 0:
                doc.close()
                return None
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.2, 1.2), colorspace=fitz.csRGB, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            doc.close()
            img.thumbnail(max_size)
            return img

        if ext == ".docx":
            soffice = _soffice_path()
            if not soffice:
                return None
            with tempfile.TemporaryDirectory() as tmp:
                pdf_path = convert_docx_to_pdf(path, tmp)
                doc = fitz.open(pdf_path)
                if len(doc) == 0:
                    doc.close()
                    return None
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.2, 1.2), colorspace=fitz.csRGB, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                doc.close()
                img.thumbnail(max_size)
                return img
    except Exception:
        return None

    return None


def resolve_to_pdf(path: str):
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        return path, False

    if ext == ".docx":
        soffice = _soffice_path()
        if not soffice:
            return None, True
        tmp_dir = make_temp_dir()
        pdf_path = convert_docx_to_pdf(path, tmp_dir)
        return pdf_path, False

    raise AnalysisError(f"Unsupported file type: {ext}. Use a .pdf or .docx file.")


def get_pdf_page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def render_pdf_page(pdf_path: str, page_index: int, max_size=(280, 360), scale: float = 1.2):
    doc = fitz.open(pdf_path)
    if page_index < 0 or page_index >= len(doc):
        doc.close()
        return None
    pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    img.thumbnail(max_size)
    return img


# ---------------- image / batch support ----------------

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

from PIL import ImageOps

IMAGE_EXTS = (".jpg", ".jpeg", ".jfif", ".jpe", ".png", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif")
DOC_EXTS = (".pdf", ".docx")
SUPPORTED_EXTS = DOC_EXTS + IMAGE_EXTS

GRID_FOR = {1: (1, 1), 2: (1, 2), 4: (2, 2)}
FIT_MARGIN_PT = 18
FIT_GAP_PT = 10
IMAGE_DPI = 300


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def load_image_rgb(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.split()[3])
        return bg
    return img.convert("RGB")


def _cell_rects(w, h, cols, rows, margin, gap):
    cw = (w - 2 * margin - gap * (cols - 1)) / cols
    ch = (h - 2 * margin - gap * (rows - 1)) / rows
    return [
        fitz.Rect(margin + c * (cw + gap), margin + r * (ch + gap),
                  margin + c * (cw + gap) + cw, margin + r * (ch + gap) + ch)
        for r in range(rows) for c in range(cols)
    ]


def _place_image(page, img: Image.Image, rect, fill: bool):
    cw, ch = rect.width, rect.height
    if (img.width > img.height) != (cw > ch):
        img = img.rotate(90, expand=True)

    if fill:
        target = cw / ch
        w, h = img.size
        if w / h > target:
            nw = int(h * target)
            left = (w - nw) // 2
            img = img.crop((left, 0, left + nw, h))
        else:
            nh = int(w / target)
            top = (h - nh) // 2
            img = img.crop((0, top, w, top + nh))
        dest = rect
    else:     # fit inside the cell, keep aspect ratio, centered
        s = min(cw / img.width, ch / img.height)
        dw, dh = img.width * s, img.height * s
        x0, y0 = rect.x0 + (cw - dw) / 2, rect.y0 + (ch - dh) / 2
        dest = fitz.Rect(x0, y0, x0 + dw, y0 + dh)

    max_w, max_h = int(dest.width / 72 * IMAGE_DPI), int(dest.height / 72 * IMAGE_DPI)
    if img.width > max_w or img.height > max_h:
        img.thumbnail((max_w, max_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    page.insert_image(dest, stream=buf.getvalue())


def build_batch_pdf(paths, paper_w_in, paper_h_in, fill=False, per_page=1):
    """Merge images / PDFs / docx into one temp PDF. Returns (pdf_path, skipped_names)."""
    if fitz is None:
        raise AnalysisError("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")

    short, long_ = sorted((paper_w_in * 72, paper_h_in * 72))
    cols, rows = GRID_FOR.get(per_page, (1, 1))
    margin, gap = (0, 0) if fill else (FIT_MARGIN_PT, FIT_GAP_PT)

    out = fitz.open()
    skipped = []
    pending = []

    def flush():
        for i in range(0, len(pending), per_page):
            imgs = []
            for p in pending[i:i + per_page]:
                try:
                    imgs.append(load_image_rgb(p))
                except Exception:
                    skipped.append(os.path.basename(p))
            if not imgs:
                continue
            if per_page == 1 and imgs[0].width > imgs[0].height:
                w, h = long_, short
            else:
                w, h = short, long_
            page = out.new_page(width=w, height=h)
            for img, rect in zip(imgs, _cell_rects(w, h, cols, rows, margin, gap)):
                _place_image(page, img, rect, fill)
        pending.clear()

    for p in paths:
        if is_image(p):
            pending.append(p)
            continue
        flush()
        try:
            pdf, _ = resolve_to_pdf(p)
            if pdf is None:
                skipped.append(os.path.basename(p))
                continue
            src = fitz.open(pdf)
            out.insert_pdf(src)
            src.close()
        except Exception:
            skipped.append(os.path.basename(p))
    flush()

    if len(out) == 0:
        out.close()
        raise AnalysisError("None of the selected files could be read.")

    pdf_path = os.path.join(make_temp_dir(), "batch.pdf")
    out.save(pdf_path)
    out.close()
    return pdf_path, skipped


def apply_preview_mode(img, mode: str = "color"):
    """Show a page the way it will print. Same rules as the cost estimate."""
    if img is None or mode == "color":
        return img
    gray = img.convert("L")  # same 0.299/0.587/0.114 weights as the analysis
    if mode == "grayscale":
        return gray.convert("RGB")
    return gray.point(lambda v: 0 if v < 128 else 255).convert("RGB")  # "bw": dark -> black, else white


# ---------------- temp file management ----------------

import glob
import threading
import time

_TEMP_DIRS = set()
_TEMP_LOCK = threading.Lock()


def make_temp_dir() -> str:
    path = tempfile.mkdtemp(prefix="printcost_")
    with _TEMP_LOCK:
        _TEMP_DIRS.add(path)
    return path


def cleanup_temp_dirs(keep_path=None):
    """Delete the temp folders this session created (except the one holding keep_path)."""
    keep = os.path.abspath(keep_path) if keep_path else None
    with _TEMP_LOCK:
        for d in list(_TEMP_DIRS):
            if keep and keep.startswith(os.path.abspath(d) + os.sep):
                continue
            shutil.rmtree(d, ignore_errors=True)
            _TEMP_DIRS.discard(d)


def cleanup_old_temp_dirs(max_age_hours: float = 24):
    """On startup: remove leftovers from earlier sessions."""
    cutoff = time.time() - max_age_hours * 3600
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "printcost_*")):
        try:
            if os.path.getmtime(d) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass