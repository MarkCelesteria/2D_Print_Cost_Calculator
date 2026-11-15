import json
import os
import re
import sys
import time

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QMessageBox

from printcost.core import updater

_USER = re.sub(r"\W", "_", os.environ.get("USERNAME") or os.environ.get("USER") or "user")
SERVER_NAME = f"PrintCostCalculator-{_USER}" + ("" if getattr(sys, "frozen", False) else "-dev")
TIMEOUT_MS = 1500


def _send(message):
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(TIMEOUT_MS):
        return None
    sock.write(json.dumps(message).encode("utf-8"))
    sock.flush()
    reply = None
    if sock.waitForReadyRead(TIMEOUT_MS):
        try:
            reply = json.loads(bytes(sock.readAll()).decode("utf-8"))
        except ValueError:
            reply = None
    sock.disconnectFromServer()
    return reply


def _is_newer(mine, theirs):
    try:
        return updater.parse_version(mine) > updater.parse_version(theirs)
    except (ValueError, TypeError):
        return False


def _wait_until_gone(seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if _send({"action": "ping"}) is None:
            return True
        time.sleep(0.2)
    return False


def _ask_replace(running):
    box = QMessageBox(
        QMessageBox.Icon.Question, "Older version is running",
        f"Version {running} is already running. You opened version {updater.APP_VERSION}.\n\n"
        "Close the old one and start this one?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    return box.exec() == QMessageBox.StandardButton.Yes


def _allow_foreground():
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)


def hand_over(file=None, quiet=False):
    running = _send({"action": "ping"})
    if running is None:
        return False
    if not quiet and _is_newer(updater.APP_VERSION, running.get("version")) and _ask_replace(running["version"]):
        _send({"action": "quit"})
        if _wait_until_gone():
            return False
    if not quiet:
        _allow_foreground()
        _send({"action": "show", "file": file})
    return True


def request_quit():
    if _send({"action": "quit"}) is not None:
        _wait_until_gone()


class InstanceServer(QObject):
    activate = pyqtSignal(str)
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)

    def listen(self):
        QLocalServer.removeServer(SERVER_NAME)
        return self._server.listen(SERVER_NAME)

    def _on_connection(self):
        sock = self._server.nextPendingConnection()
        sock.readyRead.connect(lambda: self._on_message(sock))
        sock.disconnected.connect(sock.deleteLater)

    def _on_message(self, sock):
        try:
            msg = json.loads(bytes(sock.readAll()).decode("utf-8"))
        except ValueError:
            return
        sock.write(json.dumps({"version": updater.APP_VERSION}).encode("utf-8"))
        sock.flush()
        action = msg.get("action")
        if action == "show":
            self.activate.emit(msg.get("file") or "")
        elif action == "quit":
            self.quit_requested.emit()