# Copy 4 AI (cp4ai)

## 1. Intro

Copy 4 AI turns `.docx`, `.pdf` and `.pptx` files into Markdown that is ready
to paste into ChatGPT, Claude or any other AI chat. This repository is the
**Linux CLI**; the macOS app lives in
[cp4ai-macos](https://github.com/titusng0110/cp4ai-macos).

The pipeline is **recall-first**: every word in the source matters. OCR
mistakes, duplicate text and imperfect formatting are acceptable because the
AI can interpret them; silently dropping a sentence or a table row is not.

The Linux tools are:

- **`md4ai`** — writes Markdown files next to the source (`a.pdf` becomes
  `a.pdf.md`).
- **`cp4ai`** — converts one or more files and copies the concatenated
  Markdown to the clipboard.

It keeps headings, lists, tables, footnotes, comments, speaker notes, page and
slide boundaries, and text found inside images. The conversion pipeline
mirrors the macOS 3.2 variant; the intended platform differences are the OCR
engine (Tesseract here, Apple Vision on macOS) and the clipboard tool.

The two vector-image helpers, **librsvg** and **libemf2svg**, are bundled for
both x86_64 and aarch64, together with Tesseract's English language data.
Pandoc and the Tesseract binary are supplied by the user.

Released under the MIT License. Bundled third-party components and their
licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## 2. Setup

### Requirements

- Linux on **x86_64** or **aarch64**
- [uv](https://docs.astral.sh/uv/) (it installs the required Python 3.13)
- **Pandoc** on `PATH`:
  - Pandoc **3.0 or newer** for `.pptx` input
  - Pandoc 2.x is enough for `.docx`
  - PDF conversion does not use Pandoc
- **Tesseract** on `PATH` unless you pass `--no-ocr`. The English
  `eng.traineddata` and `tsv` config are bundled, so no language pack is
  required.
- For `cp4ai`: `wl-copy` on Wayland, or `xclip`/`xsel` on X11.

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
is harmless. Copy 4 AI always prefers its bundled English model. If your
distribution ships an old Pandoc, install a current release from
https://pandoc.org/installing.html when you need PPTX input.

### Install

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

### Update

```bash
git pull
./install.sh
```

`cp4ai-setup` reuses the cached helper tree when the bundle has not changed.

### Uninstall

```bash
uv tool uninstall cp4ai
rm -rf ~/.cache/cp4ai
```

### Build from source

For development:

```bash
uv sync
uv run md4ai --help
uv run python -m compileall src/cp4ai
uv run python -m unittest discover -s tests -v
```

Build the wheel:

```bash
uv build
```

The wheel contains `helpers.zip` for both architectures, so it can be installed
on either x86_64 or aarch64. The bundle is extracted on install or first use
rather than shipped as loose files.

Rebuild the bundled vector helpers with Docker (buildx, for example
OrbStack/Docker Desktop):

```bash
./packaging/build_linux_helpers.sh
```

The script builds each architecture in Alpine, runs a smoke test inside the
container, and writes `src/cp4ai/resources/helpers.zip`. Both architectures are
kept in one archive; `cp4ai-setup` extracts only `linux-<arch>` for the host.

PyMuPDF publishes glibc wheels. On a musl distribution such as Alpine you need
to build PyMuPDF and its dependencies from source.

## 3. Usage

### `md4ai` — Markdown files

```bash
md4ai a.pdf                   # writes a.pdf.md next to it
md4ai a.docx                  # writes a.docx.md
md4ai a.pptx                  # writes a.pptx.md

md4ai -r notes/               # every .docx/.pdf/.pptx under notes/
md4ai -rf notes/              # recursive, overwrite existing .md
md4ai -o b.md a.pdf           # write exactly b.md
md4ai -o out/ -r notes/       # preserve the input tree under out/
md4ai --no-ocr scanned.pdf    # skip image OCR
```

Output names never collide with the source: `name.pdf` becomes
`name.pdf.md`, and the same pattern applies to DOCX and PPTX. If an output
exists, the command refuses to overwrite it unless `-f/--force` is given.
For a recursive run it checks every destination first, so it will not partially
convert a directory and then stop.

| Option | Meaning |
| --- | --- |
| `-r`, `--recursive` | Convert every `.docx`/`.pdf`/`.pptx` under a directory |
| `-f`, `--force` | Overwrite existing Markdown |
| `-o`, `--out` | Output `.md` file, or directory preserving the input tree |
| `--jobs N` | Parallel workers (default: all active CPU cores) |
| `--no-ocr` | Do not OCR images; Tesseract is not required |
| `-h`, `--help` | Show help |
| `--version` | Show the version |

`-rf` and `-r -f` are equivalent. Directory input requires `-r`. A path ending
in `.md` is treated as a file; anything else is treated as a directory.

### `cp4ai` — clipboard

```bash
cp4ai a.docx b.pdf            # convert and copy the combined result
cp4ai --no-ocr a.pdf          # skip OCR
cp4ai --jobs 4 notes/*.pptx   # four workers
```

This concatenates the converted documents, wraps each file in
`[name BEGIN]` / `[name END]`, and writes the result to the clipboard using
the first available of `wl-copy`, `xclip` or `xsel`.

On success:

```text
=========================
Copied to clipboard! (26524 characters / 5980 tokens)
```

If any file fails, the clipboard is left unchanged and the command exits with
a non-zero status. `cp4ai` accepts only files, not directories.

| Option | Meaning |
| --- | --- |
| `--no-ocr` | Do not OCR images |
| `--jobs N` | Parallel workers (default: all active CPU cores) |
| `-h`, `--help` | Show help |
| `--version` | Show the version |

### What the Markdown contains

- YAML front matter with source path, format, date, page/slide counts and
  which tools were used.
- PDF pages marked `<!-- page N of M -->`; PPTX slides marked
  `<!-- slide N of M -->`.
- Image text under a clearly marked `Image text (OCR)` blockquote. PDF blocks
  include `[top: …, left: …, width: …, height: …]` percentages; PPTX blocks
  include them when the slide XML has picture coordinates.
- OCR text shorter than 10 non-whitespace characters is dropped entirely,
  without a placeholder, for every format.
- DOCX comments next to the passage they annotate, with author and date.
  Comments without an anchor go into an appendix.
- PPTX speaker notes and comments after their slide.
- DOCX floating/anchored images that Pandoc could not place in the body in a
  `## Figures from the original document (OCR text)` section.
- A `## Vector images (not OCR'd)` list if an EMF, WMF or SVG drawing could
  not be rendered. PPTX drops an unrenderable vector picture like empty OCR
  text.

Image files themselves are never copied into the Markdown.

## 4. Design / Algorithm

### Architecture

```text
cp4ai / md4ai
├── CLI + conversion pipeline (Python)
├── PyMuPDF              PDF text extraction and placement rendering
├── Tesseract            OCR (best of four page-segmentation modes)
└── resources/
    ├── helpers.zip
    │   ├── rsvg-convert     librsvg
    │   ├── emf2svg-conv     libemf2svg
    │   └── musl loader + shared libraries
    └── tessdata/
        ├── eng.traineddata
        └── configs/tsv
```

Everything runs locally. There is no GUI and no network access during
conversion. The Python package owns the pipeline; the only bundled native
executables are the two vector converters, which are extracted from
`helpers.zip` into the user's cache and launched through the bundled musl
loader.

This pipeline intentionally follows the macOS 3.2 implementation. The
remaining differences are the OCR engine (Tesseract here) and platform
integration (clipboard tools, install paths).

### DOCX

1. Pandoc converts the document to GitHub-flavored Markdown with headings,
   lists, tables, footnotes, text boxes and tracked changes preserved.
2. Every Markdown image link and raw `<img>` tag is replaced by its OCR text.
   Table cells get inline OCR so the table stays valid; other images become
   blockquotes. Identical image bytes are OCR'd once per document.
3. Floating drawings that Pandoc extracts but cannot place in the body are
   OCR'd in a figures section at the end.
4. `word/comments.xml` and `word/document.xml` are parsed in document order.
   Comments are inserted after the passage they annotate; unanchored comments
   go into an appendix.
5. Unusual images are rasterised before OCR:
   - EMF+ bitmaps are read directly from the EMF record;
   - classic EMF goes through `emf2svg-conv`, then `rsvg-convert`;
   - SVG goes through `rsvg-convert`;
   - WMF has no renderer and is reported as a vector image that was not OCR'd.
6. The media directory is temporary and deleted afterwards.

### PDF

1. PyMuPDF extracts the native text layer one page chunk at a time with layout
   enabled and its own OCR disabled.
2. Native page Markdown is normalised: `ff`, `fi`, `fl`, `ffi` and `ffl`
   ligatures expand; no-break spaces become spaces; soft hyphens are removed;
   ordinal brackets such as `1[st]` become `1st`; trailing spaces, tabs and
   carriage returns are trimmed from each line; runs of three or more newlines
   collapse to two; outer whitespace is trimmed.
3. Image discovery uses the PDF image objects, not layout inference. It scans
   the default layer state plus each checkbox/radio layer with a UI entry,
   hiding all layers and then enabling one at a time, and unions the visible
   placements. Bounding boxes are clipped to the page; placements 1 pt or
   smaller in either dimension are ignored. Placements are deduplicated by
   xref, bbox rounded to 0.1 pt and image digest.
4. A placement is skipped when a later-painted image completely contains its
   clipped bbox (0.5 pt tolerance) and is fully opaque. Fully opaque means no
   mask, or a soft mask whose bytes are all 255. Unknown and color-key masks
   are treated as transparent, and PDF graphics-state transparency is not
   analysed. The rule is applied per layer state, so a lower image that is
   visible in another state is still kept.
5. A surviving placement that intersects another surviving placement in the
   same state is OCR'd from the raw image XObject so the crop cannot contain
   the other image; if that extraction fails it falls back to a page crop.
   Other placements are rendered from the page at 300 dpi. Positions use the
   clipped bbox.
6. If a page has no placements and fewer than 40 distinct native word tokens,
   the full page is rendered at 300 dpi for OCR. A token is a lowercase run of
   at least two ASCII letters or digits from `page.get_text()`; this catches
   vector-only pages whose text was converted to outlines.
7. Pages are marked `<!-- page N of M -->`. When at least one OCR block
   survives the filters, the page gets one OCR warning comment followed by one
   blockquote per surviving image. Placements are emitted in discovery order:
   default state first, then each isolated layer, with paint order inside a
   state. OCR is dropped when it has fewer than 10 non-whitespace characters
   or when the deduplication rule below matches the same page. Positions are
   page-relative integer percentages rounded to the nearest 1% and clamped to
   0–100%. Ordinary placements are labelled `image`; a full-page fallback is
   labelled `full page`.
8. Images that exist only as PDF annotations, and vector-only content on
   hidden layers, are outside image-object discovery. The full-page fallback
   uses the default layer state.

### PPTX

1. Pandoc converts slides to GitHub-flavored Markdown; slide titles become
   level-2 headings.
2. The package parses the PPTX XML for slide order, speaker notes, comments and
   picture coordinates.
3. Slides are marked `<!-- slide N of M -->`.
4. Images are replaced by OCR text. Pictures in pipe-table rows use inline
   text. When a picture position is available it is appended as percentages
   rounded to the nearest 10% and clamped to 0–100%. A normal picture gets
   `- **Position:** [top: 45%, left: 60%, width: 40%, height: 20%]`; a picture
   inside a pipe table gets `<br>**Position:** [...]` inline. OCR text under
   10 non-whitespace characters drops the image and its position.
5. Speaker notes and comments are appended at the end of their slide.

### OCR

OCR uses Tesseract with `-l eng` and the bundled English model. Every image is
run through four page-segmentation modes and the best result is kept:

| PSM | Assumption | Good for |
| --- | --- | --- |
| 3 | automatic layout | normal pages and mixed layouts |
| 4 | single column | tables, receipts, row-wise data |
| 6 | single uniform block | cropped paragraphs and text blocks |
| 11 | sparse text | diagrams, labels, screenshots |

Confidence alone favours short, easy reads, so each result is ranked by:

```text
confidence × (words / max_words)
```

This discounts a result that dropped table rows; it never rewards extra words.
Each image placement uses one Tesseract process at a time
(`OMP_THREAD_LIMIT=1`); the job queue controls parallelism with `--jobs`.

### Text-layer deduplication

PDF OCR is compared with the **same page's** native text layer after removing
all Unicode whitespace from both strings. OCR is discarded only when the
stripped OCR text is an **ordered subsequence** of the stripped native text:
every OCR character must appear in the native text in the same order, although
the characters do not need to be contiguous.

Case, punctuation and repeated characters still matter. Native
`the court may adjourn` and OCR `court adjourn` deduplicate because
`courtadjourn` is an ordered subsequence of `thecourtmayadjourn`. OCR
`adjourn court` is kept because the order is wrong. A bag-of-words match is
not enough.

For PDF, this check runs separately for every image placement, after the
10-character drop, against the cleaned native text of the same page. Two images
on the same page can both contribute the same OCR text; only text already
present in the native layer is removed. It is not used for DOCX or PPTX.

### Vector images

- **EMF+** records are parsed directly: the embedded BGRA/BGR bitmap is
  extracted, alpha-flattened onto white and written as PNG.
- **EMF** is converted to SVG with `emf2svg-conv`, then to PNG with
  `rsvg-convert`.
- **SVG** is rendered with `rsvg-convert`.
- **WMF** has no renderer; DOCX reports it as a vector image that was not OCR'd.
- If none of these produce an image, a DOCX file is listed under
  `## Vector images (not OCR'd)` rather than silently dropped. PPTX has no
  vector-failure list; its unrenderable vector pictures are dropped like empty
  OCR text.

The helpers are musl-linked, so the same bundle runs on glibc and musl hosts.
They are launched through the bundled loader with `--library-path`.

### Markdown and token counts

The Markdown is assembled in Python: front matter, title, body, figures,
comments and page/slide markers. `cp4ai` reports exact `cl100k_base` token
counts using `tiktoken`.

### Parallelism

Both CLIs use one dynamic job queue:

1. **Text extraction** — Pandoc for DOCX/PPTX.
2. **PDF extraction** — PyMuPDF produces page Markdown and per-image
   placement PNGs.
3. **OCR** — Tesseract processes one image placement.

Each document starts with an extraction job. When it finishes, it enqueues its
OCR jobs; extraction for other documents continues while those OCR jobs run.
There is no stage barrier between documents. The queue has N workers, each
worker executes one job at a time on one thread, and jobs never spawn nested
worker pools.

`--jobs N` sets N; both CLIs default to all active CPU cores. OCR text is not
inserted while the queue is running. Once the queue is completely drained,
Python assembles every document in input order, replaces images with the cached
OCR text and writes the outputs. `md4ai` checks all output paths before starting,
so a batch fails before any file is written if an output would collide or
already exists.

### Design choices

- **Recall before cleanliness.** OCR errors and formatting noise are kept
  because the downstream AI can interpret them; losing a point is not
  recoverable.
- **Native text first.** OCR is additive and is removed only by the exact
  ordered-subsequence rule above.
- **Images never enter the output.** Only their recognized text does.
- **Comments stay in context.** DOCX comments follow their anchored passage;
  PPTX notes and comments follow their slide.
- **Page and slide markers are HTML comments**, so they remain searchable
  without rendering.
- **Everything is local.** No uploads, no accounts and no network calls during
  conversion.
- **Bundled helpers, not installed.** librsvg, libemf2svg and the English OCR
  model ship in the wheel; users only need Pandoc, Tesseract and a clipboard
  tool.
