"""Shared helpers: locating external tools, ffprobe, formatting."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

if getattr(sys, "frozen", False):
    # PyInstaller: settings/bin live next to the exe, bundled data in _MEIPASS
    APP_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = APP_DIR
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_path_cache: dict[str, str | None] = {}


def clear_path_cache():
    """Called after a tool update so that bin\\ gets scanned again."""
    _path_cache.clear()


def tool_path(name: str, custom_path: str = "") -> str | None:
    """Locates ffmpeg/ffprobe/yt-dlp.

    Priority: custom path from the settings → bin/ next to the app → PATH.
    """
    if custom_path and os.path.isfile(custom_path):
        return custom_path
    if name in _path_cache:
        return _path_cache[name]
    candidate = os.path.join(APP_DIR, "bin", name + (".exe" if os.name == "nt" else ""))
    path = candidate if os.path.isfile(candidate) else shutil.which(name)
    _path_cache[name] = path
    return path


def ffprobe_info(file: str, ffprobe: str) -> dict | None:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", file],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def duration_seconds(file: str, ffprobe: str) -> float | None:
    info = ffprobe_info(file, ffprobe)
    if info:
        try:
            return float(info["format"]["duration"])
        except (KeyError, ValueError):
            pass
    return None


# Hardware encoders to probe: NVIDIA (nvenc), AMD (amf), Intel (qsv)
# AV1 encoding only exists on newer cards (RTX 40+, RX 7000+, Arc) —
# on machines where the test encode fails, the preset simply doesn't show up.
HARDWARE_ENCODERS = (
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
    "h264_amf", "hevc_amf", "av1_amf",
    "h264_qsv", "hevc_qsv", "av1_qsv",
)


def encoder_works(ffmpeg: str, encoder: str) -> bool:
    """Whether the encoder actually works on this machine (1-frame test encode).

    Being listed is not enough: without the driver/hardware the encoder fails on open.
    """
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1",
             "-c:v", encoder, "-f", "null", "-"],
            capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def working_hardware_encoders(ffmpeg: str) -> set[str]:
    return {e for e in HARDWARE_ENCODERS if encoder_works(ffmpeg, e)}


def normalize_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def unique_path(path: str, reserved: set[str] = frozenset()) -> str:
    """If the file exists, counts up as 'name (1).ext', 'name (2).ext'.

    `reserved` holds normalized paths that queued jobs will write to but that
    don't exist on disk yet, so two queued jobs never get the same output.
    """
    def taken(p: str) -> bool:
        return os.path.exists(p) or normalize_path(p) in reserved

    if not taken(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while taken(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def human_time(seconds: float) -> str:
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_time(text: str) -> float | None:
    """Converts inputs like '90', '1:30', '01:02:03.5' to seconds."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    if len(parts) in (2, 3):
        try:
            total = 0.0
            for p in parts:
                total = total * 60 + float(p)
            return total
        except ValueError:
            return None
    return None


def media_summary(info: dict, file: str) -> str:
    """Builds a readable summary from ffprobe output."""
    lines = [f"File     : {os.path.basename(file)}"]
    fmt = info.get("format", {})
    try:
        lines.append(f"Size     : {human_size(float(fmt.get('size', 0)))}")
    except ValueError:
        pass
    try:
        lines.append(f"Duration : {human_time(float(fmt['duration']))}")
    except (KeyError, ValueError):
        pass
    if fmt.get("bit_rate"):
        lines.append(f"Bitrate  : {int(fmt['bit_rate']) // 1000} kb/s (total)")
    lines.append(f"Format   : {fmt.get('format_long_name', fmt.get('format_name', '?'))}")
    lines.append("")

    for stream in info.get("streams", []):
        kind = stream.get("codec_type")
        codec = stream.get("codec_name", "?")
        lang = stream.get("tags", {}).get("language", "")
        lang = f" [{lang}]" if lang else ""
        if kind == "video":
            fps = stream.get("avg_frame_rate", "0/1")
            try:
                num, den = fps.split("/")
                fps = f"{float(num) / float(den):.2f}" if float(den) else "?"
            except ValueError:
                fps = "?"
            br = stream.get("bit_rate")
            br = f", {int(br) // 1000} kb/s" if br else ""
            lines.append(
                f"Video #{stream.get('index')}: {codec}, "
                f"{stream.get('width')}x{stream.get('height')}, {fps} fps{br}{lang}"
            )
        elif kind == "audio":
            br = stream.get("bit_rate")
            br = f", {int(br) // 1000} kb/s" if br else ""
            lines.append(
                f"Audio #{stream.get('index')}: {codec}, "
                f"{stream.get('channels', '?')} ch, {stream.get('sample_rate', '?')} Hz{br}{lang}"
            )
        elif kind == "subtitle":
            lines.append(f"Subtitle #{stream.get('index')}: {codec}{lang}")
        else:
            lines.append(f"{kind or '?'} #{stream.get('index')}: {codec}")
    return "\n".join(lines)
