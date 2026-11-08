from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtGui import QPainter, QImage, QPageLayout, QPageSize
from PyQt6.QtCore import Qt, QSizeF
from PyQt6.QtWidgets import QApplication, QMessageBox
import fitz
import numpy as np

from analyzer import format_page_ranges

PRINT_DPI = 300


def _orientation(page):
    r = page.rect
    return QPageLayout.Orientation.Landscape if r.width > r.height else QPageLayout.Orientation.Portrait


def _paint_document(printer, doc, grayscale, threshold=False, only_pages=None):
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
              job_name="Print Cost Calculator", pages=None):
    grayscale = color_mode != "color"
    notes = []
    if grayscale:
        notes.append("Color mode: set it to Grayscale (or Black & white) in the print window so only black ink is used.")
    if quality in ("draft", "high"):
        notes.append(f"Quality: set the printer's quality to {quality.title()} in its Preferences so the printout matches the price.")
    if pages:
        notes.append(f"Pages: only {format_page_ranges(pages)} will print ({len(pages)} page(s)). "
                     "The Pages box in the print window will still say All pages, because Windows "
                     "can't show a custom list. Ignore it.")
    if notes:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Before you print")
        box.setText("Before you print:")
        box.setInformativeText("\n\n".join(notes))
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
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
            QMessageBox.information(
                parent, "Paper size",
                f"Your printer doesn't list \"{paper['name']}\". Pick the closest size in the print window.",
            )

    def on_paint(p):
        try:
            _paint_document(p, doc, grayscale, color_mode == "bw", pages)
        except Exception as e:
            QMessageBox.critical(parent, "Print error", str(e))

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