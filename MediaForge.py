"""MediaForge — download (yt-dlp) + convert (ffmpeg) toolbox.

Run:          python MediaForge.py
Requirements: PySide6 (pip install -r requirements.txt) + ffmpeg and yt-dlp
              in the bin\\ folder or on PATH
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QTabWidget, QWidget,
)

from helpers import RESOURCE_DIR, working_hardware_encoders
from jobs import QueueManager
from settings import Settings, SettingsDialog
from tab_convert import ConvertTab
from tab_download import DownloadTab
from tab_queue import QueueTab
from tab_tools import ToolsTab
from updater import UpdateDialog


class HardwareProbe(QObject):
    """Hardware encoder test encodes can take a few seconds, so they run on a separate thread."""
    result = Signal(object)   # set[str]

    def start(self, ffmpeg: str):
        threading.Thread(target=lambda: self.result.emit(working_hardware_encoders(ffmpeg)),
                         daemon=True).start()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MediaForge")
        self.resize(980, 640)

        self.settings = Settings()
        self.queue = QueueManager(self.settings)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.queue_tab = QueueTab(self.queue)
        self.download_tab = DownloadTab(self.queue, self.settings, self._go_to_queue)
        self.convert_tab = ConvertTab(self.queue, self.settings, self._go_to_queue)
        self.tools_tab = ToolsTab(self.queue, self.settings, self._go_to_queue)

        self.tabs.addTab(self.download_tab, "⤓  Download")
        self.tabs.addTab(self.convert_tab, "⟳  Convert")
        self.tabs.addTab(self.tools_tab, "🛠  Tools")
        self.tabs.addTab(self.queue_tab, "☰  Queue")

        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 4, 0)
        update_button = QPushButton("⇩  Update")
        update_button.setFlat(True)
        update_button.setToolTip("Check the yt-dlp and ffmpeg versions and download them into bin\\ if needed")
        update_button.clicked.connect(self._open_updates)
        settings_button = QPushButton("⚙  Settings")
        settings_button.setFlat(True)
        settings_button.clicked.connect(self._open_settings)
        corner_layout.addWidget(update_button)
        corner_layout.addWidget(settings_button)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)

        self._status_bar()
        self.queue.structure_changed.connect(self._update_badge)

        # Hardware encoders (NVENC/AMF/QSV) are probed in the background;
        # GPU presets for the working ones are added to the Convert tab.
        self._probe = HardwareProbe()
        if self.settings.ffmpeg():
            self._probe.result.connect(self._on_hardware_result)
            self._probe.start(self.settings.ffmpeg())

    def _status_bar(self):
        self.tool_status = QLabel()
        self.statusBar().addPermanentWidget(self.tool_status)
        self._update_tool_status()

    def _update_tool_status(self):
        parts = []
        for name, path in (("ffmpeg", self.settings.ffmpeg()), ("yt-dlp", self.settings.ytdlp())):
            parts.append(f"{name} {'✓' if path else '✗ NOT FOUND'}")
        text = "   ".join(parts)
        self.tool_status.setText(text)
        if "✗" in text:
            self.tool_status.setStyleSheet("color: #f28b82;")
            self.statusBar().showMessage("A tool is missing — set its path in Settings.")
        else:
            self.tool_status.setStyleSheet("color: #81c995;")

    def _on_hardware_result(self, encoders: set):
        if not encoders:
            return
        self.convert_tab.enable_hardware(encoders)
        families = []
        if any(e.endswith("nvenc") for e in encoders):
            families.append("NVENC (NVIDIA)")
        if any(e.endswith("amf") for e in encoders):
            families.append("AMF (AMD)")
        if any(e.endswith("qsv") for e in encoders):
            families.append("QSV (Intel)")
        self.statusBar().showMessage(
            "Hardware encoder found: " + ", ".join(families) + " — GPU presets added.", 8000)

    def _go_to_queue(self):
        self.tabs.setCurrentWidget(self.queue_tab)

    def _update_badge(self):
        active = self.queue.active_count()
        index = self.tabs.indexOf(self.queue_tab)
        self.tabs.setTabText(index, f"☰  Queue ({active})" if active else "☰  Queue")

    def _open_settings(self):
        if SettingsDialog(self.settings, self).exec():
            self._update_tool_status()
            self.queue.pump()

    def _open_updates(self):
        UpdateDialog(self.settings, self.queue, self._update_tool_status, self).exec()

    def closeEvent(self, event):
        if self.queue.active_count():
            answer = QMessageBox.question(
                self, "Quit?",
                "There are unfinished jobs. They will be cancelled if you quit.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.queue.cancel_all()
        event.accept()


def _dark_theme(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    background = QColor(32, 33, 36)
    panel = QColor(41, 42, 45)
    text = QColor(232, 234, 237)
    accent = QColor(38, 166, 154)
    p.setColor(QPalette.Window, background)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, panel)
    p.setColor(QPalette.AlternateBase, background)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, panel)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.Highlight, accent)
    p.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    p.setColor(QPalette.ToolTipBase, panel)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor(154, 160, 166))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 124, 130))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 124, 130))
    app.setPalette(p)
    app.setStyleSheet("""
        QProgressBar { border: 1px solid #3c4043; border-radius: 4px;
                       text-align: center; background: #292a2d; }
        QProgressBar::chunk { background-color: #26a69a; border-radius: 3px; }
        QGroupBox { border: 1px solid #3c4043; border-radius: 6px;
                    margin-top: 12px; padding-top: 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
    """)


def _dark_title_bar(window: QWidget):
    """Makes the window title bar dark on Windows (silently skipped when unsupported)."""
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4)
    except (AttributeError, OSError):
        pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MediaForge")
    icon_path = os.path.join(RESOURCE_DIR, "icon.ico")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    _dark_theme(app)
    window = MainWindow()
    window.show()
    _dark_title_bar(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
