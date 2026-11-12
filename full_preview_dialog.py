from PyQt6.QtWidgets import (
    QDialog, QScrollArea, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QStackedWidget, QProgressBar, QFileDialog, QMessageBox, QMenu
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QEvent, QRectF, QPointF, QPoint, QSize, QEasingCurve, QVariantAnimation,
    QCoreApplication
)
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap, QPen, QPainterPath

from analyzer import (
    render_pdf_page, apply_preview_mode, format_page_ranges, get_pdf_page_inches, fit_page_to_paper,
    is_image, IMAGE_EXTS
)
from pdf_editor import PdfEditor, export_pdf
from preview_widget import pil_to_qpixmap
from style import PREVIEW_BG, MUTED, PRIMARY

STRIP_PAGE_SIZE = (700, 900)   # page size at 100% zoom
ZOOM_MIN = 0.5
ZOOM_STEP = 1.25          # buttons / keyboard
WHEEL_SENSITIVITY = 1.0015  # per wheel unit: one mouse notch (120) is about x1.2, touchpads zoom smoothly
ZOOM_ANIM_MS = 140

HANDLE = 10   # grab distance of a resize handle, in pixels
MIN_PX = 14   # smallest image side while resizing
CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
}


class RenderAllPagesWorker(QThread):
    page_ready = pyqtSignal(int, object, float)  # index, image, zoom it was rendered at

    def __init__(self, pdf_path, page_count, zoom=1.0, mode="color", order=None, fit_paper=None, parent=None):
        super().__init__(parent)
        self.fit_paper = fit_paper
        self.order = order or list(range(page_count))  # pages near the viewport go first
        self.pdf_path = pdf_path
        self.mode = mode
        self.page_count = page_count
        self.zoom = zoom
        self.abort = False

    def run(self):
        size = (int(STRIP_PAGE_SIZE[0] * self.zoom), int(STRIP_PAGE_SIZE[1] * self.zoom))
        for i in self.order:
            if self.abort:
                return
            img = render_pdf_page(self.pdf_path, i, max_size=size, scale=1.2 * self.zoom)
            if self.abort:
                return
            img = apply_preview_mode(img, self.mode)
            if self.fit_paper:
                img = fit_page_to_paper(img, get_pdf_page_inches(self.pdf_path, i),
                                        self.fit_paper["width_in"], self.fit_paper["height_in"], max_size=size)
            self.page_ready.emit(i, img, self.zoom)


