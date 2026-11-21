"""Locate or extract the bundled musl helper binaries.

The package ships ``helpers.zip`` containing ``rsvg-convert`` and
``emf2svg-conv`` plus every shared library they need, for both x86_64 and
aarch64.  The helpers are built against musl, so the same tree works on glibc
and musl distributions.

Only the tree for the current machine is extracted, into the user's cache
directory.  ``cp4ai-setup`` performs the extraction explicitly after
installation; ``helper_command`` does the same lazily on first use so a plain
``uv tool install`` still works.

Because the executables have an absolute musl interpreter path, they are
launched through the bundled loader with ``--library-path``.
"""

from __future__ import annotations

import fcntl
import logging
import os
import platform
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

from . import __version__

logger = logging.getLogger(__name__)

BUNDLE_NAME = "helpers.zip"

_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
}


def machine_arch() -> str | None:
    """Return the bundle directory name for the current machine, if known."""
    return _ARCH_ALIASES.get(platform.machine().lower())


def resource_dir() -> Path:
    return Path(__file__).resolve().parent / "resources"


def bundle_path() -> Path:
    return resource_dir() / BUNDLE_NAME


def cache_root() -> Path:
    """Directory where architecture trees are extracted.

    ``CP4AI_HELPER_DIR`` overrides it, which is useful for read-only homes or
    for pointing the converter at pre-extracted helpers.
    """
    override = os.environ.get("CP4AI_HELPER_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "cp4ai" / "helpers"


def _bundle_stamp(bundle: Path) -> str:
    info = bundle.stat()
    return f"{__version__}:{info.st_size}:{info.st_mtime_ns}"


def _safe_member(name: str, prefix: str) -> Path | None:
    """Return the path inside the bundle for a zip member, or ``None``."""
    if not name.startswith(prefix):
        return None
    relative = name[len(prefix) :]
    if not relative:
        return None
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"unsafe path in {BUNDLE_NAME}: {name}")
    return path


def _chmod_executable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        # A read-only cache can still be used when the archive already carries
        # the executable bit; the caller verifies access instead.
        pass


def install_helpers(
    arch: str | None = None,
    *,
    force: bool = False,
) -> Path | None:
    """Extract the helper tree for ``arch`` (default: this machine).

    Returns the extracted directory, or ``None`` when no bundle exists for the
    platform.  Extraction is atomic and serialised with a file lock so several
    worker processes can start at the same time.
    """
    if not sys.platform.startswith("linux"):
        return None
    arch = arch or machine_arch()
    if arch is None:
        return None

    bundle = bundle_path()
    if not bundle.is_file():
        return None

    target = cache_root() / f"linux-{arch}"
    stamp = _bundle_stamp(bundle)
    stamp_file = target / ".bundle-stamp"
    if not force and stamp_file.is_file():
        try:
            if stamp_file.read_text(encoding="utf-8").strip() == stamp:
                return target
        except OSError:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"linux-{arch}/"
    lock_path = target.parent / ".lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Another process may have finished while this one waited.
        if not force and stamp_file.is_file():
            try:
                if stamp_file.read_text(encoding="utf-8").strip() == stamp:
                    return target
            except OSError:
                pass

        temporary = Path(
            tempfile.mkdtemp(prefix=f".linux-{arch}-", dir=target.parent)
        )
        try:
            with zipfile.ZipFile(bundle) as archive:
                members = [
                    info
                    for info in archive.infolist()
                    if not info.is_dir() and info.filename.startswith(prefix)
                ]
                if not members:
                    raise RuntimeError(
                        f"{BUNDLE_NAME} has no helpers for linux-{arch}"
                    )
                for info in members:
                    relative = _safe_member(info.filename, prefix)
                    if relative is None:
                        continue
                    destination = temporary / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("wb") as out:
                        shutil.copyfileobj(source, out)
                    mode = (info.external_attr >> 16) & 0o777
                    if mode:
                        destination.chmod(mode)

            for relative in (
                "bin/rsvg-convert",
                "bin/emf2svg-conv",
                f"lib/ld-musl-{arch}.so.1",
            ):
                executable = temporary / relative
                if executable.is_file():
                    _chmod_executable(executable)
            (temporary / ".bundle-stamp").write_text(stamp, encoding="utf-8")

            if target.exists():
                shutil.rmtree(target)
            os.replace(temporary, target)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    return target


def helper_root() -> Path | None:
    """Return the helper directory for this machine, extracting on first use."""
    if not sys.platform.startswith("linux"):
        return None
    arch = machine_arch()
    if arch is None:
        return None

    direct = resource_dir() / f"linux-{arch}"
    if direct.is_dir():
        return direct
    try:
        return install_helpers(arch)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        logger.warning("could not install bundled helpers for linux-%s: %s", arch, exc)
        return None


def _ensure_executable(path: Path) -> bool:
    """Make sure the musl loader can be executed on this host."""
    if os.access(path, os.X_OK):
        return True
    _chmod_executable(path)
    return os.access(path, os.X_OK)


def _bundled_helper(name: str, root: Path) -> list[str] | None:
    arch = machine_arch()
    binary = root / "bin" / name
    loader = root / "lib" / f"ld-musl-{arch}.so.1"
    if not binary.is_file() or not loader.is_file():
        return None
    if not _ensure_executable(loader) or not os.access(binary, os.R_OK):
        return None
    return [
        str(loader),
        "--library-path",
        str(root / "lib"),
        str(binary),
    ]


def _bundled_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    fontconfig = root / "etc" / "fonts"
    if fontconfig.is_dir():
        env["FONTCONFIG_PATH"] = str(fontconfig)
        env["FONTCONFIG_FILE"] = str(fontconfig / "fonts.conf")
    return env


def helper_command(name: str) -> tuple[list[str], dict[str, str]] | None:
    """Return the command and environment for a helper, or ``None``.

    On Linux the bundled helper is preferred.  If the current platform or
    architecture has no bundle, a system executable on ``PATH`` is used so the
    converter still works in development environments.
    """
    root = helper_root()
    bundled = _bundled_helper(name, root) if root is not None else None
    if root is not None and bundled is not None:
        return bundled, _bundled_env(root)

    system = shutil.which(name)
    if system is not None:
        return [system], os.environ.copy()
    return None
