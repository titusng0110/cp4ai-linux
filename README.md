# Copy 4 AI (cp4ai)

Copy 4 AI converts `.docx`, `.pdf` and `.pptx` files into recall-first
Markdown for downstream LLM ingestion. This repository is the **Linux CLI**;
the macOS app lives in
[cp4ai-macos](https://github.com/titusng0110/cp4ai-macos).

The pipeline is **recall-first**: every source word matters. OCR errors,
duplicate text and imperfect formatting are acceptable because the downstream
LLM can interpret them; silently dropping a point is not recoverable.

- `md4ai` writes Markdown files next to the source (`a.pdf` → `a.pdf.md`).
- `cp4ai` converts one or more files and copies the concatenated Markdown to
  the clipboard using `wl-copy`, `xclip` or `xsel`.
- Headings, lists, tables, footnotes, comments, speaker notes, page and slide
  boundaries, and OCR text from images are preserved.
- OCR uses **Tesseract only**. Tesseract is required on `PATH` unless
  `--no-ocr` is passed.
- The two vector-image helpers, **librsvg** and **libemf2svg**, are bundled for
  both x86_64 and aarch64, together with Tesseract's English language data.
  Pandoc and the Tesseract binary are not bundled.

Released under the MIT License. Bundled third-party components and their
licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Requirements

- Linux on **x86_64** or **aarch64**.
- [uv](https://docs.astral.sh/uv/) (it installs the required Python 3.13).
- **Pandoc** on `PATH`:
  - Pandoc **3.0 or newer** is required for `.pptx` input.
  - Pandoc 2.x is enough for `.docx` input.
  - PDF conversion does not use Pandoc.
- **Tesseract** on `PATH` unless you pass `--no-ocr`. The English
  `eng.traineddata` and the `tsv` config are bundled, so no language pack is
  required.
- For `cp4ai` (clipboard): `wl-copy` on Wayland, or `xclip`/`xsel` on X11.

Install them with your distribution, for example:

```bash
# Debian / Ubuntu
sudo apt install pandoc tesseract-ocr wl-clipboard

# Fedora
sudo dnf install pandoc tesseract wl-clipboard

# Arch
sudo pacman -S pandoc tesseract wl-clipboard
```

Some distributions make `tesseract-ocr` pull in a language pack anyway; that
is harmless. Copy 4 AI always uses its bundled English model first.

On distributions that ship an old Pandoc, install a current release from
https://pandoc.org/installing.html if you need PPTX input.

## Install

From a checkout:

```bash
git clone https://github.com/titusng0110/cp4ai-linux.git
cd cp4ai-linux
./install.sh
```

`install.sh` runs `uv tool install --force .` and then `cp4ai-setup`, which
extracts the bundled helpers for this machine.

You can also do it manually:

```bash
uv tool install .
cp4ai-setup
```

If a command is not found afterwards, run `uv tool update-shell` and restart
the terminal, or add `~/.local/bin` to `PATH`.

`cp4ai-setup` writes the helpers to
`$XDG_CACHE_HOME/cp4ai/helpers/linux-<arch>` (default
`~/.cache/cp4ai/helpers/linux-<arch>`). If you skip it, the helpers are
extracted automatically the first time a DOCX contains a vector image. Set
`CP4AI_HELPER_DIR` to use a different cache location.

To update after pulling changes:

```bash
git pull
./install.sh
```

To uninstall:

```bash
uv tool uninstall cp4ai
rm -rf ~/.cache/cp4ai
```

## Usage

Convert one file; the output is `a.pdf.md` next to it:

```bash
md4ai a.pdf
```

Convert a directory recursively:

```bash
md4ai -r notes/
md4ai -rf notes/          # overwrite existing .md outputs
md4ai -o out/ -r notes/   # write into another directory tree
```

Disable OCR when Tesseract is not installed:

```bash
md4ai --no-ocr scanned.pdf
```

`md4ai` options:

| Option | Meaning |
| --- | --- |
| `-r`, `--recursive` | Convert every `.docx`/`.pdf`/`.pptx` under a directory |
| `-f`, `--force` | Overwrite existing Markdown output |
| `-o`, `--out` | Output `.md` file, or directory preserving the input tree |
| `--jobs N` | Parallel worker processes (default: CPU cores) |
| `--no-ocr` | Do not OCR images; Tesseract is not required |
| `--version` | Print the version |

Copy to the clipboard:

```bash
cp4ai a.docx b.pdf
```

This concatenates the converted documents, wraps each file in
`[name BEGIN]` / `[name END]`, and writes the result to the clipboard. The
clipboard is not changed if any file fails. It accepts `--no-ocr`, `--jobs N`
and `--version`.

## OCR

OCR is performed by Tesseract with `-l eng`, trying four page-segmentation
modes and keeping the best result. The bundled `eng.traineddata` and
`configs/tsv` are passed with `--tessdata-dir`, so OCR does not depend on the
distribution's language packages. It runs on:

- raster images in DOCX/PPTX files;
- EMF/EMF+/SVG vector drawings in DOCX files;
- foreground image regions and scanned PDF pages.

Each OCR region uses one Tesseract process at a time (`OMP_THREAD_LIMIT=1`);
parallelism is controlled by `--jobs`.

OCR that duplicates a page's native text layer is dropped only when the
whitespace-stripped image text is an ordered subsequence of the
whitespace-stripped native text for that page. Case, punctuation and character
order still matter.

If a vector drawing cannot be rendered, the Markdown lists it under
`## Vector images (not OCR'd)` instead of dropping it silently.

## Bundled vector helpers

`src/cp4ai/resources/helpers.zip` contains, for both architectures:

- `rsvg-convert` from librsvg 2.60.0;
- `emf2svg-conv` from libemf2svg 1.8.1, built from source;
- the shared libraries they need and the musl dynamic loader;
- a small `fonts.conf` that reuses the host font directories.

The helpers are musl-linked, so the same bundle runs on glibc (Debian, Fedora,
Arch) and musl hosts. They are launched through the bundled loader with
`--library-path`. If the bundle is missing for the current architecture, the
converter falls back to `rsvg-convert`/`emf2svg-conv` on `PATH`.

Rebuild the bundle with Docker (buildx, for example OrbStack):

```bash
./packaging/build_linux_helpers.sh
```

The script builds each architecture in Alpine, runs a smoke test inside the
container, and writes `src/cp4ai/resources/helpers.zip`. Both architectures are
kept in one archive; `cp4ai-setup` extracts only `linux-<arch>` for the host.

## What the Markdown contains

- YAML front matter with the source path, format, date, page/slide counts and
  which tools were used.
- PDF pages marked `<!-- page N of M -->`, slides marked
  `<!-- slide N of M -->`.
- DOCX comments next to their anchored passage, with author and date; comments
  without a visible anchor go into an appendix.
- PPTX speaker notes and comments after each slide.
- Image OCR under a clearly marked `Image text (OCR)` blockquote, plus
  coordinates for PPTX pictures where available.
- DOCX floating/anchored images that Pandoc could not place in the body in a
  `## Figures from the original document (OCR text)` section.

Image files are not copied into the output.

## Development

```bash
uv sync
uv run md4ai --help
uv run python -m compileall src/cp4ai
```

Build the wheel:

```bash
uv build
```

The wheel contains `helpers.zip` for both architectures, so it can be installed
on either x86_64 or aarch64. The bundle is extracted on install/first use
rather than shipped as loose files.

PyMuPDF publishes glibc wheels; on a musl distribution such as Alpine you need
to build PyMuPDF and its dependencies from source.
