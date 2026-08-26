import sys

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QSystemTrayIcon, QMenu, QMessageBox
)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction
from PyQt6.QtCore import Qt, QTimer

import pricing
import autostart
from analyzer import is_libreoffice_available
from style import QSS, PRIMARY
from calculate_tab import CalculateTab
from settings_tab import SettingsTab


def make_app_icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(PRIMARY))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 60, 60)
    painter.setBrush(QColor("white"))
    painter.drawEllipse(14, 14, 36, 36)
    painter.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self, initial_file=None, auto_calculate=False):
        super().__init__()
        self.setWindowTitle("Print Cost Calculator")
        self.resize(820, 640)

        self.settings = pricing.load_settings()

        tabs = QTabWidget()
        self.calc_tab = CalculateTab(self.settings)
        self.settings_tab = SettingsTab(self.settings, on_save=self._on_settings_saved)
        tabs.addTab(self.calc_tab, "Calculate")
        tabs.addTab(self.settings_tab, "Settings")
        self.setCentralWidget(tabs)

        self.app_icon = make_app_icon()
        self.setWindowIcon(self.app_icon)

        self._setup_tray()
        QTimer.singleShot(500, self._maybe_ask_autostart)

        if initial_file:
            QTimer.singleShot(200, lambda: self.calc_tab.load_file(initial_file, auto_calculate=auto_calculate))
        QTimer.singleShot(400, self._check_libreoffice)

    def _on_settings_saved(self):
        self.calc_tab.refresh_settings(self.settings)

    def _maybe_ask_autostart(self):
        if self.settings.get("asked_about_autostart"):
            return
        self.settings["asked_about_autostart"] = True
        pricing.save_settings(self.settings)

        if sys.platform.startswith("win"):
            reply = QMessageBox.question(
                self, "Start automatically?",
                "Start Print Cost Calculator automatically when your PC turns on?\n\n"
                "It'll open quietly in the system tray — click the tray icon "
                "anytime to open it. You can change this later.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                autostart.enable_autostart()

    def _check_libreoffice(self):
        if not is_libreoffice_available():
            QMessageBox.information(
                self,
                "LibreOffice not found",
                "LibreOffice isn't installed on this computer.\n\n"
                "PDF files will still work perfectly. For accurate .docx "
                "results (and a preview), install LibreOffice (free):\n"
                "https://www.libreoffice.org/download/"
            )

    # ---------------- system tray ----------------

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("Print Cost Calculator")

        menu = QMenu()
        open_action = QAction("Open", self)
        open_action.triggered.connect(self._show_from_tray)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self._exit_app)
        menu.addAction(open_action)
        menu.addAction(exit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _exit_app(self):
        self.tray_icon.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        # minimize to tray instead of quitting — click the tray icon to bring it back
        event.ignore()
        self.hide()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(QSS)

    args = sys.argv[1:]
    start_minimized = "--minimized" in args
    if start_minimized:
        args.remove("--minimized")
    initial_file = args[0] if args else None

    window = MainWindow(initial_file=initial_file, auto_calculate=bool(initial_file))
    if not start_minimized:
        window.show()

    sys.exit(app.exec())
