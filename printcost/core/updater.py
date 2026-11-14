import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

APP_VERSION = "1.7.0"
REPO = "MarkCelesteria/2D_Print_Cost_Calculator"
ASSET_NAME = "PrintCostCalculator.exe"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = "PrintCostCalculator-updater"


class UpdateError(Exception):
    pass


def parse_version(text):
    """'v1.10.0' -> (1, 10, 0). Raises ValueError for tags that aren't plain numbers (e.g. '1.6.0-beta')."""
    nums = [int(p) for p in str(text).strip().lstrip("vV").split(".")]
    return tuple(nums + [0] * (3 - len(nums)))


def _latest_from_api():
    """Release details from GitHub's API (notes, size, checksum). Limited to 60 calls/hour per internet connection."""
    req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
    return {
        "tag": data["tag_name"],
        "name": data.get("name") or "",
        "notes": data.get("body") or "",
        "page": data["html_url"],
        "asset_url": asset["browser_download_url"] if asset else None,
        "asset_size": asset.get("size") if asset else None,
        "sha256": (asset.get("digest") or "").removeprefix("sha256:") if asset else "",
    }


def _latest_from_redirect():
    """No-API fallback: github.com/<repo>/releases/latest redirects to the newest tag's page."""
    req = urllib.request.Request(f"https://github.com/{REPO}/releases/latest", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        page = resp.geturl()
    tag = page.rstrip("/").rsplit("/", 1)[-1]
    return {
        "tag": tag, "name": "", "notes": "", "page": page,
        "asset_url": f"https://github.com/{REPO}/releases/download/{tag}/{ASSET_NAME}",
        "asset_size": None, "sha256": "",
    }


def check_for_update(current=APP_VERSION):
    """Info about the newest GitHub release if it is newer than `current`, else None. Raises on network errors."""
    try:
        info = _latest_from_api()
    except Exception:
        info = _latest_from_redirect()

    latest = parse_version(info.pop("tag"))
    if latest <= parse_version(current):
        return None
    info["version"] = ".".join(str(n) for n in latest)
    return info


def _folder_writable(path):
    try:
        test = os.path.join(path, ".write_test")
        open(test, "w").close()
        os.remove(test)
        return True
    except OSError:
        return False


def can_self_update(info):
    """Only the packaged Windows exe can replace itself, and only if its folder is writable."""
    return (os.name == "nt" and getattr(sys, "frozen", False) and bool(info.get("asset_url"))
            and _folder_writable(os.path.dirname(sys.executable)))


def download_update(info, dest_dir=None, progress_cb=None, is_cancelled=None):
    """Download the new exe next to the current one (same drive, so the swap is instant). Returns its path."""
    dest_dir = dest_dir or os.path.dirname(sys.executable)
    dest = os.path.join(dest_dir, ASSET_NAME + ".new")
    req = urllib.request.Request(info["asset_url"], headers={"User-Agent": USER_AGENT})
    sha, done = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or info.get("asset_size") or 0)
            while True:
                if is_cancelled and is_cancelled():
                    raise UpdateError("Cancelled")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
        if info.get("asset_size") and done != info["asset_size"]:
            raise UpdateError("The download was incomplete. Please try again.")
        if info.get("sha256") and sha.hexdigest().lower() != info["sha256"].lower():
            raise UpdateError("The download didn't match GitHub's checksum, so it was discarded.")
        with open(dest, "rb") as f:
            if f.read(2) != b"MZ":
                raise UpdateError("The download isn't a Windows program.")
    except BaseException:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    return dest


def _clean_env():
    """The new exe must not think it is a child of this one (PyInstaller one-file quirk)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def update_script(new_exe, target):
    return "\r\n".join([
        "@echo off",
        "set tries=0",
        ":retry",
        "set /a tries+=1",
        "if %tries% gtr 60 goto fail",
        "timeout /t 1 /nobreak >nul",
        f'move /y "{new_exe}" "{target}" >nul 2>&1',
        "if errorlevel 1 goto retry",
        f'start "" "{target}"',
        "goto end",
        ":fail",
        f'del "{new_exe}" >nul 2>&1',
        ":end",
        '(goto) 2>nul & del "%~f0"',
        "",
    ])


def apply_update(new_exe):
    """Swap the new exe in once this app has exited, then start it. Call right before quitting."""
    target = sys.executable
    script = os.path.join(tempfile.gettempdir(), "printcost_update.bat")
    with open(script, "w", newline="") as f:
        f.write(update_script(new_exe, target))
    subprocess.Popen(
        ["cmd", "/c", script], env=_clean_env(), cwd=os.path.dirname(target), close_fds=True,
        creationflags=0x08000000 | 0x00000008,
    )


def cleanup_leftovers():
    """Remove a half-finished download from an update that was interrupted."""
    if getattr(sys, "frozen", False):
        try:
            os.remove(os.path.join(os.path.dirname(sys.executable), ASSET_NAME + ".new"))
        except OSError:
            pass


class CheckWorker(QThread):
    found = pyqtSignal(object)
    up_to_date = pyqtSignal()
    failed = pyqtSignal(str)

    def run(self):
        try:
            info = check_for_update()
        except Exception as e:
            self.failed.emit(str(e))
            return
        if info:
            self.found.emit(info)
        else:
            self.up_to_date.emit()


class DownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            path = download_update(self.info, progress_cb=lambda d, t: self.progress.emit(d, t),
                                   is_cancelled=lambda: self._cancel)
            self.done.emit(path)
        except Exception as e:
            self.failed.emit(str(e))