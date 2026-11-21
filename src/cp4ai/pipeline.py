"""Shared conversion pipeline used by the CLI tools."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .jobs import JobQueue
from .progress import BatchProgressTracker, WorkState, WorkTotals

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


@dataclass(frozen=True)
class FileOutcome:
    source: Path
    output: Path
    details: str | None
    error: str | None


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
FileStartedCallback = Callable[[int, int, Path], None]


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


class DocumentWork:
    """Per-document readiness: extraction done and all OCR jobs finished."""

    def __init__(
        self,
        index: int,
        source: Path,
        output: Path,
        tracker: BatchProgressTracker,
    ):
        self.index = index
        self.source = source
        self.output = output
        self._tracker = tracker
        self._lock = threading.Lock()
        self._extraction_done = False
        self._pending_images = 0
        self._did_mark_done = False
        self.error: str | None = None
        self.context = None

    def add_images(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self._pending_images += count
        self._tracker.add_images(count)

    def image_finished(self) -> None:
        finished = False
        with self._lock:
            self._pending_images -= 1
            if (
                not self._did_mark_done
                and self._extraction_done
                and self._pending_images == 0
            ):
                self._did_mark_done = True
                finished = True
        self._tracker.image_finished()
        if finished:
            self._tracker.document_finished()

    def extraction_finished(self) -> None:
        finished = False
        with self._lock:
            self._extraction_done = True
            if not self._did_mark_done and self._pending_images == 0:
                self._did_mark_done = True
                finished = True
        if finished:
            self._tracker.document_finished()

    def fail(self, message: str) -> None:
        finished = False
        with self._lock:
            self.error = message
            self._extraction_done = True
            if not self._did_mark_done and self._pending_images == 0:
                self._did_mark_done = True
                finished = True
        if finished:
            self._tracker.document_finished()


def _prepare_document(source: Path, use_ocr: bool, queue: JobQueue, work: DocumentWork):
    from .core import prepare_docx, prepare_pdf, prepare_pptx

    suffix = source.suffix.lower()
    if suffix == ".docx":
        return prepare_docx(source, use_ocr, queue, work)
    if suffix == ".pptx":
        return prepare_pptx(source, use_ocr, queue, work)
    if suffix == ".pdf":
        return prepare_pdf(source, use_ocr, queue, work)
    raise ValueError(f"unsupported file type: {source}")


def _assemble_document(context, source: Path, output: Path, use_ocr: bool) -> str:
    from .core import (
        DocxContext,
        PDFContext,
        PptxContext,
        assemble_docx,
        assemble_pdf,
        assemble_pptx,
    )

    if isinstance(context, DocxContext):
        return assemble_docx(context, source, output, use_ocr)
    if isinstance(context, PptxContext):
        return assemble_pptx(context, source, output, use_ocr)
    if isinstance(context, PDFContext):
        return assemble_pdf(context, source, output, use_ocr)
    raise RuntimeError("conversion did not produce a document context")


def _cleanup_context(context) -> None:
    if context is not None and hasattr(context, "temporary"):
        context.temporary.cleanup()


def run_batch(
    sources: Sequence[Path],
    outputs: Sequence[Path],
    *,
    use_ocr: bool,
    jobs: int,
    progress: ProgressCallback | None = None,
    work_progress: WorkProgressCallback | None = None,
    file_started: FileStartedCallback | None = None,
) -> list[FileOutcome]:
    """Prepare and assemble documents through one dynamic job queue.

    Extraction jobs enqueue their own OCR jobs.  No OCR text is inserted while
    the queue is running; assembly starts only after every extraction and OCR
    job has finished, in the caller's input order.
    """
    sources = list(sources)
    outputs = list(outputs)
    if len(sources) != len(outputs):
        raise ValueError("output count must match source count")
    total = len(sources)

    totals = count_work(sources) if work_progress is not None else WorkTotals(0, 0)
    tracker = BatchProgressTracker(
        documents_total=total,
        images_total=totals.ocr if work_progress is not None else 0,
        callback=work_progress,
    )
    tracker.start()

    queue = JobQueue(max(1, jobs))
    works: list[DocumentWork] = []
    for index, (source, output) in enumerate(zip(sources, outputs)):
        work = DocumentWork(index, source, output, tracker)
        works.append(work)

        def extraction(item: DocumentWork = work) -> None:
            try:
                if file_started is not None:
                    file_started(item.index + 1, total, item.source)
                item.context = _prepare_document(
                    item.source,
                    use_ocr,
                    queue,
                    item,
                )
                item.extraction_finished()
            except Exception as exc:  # noqa: BLE001 - report and continue
                item.fail(str(exc))

        queue.add(extraction)

    queue.drain()
    tracker.reconcile()

    outcomes: list[FileOutcome] = []
    for work in works:
        details: str | None = None
        error = work.error
        context = work.context
        if error is None:
            try:
                details = _assemble_document(
                    context,
                    work.source,
                    work.output,
                    use_ocr,
                )
            except Exception as exc:  # noqa: BLE001 - report and continue
                error = str(exc)
            finally:
                _cleanup_context(context)
        else:
            _cleanup_context(context)

        outcome = FileOutcome(
            source=work.source,
            output=work.output,
            details=details,
            error=error,
        )
        outcomes.append(outcome)
        if progress is not None:
            progress(
                work.index + 1,
                total,
                work.source,
                details,
                error,
            )

    return outcomes


def convert_batch(
    sources: Sequence[Path],
    *,
    use_ocr: bool,
    jobs: int,
    progress: ProgressCallback | None = None,
    work_progress: WorkProgressCallback | None = None,
    file_started: FileStartedCallback | None = None,
) -> BatchResult:
    """Convert files, concatenate them, and return clipboard-ready Markdown.

    Individual failures are collected into :class:`BatchFailure`; the
    clipboard is never changed when any file fails.
    """
    sources = list(sources)
    if not sources:
        raise ValueError("convert_batch needs at least one source")

    with tempfile.TemporaryDirectory(prefix="cp4ai_clipboard_") as tmp:
        workdir = Path(tmp)
        planned = [
            (source, workdir / f"{index:04d}.md")
            for index, source in enumerate(sources)
        ]
        outcomes = run_batch(
            [source for source, _output in planned],
            [output for _source, output in planned],
            use_ocr=use_ocr,
            jobs=jobs,
            progress=progress,
            work_progress=work_progress,
            file_started=file_started,
        )
        errors = [
            (outcome.source, outcome.error)
            for outcome in outcomes
            if outcome.error is not None
        ]
        if errors:
            raise BatchFailure(errors)

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
