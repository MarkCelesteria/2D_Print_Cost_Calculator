"""Edits the images of a PDF: move, resize, delete, add. Works on a private copy, never the original."""
import contextlib
import io
import os
import shutil

import fitz
from PIL import Image, ImageOps

from analyzer import make_temp_dir

MAX_UNDO = 20
MIN_SIZE = 8.0
EPS = 1e-3
MARGIN = 18.0


class EditError(Exception):
    pass


def _encode_image(path):
    """Bytes PyMuPDF can embed. Keeps transparency (PNG) when the picture has it, otherwise JPEG."""
    img = ImageOps.exif_transpose(Image.open(path))
    img.thumbnail((2400, 2400), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    buf = io.BytesIO()
    if has_alpha:
        img.convert("RGBA").save(buf, "PNG")
    else:
        img.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue(), img.width, img.height


def export_pdf(src, dest, pages=None):
    """Save a copy of a PDF. `pages` = 1-based page numbers to keep (None = all)."""
    with fitz.open(src) as doc:
        if pages:
            doc.select([p - 1 for p in sorted(pages)])
        doc.save(dest, garbage=3, deflate=True)


class PdfEditor:
    def __init__(self, source_path, original_path=None):
        self._source = source_path
        self.original = original_path or source_path   # what Reset goes back to
        self.dir = make_temp_dir()
        self.path = os.path.join(self.dir, "edited.pdf")
        shutil.copyfile(source_path, self.path)
        self._undo = []     # (snapshot file, state) pairs
        self._n = 0
        self._edits = 0
        self.state = None   # anything the caller wants restored together with an undo

    # ---------------- state ----------------

    @property
    def dirty(self):
        return self._edits != 0

    @property
    def can_undo(self):
        return bool(self._undo)

    def release(self):
        """Drop the undo copies (the edited file itself stays)."""
        for p, _ in self._undo:
            try:
                os.remove(p)
            except OSError:
                pass
        self._undo.clear()

    # ---------------- reading ----------------

    def page_count(self):
        with fitz.open(self.path) as doc:
            return len(doc)

    def images_on_page(self, index):
        """([{'xref', 'rect': (x0, y0, x1, y1) in points, 'locked'}], (page_w, page_h)). Last = drawn on top."""
        with fitz.open(self.path) as doc:
            page = doc[index]
            size = (page.rect.width, page.rect.height)
            if page.rotation:
                return [], size
            return self._infos(page), size

    @staticmethod
    def _infos(page):
        out = []
        for info in page.get_image_info(xrefs=True):
            r = fitz.Rect(info["bbox"])
            if r.is_empty or r.width < 2 or r.height < 2 or not r.intersects(page.rect):
                continue
            a, b, c, d = info["transform"][:4]
            upright = abs(b) < EPS and abs(c) < EPS and a > 0 and d > 0
            out.append({"xref": info["xref"], "rect": (r.x0, r.y0, r.x1, r.y1),
                        "locked": info["xref"] <= 0 or not upright})
        return out

    # ---------------- editing ----------------

    def _push(self):
        snap = os.path.join(self.dir, f"undo{self._n}.pdf")
        self._n += 1
        shutil.copyfile(self.path, snap)
        self._undo.append((snap, self.state))
        if len(self._undo) > MAX_UNDO:
            os.remove(self._undo.pop(0)[0])
        self._edits += 1

    def _drop_last(self):
        try:
            os.remove(self._undo.pop()[0])
        except (IndexError, OSError):
            pass
        self._edits -= 1

    @contextlib.contextmanager
    def _edit(self):
        """Snapshot for undo, hand out the document, save it back. A failed edit leaves the file as it was."""
        self._push()
        doc = fitz.open(self.path)
        tmp = self.path + ".tmp"
        try:
            yield doc
            doc.save(tmp, garbage=1, deflate=True)
        except Exception:
            doc.close()
            self._drop_last()
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        doc.close()
        os.replace(tmp, self.path)

    @staticmethod
    def _page(doc, index):
        page = doc[index]
        if page.rotation:
            raise EditError("Rotated pages can't be edited.")
        return page

    @staticmethod
    def _target(page, image_idx, xref):
        infos = PdfEditor._infos(page)
        if image_idx >= len(infos) or infos[image_idx]["xref"] != xref:
            raise EditError("The page changed, select the image again.")
        if infos[image_idx]["locked"]:
            raise EditError("This image is rotated or inline and can't be edited.")
        return infos

    @staticmethod
    def _remove(page, infos, idx):
        """Remove ONE placement. Redaction takes every image touching the area, so the neighbours are put back."""
        target = fitz.Rect(infos[idx]["rect"])
        area = fitz.Rect(target.x0 + 0.5, target.y0 + 0.5, target.x1 - 0.5, target.y1 - 0.5)
        if area.is_empty:
            area = target
        others = [i for k, i in enumerate(infos) if k != idx and fitz.Rect(i["rect"]).intersects(area)]
        page.add_redact_annot(area, fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_NONE)
        for o in others:
            page.insert_image(fitz.Rect(o["rect"]), xref=o["xref"], keep_proportion=False, overlay=False)

    @staticmethod
    def _clamp(rect, page):
        pr = page.rect
        w = min(max(rect.width, MIN_SIZE), pr.width)
        h = min(max(rect.height, MIN_SIZE), pr.height)
        x0 = min(max(rect.x0, 0), pr.width - w)
        y0 = min(max(rect.y0, 0), pr.height - h)
        return fitz.Rect(x0, y0, x0 + w, y0 + h)

    @staticmethod
    def _xref_to_pil(doc, xref):
        """The embedded picture as a PIL image (with its transparency, if it has any)."""
        try:
            pix = fitz.Pixmap(doc, xref)
            if pix.colorspace is None or pix.colorspace.n != 3:  # grey, CMYK, indexed
                pix = fitz.Pixmap(fitz.csRGB, pix)
            smask = doc.extract_image(xref).get("smask", 0)
            if smask and not pix.alpha:
                pix = fitz.Pixmap(pix, fitz.Pixmap(doc, smask))
            return Image.frombytes("RGBA" if pix.alpha else "RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            raise EditError("This kind of image can't be cropped.")

    def crop_image(self, index, image_idx, xref, box):
        """Trim a picture. `box` = (left, top, right, bottom) as fractions of the picture. Returns its new rect."""
        l, t, r, b = box
        if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
            raise EditError("Nothing to crop.")
        with self._edit() as doc:
            page = self._page(doc, index)
            infos = self._target(page, image_idx, xref)
            rect = fitz.Rect(infos[image_idx]["rect"])
            img = self._xref_to_pil(doc, xref)
            w, h = img.size
            px = (round(l * w), round(t * h), max(round(r * w), round(l * w) + 2), max(round(b * h), round(t * h) + 2))
            cropped = img.crop(px)
            buf = io.BytesIO()
            if cropped.mode == "RGBA":
                cropped.save(buf, "PNG")
            else:
                cropped.save(buf, "JPEG", quality=95)
            new = fitz.Rect(rect.x0 + l * rect.width, rect.y0 + t * rect.height,
                            rect.x0 + r * rect.width, rect.y0 + b * rect.height)
            self._remove(page, infos, image_idx)
            page.insert_image(new, stream=buf.getvalue(), keep_proportion=False, overlay=True)
        return tuple(new)

    def arrange(self, index, image_idx, xref, how):
        """top / bottom / left / right / hcenter / vcenter put the picture on the page; front / back change its layer."""
        with self._edit() as doc:
            page = self._page(doc, index)
            infos = self._target(page, image_idx, xref)
            r, pr = fitz.Rect(infos[image_idx]["rect"]), page.rect
            x0, y0 = r.x0, r.y0
            if how == "top":
                y0 = MARGIN
            elif how == "bottom":
                y0 = pr.height - MARGIN - r.height
            elif how == "left":
                x0 = MARGIN
            elif how == "right":
                x0 = pr.width - MARGIN - r.width
            elif how == "hcenter":
                x0 = (pr.width - r.width) / 2
            elif how == "vcenter":
                y0 = (pr.height - r.height) / 2
            new = self._clamp(fitz.Rect(x0, y0, x0 + r.width, y0 + r.height), page)
            self._remove(page, infos, image_idx)
            page.insert_image(new, xref=xref, keep_proportion=False, overlay=(how != "back"))
        return tuple(new)

    def add_page(self, after_index):
        """A blank page after `after_index`, the same size as that page."""
        with self._edit() as doc:
            ref = doc[after_index].rect
            pno = after_index + 1 if after_index + 1 < len(doc) else -1
            doc.new_page(pno=pno, width=ref.width, height=ref.height)

    def delete_page(self, index):
        with self._edit() as doc:
            if len(doc) <= 1:
                raise EditError("A document needs at least one page.")
            doc.delete_page(index)

    def move_resize(self, index, image_idx, xref, rect):
        with self._edit() as doc:
            page = self._page(doc, index)
            infos = self._target(page, image_idx, xref)
            new = self._clamp(fitz.Rect(rect), page)
            self._remove(page, infos, image_idx)
            page.insert_image(new, xref=xref, keep_proportion=False, overlay=True)
        return tuple(new)

    def delete_image(self, index, image_idx, xref):
        with self._edit() as doc:
            page = self._page(doc, index)
            infos = self._target(page, image_idx, xref)
            self._remove(page, infos, image_idx)

    def add_image(self, index, path, center=None):
        """Insert a picture file, about 40% of the page wide, around `center` (points). Returns its rect."""
        try:
            data, iw, ih = _encode_image(path)
        except Exception as e:
            raise EditError(f"Couldn't read {os.path.basename(path)}: {e}")
        with self._edit() as doc:
            page = self._page(doc, index)
            pr = page.rect
            w = pr.width * 0.4
            h = w * ih / iw
            if h > pr.height * 0.6:
                h = pr.height * 0.6
                w = h * iw / ih
            cx, cy = center or (pr.width / 2, pr.height / 2)
            rect = self._clamp(fitz.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), page)
            page.insert_image(rect, stream=data, keep_proportion=False, overlay=True)
        return tuple(rect)

    def undo(self):
        if not self._undo:
            return False
        snap, self.state = self._undo.pop()
        os.replace(snap, self.path)
        self._edits -= 1
        return True

    def reset(self):
        """Back to the document as it was loaded (also undoable)."""
        if not self.dirty and os.path.abspath(self.original) == os.path.abspath(self._source):
            return
        self._push()
        shutil.copyfile(self.original, self.path)

    def export(self, dest, pages=None):
        export_pdf(self.path, dest, pages)