# Copy 4 AI 3.2.0 (Linux CLI)

This repository contains the Linux command-line tools. The macOS app lives in
[cp4ai-macos](https://github.com/titusng0110/cp4ai-macos).

## Highlights

- **Parallel document pipeline**: one dynamic job queue now runs text
  extraction and OCR jobs together. `--jobs N` controls the worker count;
  the default is all active CPU cores. Output order remains the input order.
- **Layer-aware PDF images**: images on optional-content layers are found,
  even when the layer is hidden by default.
- **Better PDF image placement**: every discovered image placement gets its
  own OCR block with a page-relative position. Opaque images that completely
  cover a lower image skip the hidden one; transparent covers keep both.
- **Cleaner overlapping images**: overlapping images on the same layer are OCR'd
  from the image object, so one image's text does not bleed into another.
- **Shared `<10` rule**: OCR text shorter than 10 non-whitespace characters is
  dropped for DOCX, PPTX and PDF.
- **Same Tesseract OCR**: four page-segmentation modes per image, with the
  existing confidence × word-coverage ranking.

## Compatibility

- Linux x86_64 and aarch64, glibc or musl.
- Python 3.13 (installed by `uv`).
- Pandoc 3.0+ for `.pptx` input; Pandoc 2.x is enough for `.docx`.
- Tesseract on `PATH` unless `--no-ocr` is used.
- PyMuPDF publishes glibc wheels; musl distributions need a source build.

## Install

```bash
git pull
./install.sh
```

`cp4ai-setup` refreshes the bundled vector helpers when the package changes.
