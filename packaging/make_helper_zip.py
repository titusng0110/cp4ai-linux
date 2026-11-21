#!/usr/bin/env python3
"""Create a deterministic zip of the bundled Linux helper trees.

The archive holds ``linux-aarch64/`` and ``linux-x86_64/`` directories.  The
runtime extracts only the tree that matches the host architecture.
"""

from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def add_tree(archive: zipfile.ZipFile, root: Path, prefix: str) -> int:
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        info = zipfile.ZipInfo(f"{prefix}/{relative.as_posix()}", FIXED_TIME)
        info.create_system = 3  # Unix, so external_attr carries the file mode
        info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with path.open("rb") as source:
            archive.writestr(info, source.read(), compresslevel=9)
        count += 1
    return count


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <bundle-root> <output.zip>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    output = Path(argv[2])
    trees = sorted(
        path for path in root.iterdir() if path.is_dir() and path.name.startswith("linux-")
    )
    if not trees:
        print(f"error: no linux-* directories under {root}", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        for tree in trees:
            files = add_tree(archive, tree, tree.name)
            print(f"  {tree.name}: {files} files")
    print(f"wrote {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
