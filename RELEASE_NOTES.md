# Copy 4 AI 3.1.0 (Linux CLI)

This repository now contains only the Linux command-line tools. The macOS app
and Finder Quick Action live in
[cp4ai-macos](https://github.com/titusng0110/cp4ai-macos).

## Highlights

- **Linux-only CLI**: `md4ai` writes Markdown files; `cp4ai` copies the
  concatenated result to the clipboard.
- **Tesseract only**: Apple Live Text and `ocrmac` are gone. The
  `--livetext`/`--tesseract` flags are gone too; OCR is Tesseract unless you
  pass `--no-ocr`.
- **Bundled English OCR data**: `eng.traineddata` and the `tsv` config ship
  with the wheel, so no Tesseract language pack is needed.
- **Bundled vector helpers**: `rsvg-convert` (librsvg 2.60.0) and
  `emf2svg-conv` (libemf2svg 1.8.1) are built against musl for x86_64 and
  aarch64, with their shared libraries and the musl loader, in a single
  `helpers.zip`.
- **Install-time extraction**: `./install.sh` runs `uv tool install` and then
  `cp4ai-setup`, which extracts only the tree for the current architecture to
  `~/.cache/cp4ai/helpers/linux-<arch>`. A plain install extracts lazily on
  first use.
- **User-supplied Pandoc**: `pandoc` and `tesseract` must be on `PATH`. The CLI
  no longer installs anything with Homebrew and does not bundle Pandoc.
- **Linux clipboard**: `wl-copy`, `xclip` or `xsel`.

## Compatibility

- Linux x86_64 and aarch64, glibc or musl.
- Python 3.13 (installed by `uv`).
- Pandoc 3.0+ for `.pptx` input; Pandoc 2.x is enough for `.docx`.
- PyMuPDF publishes glibc wheels; musl distributions need a source build.

## Removed

- macOS GUI, Finder Quick Action, DMG/app packaging, branding assets and
  Homebrew setup.
- `ocrmac`, Pillow, psutil and tabulate dependencies.
- The `tools/tessdata` setup script; the language data is now packaged.
