"""Download tab: downloads video/audio from links with yt-dlp."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from jobs import DownloadJob

# (label, yt-dlp arguments, audio only?)
FORMATS = [
    ("Video — best quality (MP4)",
     ["-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b", "--merge-output-format", "mp4"],
     False),
    ("Video — best quality (original format)",
     ["-f", "bv*+ba/b"],
     False),
    ("Video — 1080p (MP4)",
     ["-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
      "--merge-output-format", "mp4"],
     False),
    ("Video — 720p (MP4)",
     ["-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b",
      "--merge-output-format", "mp4"],
     False),
    ("Audio — MP3",
     ["-x", "--audio-format", "mp3", "--audio-quality", "0"],
     True),
    ("Audio — original format (m4a/opus)",
     ["-x"],
     True),
]


class DownloadTab(QWidget):
    def __init__(self, queue, settings, go_to_queue):
        super().__init__()
        self.queue = queue
        self.settings = settings
        self.go_to_queue = go_to_queue

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Links (one per line):"))
        self.url_box = QPlainTextEdit()
        self.url_box.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url_box.setMaximumHeight(120)
        layout.addWidget(self.url_box)

        paste = QPushButton("Paste from Clipboard")
        paste.clicked.connect(self._paste)
        row0 = QHBoxLayout()
        row0.addWidget(paste)
        row0.addStretch()
        layout.addLayout(row0)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Format:"))
        self.format = QComboBox()
        self.format.addItems([f[0] for f in FORMATS])
        self.format.setCurrentIndex(int(settings["download_format"]))
        row1.addWidget(self.format, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Folder:"))
        self.folder_box = QLineEdit(settings["download_dir"])
        row2.addWidget(self.folder_box, 1)
        browse_folder = QPushButton("Browse…")
        browse_folder.clicked.connect(self._browse_folder)
        row2.addWidget(browse_folder)
        layout.addLayout(row2)

        self.playlist_box = QCheckBox("Download the whole playlist for playlist links")
        self.playlist_box.setChecked(bool(settings["whole_playlist"]))
        layout.addWidget(self.playlist_box)

        self.tags_box = QCheckBox("Embed cover art and metadata in audio files")
        self.tags_box.setChecked(bool(settings["embed_audio_tags"]))
        layout.addWidget(self.tags_box)

        self.download_button = QPushButton("Download  ⤓")
        self.download_button.setMinimumHeight(36)
        self.download_button.clicked.connect(self._download)
        layout.addWidget(self.download_button)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #9aa0a6;")
        layout.addWidget(self.status)
        layout.addStretch()

    def _paste(self):
        text = QApplication.clipboard().text().strip()
        if text:
            current = self.url_box.toPlainText().rstrip()
            self.url_box.setPlainText((current + "\n" + text).strip())

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Download folder", self.folder_box.text())
        if path:
            self.folder_box.setText(path)

    def _download(self):
        ytdlp = self.settings.ytdlp()
        if not ytdlp:
            QMessageBox.warning(self, "yt-dlp missing",
                                "yt-dlp was not found. Set its path in Settings\n"
                                "or install it with 'pip install yt-dlp'.")
            return

        urls = [s.strip() for s in self.url_box.toPlainText().splitlines()
                if s.strip().startswith(("http://", "https://"))]
        if not urls:
            QMessageBox.information(self, "No links", "Paste a valid link (it must start with http…).")
            return

        folder = self.folder_box.text().strip()
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "Folder missing", f"Download folder not found:\n{folder}")
            return

        # Remember the choices
        s = self.settings
        s["download_dir"] = folder
        s["download_format"] = self.format.currentIndex()
        s["whole_playlist"] = self.playlist_box.isChecked()
        s["embed_audio_tags"] = self.tags_box.isChecked()
        s.save()

        _, format_args, audio_only = FORMATS[self.format.currentIndex()]
        args = list(format_args)
        args.append("--yes-playlist" if self.playlist_box.isChecked() else "--no-playlist")
        if audio_only and self.tags_box.isChecked():
            args += ["--embed-thumbnail", "--embed-metadata"]

        for url in urls:
            self.queue.add(DownloadJob(url, folder, args, ytdlp, s.ffmpeg()))

        self.url_box.clear()
        self.status.setText(f"{len(urls)} download(s) added to the queue.")
        self.go_to_queue()
