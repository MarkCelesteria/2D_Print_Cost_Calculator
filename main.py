import sys
import time

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QSystemTrayIcon, QMenu, QMessageBox,
    QWidget, QVBoxLayout, QHBoxLayout, QProgressDialog
)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction, QDesktopServices
from PyQt6.QtCore import Qt, QTimer, QUrl

import pricing
import autostart
import updater
from analyzer import is_libreoffice_available, cleanup_temp_dirs, cleanup_old_temp_dirs
from style import QSS, TEXT
from calculate_tab import CalculateTab
from settings_tab import SettingsTab


CMYK_COLORS = ["#00aeef", "#ec008c", "#fff200", "#231f20"]


def make_app_icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(TEXT))
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    dots = [(12, 12, "#00aeef"), (34, 12, "#ec008c"), (12, 34, "#fff200"), (34, 34, "#ffffff")]
    for x, y, color in dots:
        painter.setBrush(QColor(color))
        painter.drawEllipse(x, y, 18, 18)
    painter.end()
    return QIcon(pix)


class CmykBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)

    def paintEvent(self, event):
        painter = QPainter(self)
        seg = self.width() / len(CMYK_COLORS)
        for i, color in enumerate(CMYK_COLORS):
            painter.fillRect(int(i * seg), 0, int(seg) + 1, self.height(), QColor(color))


class MainWindow(QMainWindow):
    def __init__(self, initial_file=None, auto_calculate=False):
        super().__init__()
        self.setWindowTitle(f"Print Cost Calculator v{updater.APP_VERSION}")
        self.resize(900, 720)

        self.settings = pricing.load_settings()

        tabs = QTabWidget()
        self.calc_tab = CalculateTab(self.settings)
        self.settings_tab = SettingsTab(self.settings, on_save=self._on_settings_saved)
        tabs.addTab(self.calc_tab, "Calculate")
        tabs.addTab(self.settings_tab, "Settings")

        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 4, 14, 4)
        corner_layout.addWidget(self.calc_tab.refresh_btn)
        tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        tabs.currentChanged.connect(lambda i: corner.setVisible(i == 0))
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(CmykBar())
        central_layout.addWidget(tabs)
        self.setCentralWidget(central)

        self.app_icon = make_app_icon()
        self.setWindowIcon(self.app_icon)

        self._setup_tray()
        self._setup_updates()
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

    # ---------------- updates ----------------

    def _setup_updates(self):
        self.pending_update = None
        self._check_worker = None
        self._download_worker = None
        self._progress = None
        updater.cleanup_leftovers()
        self.calc_tab.on_calculated = self._maybe_show_update  # offer the update after a calculation finishes
        QTimer.singleShot(4000, self._start_update_check)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._start_update_check)
        self._update_timer.start(24 * 60 * 60 * 1000)  # the app lives in the tray, so re-check daily

    def _start_update_check(self, manual=False):
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        worker = updater.CheckWorker(self)
        worker.found.connect(lambda info: self._on_update_found(info, manual))
        worker.up_to_date.connect(lambda: self._on_up_to_date(manual))
        worker.failed.connect(lambda msg: self._on_update_check_failed(manual))
        self._check_worker = worker
        worker.start()

    def _on_update_found(self, info, manual):
        self.pending_update = info
        if manual:
            self._show_from_tray()
            self._show_update_dialog()

    def _on_up_to_date(self, manual):
        self.pending_update = None
        if manual:
            self._show_from_tray()
            QMessageBox.information(self, "Up to date", f"You have the latest version ({updater.APP_VERSION}).")

    def _on_update_check_failed(self, manual):
        if manual:  # background checks stay silent
            self._show_from_tray()
            QMessageBox.warning(self, "Couldn't check for updates", "Check your internet connection and try again.")

    def _maybe_show_update(self):
        """Called after a calculation finishes, so the notice never interrupts the middle of a task."""
        if self.pending_update is None or time.time() < self.settings.get("update_remind_after", 0):
            return
        self._show_update_dialog()

    def _remind_later(self):
        self.settings["update_remind_after"] = time.time() + 24 * 60 * 60
        pricing.save_settings(self.settings)

    def _show_update_dialog(self):
        info = self.pending_update
        if not info:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Update available")
        box.setText(f"Version {info['version']} is available. You have {updater.APP_VERSION}.")
        if info.get("name"):
            box.setInformativeText(info["name"])
        if info.get("notes"):
            box.setDetailedText(info["notes"])
        update_btn = None
        if updater.can_self_update(info):
            update_btn = box.addButton("Update now", QMessageBox.ButtonRole.AcceptRole)
        page_btn = box.addButton("Open download page", QMessageBox.ButtonRole.ActionRole)
        later_btn = box.addButton("Remind me later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(update_btn or page_btn)
        box.setEscapeButton(later_btn)
        box.exec()

        clicked = box.clickedButton()
        if update_btn is not None and clicked is update_btn:
            self._start_update(info)
        elif clicked is page_btn:
            QDesktopServices.openUrl(QUrl(info["page"]))
            self._remind_later()
        else:  # Remind me later, or the window was closed
            self._remind_later()

    def _start_update(self, info):
        self._progress = QProgressDialog("Downloading the update...", "Cancel", 0, 100, self)
        self._progress.setWindowTitle("Updating")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        self._progress.setValue(0)

        worker = updater.DownloadWorker(info, self)
        worker.progress.connect(self._on_download_progress)
        worker.done.connect(self._on_download_done)
        worker.failed.connect(lambda msg: self._on_download_failed(msg, info))
        self._progress.canceled.connect(worker.cancel)
        self._download_worker = worker
        worker.start()

    def _on_download_progress(self, done, total):
        if total:
            self._progress.setValue(int(done * 100 / total))

    def _on_download_done(self, path):
        self._progress.close()
        QMessageBox.information(self, "Update ready", "The update is downloaded. The app will restart now.")
        try:
            updater.apply_update(path)
        except Exception as e:
            QMessageBox.warning(self, "Update failed", f"{e}\n\nPlease download it from the release page instead.")
            return
        self._exit_app()

    def _on_download_failed(self, msg, info):
        self._progress.close()
        if msg == "Cancelled":
            return
        QMessageBox.warning(self, "Update failed", f"{msg}\n\nOpening the download page instead.")
        QDesktopServices.openUrl(QUrl(info["page"]))

    # ---------------- system tray ----------------

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("Print Cost Calculator")

        menu = QMenu()
        open_action = QAction("Open", self)
        open_action.triggered.connect(self._show_from_tray)
        update_action = QAction("Check for updates", self)
        update_action.triggered.connect(lambda checked=False: self._start_update_check(manual=True))
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self._exit_app)
        menu.addAction(open_action)
        menu.addAction(update_action)
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
    app.setApplicationName("Print Cost Calculator")
    app.setQuitOnLastWindowClosed(False)
    app.aboutToQuit.connect(cleanup_temp_dirs)
    cleanup_old_temp_dirs()
    app.setStyle("Fusion")
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
