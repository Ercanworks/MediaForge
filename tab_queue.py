"""Queue tab: all download/convert jobs in a single table."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from jobs import DONE, FAILED, RUNNING, Job


class QueueTab(QWidget):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue
        self._connected: set[int] = set()   # job ids whose signals are connected
        self._rows: dict[int, int] = {}     # job_id → row index

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Type", "Title", "Progress", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        # Fixed status column: sizing it to its contents makes the column jump
        # on every speed update, which causes flickering.
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 260)
        self.table.itemDoubleClicked.connect(lambda _item: self._show_log())
        layout.addWidget(self.table)

        row = QHBoxLayout()
        pause = QPushButton("Pause")
        pause.clicked.connect(self._pause_selected)
        resume = QPushButton("Resume")
        resume.clicked.connect(self._resume_selected)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self._cancel_selected)
        folder = QPushButton("Open Folder")
        folder.clicked.connect(self._open_folder)
        log = QPushButton("Show Log")
        log.clicked.connect(self._show_log)
        clear = QPushButton("Clear Finished")
        clear.clicked.connect(self.queue.clear_finished)
        row.addWidget(pause)
        row.addWidget(resume)
        row.addWidget(cancel)
        row.addWidget(folder)
        row.addWidget(log)
        row.addStretch()
        row.addWidget(clear)
        layout.addLayout(row)

        queue.structure_changed.connect(self._build_table)
        self._build_table()

    # --- table setup ---
    def _build_table(self):
        jobs = self.queue.jobs
        self._rows = {job.job_id: i for i, job in enumerate(jobs)}
        self.table.setRowCount(len(jobs))
        for i, job in enumerate(jobs):
            self.table.setItem(i, 0, QTableWidgetItem(job.kind_label))
            self.table.setItem(i, 1, QTableWidgetItem(job.title))
            bar = QProgressBar()
            bar.setTextVisible(True)
            self._apply_bar(bar, job)
            self.table.setCellWidget(i, 2, bar)
            self.table.setItem(i, 3, QTableWidgetItem(self._status_text(job)))
            if job.job_id not in self._connected:
                self._connected.add(job.job_id)
                job.changed.connect(lambda job=job: self._update_row(job))
                job.progress.connect(lambda _p, job=job: self._update_bar(job))

    @staticmethod
    def _status_text(job: Job) -> str:
        return f"{job.status} — {job.detail}" if job.detail else job.status

    @staticmethod
    def _apply_bar(bar: QProgressBar, job: Job):
        if job.percent < 0:
            if job.status == RUNNING:
                bar.setRange(0, 0)          # indeterminate (animated)
            else:
                bar.setRange(0, 100)
                bar.setValue(0)
        else:
            bar.setRange(0, 100)
            bar.setValue(job.percent)

    def _update_row(self, job: Job):
        row = self._rows.get(job.job_id)
        if row is None or row >= self.table.rowCount():
            return
        self.table.item(row, 1).setText(job.title)
        self.table.item(row, 3).setText(self._status_text(job))
        self._update_bar(job)

    def _update_bar(self, job: Job):
        row = self._rows.get(job.job_id)
        if row is None:
            return
        bar = self.table.cellWidget(row, 2)
        if bar:
            self._apply_bar(bar, job)

    # --- buttons ---
    def _selected_job(self) -> Job | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.queue.jobs):
            return self.queue.jobs[row]
        return None

    def _cancel_selected(self):
        job = self._selected_job()
        if job:
            job.cancel()

    def _pause_selected(self):
        job = self._selected_job()
        if job:
            job.pause()

    def _resume_selected(self):
        job = self._selected_job()
        if job:
            job.resume()
            # A job coming back to the waiting state may be able to start now
            self.queue.pump()

    def _open_folder(self):
        job = self._selected_job()
        if not job:
            return
        if job.status == DONE and job.output_file and os.path.isfile(job.output_file):
            os.startfile(os.path.dirname(job.output_file))
        elif job.output_dir and os.path.isdir(job.output_dir):
            os.startfile(job.output_dir)

    def _show_log(self):
        job = self._selected_job()
        if not job:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Log — {job.title}")
        dialog.resize(760, 420)
        v = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFontFamily("Consolas")
        text.setPlainText("\n".join(job.log))
        if job.status == FAILED:
            text.moveCursor(text.textCursor().MoveOperation.End)
        v.addWidget(text)
        dialog.exec()
