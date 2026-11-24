import os

from PyQt6.QtCore import Qt, QPointF, QRectF, QSize, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from printcost.core import docscan
from printcost.core.analyzer import IMAGE_EXTS
from printcost.ui.modal import ModalDialog
from printcost.ui.preview_widget import pil_to_qpixmap
from printcost.ui.style import BORDER, MAGENTA, MUTED, PREVIEW_BG

PAD = 24
GRAB_PX = 20
LOUPE = 130
LOUPE_SOURCE_PX = 46
THUMB = QSize(72, 92)
PICK_FILTER = "Pictures (" + " ".join(f"*{e}" for e in IMAGE_EXTS) + ")"
FILTER_CSS = (f"QPushButton {{ text-align: left; padding: 8px 12px; border: 1px solid {BORDER}; "
              f"border-radius: 8px; background: white; }} "
              f"QPushButton:checked {{ border: 2px solid {MAGENTA}; font-weight: 700; }}")
MODE_CSS = (f"QPushButton {{ padding: 6px 16px; border: 1px solid {BORDER}; background: white; }} "
            f"QPushButton:checked {{ background: {MAGENTA}; color: white; border-color: {MAGENTA}; }}")


class ScanPage:
    def __init__(self, name, src, corners, found, filter_name):
        self.name = name
        self.src = src
        self.prev = docscan.preview_source(src)
        self.corners = [tuple(c) for c in corners]
        self.found = found
        self.turns = 0
        self.filter = filter_name
        self.brightness = 0
        self.contrast = 0
        self.prev_pix = pil_to_qpixmap(self.prev)
        self.result_pix = None


class _LoadWorker(QThread):
    page_ready = pyqtSignal(object)
    page_failed = pyqtSignal(str, str)

    def __init__(self, items, filter_name):
        super().__init__()
        self._items = items
        self._filter = filter_name
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        for item in self._items:
            if self._stop:
                return
            name = f"Page {item[2] + 1}" if isinstance(item, tuple) else os.path.basename(item)
            try:
                if isinstance(item, tuple):
                    src = docscan.render_pdf_page(item[1], item[2])
                    corners, found, filter_name = docscan.default_corners(0.0), True, "original"
                else:
                    src = docscan.load_image(item)
                    corners, found = docscan.detect_corners(docscan.preview_source(src))
                    filter_name = self._filter
                self.page_ready.emit(("page", name, src, corners, found, filter_name))
            except Exception as e:
                self.page_failed.emit(name, str(e))


class _ExportWorker(QThread):
    progress = pyqtSignal(int)
    saved = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, jobs, page_size, dest):
        super().__init__()
        self._jobs = jobs
        self._page_size = page_size
        self._dest = dest
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            pages = []
            for n, (src, corners, turns, name, brightness, contrast) in enumerate(self._jobs):
                if self._stop:
                    return
                pages.append((docscan.process(src, corners, turns, name, brightness, contrast), name))
                self.progress.emit(n + 1)
            os.makedirs(os.path.dirname(self._dest), exist_ok=True)
            docscan.build_pdf(pages, self._dest, self._page_size)
            self.saved.emit(self._dest)
        except Exception as e:
            self.failed.emit(str(e))


