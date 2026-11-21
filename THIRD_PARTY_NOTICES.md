# Third-party notices

Copy 4 AI itself is released under the MIT License.

The package bundles two converter programs and the dynamic libraries they need
in `src/cp4ai/resources/helpers.zip`, for `linux-x86_64` and `linux-aarch64`.
The helpers are built against musl (Alpine 3.22) and are unmodified apart from
stripping and, where needed, making their interpreter path relocatable at
runtime. They are extracted to the user cache by `cp4ai-setup` or on first use;
the installed package is not modified.

Pandoc and the Tesseract binary are **not** bundled. The user installs them
separately; see [README.md](README.md). Tesseract's English traineddata and
`tsv` config are bundled so OCR works without a distribution language pack.

## Bundled executables

- **librsvg / rsvg-convert 2.60.0** — LGPL-2.1-or-later
  https://gitlab.gnome.org/GNOME/librsvg
- **libemf2svg / emf2svg-conv 1.8.1** — GPL-2.0-only
  https://github.com/kakwa/libemf2svg
  Built from source with `libargp` (argp-standalone, LGPL-2.1-or-later)
  linked statically.

## Bundled OCR data

- **Tesseract `eng.traineddata`** (4.1 MiB, LSTM/legacy model from the
  `tesseract-ocr/tessdata` repository) — Apache-2.0
  https://github.com/tesseract-ocr/tessdata
- **Tesseract `configs/tsv`** — Apache-2.0
  https://github.com/tesseract-ocr/tesseract

## Bundled shared libraries

The following libraries are included for both architectures. Versions follow
the Alpine 3.22 packages used in the build.

| Library | License |
| --- | --- |
| Cairo | LGPL-2.1-or-later OR MPL-1.1 |
| Fontconfig | MIT |
| FreeType | FTL OR GPL-2.0-or-later |
| FriBidi | LGPL-2.1-or-later |
| GLib / GObject / GIO | LGPL-2.1-or-later |
| Graphite2 | LGPL-2.1-or-later OR MPL-1.1 |
| HarfBuzz | MIT |
| libblkid / libmount | LGPL-2.1-or-later |
| libbsd | BSD-3-Clause |
| libbz2 | bzip2-1.0.6 |
| libdav1d | BSD-2-Clause |
| libeconf | MIT |
| libexpat | MIT |
| libffi | MIT |
| libgcc / libstdc++ | GPL-2.0-or-later AND LGPL-2.1-or-later |
| libintl | LGPL-2.1-or-later |
| libmd | BSD-3-Clause AND BSD-2-Clause AND ISC AND Beerware AND Public Domain |
| libpng | Libpng |
| libX11 | X11 |
| libXau / libxcb / libXdmcp / libXext / libXft / libXrender | MIT |
| libxml2 | MIT |
| Pango | LGPL-2.1-or-later |
| PCRE2 | BSD-3-Clause |
| Pixman | MIT |
| XZ Utils (liblzma) | GPL-2.0-or-later AND 0BSD AND Public-Domain AND LGPL-2.1-or-later |

Source for each library is available from the upstream projects listed by
Alpine at https://pkgs.alpinelinux.org/packages?name=<package>&branch=v3.22,
or from the project sites:

- https://gitlab.freedesktop.org/cairo/cairo
- https://gitlab.freedesktop.org/fontconfig/fontconfig
- https://gitlab.freedesktop.org/freetype/freetype
- https://gitlab.gnome.org/GNOME/glib
- https://gitlab.gnome.org/GNOME/pango
- https://github.com/harfbuzz/harfbuzz
- https://github.com/fribidi/fribidi
- https://github.com/silnrsi/graphite
- https://www.libpng.org/pub/png/libpng.html
- https://gitlab.freedesktop.org/pixman/pixman
- https://www.x.org/

## Python packages

- **PyMuPDF / pymupdf4llm / pymupdf-layout** — AGPL-3.0
  https://github.com/pymupdf/PyMuPDF
- **ONNX Runtime** — MIT
  https://github.com/microsoft/onnxruntime
- **Tiktoken** — MIT
  https://github.com/openai/tiktoken
- **NumPy** — BSD-3-Clause
  https://numpy.org/
- **PyYAML** — MIT
  https://pyyaml.org/
- **NetworkX** — BSD-3-Clause
  https://networkx.org/
- **psutil** — BSD-3-Clause
  https://github.com/giampaolo/psutil
- **tabulate** — MIT
  https://github.com/astanin/python-tabulate
- **requests / urllib3 / certifi / idna / charset-normalizer** — Apache-2.0 / MIT / MPL-2.0 / BSD-3-Clause / MIT
  https://requests.readthedocs.io/
- **Python** — PSF-2.0
  https://www.python.org/

## External tools

Pandoc is GPL-2.0-or-later and the Tesseract binary is Apache-2.0. They are
installed by the user and are not part of the wheel.
