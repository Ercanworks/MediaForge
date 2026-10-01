"""Persistent settings (settings.json) and the settings dialog."""
from __future__ import annotations

import json
import os

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from helpers import APP_DIR, tool_path

SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

DEFAULTS = {
    "download_dir": os.path.join(os.path.expanduser("~"), "Downloads"),
    "convert_to_source_dir": True,
    "convert_dir": "",
    "max_parallel_downloads": 2,
    "max_parallel_ffmpeg": 1,
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "ytdlp_path": "",
    "whole_playlist": False,
    "embed_audio_tags": True,
    "download_format": 0,
}

# Settings file and keys of versions before 1.1, migrated on first start
LEGACY_SETTINGS_FILE = os.path.join(APP_DIR, "ayarlar.json")
LEGACY_KEYS = {
    "indirme_klasoru": "download_dir",
    "donusturme_ayni_klasore": "convert_to_source_dir",
    "donusturme_klasoru": "convert_dir",
    "es_zamanli_indirme": "max_parallel_downloads",
    "es_zamanli_ffmpeg": "max_parallel_ffmpeg",
    "ffmpeg_yolu": "ffmpeg_path",
    "ffprobe_yolu": "ffprobe_path",
    "ytdlp_yolu": "ytdlp_path",
    "playlist_tamami": "whole_playlist",
    "ses_etiket_gom": "embed_audio_tags",
    "indirme_bicimi": "download_format",
}


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


class Settings:
    def __init__(self):
        self.d = dict(DEFAULTS)
        stored = _read_json(SETTINGS_FILE)
        if stored is not None:
            self.d.update(stored)
            return
        legacy = _read_json(LEGACY_SETTINGS_FILE)
        if legacy is not None:
            self.d.update({LEGACY_KEYS[k]: v for k, v in legacy.items() if k in LEGACY_KEYS})
            self.save()

    def save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.d, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def __getitem__(self, key):
        return self.d.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.d[key] = value

    # Tool paths (located automatically when the custom path is empty)
    def ffmpeg(self):
        return tool_path("ffmpeg", self["ffmpeg_path"])

    def ffprobe(self):
        return tool_path("ffprobe", self["ffprobe_path"])

    def ytdlp(self):
        return tool_path("yt-dlp", self["ytdlp_path"])


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.parallel_downloads = QSpinBox()
        self.parallel_downloads.setRange(1, 8)
        self.parallel_downloads.setValue(int(settings["max_parallel_downloads"]))
        form.addRow("Parallel downloads:", self.parallel_downloads)

        self.parallel_ffmpeg = QSpinBox()
        self.parallel_ffmpeg.setRange(1, 4)
        self.parallel_ffmpeg.setValue(int(settings["max_parallel_ffmpeg"]))
        form.addRow("Parallel conversions:", self.parallel_ffmpeg)

        self.ffmpeg_box = self._path_row(form, "ffmpeg path:", settings["ffmpeg_path"])
        self.ffprobe_box = self._path_row(form, "ffprobe path:", settings["ffprobe_path"])
        self.ytdlp_box = self._path_row(form, "yt-dlp path:", settings["ytdlp_path"])

        hint = QLabel(
            "If the paths are left empty, the bin\\ folder next to the app and PATH are searched.\n"
            f"Found: ffmpeg → {settings.ffmpeg() or 'NONE'}\n"
            f"Found: yt-dlp → {settings.ytdlp() or 'NONE'}"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _path_row(self, form: QFormLayout, label: str, value: str) -> QLineEdit:
        box = QLineEdit(value)
        box.setPlaceholderText("(automatic)")
        browse = QPushButton("Browse…")

        def on_browse():
            path, _ = QFileDialog.getOpenFileName(self, label, "", "Programs (*.exe);;All files (*.*)")
            if path:
                box.setText(path)

        browse.clicked.connect(on_browse)
        row = QHBoxLayout()
        row.addWidget(box)
        row.addWidget(browse)
        form.addRow(label, row)
        return box

    def accept(self):
        s = self.settings
        s["max_parallel_downloads"] = self.parallel_downloads.value()
        s["max_parallel_ffmpeg"] = self.parallel_ffmpeg.value()
        s["ffmpeg_path"] = self.ffmpeg_box.text().strip()
        s["ffprobe_path"] = self.ffprobe_box.text().strip()
        s["ytdlp_path"] = self.ytdlp_box.text().strip()
        s.save()
        super().accept()
