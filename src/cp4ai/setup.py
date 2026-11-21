"""Extract the bundled librsvg/libemf2svg helpers for this machine."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .resource_paths import (
    bundle_path,
    install_helpers,
    machine_arch,
)

ARCHES = ("aarch64", "x86_64")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cp4ai-setup",
        description=(
            "Extract the bundled rsvg-convert and emf2svg-conv helpers for "
            "this Linux architecture. The converters also do this "
            "automatically on first use."
        ),
    )
    parser.add_argument(
        "--arch",
        choices=ARCHES,
        default=None,
        help="architecture to install (default: detect this machine)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="extract again even when the cached copy is current",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not sys.platform.startswith("linux"):
        print("ERROR: the bundled helpers only run on Linux", file=sys.stderr)
        return 1

    arch = args.arch or machine_arch()
    if arch is None:
        print(
            "ERROR: this machine is not x86_64 or aarch64; install "
            "rsvg-convert and emf2svg-conv on PATH instead",
            file=sys.stderr,
        )
        return 1

    if not bundle_path().is_file():
        print(
            f"ERROR: {bundle_path()} is missing; reinstall cp4ai",
            file=sys.stderr,
        )
        return 1

    try:
        target = install_helpers(arch, force=args.force)
    except Exception as exc:  # noqa: BLE001 - report a usable message
        print(f"ERROR: could not extract the helpers: {exc}", file=sys.stderr)
        return 1

    if target is None:
        print(
            f"ERROR: no bundled helpers for linux-{arch}",
            file=sys.stderr,
        )
        return 1

    print(f"Bundled helpers for linux-{arch} are ready in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
