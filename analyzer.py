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


class AnalysisError(Exception):
    pass


def rgb_array_to_cmyk_coverage(rgb_array: np.ndarray):
    rgb = rgb_array.astype(np.float64) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    k = 1.0 - np.maximum(np.maximum(r, g), b)
    # Avoid divide-by-zero on pure black (k == 1) pixels
    safe_denom = np.where(k < 1.0, 1.0 - k, 1.0)

    c = (1.0 - r - k) / safe_denom
    m = (1.0 - g - k) / safe_denom
    y = (1.0 - b - k) / safe_denom

    # Where k == 1 (pure black), c=m=y should be 0 (all "ink" is K)
    c = np.where(k >= 1.0, 0.0, c)
    m = np.where(k >= 1.0, 0.0, m)
    y = np.where(k >= 1.0, 0.0, y)

    return {
        "C": float(np.clip(c, 0, 1).mean()),
        "M": float(np.clip(m, 0, 1).mean()),
        "Y": float(np.clip(y, 0, 1).mean()),
        "K": float(np.clip(k, 0, 1).mean()),
    }


def _pixmap_to_rgb_array(pix) -> np.ndarray:
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return np.array(img)


def analyze_pdf(path: str, progress_cb=None):
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
        pages_coverage.append(rgb_array_to_cmyk_coverage(rgb_array))
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


def analyze_docx_fallback(path: str):
    if docx is None:
        raise AnalysisError("python-docx is not installed. Run: pip install python-docx")

    document = docx.Document(path)

    # crude page count estimate: ~500 words per page
    word_count = sum(len(p.text.split()) for p in document.paragraphs)
    est_pages = max(1, round(word_count / 500))

    # flat assumption: typical text-only page ~= 5% black coverage
    TEXT_K_COVERAGE = 0.05
    pages_coverage = [{"C": 0.0, "M": 0.0, "Y": 0.0, "K": TEXT_K_COVERAGE} for _ in range(est_pages)]

    # fold in embedded images, spread across the estimated pages
    image_parts = [rel.target_part for rel in document.part.rels.values()
                   if "image" in rel.reltype]
    for idx, part in enumerate(image_parts):
        try:
            img = Image.open(io.BytesIO(part.blob)).convert("RGB")
            cov = rgb_array_to_cmyk_coverage(np.array(img))
            target_page = idx % est_pages
            for ch in "CMYK":
                pages_coverage[target_page][ch] = max(pages_coverage[target_page][ch], cov[ch])
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


def compute_cost(pages_coverage, ink_prices: dict, paper_price_per_sheet: float, percentages: dict):
    per_page = []
    ink_total = 0.0

    for cov in pages_coverage:
        page_ink_cost = sum(cov[ch] * ink_prices.get(ch, 0.0) for ch in "CMYK")
        ink_total += page_ink_cost
        per_page.append({"coverage": cov, "ink_cost": page_ink_cost})

    num_pages = len(pages_coverage)
    paper_total = paper_price_per_sheet * num_pages
    subtotal = ink_total + paper_total

    category_amounts = {name: subtotal * (pct / 100.0) for name, pct in percentages.items()}
    total = subtotal + sum(category_amounts.values())

    return {
        "num_pages": num_pages,
        "per_page": per_page,
        "ink_total": ink_total,
        "paper_total": paper_total,
        "subtotal": subtotal,
        "percentages": percentages,
        "category_amounts": category_amounts,
        "total": total,
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
        tmp_dir = tempfile.mkdtemp(prefix="printcost_")
        pdf_path = convert_docx_to_pdf(path, tmp_dir)
        return pdf_path, False

    raise AnalysisError(f"Unsupported file type: {ext}. Use a .pdf or .docx file.")


def get_pdf_page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def render_pdf_page(pdf_path: str, page_index: int, max_size=(280, 360)):
    doc = fitz.open(pdf_path)
    if page_index < 0 or page_index >= len(doc):
        doc.close()
        return None
    pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(1.2, 1.2), colorspace=fitz.csRGB, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    img.thumbnail(max_size)
    return img