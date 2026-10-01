"""Job classes (download / ffmpeg) and the queue manager."""
from __future__ import annotations

import collections
import ctypes
import itertools
import os
import re
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from helpers import human_time, normalize_path

WAITING = "Waiting"
RUNNING = "Running"
PAUSED = "Paused"
DONE = "Done"
FAILED = "Failed"
CANCELLED = "Cancelled"

ACTIVE = (WAITING, RUNNING, PAUSED)


def _suspend_process(pid: int, suspend: bool) -> bool:
    """Suspends/resumes a process (NtSuspendProcess — the only way on Windows)."""
    if os.name != "nt" or not pid:
        return False
    PROCESS_SUSPEND_RESUME = 0x0800
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, int(pid))
    if not handle:
        return False
    try:
        nt = ctypes.windll.ntdll
        result = nt.NtSuspendProcess(handle) if suspend else nt.NtResumeProcess(handle)
        return result == 0
    finally:
        kernel32.CloseHandle(handle)


class Job(QObject):
    """A single queue job. Subclasses define command() and handle_line()."""

    progress = Signal(int)      # 0-100, -1 = indeterminate
    changed = Signal()          # title/status/detail text changed
    finished_signal = Signal(object)

    kind = "generic"
    kind_label = "Job"
    _counter = itertools.count()

    def __init__(self, title: str):
        super().__init__()
        self.job_id = next(Job._counter)   # stable id for mapping to table rows
        self.title = title
        self.status = WAITING
        self.detail = ""
        self.percent = -1
        self.log: collections.deque[str] = collections.deque(maxlen=400)
        self.process: QProcess | None = None
        self.output_file = ""       # file/folder to open once finished
        self.output_dir = ""
        self._cancel_requested = False
        self._buffer_out = ""
        self._buffer_err = ""
        self._last_detail_time = 0.0

    def _emit_detail(self):
        """Limits progress-driven detail updates to ~3/s at most
        (yt-dlp/ffmpeg print lines so often that the UI text flickers)."""
        now = time.monotonic()
        if now - self._last_detail_time >= 0.3:
            self._last_detail_time = now
            self.changed.emit()

    # --- subclasses ---
    def command(self) -> list[str]:
        raise NotImplementedError

    def handle_line(self, line: str, is_stderr: bool):
        pass

    def cleanup_on_cancel(self):
        pass

    def cleanup_on_finish(self):
        """Called on every termination (success/failure/cancel)."""
        pass

    # --- running ---
    def start(self):
        cmd = self.command()
        self.log.append("$ " + " ".join(cmd))
        self.process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read_out)
        self.process.readyReadStandardError.connect(self._read_err)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_process_error)
        self.status = RUNNING
        self.detail = "Started"
        self.changed.emit()
        self.process.setProgram(cmd[0])
        self.process.setArguments(cmd[1:])
        self.process.start()

    def cancel(self):
        self._cancel_requested = True
        if self.process and self.process.state() != QProcess.NotRunning:
            if self.status == PAUSED:
                # A suspended process is resumed before it gets killed
                _suspend_process(self.process.processId(), False)
            self.process.kill()
        elif self.status in (WAITING, PAUSED):
            self.status = CANCELLED
            self.detail = ""
            self.changed.emit()
            self.finished_signal.emit(self)

    def pause(self):
        if self.status == RUNNING and self.process and self.process.state() != QProcess.NotRunning:
            if _suspend_process(self.process.processId(), True):
                self.status = PAUSED
                self.changed.emit()
        elif self.status == WAITING:
            # Not started even when its turn comes; rejoins the queue when resumed
            self.status = PAUSED
            self.changed.emit()

    def resume(self):
        if self.status != PAUSED:
            return
        if self.process and self.process.state() != QProcess.NotRunning:
            if _suspend_process(self.process.processId(), False):
                self.status = RUNNING
                self.changed.emit()
        else:
            self.status = WAITING
            self.changed.emit()

    # --- internals ---
    def _read_out(self):
        self._buffer_out = self._dispatch(
            self._buffer_out + bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace"),
            False,
        )

    def _read_err(self):
        self._buffer_err = self._dispatch(
            self._buffer_err + bytes(self.process.readAllStandardError()).decode("utf-8", "replace"),
            True,
        )

    def _dispatch(self, text: str, is_stderr: bool) -> str:
        lines = re.split(r"[\r\n]", text)
        rest = lines.pop()  # the last piece may not be complete yet
        for line in lines:
            line = line.strip()
            if line:
                self.log.append(line)
                self.handle_line(line, is_stderr)
        return rest

    def _on_process_error(self, error):
        if error == QProcess.FailedToStart:
            self.status = FAILED
            self.detail = "Program could not be started — check its path in Settings"
            self.cleanup_on_finish()
            self.changed.emit()
            self.finished_signal.emit(self)

    def _on_finished(self, exit_code: int, _status):
        if self._cancel_requested:
            self.status = CANCELLED
            self.detail = ""
            self.cleanup_on_cancel()
        elif exit_code == 0:
            self.status = DONE
            self.percent = 100
            self.detail = "Completed"
            self.progress.emit(100)
        else:
            self.status = FAILED
            error_line = next(
                (s for s in reversed(self.log) if "error" in s.lower() or "invalid" in s.lower()),
                None,
            )
            self.detail = (error_line or f"Exit code {exit_code}")[:160]
        self.cleanup_on_finish()
        self.changed.emit()
        self.finished_signal.emit(self)


