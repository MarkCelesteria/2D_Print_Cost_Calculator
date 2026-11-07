from PyQt6.QtCore import QThread, pyqtSignal

from analyzer import (
    resolve_to_pdf, get_pdf_page_count, render_pdf_page, build_batch_pdf, is_image,
    analyze_pdf, analyze_docx_fallback, compute_cost, AnalysisError,
)


class LoadDocumentWorker(QThread):
    ready = pyqtSignal(object, int, object, bool)  # pdf_path_or_None, page_count, first_page_img_or_None, is_approx
    warning = pyqtSignal(str)

    def __init__(self, paths, paper, fill=False, per_page=1, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.paper = paper
        self.fill = fill
        self.per_page = per_page

    def run(self):
        try:
            if len(self.paths) == 1 and not is_image(self.paths[0]):
                pdf_path, is_approx = resolve_to_pdf(self.paths[0])
            else:
                pdf_path, skipped = build_batch_pdf(
                    self.paths, self.paper["width_in"], self.paper["height_in"],
                    fill=self.fill, per_page=self.per_page,
                )
                is_approx = False
                if skipped:
                    self.warning.emit("Skipped (unreadable or no converter): " + ", ".join(skipped))
            if pdf_path is None:
                self.ready.emit(None, 1, None, True)
                return
            page_count = get_pdf_page_count(pdf_path)
            first_page = render_pdf_page(pdf_path, 0)
            self.ready.emit(pdf_path, page_count, first_page, is_approx)
        except Exception as e:
            self.warning.emit(str(e))  # no more silent failures
            self.ready.emit(None, 1, None, True)


class AnalyzeWorker(QThread):
    progress = pyqtSignal(int, int)
    succeeded = pyqtSignal(dict, bool)
    failed = pyqtSignal(str)

    def __init__(self, pdf_path, original_path, ink_prices, paper_price, percentages, is_approx, mode="color",
                 min_price=None, discount_pct=0.0, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.original_path = original_path
        self.ink_prices = ink_prices
        self.paper_price = paper_price
        self.percentages = percentages
        self.is_approx = is_approx
        self.mode = mode
        self.min_price = min_price
        self.discount_pct = discount_pct

    def run(self):
        try:
            def progress_cb(cur, total):
                self.progress.emit(cur, total)

            if self.pdf_path:
                pages_coverage = analyze_pdf(self.pdf_path, progress_cb=progress_cb, mode=self.mode)
            else:
                pages_coverage, _ = analyze_docx_fallback(self.original_path, mode=self.mode)

            result = compute_cost(pages_coverage, self.ink_prices, self.paper_price, self.percentages,
                                  min_price_per_page=self.min_price, discount_pct=self.discount_pct)
            self.succeeded.emit(result, self.is_approx)
        except AnalysisError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")