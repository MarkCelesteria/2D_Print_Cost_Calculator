from PyQt6.QtCore import QThread, pyqtSignal

from analyzer import (
    resolve_to_pdf, get_pdf_page_count, render_pdf_page,
    analyze_pdf, analyze_docx_fallback, compute_cost, AnalysisError,
)


class LoadDocumentWorker(QThread):
    ready = pyqtSignal(object, int, object, bool)  # pdf_path_or_None, page_count, first_page_img_or_None, is_approx

    def __init__(self, file_path, parent=None):
        super().__init__(parent)
        self.file_path = file_path

    def run(self):
        try:
            pdf_path, is_approx = resolve_to_pdf(self.file_path)
            if pdf_path is None:
                self.ready.emit(None, 1, None, True)
                return
            page_count = get_pdf_page_count(pdf_path)
            first_page = render_pdf_page(pdf_path, 0)
            self.ready.emit(pdf_path, page_count, first_page, is_approx)
        except Exception:
            self.ready.emit(None, 1, None, True)


class AnalyzeWorker(QThread):
    progress = pyqtSignal(int, int)
    succeeded = pyqtSignal(dict, bool)
    failed = pyqtSignal(str)

    def __init__(self, pdf_path, original_path, ink_prices, paper_price, percentages, is_approx, mode="color", parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.original_path = original_path
        self.ink_prices = ink_prices
        self.paper_price = paper_price
        self.percentages = percentages
        self.is_approx = is_approx
        self.mode = mode

    def run(self):
        try:
            def progress_cb(cur, total):
                self.progress.emit(cur, total)

            if self.pdf_path:
                pages_coverage = analyze_pdf(self.pdf_path, progress_cb=progress_cb, mode=self.mode)
            else:
                pages_coverage, _ = analyze_docx_fallback(self.original_path, mode=self.mode)

            result = compute_cost(pages_coverage, self.ink_prices, self.paper_price, self.percentages)
            self.succeeded.emit(result, self.is_approx)
        except AnalysisError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")