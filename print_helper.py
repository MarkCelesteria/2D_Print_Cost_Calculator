from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtGui import QPainter, QImage
from PyQt6.QtCore import Qt
import fitz


def print_pdf(pdf_path, parent=None):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    dialog = QPrintDialog(printer, parent)
    if dialog.exec() != QPrintDialog.DialogCode.Accepted:
        return False

    doc = fitz.open(pdf_path)
    painter = QPainter()
    if not painter.begin(printer):
        doc.close()
        return False

    try:
        for i, page in enumerate(doc):
            zoom = 300 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.width * 3, QImage.Format.Format_RGB888)

            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            scaled = img.scaled(
                int(page_rect.width()), int(page_rect.height()),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )

            x = (page_rect.width() - scaled.width()) / 2
            y = (page_rect.height() - scaled.height()) / 2
            painter.drawImage(int(x), int(y), scaled)

            if i < len(doc) - 1:
                printer.newPage()
    finally:
        painter.end()
        doc.close()

    return True