"""Shared conversion pipeline used by the CLI tools."""

from __future__ import annotations

import logging
import multiprocessing
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .progress import (
    CallbackSink,
    QueueSink,
    WorkState,
    WorkTotals,
    set_sink,
)

logger = logging.getLogger(__name__)

SOURCE_SUFFIXES = {".docx", ".pdf", ".pptx"}


@dataclass(frozen=True)
class Rejected:
    path: Path
    reason: str


@dataclass(frozen=True)
class BatchResult:
    markdown: str
    chars: int
    tokens: int
    files: list[Path]


class BatchFailure(Exception):
    """Raised when one or more files failed; the clipboard is untouched."""

    def __init__(self, errors: Sequence[tuple[Path, str]]):
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} file(s) failed")


ProgressCallback = Callable[
    [int, int, Path, str | None, str | None],
    None,
]
WorkProgressCallback = Callable[[WorkState], None]


def _init_worker_progress(queue) -> None:
    set_sink(QueueSink(queue))


def filter_sources(paths: Iterable[Path | str]) -> tuple[list[Path], list[Rejected]]:
    """Split selections into supported files and rejected entries.

    Directories are never accepted.  Duplicate paths are collapsed while
    preserving the caller's order.
    """
    valid: list[Path] = []
    rejected: list[Rejected] = []
    seen: set[Path] = set()

    for raw in paths:
        path = Path(raw).expanduser()
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)

        if path.is_dir():
            rejected.append(Rejected(path, "directories are not supported"))
        elif not path.exists():
            rejected.append(Rejected(path, "not found"))
        elif not path.is_file():
            rejected.append(Rejected(path, "not a regular file"))
        elif path.suffix.lower() not in SOURCE_SUFFIXES:
            rejected.append(
                Rejected(path, "unsupported file type (expected .docx, .pdf or .pptx)")
            )
        else:
            valid.append(path)
    return valid, rejected


def check_pandoc(sources: Sequence[Path]) -> str | None:
    """Return an error message when Pandoc is missing or too old."""
    if not any(path.suffix.lower() in {".docx", ".pptx"} for path in sources):
        return None
    from .pandoc import find_pandoc, pandoc_supports_pptx

    if not find_pandoc():
        return (
            "pandoc not found in PATH. Install Pandoc with your system package "
            "manager."
        )
    if any(path.suffix.lower() == ".pptx" for path in sources) and not pandoc_supports_pptx():
        return (
            "this Pandoc cannot read .pptx files; Pandoc 3.0 or newer is "
            "required for PowerPoint input. Upgrade Pandoc, or convert the "
            "slides to PDF/DOCX first."
        )
    return None


def check_tesseract(use_ocr: bool) -> str | None:
    """Tesseract must be supplied by the user when OCR is enabled."""
    if not use_ocr:
        return None
    if shutil.which("tesseract"):
        return None
    return (
        "tesseract not found in PATH. Install the Tesseract binary, or pass "
        "--no-ocr. The English language data is bundled."
    )


def count_work(sources: Sequence[Path]) -> WorkTotals:
    """Estimate pages and OCR jobs before conversion starts."""
    from .core import count_ocr_jobs, count_pages

    pages = 0
    ocr = 0
    for source in sources:
        try:
            pages += count_pages(source)
            ocr += count_ocr_jobs(source)
        except Exception as exc:  # noqa: BLE001 - conversion reports the real error
            logger.debug("could not count work for %s: %s", source, exc)
            continue
    return WorkTotals(pages=pages, ocr=ocr)


