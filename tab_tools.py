"""Tools tab: media info, trimming, audio extraction, joining videos."""
from __future__ import annotations

import os
import tempfile

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QScrollArea,
    QTextEdit, QVBoxLayout, QWidget,
)

from helpers import duration_seconds, ffprobe_info, media_summary, parse_time, unique_path
from jobs import FfmpegJob
from tab_convert import MEDIA_FILTER

# audio codec → file extension for lossless extraction
COPY_EXTENSION = {
    "aac": "m4a", "alac": "m4a", "mp3": "mp3", "opus": "opus",
    "vorbis": "ogg", "flac": "flac", "ac3": "ac3", "eac3": "eac3",
}


class ToolsTab(QWidget):
    def __init__(self, queue, settings, go_to_queue):
        super().__init__()
        self.queue = queue
        self.settings = settings
        self.go_to_queue = go_to_queue

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        layout.addWidget(self._info_group())
        layout.addWidget(self._trim_group())
        layout.addWidget(self._audio_group())
        layout.addWidget(self._join_group())
        layout.addStretch()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

    # --- shared parts ---
    def _file_row(self) -> tuple[QHBoxLayout, QLineEdit]:
        box = QLineEdit()
        box.setPlaceholderText("Select a file…")
        browse = QPushButton("Browse…")

        def on_browse():
            path, _ = QFileDialog.getOpenFileName(self, "Select file", "", MEDIA_FILTER)
            if path:
                box.setText(path)

        browse.clicked.connect(on_browse)
        row = QHBoxLayout()
        row.addWidget(QLabel("File:"))
        row.addWidget(box, 1)
        row.addWidget(browse)
        return row, box

    def _validate_file(self, box: QLineEdit) -> str | None:
        path = box.text().strip().strip('"')
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "No file", "Select a valid file first.")
            return None
        return path

    # --- 1) Media info ---
    def _info_group(self) -> QGroupBox:
        group = QGroupBox("Media Info (ffprobe)")
        layout = QVBoxLayout(group)
        row, self.info_file = self._file_row()
        layout.addLayout(row)
        show = QPushButton("Show Info")
        show.clicked.connect(self._show_info)
        layout.addWidget(show)
        return group

    def _show_info(self):
        path = self._validate_file(self.info_file)
        if not path:
            return
        ffprobe = self.settings.ffprobe()
        if not ffprobe:
            QMessageBox.warning(self, "ffprobe missing", "ffprobe was not found (it ships with ffmpeg).")
            return
        info = ffprobe_info(path, ffprobe)
        if not info:
            QMessageBox.warning(self, "Unreadable", "Could not read file info — corrupt or unsupported file.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(os.path.basename(path))
        dialog.resize(560, 380)
        v = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFontFamily("Consolas")
        text.setPlainText(media_summary(info, path))
        v.addWidget(text)
        dialog.exec()

    # --- 2) Trimming ---
    def _trim_group(self) -> QGroupBox:
        group = QGroupBox("Trim Video / Audio")
        layout = QVBoxLayout(group)
        row, self.trim_file = self._file_row()
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Start:"))
        self.trim_start = QLineEdit()
        self.trim_start.setPlaceholderText("0:00 or 90 (seconds)")
        row2.addWidget(self.trim_start)
        row2.addWidget(QLabel("End:"))
        self.trim_end = QLineEdit()
        self.trim_end.setPlaceholderText("empty = until the end")
        row2.addWidget(self.trim_end)
        layout.addLayout(row2)

        self.trim_exact = QCheckBox("Frame-accurate trim (re-encodes)")
        layout.addWidget(self.trim_exact)
        hint = QLabel("When off, the trim is done without re-encoding; "
                      "the start point snaps to the nearest keyframe.")
        hint.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        layout.addWidget(hint)

        add = QPushButton("Add to Queue")
        add.clicked.connect(self._add_trim)
        layout.addWidget(add)
        return group

    def _add_trim(self):
        path = self._validate_file(self.trim_file)
        if not path:
            return
        ffmpeg = self.settings.ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, "ffmpeg missing", "ffmpeg was not found. Set its path in Settings.")
            return

        start = parse_time(self.trim_start.text()) or 0.0
        end = parse_time(self.trim_end.text())
        if end is not None and end <= start:
            QMessageBox.warning(self, "Invalid time", "End must be greater than start.")
            return

        stem, ext = os.path.splitext(path)
        output = unique_path(f"{stem} (cut){ext}")

        args = ["-ss", str(start), "-i", path]
        total = duration_seconds(path, self.settings.ffprobe()) if self.settings.ffprobe() else None
        if end is not None:
            args += ["-t", str(end - start)]
            job_duration = end - start
        else:
            job_duration = (total - start) if total else None

        if self.trim_exact.isChecked():
            args += ["-c:v", "libx264", "-crf", "20", "-preset", "medium",
                     "-c:a", "aac", "-b:a", "192k"]
        else:
            args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        args.append(output)

        self.queue.add(FfmpegJob(os.path.basename(output), ffmpeg, args, output, job_duration))
        self.go_to_queue()

    # --- 3) Audio extraction ---
    def _audio_group(self) -> QGroupBox:
        group = QGroupBox("Extract Audio from Video")
        layout = QVBoxLayout(group)
        row, self.audio_file = self._file_row()
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Format:"))
        self.audio_format = QComboBox()
        self.audio_format.addItems([
            "Lossless — original codec, no re-encoding",
            "MP3 — 192 kb/s",
            "MP3 — 320 kb/s",
        ])
        row2.addWidget(self.audio_format, 1)
        layout.addLayout(row2)

        add = QPushButton("Add to Queue")
        add.clicked.connect(self._add_audio)
        layout.addWidget(add)
        return group

    def _add_audio(self):
        path = self._validate_file(self.audio_file)
        if not path:
            return
        ffmpeg = self.settings.ffmpeg()
        ffprobe = self.settings.ffprobe()
        if not ffmpeg:
            QMessageBox.warning(self, "ffmpeg missing", "ffmpeg was not found. Set its path in Settings.")
            return

        stem = os.path.splitext(path)[0]
        choice = self.audio_format.currentIndex()
        duration = duration_seconds(path, ffprobe) if ffprobe else None

        if choice == 0:
            codec = ""
            if ffprobe:
                info = ffprobe_info(path, ffprobe)
                if info:
                    codec = next((s.get("codec_name", "") for s in info.get("streams", [])
                                  if s.get("codec_type") == "audio"), "")
            if not codec:
                QMessageBox.warning(self, "No audio", "No audio stream found in the file.")
                return
            ext = COPY_EXTENSION.get(codec, "wav" if codec.startswith("pcm") else "mka")
            output = unique_path(f"{stem}.{ext}")
            args = ["-i", path, "-vn", "-c:a", "copy", output]
        else:
            bitrate = "192k" if choice == 1 else "320k"
            output = unique_path(f"{stem}.mp3")
            args = ["-i", path, "-vn", "-c:a", "libmp3lame", "-b:a", bitrate, output]

        self.queue.add(FfmpegJob(os.path.basename(output), ffmpeg, args, output, duration))
        self.go_to_queue()

    # --- 4) Joining videos ---
    def _join_group(self) -> QGroupBox:
        group = QGroupBox("Join Videos")
        layout = QVBoxLayout(group)

        self.join_list = QListWidget()
        self.join_list.setMaximumHeight(110)
        self.join_list.setAcceptDrops(True)
        self.join_list.dragEnterEvent = self._join_drag
        self.join_list.dragMoveEvent = self._join_drag
        self.join_list.dropEvent = self._join_drop
        layout.addWidget(self.join_list)

        row = QHBoxLayout()
        add = QPushButton("Add Files…")
        add.clicked.connect(self._join_add_files)
        up = QPushButton("Up")
        up.clicked.connect(lambda: self._join_move(-1))
        down = QPushButton("Down")
        down.clicked.connect(lambda: self._join_move(1))
        remove = QPushButton("Remove")
        remove.clicked.connect(self._join_remove)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.join_list.clear)
        for b in (add, up, down, remove, clear):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)

        self.join_lossless = QCheckBox("Join losslessly (no re-encoding)")
        self.join_lossless.setToolTip(
            "Joins instantly if all parts share the same codec, resolution and parameters "
            "(e.g. one recording split into parts). Uncheck it for different sources; the parts "
            "are then re-encoded to match the resolution and frame rate of the first video.")
        layout.addWidget(self.join_lossless)

        join = QPushButton("Add to Queue")
        join.clicked.connect(self._add_join)
        layout.addWidget(join)
        return group

    def _join_drag(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _join_drop(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.isfile(path):
                self.join_list.addItem(path)
        event.acceptProposedAction()

    def _join_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Files to join", "", MEDIA_FILTER)
        for f in files:
            self.join_list.addItem(f)

    def _join_remove(self):
        for item in self.join_list.selectedItems():
            self.join_list.takeItem(self.join_list.row(item))

    def _join_move(self, direction: int):
        row = self.join_list.currentRow()
        target = row + direction
        if row < 0 or not (0 <= target < self.join_list.count()):
            return
        item = self.join_list.takeItem(row)
        self.join_list.insertItem(target, item)
        self.join_list.setCurrentRow(target)

    def _add_join(self):
        files = [self.join_list.item(i).text() for i in range(self.join_list.count())]
        if len(files) < 2:
            QMessageBox.information(self, "Not enough files", "Add at least two files to join.")
            return
        ffmpeg = self.settings.ffmpeg()
        ffprobe = self.settings.ffprobe()
        if not ffmpeg or not ffprobe:
            QMessageBox.warning(self, "Tools missing", "ffmpeg/ffprobe was not found. Set the paths in Settings.")
            return

        durations = [duration_seconds(f, ffprobe) for f in files]
        total_duration = sum(durations) if all(d is not None for d in durations) else None
        first = files[0]
        stem = os.path.splitext(os.path.basename(first))[0]

        if self.join_lossless.isChecked():
            ext = os.path.splitext(first)[1] or ".mp4"
            output = unique_path(os.path.join(os.path.dirname(first), f"{stem} (joined){ext}"))
            fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="mediaforge_concat_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for file in files:
                    escaped = file.replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")
            args = ["-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output]
            job = FfmpegJob(os.path.basename(output), ffmpeg, args, output, total_duration)
            job.temp_files.append(list_path)
        else:
            output = unique_path(os.path.join(os.path.dirname(first), f"{stem} (joined).mp4"))
            job = self._reencoding_join(files, output, total_duration, ffmpeg, ffprobe)
            if job is None:
                return

        self.queue.add(job)
        self.join_list.clear()
        self.go_to_queue()

    def _reencoding_join(self, files, output, total_duration,
                         ffmpeg, ffprobe) -> FfmpegJob | None:
        """Matches the parts to the first video's resolution/fps and joins them with the concat filter."""
        infos = [ffprobe_info(f, ffprobe) for f in files]
        if any(i is None for i in infos):
            QMessageBox.warning(self, "Unreadable", "One of the files could not be analyzed; try them one by one to see the log.")
            return None

        def video_stream(info):
            return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)

        first_video = video_stream(infos[0])
        if not first_video:
            QMessageBox.warning(self, "No video", "No video stream found in the first file.")
            return None
        width = int(first_video.get("width", 1920))
        height = int(first_video.get("height", 1080))
        fps = first_video.get("avg_frame_rate", "30")
        if not fps or fps.startswith("0"):
            fps = "30"

        # Audio: kept if every part has it, otherwise the output is silent
        has_audio = all(any(s.get("codec_type") == "audio" for s in info.get("streams", []))
                        for info in infos)

        args: list[str] = []
        for f in files:
            args += ["-i", f]
        filters, inputs = [], ""
        for i in range(len(files)):
            filters.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]")
            inputs += f"[v{i}]"
            if has_audio:
                filters.append(f"[{i}:a]aresample=48000[a{i}]")
                inputs += f"[a{i}]"
        filters.append(
            f"{inputs}concat=n={len(files)}:v=1:a={1 if has_audio else 0}"
            + ("[v][a]" if has_audio else "[v]"))

        args += ["-filter_complex", ";".join(filters), "-map", "[v]"]
        if has_audio:
            args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        args += ["-c:v", "libx264", "-crf", "20", "-preset", "medium",
                 "-movflags", "+faststart", output]
        return FfmpegJob(os.path.basename(output), ffmpeg, args, output, total_duration)
