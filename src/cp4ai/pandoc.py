"""Locate the Pandoc executable supplied by the user.

The CLI expects ``pandoc`` on ``PATH``; the package does not bundle or install
it.
"""

from __future__ import annotations

import functools
import shutil
import subprocess


def find_pandoc() -> str | None:
    """Return the Pandoc path from ``PATH``, or ``None``."""
    return shutil.which("pandoc")


def pandoc_binary() -> str:
    """Return the Pandoc path or raise a helpful error."""
    path = find_pandoc()
    if path:
        return path
    raise RuntimeError(
        "pandoc not found in PATH. Install Pandoc with your system package "
        "manager (for example: apt install pandoc, dnf install pandoc, or "
        "pacman -S pandoc)."
    )


@functools.lru_cache(maxsize=1)
def pandoc_version() -> str:
    """Return the Pandoc version string, for front-matter metadata."""
    try:
        proc = subprocess.run(
            [pandoc_binary(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = proc.stdout.splitlines()[0].strip() if proc.stdout else ""
    if first.lower().startswith("pandoc"):
        return first[6:].strip() or "unknown"
    return first or "unknown"


@functools.lru_cache(maxsize=1)
def pandoc_supports_pptx() -> bool:
    """Return whether this Pandoc can read PPTX files (Pandoc 3.0+)."""
    try:
        proc = subprocess.run(
            [pandoc_binary(), "--list-input-formats"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "pptx" in proc.stdout.split()
