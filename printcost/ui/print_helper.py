from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtGui import QPainter, QImage, QPageLayout, QPageSize
from PyQt6.QtCore import Qt, QSizeF
from PyQt6.QtWidgets import QApplication, QMessageBox
import fitz
import numpy as np

from printcost.core import layout as page_layout
from printcost.core.analyzer import format_page_ranges, format_page_plan, has_copies
from printcost.ui.notes import notice

PRINT_DPI = 300


def _orientation(page):
    r = page.rect
    return QPageLayout.Orientation.Landscape if r.width > r.height else QPageLayout.Orientation.Portrait


def _paint_document(printer, doc, grayscale, threshold=False, only_pages=None, actual_size=False, rotate=False):
    pages = list(range(len(doc)))
    if only_pages:
        pages = [n - 1 for n in only_pages if 1 <= n <= len(doc)] or pages
    elif printer.printRange() == QPrinter.PrintRange.PageRange and printer.fromPage() > 0:
        first = max(1, printer.fromPage())
        last = min(len(doc), printer.toPage() or len(doc))
        pages = list(range(first - 1, last)) or pages

    painter = QPainter()
    printer.setPageOrientation(_orientation(doc[pages[0]]))
    if not painter.begin(printer):
        return False

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        mat = fitz.Matrix(PRINT_DPI / 72.0, PRINT_DPI / 72.0)
        for n, idx in enumerate(pages):
            page = doc[idx]
            if n > 0:
                printer.setPageOrientation(_orientation(page))
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
            if rotate:
                img = img.mirrored(True, True)

            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            if actual_size:
                res = printer.resolution()
                w_px, h_px = round(page.rect.width / 72 * res), round(page.rect.height / 72 * res)
                scaled = img.scaled(w_px, h_px, Qt.AspectRatioMode.IgnoreAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
                paper_rect = printer.paperRect(QPrinter.Unit.DevicePixel)
                x = paper_rect.x() - page_rect.x() + (paper_rect.width() - w_px) / 2
                y = paper_rect.y() - page_rect.y() + (paper_rect.height() - h_px) / 2
                painter.drawImage(int(x), int(y), scaled)
                continue
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


def _setting_notes(color_mode, media, quality):
    notes = []
    if color_mode != "color":
        notes.append(("warn", "Set color to Grayscale in the print window."))
    if media and media.lower() != "plain paper":
        notes.append(("warn", f'Set paper type to "{media}" in Preferences.'))
    if quality in ("draft", "high"):
        notes.append(("warn", f"Set quality to {quality.title()} in Preferences."))
    return notes


def _make_printer(doc, paper, color_mode, parent):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if paper:
        size = QPageSize(QSizeF(paper["width_in"], paper["height_in"]), QPageSize.Unit.Inch, paper["name"])
        if not printer.setPageSize(size):
            notice(parent, "warn", "Paper size", f"Printer doesn't list \"{paper['name']}\". Pick the closest size.")
    printer.setPageOrientation(_orientation(doc[0]))
    printer.setColorMode(QPrinter.ColorMode.GrayScale if color_mode != "color" else QPrinter.ColorMode.Color)
    return printer


def print_pdf(pdf_path, parent=None, paper=None, color_mode="color", quality="standard",
              job_name="Print Cost Calculator", pages=None, media="", actual_size=False):
    grayscale = color_mode != "color"
    notes = _setting_notes(color_mode, media, quality)
    if actual_size:
        notes.append(("warn", "These sheets print at 100% so the sizes stay exact. In Preferences, turn off Fit to page / Scale to fit."))
    if pages:
        text = (f"Only pages {format_page_plan(pages)} print ({len(pages)} pages). "
                "The window will still say All pages. Ignore it.")
        if has_copies(pages):
            text += " The copies are already counted, so keep the printer's own Copies at 1."
        notes.append(("info", text))
    if notes:
        if notice(parent, "info", "Before you print", notes,
                  QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return False

    doc = fitz.open(pdf_path)
    if len(doc) == 0:
        doc.close()
        return False

    printer = _make_printer(doc, paper, color_mode, parent)
    printer.setDocName(job_name + (f" (pages {format_page_ranges(pages)})" if pages else ""))
    dialog = QPrintDialog(printer, parent)
    dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, not pages)
    dialog.setMinMax(1, len(doc))
    if dialog.exec() == QPrintDialog.DialogCode.Accepted:
        if printer.colorMode() == QPrinter.ColorMode.GrayScale:
            grayscale = True
        try:
            _paint_document(printer, doc, grayscale, color_mode == "bw", pages, actual_size)
        except Exception as e:
            notice(parent, "danger", "Print error", str(e))

    doc.close()
    return True


def print_duplex(pdf_path, parent=None, paper=None, color_mode="color", quality="standard",
                 job_name="Print Cost Calculator", media="", prefs=None, prefs_for=None, finish_note=""):
    """Front sides, a prompt to put the stack back, then the back sides.
    Returns {"printer", "prefs"} when both passes were sent, otherwise None."""
    prefs = dict(prefs or page_layout.FLIP_DEFAULTS["duplex"])
    doc = fitz.open(pdf_path)
    total = len(doc)
    if total == 0:
        doc.close()
        return None
    grayscale = color_mode != "color"

    front, back = page_layout.pass_orders(total, prefs["output"], prefs["reverse"])
    notes = _setting_notes(color_mode, media, quality)
    notes.append(("info", f"Step 1 prints the {len(front)} front sides. Step 2 prints the backs. "
                          "Don't reorder the sheets in between. The finished stack comes out with "
                          "page 1 on top."))
    notes.append(("warn", "Turn off the printer's own double-sided option in Preferences."))
    if notice(parent, "info", "Before you print", notes,
              QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
              ok_text="Print front sides") != QMessageBox.StandardButton.Ok:
        doc.close()
        return None

    printer = _make_printer(doc, paper, color_mode, parent)
    printer.setDocName(f"{job_name} (front sides)")
    dialog = QPrintDialog(printer, parent)
    dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, False)
    dialog.setMinMax(1, total)
    if dialog.exec() != QPrintDialog.DialogCode.Accepted:
        doc.close()
        return None
    if printer.colorMode() == QPrinter.ColorMode.GrayScale:
        grayscale = True

    name = printer.printerName()
    saved = prefs_for(name) if prefs_for else None
    if saved:
        prefs = saved
        front, back = page_layout.pass_orders(total, prefs["output"], prefs["reverse"])
    landscape = _orientation(doc[0]) == QPageLayout.Orientation.Landscape
    rotate = page_layout.needs_rotation(prefs["flip"], landscape)

    try:
        if not _paint_document(printer, doc, grayscale, color_mode == "bw", front):
            notice(parent, "danger", "Print error", "The printer couldn't start the front sides.")
            doc.close()
            return None
    except Exception as e:
        notice(parent, "danger", "Print error", str(e))
        doc.close()
        return None

    if prefs["output"] == "up":
        lines = [("info", f"Take the whole stack without moving any sheet. Flip it over on the {prefs['flip']} edge "
                          "and put it back in the tray, blank side up.")]
    else:
        lines = [("info", "Take the whole stack without moving any sheet and put it back in the tray "
                          "as it is, blank side facing the way the printer prints.")]
    if prefs["reverse"]:
        lines.append(("info", "The backs print in reverse order (your printer setting)."))
    if saved:
        lines.append(("info", f"Using your saved settings for {name}."))
    if notice(parent, "info", "Put the printed stack back in the tray", lines,
              QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
              ok_text="Print back sides") != QMessageBox.StandardButton.Ok:
        doc.close()
        return None

    printer.setDocName(f"{job_name} (back sides)")
    try:
        sent = _paint_document(printer, doc, grayscale, color_mode == "bw", back, rotate=rotate)
    except Exception as e:
        notice(parent, "danger", "Print error", str(e))
        sent = False
    doc.close()
    if sent:
        text = ("Flip the whole finished stack over. " if prefs["output"] == "up" else "")
        notice(parent, "info", "Finished", text + "The fronts face up with page 1 on top. " + finish_note)
    return {"printer": name, "prefs": prefs} if sent else None