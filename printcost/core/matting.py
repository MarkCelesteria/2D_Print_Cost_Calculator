import hashlib
import importlib.util
import os
import shutil
import sys

import numpy as np
from PIL import Image, ImageFilter
from PyQt6.QtCore import QThread, pyqtSignal

from printcost.core import pricing, updater

MODEL_FILE = "modnet.onnx"
MODEL_URL = "https://huggingface.co/Xenova/modnet/resolve/main/onnx/model.onnx"
MODEL_SIZE_MB = 26
MODEL_MIN_BYTES = 20 * 1024 * 1024
MODEL_SHA256 = ""
REF_SIZE = 512

HQ_FILE = "birefnet_lite.onnx"
HQ_URL = f"https://github.com/{updater.REPO}/releases/download/models-v1/{HQ_FILE}"
HQ_SIZE_MB = 200
HQ_MIN_BYTES = 80 * 1024 * 1024
HQ_SHA256 = "1e0da42f0fde010e32e938bad388457ecefe35806fde9d923421997861ae9391"
HQ_LONG_SIDE = 1024
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_sessions = {}


class MattingError(Exception):
    pass


def _valid(path, minimum=MODEL_MIN_BYTES):
    return os.path.exists(path) and os.path.getsize(path) >= minimum


def bundled_path():
    if getattr(sys, "frozen", False):
        base = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "models")
    else:
        base = os.path.join(pricing._app_data_dir(), "assets")
    return os.path.join(base, MODEL_FILE)


def download_path(kind="fast"):
    return os.path.join(pricing._app_data_dir(), "models", HQ_FILE if kind == "hq" else MODEL_FILE)


def hq_installed():
    return _valid(download_path("hq"), HQ_MIN_BYTES)


def model_path():
    bundled = bundled_path()
    return bundled if _valid(bundled) else download_path()


def is_installed():
    return _valid(model_path())


def runtime_error():
    if importlib.util.find_spec("onnxruntime") is None:
        return "The background remover needs the onnxruntime package. Run: pip install onnxruntime"
    return None


def download_model(progress_cb=None, is_cancelled=None, kind="fast"):
    url, minimum, expected = ((HQ_URL, HQ_MIN_BYTES, HQ_SHA256) if kind == "hq"
                              else (MODEL_URL, MODEL_MIN_BYTES, MODEL_SHA256))
    dest = download_path(kind)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": updater.USER_AGENT})
    sha, done = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            while True:
                if is_cancelled and is_cancelled():
                    raise MattingError("Cancelled")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
        if done < minimum:
            raise MattingError("The download was incomplete. Please try again.")
        if expected and sha.hexdigest().lower() != expected.lower():
            raise MattingError("The download didn't match its checksum, so it was discarded.")
        os.replace(part, dest)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return dest


def install_hq(path):
    if not os.path.isfile(path) or os.path.getsize(path) < HQ_MIN_BYTES:
        raise MattingError("This isn't the high quality model file (it is too small).")
    if HQ_SHA256:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
        if sha.hexdigest().lower() != HQ_SHA256.lower():
            raise MattingError("This file isn't the expected high quality model.")
    dest = download_path("hq")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    try:
        shutil.copyfile(path, part)
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)
    _sessions.pop("hq", None)
    return dest


def _get_session(kind="fast"):
    if kind in _sessions:
        return _sessions[kind]
    err = runtime_error()
    if err:
        raise MattingError(err)
    path = download_path("hq") if kind == "hq" else model_path()
    if not (hq_installed() if kind == "hq" else is_installed()):
        raise MattingError("The high quality model isn't installed yet." if kind == "hq"
                           else "The background removal model isn't downloaded yet.")
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    try:
        _sessions[kind] = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
    except Exception as e:
        if kind == "hq":
            raise MattingError("The high quality model couldn't start. Update onnxruntime "
                               f"(pip install -U onnxruntime) or install the pack again.\n\n{e}")
        if path == download_path():
            try:
                os.remove(path)
            except OSError:
                pass
        raise MattingError("The model file is damaged. Download it again or reinstall the app.")
    return _sessions[kind]


