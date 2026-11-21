"""A bounded queue for dynamically-created conversion jobs.

Each worker runs one job at a time on its own thread.  A job may enqueue more
jobs (for example, a PDF extraction job adds its OCR jobs), and ``drain`` only
returns once every dynamically-added job has finished.  This mirrors the
``JobQueue`` used by the macOS app; jobs never create nested pools.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class JobQueue:
    def __init__(self, max_concurrent: int):
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_concurrent),
            thread_name_prefix="cp4ai",
        )
        self._condition = threading.Condition()
        self._pending = 0
        self._stopped = False

    def add(self, job: Callable[[], None]) -> None:
        with self._condition:
            if self._stopped:
                raise RuntimeError("cannot add a job after the queue is drained")
            self._pending += 1
        self._executor.submit(self._run, job)

    def _run(self, job: Callable[[], None]) -> None:
        try:
            job()
        except BaseException:
            logger.exception("conversion job failed")
        finally:
            with self._condition:
                self._pending -= 1
                if self._pending == 0:
                    self._condition.notify_all()

    def drain(self) -> None:
        with self._condition:
            while self._pending > 0:
                self._condition.wait()
        self._executor.shutdown(wait=True)
        self._stopped = True