def convert_batch(
    sources: Sequence[Path],
    *,
    use_ocr: bool,
    jobs: int,
    progress: ProgressCallback | None = None,
    work_progress: WorkProgressCallback | None = None,
) -> BatchResult:
    """Convert files, concatenate them, and return clipboard-ready Markdown.

    Individual failures are reported through ``progress`` and collected into
    :class:`BatchFailure`; successful files are never copied when any file
    fails.  ``work_progress`` receives page/OCR counters aggregated across all
    files.
    """
    # Imported lazily so the CLIs can report argument errors before pymupdf,
    # tiktoken and the rest of the conversion stack are loaded.
    from .md4ai import convert_one

    sources = list(sources)
    total = len(sources)
    errors: list[tuple[Path, str]] = []
    state: WorkState | None = None
    if work_progress is not None:
        totals = count_work(sources)
        state = WorkState(pages_total=totals.pages, ocr_total=totals.ocr)
        work_progress(state)

    with tempfile.TemporaryDirectory(prefix="cp4ai_clipboard_") as tmp:
        workdir = Path(tmp)
        planned = [
            (source, workdir / f"{index:04d}.md")
            for index, source in enumerate(sources)
        ]

        use_processes = jobs > 1 and total > 1
        if not use_processes:
            sink = (
                CallbackSink(state, work_progress)
                if state is not None and work_progress is not None
                else None
            )
            if sink:
                set_sink(sink)
            try:
                for index, (source, output) in enumerate(planned, start=1):
                    try:
                        details = convert_one(source, output, use_ocr)
                    except Exception as exc:  # noqa: BLE001 - report and continue
                        errors.append((source, str(exc)))
                        if progress:
                            progress(index, total, source, None, str(exc))
                    else:
                        if progress:
                            progress(index, total, source, details, None)
            finally:
                if sink:
                    set_sink(None)
        else:
            workers = min(max(1, jobs), total)
            completed = 0
            queue = None
            drain = None
            if state is not None and work_progress is not None:
                ctx = multiprocessing.get_context("spawn")
                queue = ctx.Queue()

                def drain_events() -> None:
                    while True:
                        event = queue.get()
                        if event is None:
                            break
                        state.apply(event)
                        work_progress(state)

                drain = threading.Thread(target=drain_events, daemon=True)
                drain.start()

            initializer = _init_worker_progress if queue is not None else None
            initargs = (queue,) if queue is not None else ()
            try:
                with ProcessPoolExecutor(
                    max_workers=workers,
                    initializer=initializer,
                    initargs=initargs,
                ) as pool:
                    futures = {
                        pool.submit(convert_one, source, output, use_ocr): source
                        for source, output in planned
                    }
                    for future in as_completed(futures):
                        source = futures[future]
                        completed += 1
                        try:
                            details = future.result()
                        except Exception as exc:  # noqa: BLE001 - report and continue
                            errors.append((source, str(exc)))
                            if progress:
                                progress(
                                    completed, total, source, None, str(exc)
                                )
                        else:
                            if progress:
                                progress(
                                    completed, total, source, details, None
                                )
            finally:
                if queue is not None:
                    queue.put(None)
                    drain.join(timeout=10)
                    queue.close()
                    queue.join_thread()

        if errors:
            raise BatchFailure(errors)

        if state is not None and work_progress is not None:
            state.pages_total = state.pages_done
            state.ocr_total = state.ocr_done
            work_progress(state)

        sections = []
        for source, output in planned:
            body = output.read_text(encoding="utf-8").rstrip()
            sections.append(f"[{source.name} BEGIN]\n{body}\n[{source.name} END]")
        markdown = "\n\n".join(sections) + "\n"

    return BatchResult(
        markdown=markdown,
        chars=len(markdown),
        tokens=count_tokens(markdown),
        files=sources,
    )


def count_tokens(text: str) -> int:
    import tiktoken

    return len(tiktoken.get_encoding("cl100k_base").encode(text))


def copy_to_clipboard(text: str) -> None:
    """Copy text using the first working Linux clipboard tool on PATH."""
    data = text.encode("utf-8")
    candidates = (
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
    )
    errors: list[str] = []
    for name, command in candidates:
        if not shutil.which(name):
            continue
        try:
            subprocess.run(command, input=data, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        return
    if errors:
        raise RuntimeError(
            "could not write to the clipboard: " + "; ".join(errors)
        )
    raise RuntimeError(
        "no clipboard tool found; install wl-clipboard, xclip, or xsel"
    )