class CornerCanvas(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(460, 460)
        self.setMouseTracking(True)
        self.corners = docscan.default_corners()
        self.mode = "crop"
        self._source = None
        self._result = None
        self._drag = None

    def set_content(self, source, result, corners, mode):
        self._source, self._result, self.corners, self.mode = source, result, corners, mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode == "crop" else Qt.CursorShape.ArrowCursor)
        self.update()

    def _shown(self):
        return self._source if self.mode == "crop" else self._result

    def _rect(self):
        pix = self._shown()
        area = QRectF(self.rect()).adjusted(PAD, PAD, -PAD, -PAD)
        if pix is None or pix.width() == 0:
            return area
        k = min(area.width() / pix.width(), area.height() / pix.height())
        w, h = pix.width() * k, pix.height() * k
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def _pt(self, c, rect):
        return QPointF(rect.x() + c[0] * rect.width(), rect.y() + c[1] * rect.height())

    def _handles(self, rect):
        pts = [self._pt(c, rect) for c in self.corners]
        mids = [QPointF((pts[i].x() + pts[(i + 1) % 4].x()) / 2, (pts[i].y() + pts[(i + 1) % 4].y()) / 2)
                for i in range(4)]
        return pts + mids

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(PREVIEW_BG))
        pix = self._shown()
        rect = self._rect()
        if pix is None:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Add photos to start")
            return
        p.drawPixmap(rect, pix, QRectF(pix.rect()))
        if self.mode != "crop":
            return
        poly = QPolygonF([self._pt(c, rect) for c in self.corners])
        outer = QPainterPath()
        outer.addRect(rect)
        inner = QPainterPath()
        inner.addPolygon(poly)
        inner.closeSubpath()
        p.fillPath(outer.subtracted(inner), QColor(15, 27, 45, 140))
        p.setPen(QPen(QColor(MAGENTA), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(poly)
        for i, pt in enumerate(self._handles(rect)):
            r = 9 if i < 4 else 6
            p.setPen(QPen(QColor(MAGENTA), 2))
            p.setBrush(QColor("white"))
            p.drawEllipse(pt, r, r)
        if self._drag is not None:
            self._paint_loupe(p, pix, rect)

    def _paint_loupe(self, p, pix, rect):
        hid = self._drag[0]
        if hid < 4:
            c = self.corners[hid]
        else:
            a, b = self.corners[hid - 4], self.corners[(hid - 3) % 4]
            c = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        at = self._pt(c, rect)
        x = 8 if at.x() > LOUPE + 40 or at.y() > LOUPE + 40 else self.width() - LOUPE - 8
        box = QRectF(x, 8, LOUPE, LOUPE)
        half = LOUPE_SOURCE_PX / 2
        cx, cy = c[0] * pix.width(), c[1] * pix.height()
        p.setBrush(QColor("white"))
        p.setPen(QPen(QColor(MAGENTA), 2))
        p.drawRect(box)
        p.drawPixmap(box, pix, QRectF(cx - half, cy - half, LOUPE_SOURCE_PX, LOUPE_SOURCE_PX))
        p.setPen(QPen(QColor(MAGENTA), 1))
        p.drawLine(QPointF(box.center().x(), box.top()), QPointF(box.center().x(), box.bottom()))
        p.drawLine(QPointF(box.left(), box.center().y()), QPointF(box.right(), box.center().y()))

    def mousePressEvent(self, event):
        if self.mode != "crop" or self._source is None or event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self._rect()
        pos = event.position()
        best, best_d = None, GRAB_PX
        for i, pt in enumerate(self._handles(rect)):
            d = ((pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2) ** 0.5
            if d < best_d:
                best, best_d = i, d
        if best is not None:
            self._drag = (best, pos, [tuple(c) for c in self.corners])
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        rect = self._rect()
        hid, start, origin = self._drag
        dx = (event.position().x() - start.x()) / rect.width()
        dy = (event.position().y() - start.y()) / rect.height()
        moved = {hid} if hid < 4 else {hid - 4, (hid - 3) % 4}
        new = [(min(1.0, max(0.0, c[0] + dx)), min(1.0, max(0.0, c[1] + dy))) if i in moved else c
               for i, c in enumerate(origin)]
        if docscan.is_convex(new):
            self.corners[:] = new
            self.changed.emit()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.update()


class DocScanDialog(ModalDialog):
    fill = True

    def __init__(self, parent, items, paper=None):
        super().__init__(parent)
        self.setWindowTitle("Scan documents")
        self._paper = paper
        self.result_path = None
        self.pages = []
        self._workers = []
        self._exporter = None
        self._mode = "crop"
        self._filter = "enhance"
        self._build()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._refresh)
        self.add_items(items)

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 14)
        root.setSpacing(12)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setIconSize(THUMB)
        self.list.setFixedWidth(150)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        for text, slot in (("Add photos…", self._browse), ("Move up", lambda: self._move(-1)),
                           ("Move down", lambda: self._move(1)), ("Remove", self._remove)):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.clicked.connect(slot)
            left.addWidget(b)
        root.addLayout(left)

        mid = QVBoxLayout()
        self.canvas = CornerCanvas()
        self.canvas.changed.connect(self._corners_changed)
        mid.addWidget(self.canvas, 1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        mid.addWidget(self.status)
        bar = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for key, text in (("crop", "Crop"), ("result", "Result")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(key == "crop")
            b.setStyleSheet(MODE_CSS)
            b.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            self.mode_group.addButton(b)
            bar.addWidget(b)
        bar.addSpacing(10)
        for text, slot in (("Auto detect", self._auto), ("Reset corners", self._reset),
                           ("Rotate left", lambda: self._rotate(-1)), ("Rotate right", lambda: self._rotate(1))):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        mid.addLayout(bar)
        root.addLayout(mid, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        title = QLabel("Filter")
        title.setStyleSheet("font-weight: 700;")
        right.addWidget(title)
        self.filter_group = QButtonGroup(self)
        self.filter_btns = {}
        for key, text in docscan.FILTERS:
            b = QPushButton(text.replace("&", "&&"))
            b.setCheckable(True)
            b.setStyleSheet(FILTER_CSS)
            b.clicked.connect(lambda _=False, k=key: self._pick_filter(k))
            self.filter_group.addButton(b)
            self.filter_btns[key] = b
            right.addWidget(b)
        self.bright = self._slider("Brightness", right)
        self.contrast = self._slider("Contrast", right)
        for s in (self.bright, self.contrast):
            s.valueChanged.connect(self._adjust_changed)
        all_btn = QPushButton("Apply to all pages")
        all_btn.setObjectName("secondary")
        all_btn.setToolTip("Give every page this filter, brightness and contrast.")
        all_btn.clicked.connect(self._apply_all)
        right.addWidget(all_btn)
        right.addSpacing(8)
        right.addWidget(QLabel("Page size"))
        self.size_combo = QComboBox()
        if self._paper:
            self.size_combo.addItem(f"Current paper ({self._paper['name']})",
                                    (self._paper["width_in"] * 72.0, self._paper["height_in"] * 72.0))
        for key, text, _ in docscan.PAGE_SIZES:
            self.size_combo.addItem(text, key)
        right.addWidget(self.size_combo)
        right.addStretch(1)
        self.progress = QProgressBar()
        self.progress.hide()
        right.addWidget(self.progress)
        self.save_btn = QPushButton("Use scan")
        self.save_btn.clicked.connect(self._save)
        right.addWidget(self.save_btn)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        right.addWidget(cancel)
        holder = QWidget()
        holder.setFixedWidth(210)
        holder.setLayout(right)
        right.setContentsMargins(0, 0, 0, 0)
        root.addWidget(holder)
        self.filter_btns[self._filter].setChecked(True)

    def _slider(self, text, layout):
        layout.addWidget(QLabel(text))
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(-100, 100)
        s.setValue(0)
        layout.addWidget(s)
        return s

    def _page(self):
        row = self.list.currentRow()
        return self.pages[row] if 0 <= row < len(self.pages) else None

    def add_items(self, items):
        items = [i for i in items if isinstance(i, tuple) or i.lower().endswith(IMAGE_EXTS)]
        if not items:
            return
        worker = _LoadWorker(items, self._filter)
        worker.page_ready.connect(self._page_ready)
        worker.page_failed.connect(self._page_failed)
        worker.finished.connect(self._loading_done)
        self._workers.append(worker)
        self.status.setText(f"Loading {len(items)} page(s)…")
        worker.start()

    def _page_ready(self, data):
        _, name, src, corners, found, filter_name = data
        page = ScanPage(name, src, corners, found, filter_name)
        self.pages.append(page)
        item = QListWidgetItem(str(len(self.pages)))
        item.setToolTip(name)
        self.list.addItem(item)
        self._thumb(len(self.pages) - 1, page)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def _page_failed(self, name, msg):
        QMessageBox.warning(self, "Couldn't open the photo", f"{name}\n\n{msg}")

    def _loading_done(self):
        self._workers = [w for w in self._workers if w.isRunning()]
        if not self._workers:
            self._update_status()

    def _thumb(self, row, page):
        out = docscan.process(page.prev, page.corners, page.turns, page.filter, page.brightness, page.contrast,
                              max_side=240)
        out.thumbnail((THUMB.width() * 2, THUMB.height() * 2))
        self.list.item(row).setIcon(QIcon(pil_to_qpixmap(out)))

    def _renumber(self):
        for i in range(self.list.count()):
            self.list.item(i).setText(str(i + 1))

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add photos", "", PICK_FILTER)
        self.add_items(paths)

    def _move(self, delta):
        row = self.list.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < len(self.pages):
            return
        self.pages[row], self.pages[new] = self.pages[new], self.pages[row]
        item = self.list.takeItem(row)
        self.list.insertItem(new, item)
        self.list.setCurrentRow(new)
        self._renumber()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        del self.pages[row]
        self.list.takeItem(row)
        self._renumber()
        if not self.pages:
            self.canvas.set_content(None, None, docscan.default_corners(), self._mode)
            self.status.setText("Add photos to start.")

    def _select(self, row):
        page = self._page()
        if page is None:
            return
        for w in (self.bright, self.contrast):
            w.blockSignals(True)
        self.bright.setValue(page.brightness)
        self.contrast.setValue(page.contrast)
        for w in (self.bright, self.contrast):
            w.blockSignals(False)
        self.filter_btns[page.filter].setChecked(True)
        self._refresh()
        self._update_status()

    def _update_status(self):
        page = self._page()
        if page is None:
            return
        if self._mode == "crop":
            self.status.setText("Drag the circles to match the page edges." if page.found else
                                "Couldn't find the page edges. Drag the circles onto the corners of the page.")
        else:
            self.status.setText("Preview of the finished page.")

    def _set_mode(self, mode):
        self._mode = mode
        self._refresh()
        self._update_status()

    def _corners_changed(self):
        page = self._page()
        if page is not None:
            page.found = True
            self._timer.start()

    def _schedule(self):
        self._timer.start()

    def _refresh(self):
        page = self._page()
        if page is None:
            return
        out = docscan.process(page.prev, page.corners, page.turns, page.filter, page.brightness, page.contrast,
                              max_side=docscan.PREVIEW_MAX_SIDE)
        page.result_pix = pil_to_qpixmap(out)
        small = out.copy()
        small.thumbnail((THUMB.width() * 2, THUMB.height() * 2))
        self.list.item(self.list.currentRow()).setIcon(QIcon(pil_to_qpixmap(small)))
        self.canvas.set_content(page.prev_pix, page.result_pix, page.corners, self._mode)

    def _auto(self):
        page = self._page()
        if page is None:
            return
        corners, found = docscan.detect_corners(page.prev)
        page.corners[:] = [tuple(c) for c in corners]
        page.found = found
        self._set_mode("crop")
        self._buttons_mode("crop")
        self.status.setText("Page edges found. Adjust the circles if needed." if found else
                            "Couldn't find the page edges. Drag the circles onto the corners of the page.")

    def _reset(self):
        page = self._page()
        if page is None:
            return
        page.corners[:] = docscan.default_corners(0.0)
        page.found = True
        self._set_mode("crop")
        self._buttons_mode("crop")

    def _buttons_mode(self, mode):
        for b in self.mode_group.buttons():
            b.setChecked(b.text().lower() == mode)

    def _rotate(self, direction):
        page = self._page()
        if page is None:
            return
        page.turns = (page.turns + direction) % 4
        self._buttons_mode("result")
        self._set_mode("result")

    def _pick_filter(self, key):
        self._filter = key
        page = self._page()
        if page is None:
            return
        page.filter = key
        self._timer.start()

    def _adjust_changed(self):
        page = self._page()
        if page is None:
            return
        page.brightness, page.contrast = self.bright.value(), self.contrast.value()
        self._timer.start()

    def _apply_all(self):
        page = self._page()
        if page is None:
            return
        for other in self.pages:
            other.filter, other.brightness, other.contrast = page.filter, page.brightness, page.contrast
        for row, other in enumerate(self.pages):
            self._thumb(row, other)
        self.status.setText(f"Applied to all {len(self.pages)} page(s).")

    def _save(self):
        if not self.pages:
            QMessageBox.information(self, "Nothing to save", "Add at least one photo first.")
            return
        if any(w.isRunning() for w in self._workers):
            QMessageBox.information(self, "Still loading", "Wait for the photos to finish loading.")
            return
        jobs = [(p.src, p.corners, p.turns, p.filter, p.brightness, p.contrast) for p in self.pages]
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.show()
        self.save_btn.setEnabled(False)
        self.status.setText("Saving…")
        self._exporter = _ExportWorker(jobs, self.size_combo.currentData(), docscan.new_scan_path())
        self._exporter.progress.connect(self.progress.setValue)
        self._exporter.saved.connect(self._saved)
        self._exporter.failed.connect(self._save_failed)
        self._exporter.start()

    def _saved(self, path):
        self.result_path = path
        self.accept()

    def _save_failed(self, msg):
        self.progress.hide()
        self.save_btn.setEnabled(True)
        self.status.setText("")
        QMessageBox.warning(self, "Couldn't save the scan", msg)

    def _stop_threads(self):
        for w in self._workers + ([self._exporter] if self._exporter else []):
            w.stop()
            w.wait(5000)

    def done(self, code):
        self._stop_threads()
        super().done(code)