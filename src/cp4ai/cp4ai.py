"""Copy converted documents to the Linux clipboard."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .pipeline import (
    BatchFailure,
    check_pandoc,
    check_tesseract,
    convert_batch,
    copy_to_clipboard,
    filter_sources,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cp4ai",
        description=(
            "Convert DOCX/PDF/PPTX files to Markdown and copy the concatenated "
            "result to the clipboard."
        ),
    )
    parser.add_argument("files", nargs="+", help=".docx/.pdf/.pptx files")
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="do not OCR images (Tesseract is otherwise required on PATH)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="parallel worker processes (default: CPU cores)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run_cli_progress():
    pending: dict[int, tuple[int, Path, str | None, str | None]] = {}
    next_index = 1

    def _emit(index: int, count: int, source: Path, details: str | None, error: str | None) -> None:
        if error:
            print(f"[{index}/{count}] FAILED {source}: {error}", file=sys.stderr)
        else:
            print(f"[{index}/{count}] {source} ({details})")

    def _progress(index: int, count: int, source: Path, details: str | None, error: str | None):
        nonlocal next_index
        pending[index] = (count, source, details, error)
        while next_index in pending:
            count_i, source_i, details_i, error_i = pending.pop(next_index)
            _emit(next_index, count_i, source_i, details_i, error_i)
            next_index += 1

    return _progress


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    sources, rejected = filter_sources(args.files)
    for item in rejected:
        print(f"ERROR: {item.path}: {item.reason}", file=sys.stderr)
    if rejected:
        return 1
    if not sources:
        print("ERROR: no files to convert", file=sys.stderr)
        return 1

    use_ocr = not args.no_ocr
    for problem in (check_pandoc(sources), check_tesseract(use_ocr)):
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1

    jobs = max(1, args.jobs)
    try:
        result = convert_batch(
            sources,
            use_ocr=use_ocr,
            jobs=jobs,
            progress=run_cli_progress(),
        )
    except BatchFailure as exc:
        print(f"{len(exc.errors)} file(s) failed; clipboard not changed", file=sys.stderr)
        return 1

    try:
        copy_to_clipboard(result.markdown)
    except Exception as exc:  # noqa: BLE001 - clipboard failure
        print(f"ERROR: could not write to clipboard: {exc}", file=sys.stderr)
        return 1

    print("=" * 25)
    print(f"Copied to clipboard! ({result.chars} characters / {result.tokens} tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
