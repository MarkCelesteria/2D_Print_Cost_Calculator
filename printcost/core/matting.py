import hashlib
import importlib.util
import os
import sys

import numpy as np
from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from printcost.core import pricing, updater

MODEL_FILE = "modnet.onnx"
MODEL_URL = "https://huggingface.co/Xenova/modnet/resolve/main/onnx/model.onnx"
MODEL_SIZE_MB = 26
MODEL_MIN_BYTES = 20 * 1024 * 1024
MODEL_SHA256 = ""
REF_SIZE = 512

_session = None


class MattingError(Exception):
    pass


def _valid(path):
    return os.path.exists(path) and os.path.getsize(path) >= MODEL_MIN_BYTES


def bundled_path():
    if getattr(sys, "frozen", False):
        base = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "models")
    else:
        base = os.path.join(pricing._app_data_dir(), "assets")
    return os.path.join(base, MODEL_FILE)


def download_path():
    return os.path.join(pricing._app_data_dir(), "models", MODEL_FILE)


def model_path():
    bundled = bundled_path()
    return bundled if _valid(bundled) else download_path()


def is_installed():
    return _valid(model_path())


def runtime_error():
    if importlib.util.find_spec("onnxruntime") is None:
        return "The background remover needs the onnxruntime package. Run: pip install onnxruntime"
    return None


def download_model(progress_cb=None, is_cancelled=None):
    dest = download_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    import urllib.request
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": updater.USER_AGENT})
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
        if done < MODEL_MIN_BYTES:
            raise MattingError("The download was incomplete. Please try again.")
        if MODEL_SHA256 and sha.hexdigest().lower() != MODEL_SHA256.lower():
            raise MattingError("The download didn't match its checksum, so it was discarded.")
        os.replace(part, dest)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return dest


def _get_session():
    global _session
    if _session is None:
        err = runtime_error()
        if err:
            raise MattingError(err)
        if not is_installed():
            raise MattingError("The background removal model isn't downloaded yet.")
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        try:
            _session = ort.InferenceSession(model_path(), opts, providers=["CPUExecutionProvider"])
        except Exception:
            if model_path() == download_path():
                try:
                    os.remove(download_path())
                except OSError:
                    pass
            raise MattingError("The model file is damaged. Download it again or reinstall the app.")
    return _session


def compute_matte(img):
    session = _get_session()
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


class MatteWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, img, parent=None):
        super().__init__(parent)
        self.img = img

    def run(self):
        try:
            self.done.emit(compute_matte(self.img))
        except Exception as e:
            self.failed.emit(str(e))


class ModelDownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            download_model(lambda d, t: self.progress.emit(d, t), lambda: self._cancel)
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))