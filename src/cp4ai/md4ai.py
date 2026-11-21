"""Command-line interface for md4ai."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .core import convert_docx, convert_pdf, convert_pptx
from .pipeline import check_pandoc, check_tesseract, run_batch
from .progress import CLIProgressReporter

SOURCE_SUFFIXES = {".docx", ".pdf", ".pptx"}


def collect_sources(target: Path, recursive: bool) -> list[Path]:
    if target.is_file():
        if target.suffix.lower() not in SOURCE_SUFFIXES:
            raise ValueError(f"unsupported file type: {target} (expected .docx, .pdf or .pptx)")
        return [target]
    if target.is_dir():
        if not recursive:
            raise ValueError(f"{target} is a directory; pass -r/--recursive")
        sources = [
            path
            for path in target.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SOURCE_SUFFIXES
            and not path.name.startswith("~$")
            and not any(part.startswith(".") for part in path.relative_to(target).parts)
        ]
        return sorted(sources, key=lambda path: path.as_posix().lower())
    raise ValueError(f"not found: {target}")


def out_is_directory(raw: str, path: Path) -> bool:
    if path.exists() and path.is_dir():
        return True
    if raw.endswith(("/", os.sep)):
        return True
    return path.suffix.lower() != ".md"


def plan_outputs(
    sources: list[Path],
    target: Path,
    out_value: str | None,
) -> list[tuple[Path, Path]]:
    if out_value is None:
        return [(source, source.with_name(source.name + ".md")) for source in sources]

    out_path = Path(out_value).expanduser()
    if target.is_dir():
        if not out_is_directory(out_value, out_path):
            raise ValueError("--out must be a directory when converting a directory")
        return [
            (
                source,
                out_path / source.relative_to(target).parent / (source.name + ".md"),
            )
            for source in sources
        ]

    source = sources[0]
    if out_is_directory(out_value, out_path):
        return [(source, out_path / (source.name + ".md"))]
    return [(source, out_path)]


def check_outputs(planned: list[tuple[Path, Path]], force: bool) -> None:
    outputs = [output for _, output in planned]
    if len(set(outputs)) != len(outputs):
        raise ValueError("multiple inputs map to the same output path")

    directories = [output for output in outputs if output.exists() and output.is_dir()]
    if directories:
        raise ValueError(
            "output path is a directory: "
            + ", ".join(str(path) for path in directories)
        )

    existing = [output for output in outputs if output.exists()]
    if existing and not force:
        listing = "\n".join(f"  {path}" for path in existing)
        raise ValueError(
            "refusing to overwrite existing output (use -f/--force):\n" + listing
        )


def convert_one(source: Path, output: Path, use_ocr: bool) -> str:
    if source.suffix.lower() == ".docx":
        return convert_docx(source, output, use_ocr)
    if source.suffix.lower() == ".pptx":
        return convert_pptx(source, output, use_ocr)
    return convert_pdf(source, output, use_ocr)


def run_conversions(
    planned: list[tuple[Path, Path]],
    use_ocr: bool,
    jobs: int,
) -> int:
    for _, output in planned:
        output.parent.mkdir(parents=True, exist_ok=True)

    reporter = CLIProgressReporter()
    try:
        outcomes = run_batch(
            [source for source, _output in planned],
            [output for _source, output in planned],
            use_ocr=use_ocr,
            jobs=max(1, jobs),
            work_progress=reporter,
            file_started=reporter.started,
        )
    finally:
        reporter.finish()

    failures = 0
    total = len(outcomes)
    for index, outcome in enumerate(outcomes, start=1):
        if outcome.error is not None:
            failures += 1
            print(
                f"[{index}/{total}] FAILED {outcome.source}: {outcome.error}",
                file=sys.stderr,
            )
        else:
            print(
                f"[{index}/{total}] {outcome.source} -> {outcome.output} "
                f"({outcome.details})"
            )
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="md4ai",
        description=(
            "Convert DOCX, PDF and PPTX files to recall-first Markdown. "
            "By default a.pdf becomes a.pdf.md next to it."
        ),
    )
    parser.add_argument("target", type=Path, help=".docx/.pdf/.pptx file, or a directory with -r")
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="convert every .docx/.pdf/.pptx under a directory",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="overwrite existing Markdown output",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=None,
        help=(
            "write to this .md file, or to this directory preserving structure "
            "(created if needed)"
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="parallel workers (default: CPU cores)",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="do not OCR images (Tesseract is otherwise required on PATH)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    target = args.target.expanduser()
    try:
        sources = collect_sources(target, args.recursive)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not sources:
        print(f"ERROR: no .docx/.pdf/.pptx files found under {target}", file=sys.stderr)
        return 1

    use_ocr = not args.no_ocr
    for problem in (check_pandoc(sources), check_tesseract(use_ocr)):
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1

    try:
        planned = plan_outputs(sources, target, args.out)
        check_outputs(planned, args.force)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    failures = run_conversions(planned, use_ocr, max(1, args.jobs))
    if failures:
        print(f"{failures} file(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
