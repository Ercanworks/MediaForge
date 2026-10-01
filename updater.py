"""Tool updates: checks yt-dlp and ffmpeg and downloads them into the bin\\ folder.

Sources:
- yt-dlp : official GitHub release (standalone exe)
- ffmpeg : BtbN/FFmpeg-Builds GitHub builds (win64 gpl, zip)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
import zipfile

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout,
)

from helpers import APP_DIR, CREATE_NO_WINDOW, clear_path_cache, human_size

BIN_DIR = os.path.join(APP_DIR, "bin")
YTDLP_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": "MediaForge"})


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(_request(url), timeout=20) as response:
        return json.load(response)


def _version_output(exe: str | None, arg: str) -> str:
    if not exe:
        return ""
    try:
        result = subprocess.run([exe, arg], capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=20, creationflags=CREATE_NO_WINDOW)
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


class Updater(QObject):
    """Checks and downloads run on a separate thread; results come back via signals."""

    check_result = Signal(dict)
    progress = Signal(int)
    status = Signal(str)
    job_finished = Signal(bool, str)    # success, message

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    # --- checking ---
    def start_check(self):
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        result = {"error": ""}
        try:
            # yt-dlp: the version tag can be compared directly (e.g. 2026.07.04)
            result["ytdlp_installed"] = _version_output(self.settings.ytdlp(), "--version")
            result["ytdlp_latest"] = _get_json(YTDLP_API).get("tag_name", "")

            # ffmpeg: the release line (like n8.0) is read from the installed version line;
            # the highest of BtbN's release line zips is picked
            line = _version_output(self.settings.ffmpeg(), "-version")
            match = re.search(r"\bn(\d+(?:\.\d+)+)", line)
            result["ffmpeg_installed_line"] = match.group(1) if match else ""
            result["ffmpeg_installed_text"] = (re.search(r"version (\S+)", line).group(1)
                                               if "version" in line else "")

            highest, chosen = (), None
            for asset in _get_json(FFMPEG_API).get("assets", []):
                name = asset.get("name", "")
                match = re.fullmatch(r"ffmpeg-n(\d+(?:\.\d+)+)-latest-win64-gpl-[\d.]+\.zip", name)
                if match:
                    release_line = tuple(int(p) for p in match.group(1).split("."))
                    if release_line > highest:
                        highest, chosen = release_line, asset
            if chosen:
                result["ffmpeg_latest_line"] = ".".join(str(p) for p in highest)
                result["ffmpeg_url"] = chosen["browser_download_url"]
                result["ffmpeg_size"] = chosen.get("size", 0)
        except OSError as e:
            result["error"] = f"Could not fetch version information: {e}"
        self.check_result.emit(result)

    # --- downloading ---
    def _download(self, url: str, target: str):
        with urllib.request.urlopen(_request(url), timeout=30) as response, open(target, "wb") as f:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while chunk := response.read(1 << 16):
                f.write(chunk)
                received += len(chunk)
                if total:
                    self.progress.emit(int(received * 100 / total))

    def update_ytdlp(self):
        threading.Thread(target=self._update_ytdlp, daemon=True).start()

    def _update_ytdlp(self):
        try:
            os.makedirs(BIN_DIR, exist_ok=True)
            target = os.path.join(BIN_DIR, "yt-dlp.exe")
            temp = target + ".downloading"
            self.status.emit("Downloading yt-dlp…")
            self._download(YTDLP_URL, temp)
            os.replace(temp, target)
            clear_path_cache()
            self.job_finished.emit(True, "yt-dlp updated.")
        except (OSError, PermissionError) as e:
            self.job_finished.emit(False, f"Could not update yt-dlp: {e}")

    def update_ffmpeg(self, url: str):
        threading.Thread(target=self._update_ffmpeg, args=(url,), daemon=True).start()

    def _update_ffmpeg(self, url: str):
        temp_zip = os.path.join(BIN_DIR, "ffmpeg.downloading.zip")
        try:
            os.makedirs(BIN_DIR, exist_ok=True)
            self.status.emit("Downloading ffmpeg archive…")
            self._download(url, temp_zip)

            self.status.emit("Extracting archive…")
            self.progress.emit(-1)
            extracted = 0
            with zipfile.ZipFile(temp_zip) as archive:
                for member in archive.namelist():
                    for exe in ("ffmpeg.exe", "ffprobe.exe"):
                        if member.endswith("bin/" + exe):
                            target = os.path.join(BIN_DIR, exe)
                            with archive.open(member) as source, open(target + ".new", "wb") as f:
                                shutil.copyfileobj(source, f)
                            os.replace(target + ".new", target)
                            extracted += 1
            if extracted < 2:
                raise OSError("ffmpeg.exe/ffprobe.exe not found in the archive")
            clear_path_cache()
            self.job_finished.emit(True, "ffmpeg and ffprobe updated.")
        except (OSError, PermissionError, zipfile.BadZipFile) as e:
            self.job_finished.emit(False, f"Could not update ffmpeg: {e}")
        finally:
            try:
                if os.path.isfile(temp_zip):
                    os.remove(temp_zip)
            except OSError:
                pass


class UpdateDialog(QDialog):
    def __init__(self, settings, queue, on_updated, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.on_updated = on_updated
        self.ffmpeg_url = ""
        self.setWindowTitle("Tool Updates")
        self.setMinimumWidth(560)

        self.updater = Updater(settings)
        self.updater.check_result.connect(self._on_check_result)
        self.updater.progress.connect(self._on_progress)
        self.updater.status.connect(lambda m: self.status_label.setText(m))
        self.updater.job_finished.connect(self._on_job_finished)

        layout = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        layout.addLayout(grid)

        grid.addWidget(QLabel("<b>yt-dlp</b>"), 0, 0)
        self.ytdlp_status = QLabel("Checking…")
        grid.addWidget(self.ytdlp_status, 0, 1)
        self.ytdlp_button = QPushButton("Update")
        self.ytdlp_button.setEnabled(False)
        self.ytdlp_button.clicked.connect(self._on_ytdlp_clicked)
        grid.addWidget(self.ytdlp_button, 0, 2)

        grid.addWidget(QLabel("<b>ffmpeg</b>"), 1, 0)
        self.ffmpeg_status = QLabel("Checking…")
        grid.addWidget(self.ffmpeg_status, 1, 1)
        self.ffmpeg_button = QPushButton("Update")
        self.ffmpeg_button.setEnabled(False)
        self.ffmpeg_button.clicked.connect(self._on_ffmpeg_clicked)
        grid.addWidget(self.ffmpeg_button, 1, 2)
        grid.setColumnStretch(1, 1)

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #9aa0a6;")
        layout.addWidget(self.status_label)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

        self.updater.start_check()

    # --- check result ---
    def _on_check_result(self, r: dict):
        if r.get("error"):
            self.ytdlp_status.setText("—")
            self.ffmpeg_status.setText("—")
            self.status_label.setText(r["error"])
            return

        installed, latest = r.get("ytdlp_installed", ""), r.get("ytdlp_latest", "")
        if not installed:
            self.ytdlp_status.setText(f"Not installed.  Latest: {latest}")
            self.ytdlp_button.setText("Download")
            self.ytdlp_button.setEnabled(bool(latest))
        elif latest and installed != latest:
            self.ytdlp_status.setText(f"Installed: {installed}   →   Latest: {latest}")
            self.ytdlp_button.setEnabled(True)
        else:
            self.ytdlp_status.setText(f"Up to date ({installed})")

        self.ffmpeg_url = r.get("ffmpeg_url", "")
        installed_line, latest_line = r.get("ffmpeg_installed_line", ""), r.get("ffmpeg_latest_line", "")
        size = human_size(r.get("ffmpeg_size", 0)) if r.get("ffmpeg_size") else "?"
        if not r.get("ffmpeg_installed_text"):
            self.ffmpeg_status.setText(f"Not installed.  Latest: {latest_line} ({size})")
            self.ffmpeg_button.setText("Download")
            self.ffmpeg_button.setEnabled(bool(self.ffmpeg_url))
        elif not installed_line:
            self.ffmpeg_status.setText(
                f"Installed: {r['ffmpeg_installed_text']} (different source, can't compare)   "
                f"Latest: {latest_line} ({size})")
            self.ffmpeg_button.setEnabled(bool(self.ffmpeg_url))
        elif latest_line and installed_line != latest_line:
            self.ffmpeg_status.setText(f"Installed: n{installed_line}   →   Latest: n{latest_line} ({size})")
            self.ffmpeg_button.setEnabled(True)
        else:
            self.ffmpeg_status.setText(f"Up to date (n{installed_line})")

    # --- buttons ---
    def _is_busy(self) -> bool:
        if self.queue.active_count():
            QMessageBox.warning(self, "Jobs running",
                                "Tools can't be updated while there are unfinished jobs in the queue.")
            return True
        return False

    def _download_started(self):
        self.ytdlp_button.setEnabled(False)
        self.ffmpeg_button.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)

    def _on_ytdlp_clicked(self):
        if not self._is_busy():
            self._download_started()
            self.updater.update_ytdlp()

    def _on_ffmpeg_clicked(self):
        if not self._is_busy():
            self._download_started()
            self.updater.update_ffmpeg(self.ffmpeg_url)

    # --- progress/result ---
    def _on_progress(self, percent: int):
        if percent < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(percent)

    def _on_job_finished(self, success: bool, message: str):
        self.bar.setVisible(False)
        self.status_label.setText(message)
        if success:
            self.on_updated()
            self.ytdlp_status.setText("Checking…")
            self.ffmpeg_status.setText("Checking…")
            self.updater.start_check()
        else:
            self.ytdlp_button.setEnabled(True)
            self.ffmpeg_button.setEnabled(bool(self.ffmpeg_url))