class FfmpegJob(Job):
    """Extracts percent/speed/remaining time from `-progress pipe:1` output."""

    kind = "ffmpeg"
    kind_label = "Convert"

    def __init__(self, title: str, ffmpeg: str, args: list[str],
                 output_file: str, duration: float | None):
        super().__init__(title)
        self.ffmpeg = ffmpeg
        self.args = args
        self.output_file = output_file
        self.output_dir = os.path.dirname(output_file)
        self.duration = duration
        self.temp_files: list[str] = []   # deleted when the job ends (concat list etc.)
        self._speed = 0.0

    def command(self) -> list[str]:
        return [self.ffmpeg, "-hide_banner", "-y",
                "-progress", "pipe:1", "-nostats", *self.args]

    def handle_line(self, line: str, is_stderr: bool):
        if is_stderr or "=" not in line:
            return
        key, _, value = line.partition("=")
        if key in ("out_time_us", "out_time_ms"):
            try:
                elapsed = int(value) / 1_000_000
            except ValueError:
                return
            if self.duration and self.duration > 0:
                self.percent = min(99, int(elapsed / self.duration * 100))
                self.progress.emit(self.percent)
                parts = []
                if self._speed > 0:
                    parts.append(f"{self._speed:.1f}x")
                    parts.append(human_time((self.duration - elapsed) / self._speed) + " left")
                self.detail = " • ".join(parts)
            else:
                self.detail = "Processed: " + human_time(elapsed)
            self._emit_detail()
        elif key == "speed":
            match = re.match(r"([\d.]+)x", value.strip())
            if match:
                self._speed = float(match.group(1))

    def cleanup_on_cancel(self):
        # Delete the half-written output
        try:
            if self.output_file and os.path.isfile(self.output_file):
                os.remove(self.output_file)
        except OSError:
            pass

    def cleanup_on_finish(self):
        for path in self.temp_files:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass


class DownloadJob(Job):
    """yt-dlp process; percent/speed/ETA are parsed via --progress-template."""

    kind = "download"
    kind_label = "Download"

    TEMPLATE = "download:MFP|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"

    def __init__(self, url: str, folder: str, option_args: list[str],
                 ytdlp: str, ffmpeg: str | None):
        super().__init__(url)
        self.url = url
        self.folder = folder
        self.output_dir = folder
        self.option_args = option_args
        self.ytdlp = ytdlp
        self.ffmpeg_dir = os.path.dirname(ffmpeg) if ffmpeg else ""
        self._item = ""

    def command(self) -> list[str]:
        cmd = [self.ytdlp, "--newline", "--color", "no_color",
               "--progress-template", self.TEMPLATE,
               "--windows-filenames",
               "-P", self.folder, "-o", "%(title)s.%(ext)s",
               *self.option_args]
        if self.ffmpeg_dir:
            cmd += ["--ffmpeg-location", self.ffmpeg_dir]
        cmd.append(self.url)
        return cmd

    def handle_line(self, line: str, is_stderr: bool):
        if line.startswith("MFP|"):
            parts = line.split("|")
            if len(parts) >= 4:
                try:
                    self.percent = int(float(parts[1].strip().rstrip("%")))
                    self.progress.emit(self.percent)
                except ValueError:
                    pass
                speed, eta = parts[2].strip(), parts[3].strip()
                details = []
                if speed and speed not in ("NA", "Unknown"):
                    details.append(speed)
                if eta and eta not in ("NA", "Unknown"):
                    details.append(f"{eta} left")
                self.detail = self._item + " • ".join(details)
                self._emit_detail()
            return

        match = re.search(r"\[download\] Downloading item (\d+) of (\d+)", line)
        if match:
            self._item = f"Item {match.group(1)}/{match.group(2)} • "
            return
        if line.startswith("[download] Destination:"):
            self.title = os.path.basename(line.split("Destination:", 1)[1].strip())
            self.changed.emit()
        elif line.startswith("[Merger]"):
            self.detail = self._item + "Merging video and audio…"
            self.changed.emit()
        elif line.startswith("[ExtractAudio]"):
            self.detail = self._item + "Converting audio…"
            self.changed.emit()
        elif line.startswith(("[EmbedThumbnail]", "[Metadata]")):
            self.detail = self._item + "Writing tags…"
            self.changed.emit()


class QueueManager(QObject):
    """Runs waiting jobs with a concurrency limit per job kind."""

    structure_changed = Signal()   # jobs were added to or removed from the list
    active_count_changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.jobs: list[Job] = []

    def add(self, job: Job):
        self.jobs.append(job)
        job.finished_signal.connect(self._job_finished)
        self.structure_changed.emit()
        self.active_count_changed.emit()
        self.pump()

    def reserved_outputs(self) -> set[str]:
        """Output files of unfinished jobs, which may not exist on disk yet."""
        return {normalize_path(j.output_file) for j in self.jobs
                if j.status in ACTIVE and j.output_file}

    def pump(self):
        limits = {
            "download": int(self.settings["max_parallel_downloads"]),
            "ffmpeg": int(self.settings["max_parallel_ffmpeg"]),
        }
        for kind, limit in limits.items():
            running = sum(1 for j in self.jobs if j.kind == kind and j.status == RUNNING)
            for job in self.jobs:
                if running >= limit:
                    break
                if job.kind == kind and job.status == WAITING and not job._cancel_requested:
                    job.start()
                    if job.status == RUNNING:
                        running += 1

    def _job_finished(self, _job):
        self.active_count_changed.emit()
        self.pump()

    def active_count(self) -> int:
        return sum(1 for j in self.jobs if j.status in ACTIVE)

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if j.status in ACTIVE]
        self.structure_changed.emit()

    def cancel_all(self):
        for job in self.jobs:
            if job.status in ACTIVE:
                job.cancel()