def _mark_not_included(pm):
    """Dim a page and stamp NOT INCLUDED across it (for pages that won't be printed)."""
    out = QPixmap(pm)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.fillRect(out.rect(), QColor(255, 255, 255, 150))  # fade the page

    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(max(12, int(out.width() * 0.10)))
    p.setFont(font)
    text = "NOT INCLUDED"
    tw = p.fontMetrics().horizontalAdvance(text)
    th = p.fontMetrics().height()

    p.translate(out.width() / 2, out.height() / 2)
    p.rotate(-35)
    red = QColor(200, 30, 30, 210)
    pen = p.pen()
    pen.setColor(red)
    pen.setWidth(max(2, int(out.width() * 0.008)))
    p.setPen(pen)
    pad = int(th * 0.35)
    p.drawRoundedRect(QRectF(-tw / 2 - pad, -th / 2 - pad / 2, tw + 2 * pad, th + pad), 10, 10)
    p.drawText(QRectF(-tw / 2 - pad, -th / 2 - pad / 2, tw + 2 * pad, th + pad), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return out


class PageLabel(QLabel):
    """One page. In edit mode it lets you select, move and resize the images on it and drop picture files in.
    Image rectangles are kept as fractions of the page (0..1), so zooming never breaks them."""
    changed = pyqtSignal(int, int, object)     # page, image index, new rect (fractions of the page)
    dropped = pyqtSignal(int, list, object)    # page, picture files, drop point (fractions)
    selection_changed = pyqtSignal()

    def __init__(self, index, provider, text=""):
        super().__init__(text)
        self.index = index
        self.edit = False
        self.accept_files = False
        self.sel = -1
        self._provider = provider
        self._items = None
        self._hover = -1
        self._drag = None
        self._drop_hint = False
        self._crop = None   # {'bounds', 'box'} (fractions of the page) while trimming a picture
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    # ---------------- items ----------------

    def items(self):
        if self._items is None:
            self._items = self._provider(self.index)
        return self._items

    def invalidate(self):
        self._items = None
        self.sel = self._hover = -1
        self._drag = None
        self._crop = None
        self.update()

    def select_near(self, rect):
        """Select the image whose centre is closest to `rect` (used to keep the selection after an edit)."""
        items = self.items()
        c = rect.center()
        self.sel = min(range(len(items)), default=-1, key=lambda i: abs(items[i]["rect"].center().x() - c.x())
                       + abs(items[i]["rect"].center().y() - c.y()))

    # ---------------- geometry ----------------

    def _px(self, r):
        w, h = self.width(), self.height()
        return QRectF(r.x() * w, r.y() * h, r.width() * w, r.height() * h)

    def _norm(self, r):
        w, h = max(1, self.width()), max(1, self.height())
        return QRectF(r.x() / w, r.y() / h, r.width() / w, r.height() / h)

    @staticmethod
    def _handles(r):
        cx, cy = r.center().x(), r.center().y()
        return {"nw": QPointF(r.left(), r.top()), "n": QPointF(cx, r.top()), "ne": QPointF(r.right(), r.top()),
                "e": QPointF(r.right(), cy), "se": QPointF(r.right(), r.bottom()),
                "s": QPointF(cx, r.bottom()), "sw": QPointF(r.left(), r.bottom()), "w": QPointF(r.left(), cy)}

    def _hit(self, pos):
        items = self.items()
        if 0 <= self.sel < len(items) and not items[self.sel]["locked"]:
            for name, pt in self._handles(self._px(items[self.sel]["rect"])).items():
                if abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE:
                    return "handle", name
        for i in range(len(items) - 1, -1, -1):  # last = drawn on top
            if self._px(items[i]["rect"]).contains(pos):
                return "image", i
        return None, -1

    def _resized(self, orig, handle, pos, bounds=None, keep=True):
        bd = bounds if bounds is not None else QRectF(0, 0, self.width(), self.height())
        x, y = min(max(pos.x(), bd.left()), bd.right()), min(max(pos.y(), bd.top()), bd.bottom())
        l, t, r, b = orig.left(), orig.top(), orig.right(), orig.bottom()
        if keep and len(handle) == 2:  # corner: keep the proportions, the opposite corner stays put
            ax = r if "w" in handle else l
            ay = b if "n" in handle else t
            s = max(abs(x - ax) / orig.width(), abs(y - ay) / orig.height(),
                    MIN_PX / min(orig.width(), orig.height()))
            s = min(s, ((ax - bd.left()) if "w" in handle else (bd.right() - ax)) / orig.width(),
                    ((ay - bd.top()) if "n" in handle else (bd.bottom() - ay)) / orig.height())
            w, h = orig.width() * s, orig.height() * s
            return QRectF(ax - w if "w" in handle else ax, ay - h if "n" in handle else ay, w, h)
        if "w" in handle:
            l = min(x, r - MIN_PX)
        if "e" in handle:
            r = max(x, l + MIN_PX)
        if "n" in handle:
            t = min(y, b - MIN_PX)
        if "s" in handle:
            b = max(y, t + MIN_PX)
        return QRectF(QPointF(l, t), QPointF(r, b))

    # ---------------- crop ----------------

    def start_crop(self):
        items = self.items()
        if not (0 <= self.sel < len(items)) or items[self.sel]["locked"]:
            return False
        r = items[self.sel]["rect"]
        self._crop = {"bounds": QRectF(r), "box": QRectF(r)}
        self.update()
        return True

    def cancel_crop(self):
        self._crop = None
        self.update()

    def crop_fractions(self):
        """(left, top, right, bottom) of the crop box as fractions of the picture; None if nothing is trimmed."""
        c = self._crop
        if not c:
            return None
        bd, k = c["bounds"], c["box"]
        l, t = (k.left() - bd.left()) / bd.width(), (k.top() - bd.top()) / bd.height()
        r, b = (k.right() - bd.left()) / bd.width(), (k.bottom() - bd.top()) / bd.height()
        if l < 0.01 and t < 0.01 and r > 0.99 and b > 0.99:
            return None
        return max(0.0, l), max(0.0, t), min(1.0, r), min(1.0, b)

    def _crop_hit(self, pos):
        box = self._px(self._crop["box"])
        for name, pt in self._handles(box).items():
            if abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE:
                return name
        return "move" if box.contains(pos) else None

    # ---------------- mouse ----------------

    def mousePressEvent(self, e):
        if not self.edit or e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        if self._crop:  # trimming: only the crop box reacts
            hit = self._crop_hit(e.position())
            if hit:
                box = self._px(self._crop["box"])
                self._drag = {"mode": hit, "start": e.position(), "orig": box, "cur": QRectF(box), "crop": True}
            return
        kind, v = self._hit(e.position())
        items = self.items()
        if kind == "handle":
            orig = self._px(items[self.sel]["rect"])
            self._drag = {"mode": v, "start": e.position(), "orig": orig, "cur": QRectF(orig)}
        elif kind == "image" and not items[v]["locked"]:
            self.sel = v
            orig = self._px(items[v]["rect"])
            self._drag = {"mode": "move", "start": e.position(), "orig": orig, "cur": QRectF(orig)}
        else:
            self.sel = -1
        self.selection_changed.emit()
        self.update()

    def mouseMoveEvent(self, e):
        if not self.edit:
            return super().mouseMoveEvent(e)
        pos = e.position()
        d = self._drag
        if d and d.get("crop"):
            bd = self._px(self._crop["bounds"])
            if d["mode"] == "move":
                r = QRectF(d["orig"])
                r.translate(pos - d["start"])
                r.moveLeft(min(max(r.left(), bd.left()), bd.right() - r.width()))
                r.moveTop(min(max(r.top(), bd.top()), bd.bottom() - r.height()))
            else:
                r = self._resized(d["orig"], d["mode"], pos, bd, keep=False)
            d["cur"] = r
            self._crop["box"] = self._norm(r)
            self.update()
            return
        if self._crop:  # hovering while trimming
            hit = self._crop_hit(pos)
            if hit in CURSORS:
                self.setCursor(CURSORS[hit])
            elif hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
            return
        if d:
            if d["mode"] == "move":
                r = QRectF(d["orig"])
                r.translate(pos - d["start"])
                r.moveLeft(min(max(r.left(), 0), self.width() - r.width()))
                r.moveTop(min(max(r.top(), 0), self.height() - r.height()))
                d["cur"] = r
            else:
                d["cur"] = self._resized(d["orig"], d["mode"], pos)
            self.update()
            return
        kind, v = self._hit(pos)
        items = self.items()
        hover = v if kind == "image" else -1
        if kind == "handle":
            self.setCursor(CURSORS[v])
        elif kind == "image" and items[v]["locked"]:
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
            self.setToolTip("This image is rotated or inline, so it can't be edited.")
        elif kind == "image":
            self.setCursor(Qt.CursorShape.SizeAllCursor if v == self.sel else Qt.CursorShape.PointingHandCursor)
            self.setToolTip("")
        else:
            self.unsetCursor()
            self.setToolTip("")
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mouseReleaseEvent(self, e):
        d = self._drag
        if d and e.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            if d.get("crop"):
                self.update()
                return
            cur, orig = d["cur"], d["orig"]
            moved = (abs(cur.x() - orig.x()) + abs(cur.y() - orig.y())
                     + abs(cur.width() - orig.width()) + abs(cur.height() - orig.height()))
            if moved >= 2 and self.sel >= 0:
                self.changed.emit(self.index, self.sel, self._norm(cur))
            self.update()
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(e)

    # ---------------- drag and drop of picture files ----------------

    def _has_pictures(self, e):
        return self.accept_files and e.mimeData().hasUrls() and any(
            is_image(u.toLocalFile()) for u in e.mimeData().urls())

    def dragEnterEvent(self, e):
        if self._has_pictures(e):
            e.acceptProposedAction()
            self._drop_hint = True
            self.update()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._has_pictures(e):
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drop_hint = False
        self.update()

    def dropEvent(self, e):
        self._drop_hint = False
        self.update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if is_image(u.toLocalFile())]
        if paths and self.accept_files:
            e.acceptProposedAction()
            p = e.position()
            self.dropped.emit(self.index, paths, QPointF(p.x() / max(1, self.width()), p.y() / max(1, self.height())))

    # ---------------- painting ----------------

    def paintEvent(self, e):
        super().paintEvent(e)
        if not (self.edit or self._drop_hint):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(PRIMARY)
        if self._drop_hint:
            p.setPen(QPen(accent, 3, Qt.PenStyle.DashLine))
            p.setBrush(QColor(8, 145, 178, 25))
            p.drawRect(self.rect().adjusted(2, 2, -2, -2))
            p.setPen(accent)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop image here")
        if self.edit:
            items = self.items()
            self._paint_badge(p)
            if self._crop:
                self._paint_crop(p, accent)
                p.end()
                return
            p.setBrush(Qt.BrushStyle.NoBrush)
            if 0 <= self._hover < len(items) and self._hover != self.sel:
                p.setPen(QPen(accent, 1.5, Qt.PenStyle.DashLine))
                p.drawRect(self._px(items[self._hover]["rect"]))
            if 0 <= self.sel < len(items):
                r = self._px(items[self.sel]["rect"])
                if self._drag:  # ghost of the picture at its new place while dragging
                    r = self._drag["cur"]
                    pm = self.pixmap()
                    if pm is not None and not pm.isNull():
                        n = items[self.sel]["rect"]
                        src = QRectF(n.x() * pm.width(), n.y() * pm.height(),
                                     n.width() * pm.width(), n.height() * pm.height())
                        p.setOpacity(0.85)
                        p.drawPixmap(r, pm, src)
                        p.setOpacity(1.0)
                p.setPen(QPen(accent, 2))
                p.drawRect(r)
                p.setBrush(QColor("white"))
                p.setPen(QPen(accent, 1.5))
                for pt in self._handles(r).values():
                    p.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))
        p.end()


