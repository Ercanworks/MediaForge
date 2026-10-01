"""Convert tab: batch codec/container conversion (HandBrake-style preset + quality)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget,
)

from helpers import duration_seconds, unique_path
from jobs import FfmpegJob

MEDIA_FILTER = ("Media files (*.mp4 *.mkv *.avi *.mov *.webm *.ts *.m2ts *.flv *.wmv "
                "*.mpg *.mpeg *.3gp *.mp3 *.m4a *.wav *.flac *.ogg *.opus *.aac *.wma);;"
                "All files (*.*)")

FAST = "Fast"
BALANCED = "Balanced"
SLOW = "Slow (higher compression)"
SPEED_OPTIONS = [FAST, BALANCED, SLOW]
ORIGINAL = "Original"


def _gpu_presets(encoders: frozenset | set) -> list[dict]:
    """Presets of the hardware encoders verified to work on this machine."""
    definitions = {
        "h264_nvenc": {
            "name": "H.264 (NVIDIA NVENC) — hardware accelerated",
            "crf": 23,
            "speed": {FAST: "p4", BALANCED: "p5", SLOW: "p7"},
            "v_args": lambda crf, speed: ["-c:v", "h264_nvenc", "-preset", speed,
                                          "-rc", "vbr", "-cq", str(crf), "-b:v", "0"],
        },
        "hevc_nvenc": {
            "name": "H.265 (NVIDIA NVENC) — hardware accelerated",
            "crf": 27,
            "speed": {FAST: "p4", BALANCED: "p5", SLOW: "p7"},
            "v_args": lambda crf, speed: ["-c:v", "hevc_nvenc", "-preset", speed,
                                          "-rc", "vbr", "-cq", str(crf), "-b:v", "0"],
            "mp4_extra": ["-tag:v", "hvc1"],
        },
        "av1_nvenc": {
            "name": "AV1 (NVIDIA NVENC) — hardware accelerated",
            "crf": 30,
            "speed": {FAST: "p4", BALANCED: "p5", SLOW: "p7"},
            "v_args": lambda crf, speed: ["-c:v", "av1_nvenc", "-preset", speed,
                                          "-rc", "vbr", "-cq", str(crf), "-b:v", "0"],
        },
        "h264_amf": {
            "name": "H.264 (AMD AMF) — hardware accelerated",
            "crf": 23,
            "speed": {FAST: "speed", BALANCED: "balanced", SLOW: "quality"},
            "v_args": lambda crf, speed: ["-c:v", "h264_amf", "-quality", speed,
                                          "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)],
        },
        "hevc_amf": {
            "name": "H.265 (AMD AMF) — hardware accelerated",
            "crf": 26,
            "speed": {FAST: "speed", BALANCED: "balanced", SLOW: "quality"},
            "v_args": lambda crf, speed: ["-c:v", "hevc_amf", "-quality", speed,
                                          "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)],
            "mp4_extra": ["-tag:v", "hvc1"],
        },
        "av1_amf": {
            "name": "AV1 (AMD AMF) — hardware accelerated",
            "crf": 30,
            "speed": {FAST: "speed", BALANCED: "balanced", SLOW: "quality"},
            "v_args": lambda crf, speed: ["-c:v", "av1_amf", "-quality", speed,
                                          "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)],
        },
        "h264_qsv": {
            "name": "H.264 (Intel QSV) — hardware accelerated",
            "crf": 23,
            "speed": {FAST: "veryfast", BALANCED: "medium", SLOW: "veryslow"},
            "v_args": lambda crf, speed: ["-c:v", "h264_qsv", "-preset", speed,
                                          "-global_quality", str(crf)],
        },
        "hevc_qsv": {
            "name": "H.265 (Intel QSV) — hardware accelerated",
            "crf": 26,
            "speed": {FAST: "veryfast", BALANCED: "medium", SLOW: "veryslow"},
            "v_args": lambda crf, speed: ["-c:v", "hevc_qsv", "-preset", speed,
                                          "-global_quality", str(crf)],
            "mp4_extra": ["-tag:v", "hvc1"],
        },
        "av1_qsv": {
            "name": "AV1 (Intel QSV) — hardware accelerated",
            "crf": 30,
            "speed": {FAST: "veryfast", BALANCED: "medium", SLOW: "veryslow"},
            "v_args": lambda crf, speed: ["-c:v", "av1_qsv", "-preset", speed,
                                          "-global_quality", str(crf)],
        },
    }
    presets = []
    for encoder in ("h264_nvenc", "hevc_nvenc", "av1_nvenc",
                    "h264_amf", "hevc_amf", "av1_amf",
                    "h264_qsv", "hevc_qsv", "av1_qsv"):
        if encoder in encoders:
            p = dict(definitions[encoder])
            p.update({"type": "video", "video": True, "containers": ["mp4", "mkv"]})
            presets.append(p)
    return presets


def _preset_list(encoders: frozenset | set = frozenset()) -> list[dict]:
    presets = [
        {
            "name": "H.264 (libx264) — wide compatibility",
            "type": "video",
            "crf": 22, "containers": ["mp4", "mkv", "mov"], "video": True,
            "speed": {FAST: "veryfast", BALANCED: "medium", SLOW: "slow"},
            "v_args": lambda crf, speed: ["-c:v", "libx264", "-crf", str(crf), "-preset", speed],
        },
        {
            "name": "H.265 (libx265) — high compression",
            "type": "video",
            "crf": 26, "containers": ["mp4", "mkv"], "video": True,
            "speed": {FAST: "fast", BALANCED: "medium", SLOW: "slow"},
            "v_args": lambda crf, speed: ["-c:v", "libx265", "-crf", str(crf), "-preset", speed],
            "mp4_extra": ["-tag:v", "hvc1"],
        },
        {
            "name": "AV1 (SVT-AV1) — highest compression",
            "type": "video",
            "crf": 30, "containers": ["mkv", "mp4", "webm"], "video": True,
            "speed": {FAST: "8", BALANCED: "6", SLOW: "4"},
            "v_args": lambda crf, speed: ["-c:v", "libsvtav1", "-crf", str(crf), "-preset", speed],
        },
    ]
    presets += _gpu_presets(encoders)
    presets += [
        {
            "name": "Remux — change container without re-encoding",
            "type": "remux",
            "crf": None, "containers": ["mp4", "mkv", "mov"], "video": False,
        },
        {
            "name": "Extract audio — MP3",
            "type": "mp3",
            "crf": None, "containers": ["mp3"], "video": False,
        },
    ]
    return presets


class ConvertTab(QWidget):
    def __init__(self, queue, settings, go_to_queue):
        super().__init__()
        self.queue = queue
        self.settings = settings
        self.go_to_queue = go_to_queue
        self.presets = _preset_list()

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Files (drag and drop supported):"))
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.file_list.setAcceptDrops(True)
        self.file_list.dragEnterEvent = self._drag_enter
        self.file_list.dragMoveEvent = self._drag_enter
        self.file_list.dropEvent = self._drop
        layout.addWidget(self.file_list, 1)

        row_files = QHBoxLayout()
        add = QPushButton("Add Files…")
        add.clicked.connect(self._add_files)
        remove = QPushButton("Remove Selected")
        remove.clicked.connect(self._remove_selected)
        clear = QPushButton("Clear List")
        clear.clicked.connect(self.file_list.clear)
        row_files.addWidget(add)
        row_files.addWidget(remove)
        row_files.addWidget(clear)
        row_files.addStretch()
        layout.addLayout(row_files)

        row_preset = QHBoxLayout()
        row_preset.addWidget(QLabel("Preset:"))
        self.preset_box = QComboBox()
        self.preset_box.addItems([p["name"] for p in self.presets])
        self.preset_box.currentIndexChanged.connect(self._preset_changed)
        row_preset.addWidget(self.preset_box, 1)
        row_preset.addWidget(QLabel("Format:"))
        self.container_box = QComboBox()
        row_preset.addWidget(self.container_box)
        layout.addLayout(row_preset)

        self.crf_label = QLabel()
        layout.addWidget(self.crf_label)

        row_crf = QHBoxLayout()
        self.crf_left = QLabel("High quality")
        self.crf_right = QLabel("Small file")
        for end in (self.crf_left, self.crf_right):
            end.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        self.crf = QSlider(Qt.Horizontal)
        self.crf.setRange(14, 40)
        self.crf.valueChanged.connect(self._update_crf_label)
        row_crf.addWidget(self.crf_left)
        row_crf.addWidget(self.crf, 1)
        row_crf.addWidget(self.crf_right)
        layout.addLayout(row_crf)

        row_options = QHBoxLayout()
        row_options.addWidget(QLabel("Speed:"))
        self.speed_box = QComboBox()
        self.speed_box.addItems(SPEED_OPTIONS)
        self.speed_box.setCurrentIndex(1)
        row_options.addWidget(self.speed_box)
        row_options.addWidget(QLabel("Resolution:"))
        self.resolution = QComboBox()
        self.resolution.addItems([ORIGINAL, "1080p", "720p", "480p"])
        row_options.addWidget(self.resolution)
        row_options.addWidget(QLabel("FPS:"))
        self.fps_box = QComboBox()
        self.fps_box.addItems([ORIGINAL, "60", "30", "25", "24"])
        self.fps_box.setToolTip("Frame rate of the output; left unchanged while Original is selected.")
        row_options.addWidget(self.fps_box)
        layout.addLayout(row_options)

        row_output = QHBoxLayout()
        self.same_folder = QRadioButton("Save to the source folder (with a '[MF]' suffix)")
        self.other_folder = QRadioButton("Save to:")
        if self.settings["convert_to_source_dir"]:
            self.same_folder.setChecked(True)
        else:
            self.other_folder.setChecked(True)
        self.output_box = QLineEdit(self.settings["convert_dir"])
        browse_output = QPushButton("Browse…")
        browse_output.clicked.connect(self._browse_output)
        row_output.addWidget(self.same_folder)
        row_output.addWidget(self.other_folder)
        row_output.addWidget(self.output_box, 1)
        row_output.addWidget(browse_output)
        layout.addLayout(row_output)

        self.start_button = QPushButton("Add to Queue  ▶")
        self.start_button.setMinimumHeight(36)
        self.start_button.clicked.connect(self._add_to_queue)
        layout.addWidget(self.start_button)

        self._preset_changed(0)

    # --- called by the main window once the hardware encoder probe finishes ---
    def enable_hardware(self, encoders: set[str]):
        selected_name = self.preset_box.currentText()
        self.presets = _preset_list(encoders)
        names = [p["name"] for p in self.presets]
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItems(names)
        # The selection is kept by name; the index can shift with the added GPU presets
        index = names.index(selected_name) if selected_name in names else 0
        self.preset_box.setCurrentIndex(index)
        self.preset_box.blockSignals(False)
        self._preset_changed(index)

    # --- drag and drop ---
    def _drag_enter(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.isfile(path):
                self._add_unique(path)
        event.acceptProposedAction()

    def _add_unique(self, path: str):
        existing = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        if path not in existing:
            self.file_list.addItem(path)

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select files", "", MEDIA_FILTER)
        for f in files:
            self._add_unique(f)

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Output folder", self.output_box.text())
        if path:
            self.output_box.setText(path)
            self.other_folder.setChecked(True)

    # --- preset/quality UI ---
    def _preset_changed(self, index: int):
        p = self.presets[index]
        self.container_box.clear()
        self.container_box.addItems(p["containers"])
        video = p.get("video", False)
        for w in (self.crf, self.crf_label, self.crf_left, self.crf_right,
                  self.speed_box, self.resolution, self.fps_box):
            w.setEnabled(video)
        if p.get("crf") is not None:
            self.crf.setValue(p["crf"])
        self._update_crf_label(self.crf.value())

    def _update_crf_label(self, value: int):
        # The description depends on the distance from the selected preset's recommended
        # value, so the scale difference between codecs (x264 22 ≈ x265 26 ≈ AV1 30) doesn't matter.
        default = self.presets[self.preset_box.currentIndex()].get("crf")
        description = ""
        if default is not None:
            diff = value - default
            if diff <= -6:
                description = "visually near lossless, very large file"
            elif diff <= -2:
                description = "high quality, larger file"
            elif diff <= 1:
                description = "recommended balance"
            elif diff <= 5:
                description = "smaller file, slight quality loss"
            else:
                description = "smallest file, noticeable quality loss"
        self.crf_label.setText(f"Quality (CRF): {value}" + (f"  —  {description}" if description else ""))
        self.crf.setToolTip(
            "CRF (Constant Rate Factor): the encoder's quality target.\n"
            "Moving left increases quality and file size, moving right makes the file smaller.\n"
            "The recommended value is set automatically when a preset is selected.")

    # --- job creation ---
    def _add_to_queue(self):
        ffmpeg = self.settings.ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, "ffmpeg missing",
                                "ffmpeg was not found. Set its path in Settings.")
            return
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        if not files:
            QMessageBox.information(self, "No files", "Add the files to convert first.")
            return
        if self.other_folder.isChecked() and not os.path.isdir(self.output_box.text().strip()):
            QMessageBox.warning(self, "Folder missing", "Select a valid output folder.")
            return

        s = self.settings
        s["convert_to_source_dir"] = self.same_folder.isChecked()
        s["convert_dir"] = self.output_box.text().strip()
        s.save()

        p = self.presets[self.preset_box.currentIndex()]
        container = self.container_box.currentText()
        ffprobe = s.ffprobe()

        for file in files:
            stem = os.path.splitext(os.path.basename(file))[0]
            if self.same_folder.isChecked():
                target_dir = os.path.dirname(file)
                output = os.path.join(target_dir, f"{stem} [MF].{container}")
            else:
                output = os.path.join(self.output_box.text().strip(), f"{stem}.{container}")
            if os.path.abspath(output) == os.path.abspath(file):
                output = os.path.join(os.path.dirname(file), f"{stem} [MF].{container}")
            output = unique_path(output)

            args = self._build_args(p, file, output, container)
            duration = duration_seconds(file, ffprobe) if ffprobe else None
            self.queue.add(FfmpegJob(os.path.basename(output), ffmpeg, args, output, duration))

        self.file_list.clear()
        self.go_to_queue()

    def _build_args(self, p: dict, input_file: str, output: str, container: str) -> list[str]:
        args = ["-i", input_file]

        if p["type"] == "remux":
            # remux: carry all streams into mkv, default stream selection for mp4/mov
            if container == "mkv":
                args += ["-map", "0", "-c", "copy"]
            else:
                args += ["-c", "copy"]
        elif p["type"] == "mp3":
            args += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
        else:
            speed = p["speed"][self.speed_box.currentText()]
            args += p["v_args"](self.crf.value(), speed)
            if container == "mp4" and p.get("mp4_extra"):
                args += p["mp4_extra"]
            resolution = self.resolution.currentText()
            if resolution != ORIGINAL:
                args += ["-vf", f"scale=-2:'min({resolution[:-1]},ih)'"]
            fps = self.fps_box.currentText()
            if fps != ORIGINAL:
                args += ["-r", fps]
            # audio: mp4/mov → aac, mkv/webm → opus
            if container in ("mp4", "mov"):
                args += ["-c:a", "aac", "-b:a", "192k"]
            else:
                args += ["-c:a", "libopus", "-b:a", "128k"]

        if container in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]
        args.append(output)
        return args
