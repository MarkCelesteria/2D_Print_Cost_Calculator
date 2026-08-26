import sys
import os

APP_NAME = "PrintCostCalculator"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _is_windows():
    return sys.platform.startswith("win")


def _run_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'

    python_exe = sys.executable.replace("python.exe", "pythonw.exe")
    script = os.path.abspath(sys.argv[0])
    return f'"{python_exe}" "{script}" --minimized'


def is_autostart_enabled() -> bool:
    if not _is_windows():
        return False
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False


def enable_autostart():
    if not _is_windows():
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _run_command())
    winreg.CloseKey(key)


def disable_autostart():
    if not _is_windows():
        return
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
    except FileNotFoundError:
        pass