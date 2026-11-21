#!/bin/sh
# Runs inside the Alpine builder. Collects the two converter executables, every
# shared library they need, the musl loader, and a fontconfig config into /out.
set -eu

OUT="${1:-/out}"
ARCH="$(apk --print-arch)"
LOADER="ld-musl-${ARCH}.so.1"

mkdir -p "$OUT/bin" "$OUT/lib" "$OUT/etc/fonts"

cp /usr/bin/rsvg-convert "$OUT/bin/rsvg-convert"
cp /usr/bin/emf2svg-conv "$OUT/bin/emf2svg-conv"
cp /usr/local/share/cp4ai/fonts.conf "$OUT/etc/fonts/fonts.conf"

collect_deps() {
    for file in "$@"; do
        ldd "$file" | awk '
            /=> \// { print $3 }
            /^[[:space:]]*\/lib\/ld-musl/ { print $1 }
        '
    done | sort -u
}

collect_deps "$OUT/bin/rsvg-convert" "$OUT/bin/emf2svg-conv" > /tmp/libs.txt

while IFS= read -r lib; do
    [ -n "$lib" ] || continue
    if [ ! -e "$lib" ]; then
        echo "missing shared library: $lib" >&2
        exit 1
    fi
    # Dereference symlinks and keep the SONAME (e.g. libpng16.so.16) as the
    # filename, because that is what the ELF loader looks for.
    cp -L "$lib" "$OUT/lib/$(basename "$lib")"
done < /tmp/libs.txt

cp -L "/lib/$LOADER" "$OUT/lib/$LOADER"

# Binaries and libraries from Alpine are stripped already; libemf2svg is not.
strip --strip-unneeded "$OUT/bin/rsvg-convert" "$OUT/bin/emf2svg-conv" || true
for lib in "$OUT/lib/"*.so.*; do
    strip --strip-unneeded "$lib" 2>/dev/null || true
done

run_helper() {
    "$OUT/lib/$LOADER" --library-path "$OUT/lib" "$@"
}

smoke_dir="$(mktemp -d)"
trap 'rm -rf "$smoke_dir"' EXIT

cat > "$smoke_dir/test.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">
  <rect width="200" height="80" fill="white"/>
  <text x="10" y="50" font-size="24" fill="black">Copy 4 AI</text>
</svg>
SVG

FONTCONFIG_PATH="$OUT/etc/fonts" \
    run_helper "$OUT/bin/rsvg-convert" "$smoke_dir/test.svg" \
    -o "$smoke_dir/test.png" --width 400
test -s "$smoke_dir/test.png"

emf="$(find /src/tests/resources/emf -name '*.emf' | head -1 || true)"
if [ -n "$emf" ]; then
    FONTCONFIG_PATH="$OUT/etc/fonts" \
        run_helper "$OUT/bin/emf2svg-conv" -p -i "$emf" -o "$smoke_dir/test.emf.svg"
    test -s "$smoke_dir/test.emf.svg"
    FONTCONFIG_PATH="$OUT/etc/fonts" \
        run_helper "$OUT/bin/rsvg-convert" "$smoke_dir/test.emf.svg" \
        -o "$smoke_dir/test.emf.png" --width 400
    test -s "$smoke_dir/test.emf.png"
fi

echo "bundled linux-${ARCH} helpers:"
echo "  binaries: $(ls -1 "$OUT/bin" | tr '\n' ' ')"
echo "  libraries: $(find "$OUT/lib" -maxdepth 1 -type f | wc -l | tr -d ' ')"
echo "  size: $(du -sh "$OUT" | cut -f1)"
