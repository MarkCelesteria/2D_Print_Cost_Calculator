from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtGui import QPainter, QImage, QPageLayout, QPageSize
from PyQt6.QtCore import Qt, QSizeF
from PyQt6.QtWidgets import QApplication, QMessageBox
import fitz
import numpy as np

from analyzer import format_page_ranges
from notes import notice

PRINT_DPI = 300


def _orientation(page):
    r = page.rect
    return QPageLayout.Orientation.Landscape if r.width > r.height else QPageLayout.Orientation.Portrait


def _paint_document(printer, doc, grayscale, threshold=False, only_pages=None, margin_mm=0.0):
    """Draw every page; landscape pages get a landscape sheet, portrait pages a portrait one."""
    pages = list(range(len(doc)))
    if only_pages:
        pages = [n - 1 for n in only_pages if 1 <= n <= len(doc)] or pages
    elif printer.printRange() == QPrinter.PrintRange.PageRange and printer.fromPage() > 0:
        first = max(1, printer.fromPage())
        last = min(len(doc), printer.toPage() or len(doc))
        pages = list(range(first - 1, last)) or pages

    painter = QPainter()
    printer.setPageOrientation(_orientation(doc[pages[0]]))  # first page: set before begin()
    if not painter.begin(printer):
        return False

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        mat = fitz.Matrix(PRINT_DPI / 72.0, PRINT_DPI / 72.0)
        for n, idx in enumerate(pages):
            page = doc[idx]
            if n > 0:
                printer.setPageOrientation(_orientation(page))  # applies to the next page
                printer.newPage()

            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
            if grayscale:
                rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width * 3]
                rgb = rgb.reshape(pix.height, pix.width, 3).astype(np.float32)
                gray = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
                if threshold:
                    gray = np.where(gray < 127.5, 0, 255)
                samples, stride, fmt = gray.astype(np.uint8).tobytes(), pix.width, QImage.Format.Format_Grayscale8
            else:
                samples, stride, fmt = pix.samples, pix.stride, QImage.Format.Format_RGB888
            img = QImage(samples, pix.width, pix.height, stride, fmt)

            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            m = margin_mm / 25.4 * printer.resolution()  # 0 = borderless: use the whole sheet
            page_rect = page_rect.adjusted(m, m, -m, -m)
            scaled = img.scaled(
                int(page_rect.width()), int(page_rect.height()),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
            x = (page_rect.width() - scaled.width()) / 2
            y = (page_rect.height() - scaled.height()) / 2
            painter.drawImage(int(x), int(y), scaled)
    finally:
        painter.end()
        QApplication.restoreOverrideCursor()
    return True


def print_pdf(pdf_path, parent=None, paper=None, color_mode="color", quality="standard",
              job_name="Print Cost Calculator", pages=None, media="", margin_mm=0.0):
    grayscale = color_mode != "color"
    notes = []
    if grayscale:
        notes.append(("warn", "Set color to Grayscale in the print window."))
    if media and media.lower() != "plain paper":
        notes.append(("warn", f'Set paper type to "{media}" in Preferences.'))
    if quality in ("draft", "high"):
        notes.append(("warn", f"Set quality to {quality.title()} in Preferences."))
    if pages:
        notes.append(("info", f"Only pages {format_page_ranges(pages)} print ({len(pages)}). "
                              "The window will still say All pages. Ignore it."))
    if notes:
        if notice(parent, "info", "Before you print", notes,
                  QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return False

    doc = fitz.open(pdf_path)
    if len(doc) == 0:
        doc.close()
        return False

    grayscale = color_mode != "color"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setDocName(job_name + (f" (pages {format_page_ranges(pages)})" if pages else ""))

    if paper:
        size = QPageSize(QSizeF(paper["width_in"], paper["height_in"]), QPageSize.Unit.Inch, paper["name"])
        if not printer.setPageSize(size):
            notice(parent, "warn", "Paper size", f"Printer doesn't list \"{paper['name']}\". Pick the closest size.")

    def on_paint(p):
        try:
            _paint_document(p, doc, grayscale, color_mode == "bw", pages, margin_mm)
        except Exception as e:
            notice(parent, "danger", "Print error", str(e))

    printer.setPageOrientation(_orientation(doc[0]))
    printer.setColorMode(QPrinter.ColorMode.GrayScale if grayscale else QPrinter.ColorMode.Color)
    dialog = QPrintDialog(printer, parent)
    dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, not pages)
    dialog.setMinMax(1, len(doc))
    if dialog.exec() == QPrintDialog.DialogCode.Accepted:
        if printer.colorMode() == QPrinter.ColorMode.GrayScale:
            grayscale = True
        on_paint(printer)

    doc.close()
    return True