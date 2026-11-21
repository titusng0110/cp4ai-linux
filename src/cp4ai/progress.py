"""Shared work counters for the conversion queue."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, replace
from typing import Protocol, TextIO


@dataclass
class WorkTotals:
    pages: int
    ocr: int


@dataclass
class WorkState:
    documents_total: int
    images_total: int
    documents_done: int = 0
    images_done: int = 0

    @property
    def fraction(self) -> float:
        total = self.documents_total + self.images_total
        if total <= 0:
            return 0.0
        return min(1.0, (self.documents_done + self.images_done) / total)


class WorkCallback(Protocol):
    def __call__(self, state: WorkState) -> None: ...


class BatchProgressTracker:
    """Thread-safe document/image counters with a callback for each change."""

    def __init__(
        self,
        documents_total: int,
        images_total: int,
        callback: WorkCallback | None = None,
    ):
        self._lock = threading.Lock()
        self._callback = callback
        self._state = WorkState(
            documents_total=documents_total,
            images_total=images_total,
        )
        self._images_queued = 0

    @property
    def state(self) -> WorkState:
        with self._lock:
            return replace(self._state)

    def start(self) -> None:
        self._publish()

    def add_images(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self._images_queued += count
            self._state.images_total = max(
                self._state.images_total, self._images_queued
            )
            snapshot = replace(self._state)
        self._emit(snapshot)

    def image_finished(self) -> None:
        with self._lock:
            self._state.images_done += 1
            self._state.images_total = max(
                self._state.images_total, self._state.images_done
            )
            snapshot = replace(self._state)
        self._emit(snapshot)

    def document_finished(self) -> None:
        with self._lock:
            self._state.documents_done += 1
            snapshot = replace(self._state)
        self._emit(snapshot)

    def reconcile(self) -> None:
        with self._lock:
            self._state.images_total = self._state.images_done
            snapshot = replace(self._state)
        self._emit(snapshot)

    def _publish(self) -> None:
        with self._lock:
            snapshot = replace(self._state)
        self._emit(snapshot)

    def _emit(self, snapshot: WorkState) -> None:
        if self._callback is not None:
            self._callback(snapshot)


class CLIProgressReporter:
    """Live ``documents + images`` feedback on stderr, like the macOS CLI."""

    def __init__(self, stream: TextIO | None = None):
        self._stream = stream or sys.stderr
        self._tty = bool(getattr(self._stream, "isatty", lambda: False)())
        self._lock = threading.Lock()
        self._last_update = 0.0
        self._last_documents_done = -1
        self._line_active = False

    def started(self, index: int, total: int, source) -> None:
        with self._lock:
            self._clear_line_locked()
            self._write(f"[working] {source.name}\n")

    def __call__(self, state: WorkState) -> None:
        with self._lock:
            now = time.monotonic()
            documents_changed = state.documents_done != self._last_documents_done
            if not (self._tty or documents_changed or now - self._last_update >= 1.0):
                return
            self._last_update = now
            self._last_documents_done = state.documents_done
            line = (
                f"Progress: {state.documents_done}/{state.documents_total} documents"
            )
            if state.images_total > 0 or state.images_done > 0:
                line += f" + {state.images_done}/{state.images_total} images"
            if self._tty:
                self._write("\r\x1b[2K" + line)
                self._line_active = True
            else:
                self._write(line + "\n")

    def clear_line(self) -> None:
        with self._lock:
            self._clear_line_locked()

    def finish(self) -> None:
        with self._lock:
            if self._line_active:
                self._clear_line_locked()
                self._write("\n")

    def _clear_line_locked(self) -> None:
        if self._line_active:
            self._write("\r\x1b[2K")
            self._line_active = False

    def _write(self, text: str) -> None:
        try:
            self._stream.write(text)
            self._stream.flush()
        except (OSError, ValueError):
            pass
