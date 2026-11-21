#!/bin/bash
# Build the bundled musl helpers for linux/arm64 and linux/amd64.
#
# Requirements: Docker with buildx (OrbStack works) and Python 3 on the host.
# Output: src/cp4ai/resources/helpers.zip, containing one tree per architecture.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OUT="$ROOT/packaging/.out"
ZIP="$ROOT/src/cp4ai/resources/helpers.zip"
rm -rf "$OUT"
mkdir -p "$OUT"

for platform in arm64 amd64; do
    case "$platform" in
        arm64) arch="aarch64" ;;
        amd64) arch="x86_64" ;;
    esac

    echo "==> Building linux/$platform (linux-$arch) helpers"
    docker buildx build \
        --platform "linux/$platform" \
        --output "type=local,dest=$OUT/linux-$arch" \
        --file packaging/linux/Dockerfile \
        packaging/linux
    rm -rf "$ROOT/src/cp4ai/resources/linux-$arch"
done

# One archive keeps the wheel platform-independent while the installer only
# extracts the tree for the current machine.
python3 packaging/make_helper_zip.py "$OUT" "$ZIP"

rm -rf "$OUT"
echo "==> Wrote $ZIP"