def _fast_matte(img):
    session = _get_session("fast")
    w, h = img.size
    if w >= h:
        rh, rw = REF_SIZE, round(w / h * REF_SIZE)
    else:
        rw, rh = REF_SIZE, round(h / w * REF_SIZE)
    rw, rh = max(32, rw - rw % 32), max(32, rh - rh % 32)
    small = img.convert("RGB").resize((rw, rh), Image.LANCZOS)
    x = (np.asarray(small, dtype=np.float32) - 127.5) / 127.5
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
    out = session.run(None, {session.get_inputs()[0].name: x})[0][0, 0]
    matte = Image.fromarray((np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
    return np.asarray(matte.resize((w, h), Image.BILINEAR)).copy()


def _hq_matte(img):
    session = _get_session("hq")
    w, h = img.size
    rw = rh = HQ_LONG_SIDE
    small = img.convert("RGB").resize((rw, rh), Image.LANCZOS)
    x = (np.asarray(small, dtype=np.float32) / 255.0 - _MEAN) / _STD
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None].astype(np.float32))
    outs = session.run(None, {session.get_inputs()[0].name: x})
    out = next((o for o in outs if o.ndim == 4 and tuple(o.shape[-2:]) == (rh, rw)), outs[-1])
    prob = 1.0 / (1.0 + np.exp(-np.clip(np.squeeze(out), -30.0, 30.0)))
    matte = Image.fromarray((prob * 255.0 + 0.5).astype(np.uint8))
    return np.asarray(matte.resize((w, h), Image.BILINEAR)).copy()


def merge_parts(soft, hq, detail=50):
    """Merges the portrait matte (soft hair) with the big model's shape (cap boards, tassels, cords).
    detail 0 = trust the portrait model near the body, 100 = trust the big model everywhere.
    Returns (merged, added): `added` is only what the big model contributed, so it can survive edge snapping."""
    d = max(0.0, min(1.0, detail / 100.0))
    soft_f = soft.astype(np.float32) / 255.0
    hq_f = hq.astype(np.float32) / 255.0

    def mask(a):
        return Image.fromarray(a.astype(np.uint8) * 255)

    def on(img):
        return np.asarray(img) > 0

    def size(radius):
        return 2 * int(round(radius)) + 1

    shape = mask(hq_f > 0.5)
    gap = size(2 * d)
    if gap > 1:
        shape = shape.filter(ImageFilter.MaxFilter(gap)).filter(ImageFilter.MinFilter(gap))
    shape = shape.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    inner = on(shape.filter(ImageFilter.MinFilter(5)))
    reach = on(shape.filter(ImageFilter.MaxFilter(7)))
    core = mask(soft_f > 0.95)
    near_core = np.asarray(core.filter(ImageFilter.GaussianBlur(max(1.0, 9.0 * (1 - d))))) > 8
    hair = on(mask((soft_f > 0.05) & (soft_f < 0.95)).filter(ImageFilter.MaxFilter(size(1 + 5 * (1 - d)))))
    crisp = on(core.filter(ImageFilter.MaxFilter(size(1 + 2 * (1 - d)))))
    edge = reach & ~inner & (soft_f < 0.35) & ~hair & ~crisp
    soft_edge = np.asarray(shape.filter(ImageFilter.GaussianBlur(1)), dtype=np.float32) / 255.0
    out = soft_f.copy()
    out[inner & ~near_core] = 1.0
    out[edge] = np.maximum(out[edge], soft_edge[edge])
    added = np.where(out > soft_f + 0.05, out, 0.0)
    return (out * 255.0 + 0.5).astype(np.uint8), (added * 255.0 + 0.5).astype(np.uint8)


def merge_matte(soft, hq, detail=50):
    return merge_parts(soft, hq, detail)[0]


def compute_matte(img, hq=False):
    matte = _fast_matte(img)
    return merge_matte(matte, _hq_matte(img)) if hq else matte


class MatteWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, img, parent=None, hq=False):
        super().__init__(parent)
        self.img = img
        self.hq = hq is True

    def run(self):
        try:
            self.parts = None
            if self.hq:
                soft = _fast_matte(self.img)
                self.parts = (soft, _hq_matte(self.img))
                self.done.emit(merge_matte(*self.parts))
            else:
                self.done.emit(_fast_matte(self.img))
        except Exception as e:
            self.failed.emit(str(e))


class ModelDownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None, kind="fast"):
        super().__init__(parent)
        self.kind = kind
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            download_model(lambda d, t: self.progress.emit(d, t), lambda: self._cancel, self.kind)
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))