def _paint_page_badge(label, p):
    text = f"Page {label.index + 1}"
    w = p.fontMetrics().horizontalAdvance(text) + 16
    r = QRectF(8, 8, w, 22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(15, 27, 45, 175))
    p.drawRoundedRect(r, 6, 6)
    p.setPen(QColor("white"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


def _paint_crop_box(label, p, accent):
    bd, box = label._px(label._crop["bounds"]), label._px(label._crop["box"])
    outside = QPainterPath()
    outside.addRect(bd)
    inner = QPainterPath()
    inner.addRect(box)
    p.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 150))  # darken what will be trimmed away
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("white"), 1))
    p.drawRect(box)
    p.setPen(QPen(accent, 2, Qt.PenStyle.DashLine))
    p.drawRect(box)
    p.setBrush(QColor("white"))
    p.setPen(QPen(accent, 1.5))
    for pt in label._handles(box).values():
        p.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))


PageLabel._paint_badge = _paint_page_badge
PageLabel._paint_crop = _paint_crop_box


class FullPreviewDialog(QDialog):
    def __init__(self, pdf_path, page_count, parent=None, mode="color", included=None, fit_paper=None,
                 original_path=None, doc_name="document"):
        super().__init__(parent)
        self.mode = mode
        self.fit_paper = fit_paper
        self.included = set(included) if included else None  # 1-based pages that will print (None = all)
        self.setWindowTitle("Full Preview")
        self.setObjectName("fullPreview")
        self.setStyleSheet(f"""
            #fullPreview, #stripContainer {{ background: {PREVIEW_BG}; }}
            #stripContainer QLabel {{ color: {MUTED}; }}
        """)

        self.pdf_path = pdf_path
        self.original_path = original_path or pdf_path
        self.doc_name = doc_name
        self.page_count = page_count
        self.zoom = 1.0
        self.max_zoom = 2.0 if page_count <= 40 else 1.5  # keep memory reasonable on long documents
        self._pixmaps = {}      # page index -> (QPixmap, zoom it was rendered at)
        self._workers = []
        self.worker = None
        self._seen = set()  # pages rendered at least once (drives the loading screen)
        self._anim = None
        self._anim_target = 1.0
        self.editor = None      # created the first time Edit is switched on
        self.edit_mode = False
        self.can_edit = fit_paper is None   # a page shown on another paper no longer lines up with the file
        self._page_pts = {}     # page index -> (width, height) in points
        self._crop_label = None  # the page whose picture is being trimmed

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        mode_names = {"color": "Color", "grayscale": "Grayscale", "bw": "Black & White"}
        self._mode_name = mode_names.get(mode, "Color")
        self._edit_hint = ("Click an image to select  ·  drag to move, handles to resize  ·  Delete removes  ·  "
                           "drop picture files onto a page to add them")
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self._refresh_hint()
        top_row.addWidget(self.hint)
        top_row.addStretch()

        def button(text, slot, tip=""):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setObjectName("secondary")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setEnabled(False)  # on once the pages are ready
        self.edit_btn.setToolTip("Move, resize, delete and add images" if self.can_edit else
                                 "Editing isn't available while the pages are shown on another paper size.")
        self.edit_btn.toggled.connect(self._set_edit)
        self.undo_btn = button("Undo", self._undo, "Undo (Ctrl + Z)")
        self.reset_btn = button("Reset", self._reset, "Back to the document as it was loaded")
        self.add_btn = button("Add image", self._add_clicked, "Add a picture (or drop files onto a page)")
        self.delete_btn = button("Delete image", self._delete_selected, "Delete the selected image (Delete)")
        self.crop_btn = button("Crop", self._start_crop, "Trim the selected image")
        self.apply_crop_btn = button("Apply crop", self._apply_crop, "Apply the crop (Enter)")
        self.cancel_crop_btn = button("Cancel crop", self._cancel_crop, "Cancel the crop (Esc)")

        self.position_btn = QPushButton("Position")
        self.position_btn.setObjectName("secondary")
        self.position_btn.setToolTip("Place the selected image on the page")
        pos_menu = QMenu(self.position_btn)
        for item in (("Top", "top"), ("Middle", "vcenter"), ("Bottom", "bottom"), None,
                     ("Left", "left"), ("Center", "hcenter"), ("Right", "right"), None,
                     ("Bring to front", "front"), ("Send to back", "back")):
            if item is None:
                pos_menu.addSeparator()
                continue
            act = pos_menu.addAction(item[0])
            act.triggered.connect(lambda checked=False, how=item[1]: self._arrange(how))
        self.position_btn.setMenu(pos_menu)

        self.pages_btn = QPushButton("Pages")
        self.pages_btn.setObjectName("secondary")
        self.pages_btn.setToolTip("Add or delete pages")
        pages_menu = QMenu(self.pages_btn)
        self.act_add_after = pages_menu.addAction("Add blank page")
        self.act_add_after.triggered.connect(lambda checked=False: self._add_page("after"))
        self.act_add_end = pages_menu.addAction("Add blank page at the end")
        self.act_add_end.triggered.connect(lambda checked=False: self._add_page("end"))
        pages_menu.addSeparator()
        self.act_del_page = pages_menu.addAction("Delete page")
        self.act_del_page.triggered.connect(lambda checked=False: self._delete_page())
        pages_menu.aboutToShow.connect(self._refresh_page_menu)
        self.pages_btn.setMenu(pages_menu)

        self.edit_tip = QLabel(self._edit_hint)
        self.edit_tip.setObjectName("muted")
        self.edit_tools = [self.undo_btn, self.reset_btn, self.add_btn, self.delete_btn, self.crop_btn,
                           self.position_btn, self.pages_btn, self.edit_tip]
        for w in self.edit_tools + [self.apply_crop_btn, self.cancel_crop_btn]:
            w.setVisible(False)
        export_btn = button("Export", self._export, "Save the document (with your edits) as a PDF")

        out_btn = QPushButton("−")
        out_btn.setObjectName("secondary")
        out_btn.setFixedWidth(40)
        out_btn.setToolTip("Zoom out (Ctrl + -)")
        out_btn.clicked.connect(lambda: self._zoom_by(1 / ZOOM_STEP))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setMinimumWidth(50)
        in_btn = QPushButton("+")
        in_btn.setObjectName("secondary")
        in_btn.setFixedWidth(40)
        in_btn.setToolTip("Zoom in (Ctrl + +)")
        in_btn.clicked.connect(lambda: self._zoom_by(ZOOM_STEP))
        reset_zoom_btn = QPushButton("100%")
        reset_zoom_btn.setObjectName("secondary")
        reset_zoom_btn.setToolTip("Reset zoom (Ctrl + 0)")
        reset_zoom_btn.clicked.connect(lambda: self._animate_zoom(1.0))
        close_btn = QPushButton("Close (Esc)")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.close)
        top_row.addWidget(self.edit_btn)
        top_row.addSpacing(8)
        top_row.addWidget(export_btn)
        top_row.addSpacing(12)
        for w in (out_btn, self.zoom_label, in_btn, reset_zoom_btn):
            top_row.addWidget(w)
        top_row.addSpacing(12)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)

        edit_row = QHBoxLayout()   # second row, only shown in edit mode
        for w in (self.undo_btn, self.reset_btn):
            edit_row.addWidget(w)
        edit_row.addSpacing(10)
        for w in (self.add_btn, self.delete_btn, self.crop_btn, self.position_btn):
            edit_row.addWidget(w)
        edit_row.addSpacing(10)
        edit_row.addWidget(self.pages_btn)
        edit_row.addWidget(self.apply_crop_btn)
        edit_row.addWidget(self.cancel_crop_btn)
        edit_row.addStretch()
        edit_row.addWidget(self.edit_tip)
        layout.addLayout(edit_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().installEventFilter(self)  # Ctrl + wheel zoom
        container = QWidget()
        container.setObjectName("stripContainer")
        strip_layout = QVBoxLayout(container)
        strip_layout.setSpacing(16)
        strip_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self.strip_layout = strip_layout
        self.page_labels = []
        for i in range(page_count):
            lbl = self._make_label(i)
            self.page_labels.append(lbl)
            strip_layout.addWidget(lbl)

        self.scroll.setWidget(container)

        # loading screen (shown until every page is rendered), then the pages
        loading = QWidget()
        loading.setObjectName("stripContainer")
        loading_layout = QVBoxLayout(loading)
        loading_layout.addStretch()
        self.loading_label = QLabel(f"Preparing preview... 0 of {page_count}")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        loading_layout.addWidget(self.loading_label)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, page_count)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedWidth(320)
        loading_layout.addWidget(self.loading_bar, 0, Qt.AlignmentFlag.AlignHCenter)
        loading_layout.addStretch()

        self.stack = QStackedWidget()
        self.stack.addWidget(loading)      # index 0
        self.stack.addWidget(self.scroll)  # index 1
        layout.addWidget(self.stack, 1)

        self._rerender_timer = QTimer(self)
        self._rerender_timer.setSingleShot(True)
        self._rerender_timer.timeout.connect(self._start_worker)

        self._start_worker()
        self.showFullScreen()

    # ---------------- rendering ----------------

    def _start_worker(self):
        if self.worker is not None:
            self.worker.abort = True
            self.worker.page_ready.disconnect(self._on_worker_page)
        self.worker = RenderAllPagesWorker(self.pdf_path, self.page_count, self.zoom, self.mode,
                                           order=self._render_order(), fit_paper=self.fit_paper)
        self.worker.page_ready.connect(self._on_worker_page)
        self._workers.append(self.worker)  # keep a reference until the dialog closes
        self.worker.start()

    def _quiesce(self):
        """Stop background rendering so the file can be changed. True if a render was still running."""
        running = any(w.isRunning() for w in self._workers)
        for w in self._workers:
            w.abort = True
        for w in self._workers:
            w.wait(5000)
        self.worker = None  # anything such a worker already queued is ignored in _on_worker_page
        if self.editor is not None:
            self.editor.state = self.included  # saved with the next undo step
        return running

    def _render_page_now(self, index):
        size = (int(STRIP_PAGE_SIZE[0] * self.zoom), int(STRIP_PAGE_SIZE[1] * self.zoom))
        img = render_pdf_page(self.pdf_path, index, max_size=size, scale=1.2 * self.zoom)
        self._on_page_ready(index, apply_preview_mode(img, self.mode), self.zoom)

    def _on_worker_page(self, index, img, zoom):
        if self.sender() is not self.worker:
            return  # a render that was stopped must not overwrite a page that was just edited
        self._on_page_ready(index, img, zoom)

    def _on_page_ready(self, index, img, zoom):
        if img is None:
            self.page_labels[index].setText(f"Page {index + 1}: preview unavailable")
        else:
            pm = pil_to_qpixmap(img)
            if self.included is not None and (index + 1) not in self.included:
                pm = _mark_not_included(pm)
            self._pixmaps[index] = (pm, zoom)
            self._show_page(index)

        self._seen.add(index)
        self.loading_bar.setValue(len(self._seen))
        self.loading_label.setText(f"Preparing preview... {len(self._seen)} of {self.page_count}")
        if len(self._seen) == self.page_count and self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.edit_btn.setEnabled(self.can_edit)

    def _render_order(self):
        """Pages closest to the middle of the screen first, so what you're looking at sharpens first."""
        if self.stack.currentIndex() == 0:
            return None
        y = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() / 2
        centre = min(range(self.page_count),
                     key=lambda i: abs(self.page_labels[i].y() + self.page_labels[i].height() / 2 - y))
        return sorted(range(self.page_count), key=lambda i: abs(i - centre))

    def _page_at(self, y):
        """The page under content row y, and how far down it (0 = top, 1 = bottom)."""
        best, best_d = 0, None
        for i, lbl in enumerate(self.page_labels):
            top, bottom = lbl.y(), lbl.y() + lbl.height()
            if top <= y < bottom + 16:  # include the gap below the page
                return i, (y - top) / max(1, lbl.height())
            d = min(abs(y - top), abs(y - bottom))
            if best_d is None or d < best_d:
                best, best_d = i, d
        lbl = self.page_labels[best]
        return best, (y - lbl.y()) / max(1, lbl.height())

    def _show_page(self, index):
        pm, _ = self._pixmaps[index]
        lbl = self.page_labels[index]
        lbl.setPixmap(pm)
        lbl.setText("")
        self._size_page(index)

    def _size_page(self, index):
        pm, rendered_zoom = self._pixmaps[index]
        ratio = self.zoom / rendered_zoom  # old image is stretched until the sharp one arrives
        self.page_labels[index].setFixedSize(QSize(round(pm.width() * ratio), round(pm.height() * ratio)))

    # ---------------- editing ----------------

    def edited_path(self):
        """The edited PDF if anything was changed, else None."""
        return self.editor.path if self.editor is not None and self.editor.dirty else None

    def _ensure_editor(self):
        if self.editor is None:
            running = self._quiesce()
            self.editor = PdfEditor(self.pdf_path, self.original_path)  # works on a copy, never the original
            self.pdf_path = self.editor.path
            self.editor.state = self.included
            if running:
                self._start_worker()

    def _set_edit(self, on):
        if on:
            if not self.can_edit or self.stack.currentIndex() == 0:
                self.edit_btn.setChecked(False)
                return
            try:
                self._ensure_editor()
            except Exception as e:
                QMessageBox.warning(self, "Can't edit", f"Couldn't open the document for editing:\n{e}")
                self.edit_btn.setChecked(False)
                return
        self._cancel_crop()
        self.edit_mode = on
        for w in self.edit_tools:
            w.setVisible(on)
        for lbl in self.page_labels:
            lbl.edit = on
            lbl.invalidate()
            if not on:
                lbl.unsetCursor()
        self._update_edit_buttons()

    def _items_for(self, index):
        if self.editor is None:
            return []
        try:
            infos, (pw, ph) = self.editor.images_on_page(index)
        except Exception:
            return []
        self._page_pts[index] = (pw, ph)
        return [{"xref": i["xref"], "locked": i["locked"],
                 "rect": QRectF(i["rect"][0] / pw, i["rect"][1] / ph,
                                (i["rect"][2] - i["rect"][0]) / pw, (i["rect"][3] - i["rect"][1]) / ph)}
                for i in infos]

    def _selected_label(self):
        return next((l for l in self.page_labels if l.sel >= 0), None)

    def _update_edit_buttons(self):
        cropping = self._crop_label is not None
        has_sel = self._selected_label() is not None
        self.undo_btn.setEnabled(self.editor is not None and self.editor.can_undo and not cropping)
        self.delete_btn.setEnabled(has_sel and not cropping)
        self.crop_btn.setEnabled(has_sel and not cropping)
        self.position_btn.setEnabled(has_sel and not cropping)
        for w in (self.reset_btn, self.add_btn, self.pages_btn):
            w.setEnabled(not cropping)
        self.apply_crop_btn.setVisible(cropping)
        self.cancel_crop_btn.setVisible(cropping)

    def _on_selection(self):
        src = self.sender()
        for l in self.page_labels:
            if l is not src and l.sel >= 0:
                l.sel = -1
                l.update()
        self._update_edit_buttons()

    def _edit_page(self, page, op, select_last=False):
        """Run one edit on a page, then show the result. `op` may return the image's new rect (points)."""
        running = self._quiesce()
        result = None
        try:
            result = op()
        except Exception as e:
            QMessageBox.warning(self, "Couldn't edit", str(e))
        lbl = self.page_labels[page]
        lbl.invalidate()
        items = lbl.items()  # also refreshes the page size
        if isinstance(result, tuple) and len(result) == 4 and page in self._page_pts:
            pw, ph = self._page_pts[page]
            lbl.select_near(QRectF(result[0] / pw, result[1] / ph,
                                   (result[2] - result[0]) / pw, (result[3] - result[1]) / ph))
        elif select_last:
            lbl.sel = len(items) - 1
        self._render_page_now(page)
        if running:
            self._start_worker()  # finish sharpening the other pages
        self._update_edit_buttons()

    def _on_changed(self, page, idx, nrect):
        item = self.page_labels[page].items()[idx]
        pw, ph = self._page_pts[page]
        rect = (nrect.x() * pw, nrect.y() * ph, nrect.right() * pw, nrect.bottom() * ph)
        self._edit_page(page, lambda: self.editor.move_resize(page, idx, item["xref"], rect))

    def _delete_selected(self):
        lbl = self._selected_label()
        if lbl is None:
            return
        page, idx = lbl.index, lbl.sel
        xref = lbl.items()[idx]["xref"]
        self._edit_page(page, lambda: self.editor.delete_image(page, idx, xref))

    def _add_images(self, page, paths, point):
        self.page_labels[page].items()  # makes sure the page size is known
        pw, ph = self._page_pts.get(page, (612.0, 792.0))

        def op():
            for n, path in enumerate(paths):
                self.editor.add_image(page, path, (point.x() * pw + 16 * n, point.y() * ph + 16 * n))
        self._edit_page(page, op, select_last=True)

    def _add_clicked(self):
        exts = " ".join(f"*{e}" for e in IMAGE_EXTS)
        paths, _ = QFileDialog.getOpenFileNames(self, "Add images", "", f"Images ({exts})")
        if not paths:
            return
        y = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() / 2
        page, frac = self._page_at(y)  # the page in the middle of the screen
        self._add_images(page, paths, QPointF(0.5, min(max(frac, 0.15), 0.85)))

    def _on_dropped(self, page, paths, point):
        if not self.edit_mode:
            self.edit_btn.setChecked(True)  # dropping a picture switches Edit on
            if not self.edit_mode:
                return
        self._add_images(page, paths, point)

    # ---------------- crop and position ----------------

    def _start_crop(self):
        lbl = self._selected_label()
        if lbl is not None and lbl.start_crop():
            self._crop_label = lbl
            self._update_edit_buttons()

    def _cancel_crop(self):
        if self._crop_label is not None:
            self._crop_label.cancel_crop()
            self._crop_label = None
        self._update_edit_buttons()

    def _apply_crop(self):
        lbl = self._crop_label
        if lbl is None:
            return
        box = lbl.crop_fractions()
        page, idx = lbl.index, lbl.sel
        xref = lbl.items()[idx]["xref"]
        self._cancel_crop()
        if box is not None:
            self._edit_page(page, lambda: self.editor.crop_image(page, idx, xref, box))

    def _arrange(self, how):
        lbl = self._selected_label()
        if lbl is None:
            return
        page, idx = lbl.index, lbl.sel
        xref = lbl.items()[idx]["xref"]
        self._edit_page(page, lambda: self.editor.arrange(page, idx, xref, how))

    # ---------------- pages ----------------

    def _make_label(self, i):
        lbl = PageLabel(i, self._items_for, f"Loading page {i + 1}...")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setScaledContents(True)  # Qt stretches the page smoothly, and only for pages on screen
        lbl.setMinimumHeight(200)
        lbl.accept_files = self.can_edit
        lbl.edit = self.edit_mode
        lbl.changed.connect(self._on_changed)
        lbl.dropped.connect(self._on_dropped)
        lbl.selection_changed.connect(self._on_selection)
        return lbl

    def _refresh_hint(self):
        text = f"{self._mode_name} preview  ·  Ctrl + scroll to zoom"
        if self.included:
            text += f"  ·  Printing pages {format_page_ranges(self.included)}"
        if self.fit_paper:
            text += f"  ·  Shown on {self.fit_paper['name']}"
        self.hint.setText(text)

    def _current_page(self):
        """The page in the middle of the screen."""
        y = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() / 2
        return self._page_at(y)[0]

    def _refresh_page_menu(self):
        n = self._current_page() + 1
        self.act_add_after.setText(f"Add blank page after page {n}")
        self.act_del_page.setText(f"Delete page {n}")
        self.act_del_page.setEnabled(self.page_count > 1)

    def _reindex(self):
        for i, lbl in enumerate(self.page_labels):
            lbl.index = i
            lbl.invalidate()
        self._page_pts.clear()

    def _insert_label(self, n):
        lbl = self._make_label(n)
        self.page_labels.insert(n, lbl)
        self.strip_layout.insertWidget(n, lbl)
        self._pixmaps = {(i + 1 if i >= n else i): v for i, v in self._pixmaps.items()}
        self.page_count += 1
        if self.included is not None:  # the new page prints, the later ones move down a number
            self.included = {p + 1 if p >= n + 1 else p for p in self.included} | {n + 1}
        self._reindex()

    def _remove_label(self, k):
        lbl = self.page_labels.pop(k)
        self.strip_layout.removeWidget(lbl)
        lbl.hide()
        lbl.deleteLater()
        self._pixmaps = {(i - 1 if i > k else i): v for i, v in self._pixmaps.items() if i != k}
        self.page_count -= 1
        if self.included is not None:
            self.included = {p - 1 if p > k + 1 else p for p in self.included if p != k + 1} or None
        self._reindex()

    def _rebuild_labels(self, count):
        for lbl in self.page_labels:
            self.strip_layout.removeWidget(lbl)
            lbl.hide()
            lbl.deleteLater()
        self.page_count = count
        self.page_labels = [self._make_label(i) for i in range(count)]
        for lbl in self.page_labels:
            self.strip_layout.addWidget(lbl)
        self._pixmaps.clear()
        self._page_pts.clear()

    def _finish_page_edit(self, running, focus):
        if running:
            self._start_worker()
        self._refresh_hint()
        self._update_edit_buttons()
        QTimer.singleShot(0, lambda: self._scroll_to_page(focus))

    def _scroll_to_page(self, index):
        if self.page_labels:  # the list may have been rebuilt since this was queued
            self.scroll.ensureWidgetVisible(self.page_labels[min(index, len(self.page_labels) - 1)])

    def _add_page(self, where):
        page = self.page_count - 1 if where == "end" else self._current_page()
        running = self._quiesce()
        try:
            self.editor.add_page(page)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't add the page", str(e))
            if running:
                self._start_worker()
            return
        self._insert_label(page + 1)
        self._render_page_now(page + 1)
        self._finish_page_edit(running, page + 1)

    def _delete_page(self):
        if self.page_count <= 1:
            return
        page = self._current_page()
        running = self._quiesce()
        try:
            self.editor.delete_page(page)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't delete the page", str(e))
            if running:
                self._start_worker()
            return
        self._remove_label(page)
        self._finish_page_edit(running, page)

    # ---------------- history ----------------

    def _after_history(self):
        self.included = self.editor.state  # Undo brings back the pages-to-print that went with that step
        count = self.editor.page_count()
        if count != self.page_count:
            self._rebuild_labels(count)
        for lbl in self.page_labels:
            lbl.invalidate()
        self._refresh_hint()
        self._start_worker()
        self._update_edit_buttons()

    def _undo(self):
        if self.editor is None or not self.editor.can_undo:
            return
        self._quiesce()
        self.editor.undo()
        self._after_history()

    def _reset(self):
        if self.editor is None:
            return
        self._quiesce()
        before = self.page_count
        self.editor.reset()
        if self.editor.page_count() != before:
            self.editor.state = None  # the page numbers no longer line up: print all pages
        self._after_history()

    def _export(self):
        pages = None
        if self.included and len(self.included) < self.page_count:
            box = QMessageBox(self)
            box.setWindowTitle("Export")
            box.setText(f"Export only the {len(self.included)} selected pages, or all {self.page_count}?")
            only_btn = box.addButton("Selected pages", QMessageBox.ButtonRole.AcceptRole)
            all_btn = box.addButton("All pages", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is only_btn:
                pages = sorted(self.included)
            elif box.clickedButton() is not all_btn:
                return
        edited = self.editor is not None and self.editor.dirty
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", f"{self.doc_name}{'-edited' if edited else ''}.pdf", "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            export_pdf(self.pdf_path, path, pages)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't export", f"{e}\n\nTry a different file name.")
            return
        QMessageBox.information(self, "Exported", f"Saved to:\n{path}")

    # ---------------- zoom ----------------

    def _zoom_by(self, factor):
        base = self._anim_target if self._anim is not None else self.zoom
        self._animate_zoom(base * factor)

    def _animate_zoom(self, target):
        """Glide to the target zoom (buttons and keyboard)."""
        target = max(ZOOM_MIN, min(self.max_zoom, target))
        if self.stack.currentIndex() == 0 or abs(target - self.zoom) < 1e-4:
            return
        if self._anim is not None:
            self._anim.stop()
        start = self.zoom
        self._anim_target = target
        anim = QVariantAnimation(self)
        anim.setDuration(ZOOM_ANIM_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # interpolate in log space so zooming in and out feel the same speed
        anim.valueChanged.connect(lambda t: self._apply_zoom(start * (target / start) ** t))
        anim.finished.connect(lambda: setattr(self, "_anim", None))
        self._anim = anim
        anim.start()

    def _set_zoom(self, z):
        self._apply_zoom(z)

    def _apply_zoom(self, z, anchor=None):
        """Zoom immediately, keeping the point under `anchor` (viewport coordinates) where it is."""
        z = max(ZOOM_MIN, min(self.max_zoom, z))
        if abs(z - self.zoom) < 1e-4 or self.stack.currentIndex() == 0:
            return
        vp = self.scroll.viewport()
        bv, bh = self.scroll.verticalScrollBar(), self.scroll.horizontalScrollBar()
        if anchor is None:
            anchor = QPoint(vp.width() // 2, vp.height() // 2)
        ratio = z / self.zoom
        cx = bh.value() + anchor.x()
        idx, frac = self._page_at(bv.value() + anchor.y())  # the page spot under the anchor
        had_h_scroll = bh.maximum() > 0

        self.zoom = z
        self.zoom_label.setText(f"{round(z * 100)}%")
        for i in self._pixmaps:
            self._size_page(i)

        container = self.scroll.widget()
        container.layout().activate()  # update the scroll range right now, not on the next event loop turn
        QCoreApplication.sendPostedEvents(container, QEvent.Type.LayoutRequest)
        QCoreApplication.sendPostedEvents(vp, QEvent.Type.LayoutRequest)

        lbl = self.page_labels[idx]
        bv.setValue(round(lbl.y() + frac * lbl.height() - anchor.y()))
        if had_h_scroll:
            bh.setValue(round(cx * ratio - anchor.x()))
        else:
            bh.setValue(bh.maximum() // 2)  # pages are centred, so stay centred
        self._rerender_timer.start(250)  # sharp re-render once the user stops zooming

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self._anim is not None:
                self._anim.stop()
                self._anim = None
            vp = self.scroll.viewport()
            anchor = vp.mapFromGlobal(event.globalPosition().toPoint())  # zoom around the mouse pointer
            self._apply_zoom(self.zoom * WHEEL_SENSITIVITY ** event.angleDelta().y(), anchor)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        key = event.key()
        if self._crop_label is not None and key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if key == Qt.Key.Key_Escape:
                self._cancel_crop()
            else:
                self._apply_crop()
        elif key == Qt.Key.Key_Escape:
            sel = self._selected_label() if self.edit_mode else None
            if sel is not None:  # first Esc just deselects
                sel.sel = -1
                sel.update()
                self._update_edit_buttons()
            else:
                self.close()
        elif self.edit_mode and self._crop_label is None and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected()
        elif self.edit_mode and self._crop_label is None and ctrl and key == Qt.Key.Key_Z:
            self._undo()
        elif ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_by(ZOOM_STEP)
        elif ctrl and key == Qt.Key.Key_Minus:
            self._zoom_by(1 / ZOOM_STEP)
        elif ctrl and key == Qt.Key.Key_0:
            self._animate_zoom(1.0)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._rerender_timer.stop()
        if self._anim is not None:
            self._anim.stop()
        for w in self._workers:
            w.abort = True
        for w in self._workers:
            w.quit()
            w.wait(1000)
        if self.editor is not None:
            self.editor.release()  # the undo copies; the edited file stays for the app to pick up
        super().closeEvent(event)