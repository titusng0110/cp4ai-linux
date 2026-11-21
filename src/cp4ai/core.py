"""Convert lecture notes from DOCX, PDF and PPTX into AI-friendly Markdown.

What this package guarantees
----------------------------
* It never summarizes or truncates the source text.
* DOCX files go through Pandoc, so headings, lists, tables, footnotes, text
  boxes and tracked changes survive.
* Every raster DOCX image is OCR'd and its text is inserted into the Markdown
  at the image's position, whether Pandoc emitted a Markdown link or an HTML
  ``<img>`` tag. Image files are *not* copied into the output.
* Every PDF keeps its digital text layer and page order, with page boundaries
  marked as ``<!-- page N of M -->``. Text that exists only inside PDF images
  (screenshots, table fragments, diagrams) is OCR'd and appended on the same
  page, under a clearly marked OCR comment.
* OCR uses Tesseract, which the user must provide on ``PATH``. Transparent
  images are handled by Tesseract directly.
* DOCX comments are inserted next to the passage they annotate, with author
  and date; comments without a visible anchor go into an appendix. Pandoc
  preserves the anchors as ``comment-start``/``comment-end`` spans.
* PPTX slides are marked ``<!-- slide N of M -->``; speaker notes and comments
  are appended at the end of each slide. PPTX image OCR has no minimum-length
  filter and includes shape coordinates when available.
* A few DOCX files contain EMF/vector drawings. EMF+ bitmaps are extracted
  directly from the EMF record and OCR'd; classic EMF/SVG are rasterised with
  libemf2svg + librsvg. Only images that still fail are listed in a short
  "Vector images (not OCR'd)" section at the end of the Markdown.

Usage
-----
    uv tool install .
    md4ai a.pdf
    md4ai a.pptx
    md4ai -r notes/
    md4ai -rf notes/
    md4ai -o out/ -r notes/
    md4ai --no-ocr a.pdf

External tools
--------------
    Pandoc    (required for DOCX/PPTX; user-supplied on PATH)
    Tesseract (required for OCR unless --no-ocr; user-supplied on PATH)

Reproducible environment
------------------------
    Python 3.13
    pymupdf==2.1
    numpy==2.5.5
    tiktoken==0.14.0
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import posixpath
import re
import struct
import subprocess
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import numpy as np
import pymupdf

pymupdf.use_layout(True)

logger = logging.getLogger(__name__)

from .pandoc import pandoc_binary, pandoc_version
from .progress import ocr_done, page_done
from .resource_paths import helper_command

PACKAGE_DIR = Path(__file__).resolve().parent
TESSDATA_DIR = PACKAGE_DIR / "resources" / "tessdata"

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
PPTX_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)')
HTML_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
HTML_IMG_SRC_RE = re.compile(
    r"""\bsrc\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL
)
HTML_IMG_ALT_RE = re.compile(
    r"""\balt\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL
)
OCR_COMMENT = (
    "<!-- OCR text from image(s) on this page; the text existed only inside "
    "a picture and may contain recognition errors -->"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """Normalize PDF text without changing its substantive content."""
    for src, dst in LIGATURES.items():
        text = text.replace(src, dst)
    text = text.replace("\u00a0", " ").replace("\u00ad", "")
    # Superscripts such as "1 [st] loan" are common in the PDF text layer.
    text = re.sub(r"(?<=\d)\s*\[(st|nd|rd|th)\]", r"\1", text, flags=re.IGNORECASE)
    lines = []
    for line in text.splitlines():
        lines.append(line.rstrip())
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def yaml_value(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def md_title(path: Path) -> str:
    return re.sub(r"\s+", " ", path.stem).strip()


def display_path(path: Path) -> str:
    """Prefer a working-directory-relative path so the Markdown stays portable."""
    cwd = Path.cwd()
    if cwd != Path("/"):
        try:
            return path.resolve().relative_to(cwd).as_posix()
        except ValueError:
            pass
    return path.resolve().as_posix()


def front_matter(path: Path, source_format: str, **extra: object) -> str:
    lines = [
        "---",
        f"source: {yaml_value(display_path(path))}",
        f"format: {source_format}",
        f"converted: {yaml_value(date.today().isoformat())}",
    ]
    for key, value in extra.items():
        if value is None or value == "":
            continue
        lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def write_markdown(out_path: Path, body: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body.rstrip() + "\n", encoding="utf-8")


def token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", text.lower()))


def useful_ocr(text: str) -> bool:
    """Discard OCR from decorative images: fewer than 10 non-space characters."""
    return len(re.sub(r"\s+", "", text)) >= 10


def is_ordered_subsequence_after_stripping_whitespace(
    candidate: str, text: str
) -> bool:
    """Return whether candidate is an ordered subsequence of text.

    All Unicode whitespace is removed from both strings first. Case,
    punctuation and duplicate characters remain significant. This is used to
    drop OCR only when it already appears, in order, in the same page's native
    text layer.
    """
    needle = [char for char in candidate if not char.isspace()]
    if not needle:
        return True

    index = 0
    for char in text:
        if char.isspace():
            continue
        if char == needle[index]:
            index += 1
            if index == len(needle):
                return True
    return False


# ---------------------------------------------------------------------------
# DOCX conversion
# ---------------------------------------------------------------------------


def docx_xml_text(data: bytes) -> str:
    root = ET.fromstring(data)
    chunks: list[str] = []
    for para in root.iter(W_NS + "p"):
        text = "".join(node.text or "" for node in para.iter(W_NS + "t")).strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def extract_docx_header(path: Path) -> str:
    """Return the first substantive Word header, ignoring page-number footers."""
    headers: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in sorted(zf.namelist()):
                if not re.fullmatch(r"word/header\d+\.xml", name):
                    continue
                text = " ".join(docx_xml_text(zf.read(name)).split())
                if text and not re.fullmatch(r"PAGE(?:\s*\d+)?", text, re.IGNORECASE):
                    headers.append(text)
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return ""
    return headers[0] if headers else ""


def extract_docx_comments(path: Path) -> list[dict[str, str]]:
    """Extract DOCX comments, authors, dates and the passage they annotate."""
    try:
        with zipfile.ZipFile(path) as zf:
            if "word/comments.xml" not in zf.namelist():
                return []
            comments_root = ET.fromstring(zf.read("word/comments.xml"))
            document_root = ET.fromstring(zf.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return []

    anchors: dict[str, list[str]] = {}
    order: list[str] = []
    for para in document_root.iter(W_NS + "p"):
        ids = [
            node.get(W_NS + "id")
            for node in para.iter()
            if node.tag in (W_NS + "commentRangeStart", W_NS + "commentReference")
        ]
        ids = [i for i in ids if i is not None]
        if not ids:
            continue
        text = "".join(node.text or "" for node in para.iter(W_NS + "t")).strip()
        for comment_id in ids:
            if comment_id not in order:
                order.append(comment_id)
            anchors.setdefault(comment_id, [])
            if text and text not in anchors[comment_id]:
                anchors[comment_id].append(text)

    comments: list[dict[str, str]] = []
    for node in comments_root.findall(W_NS + "comment"):
        paragraphs: list[str] = []
        for para in node.iter(W_NS + "p"):
            text = "".join(child.text or "" for child in para.iter(W_NS + "t")).strip()
            if text:
                paragraphs.append(text)
        if not paragraphs:
            continue
        comments.append(
            {
                "id": node.get(W_NS + "id", ""),
                "author": node.get(W_NS + "author", "Unknown"),
                "date": (node.get(W_NS + "date") or "")[:10],
                "text": "\n\n".join(paragraphs),
                "anchor": " … ".join(anchors.get(node.get(W_NS + "id", ""), [])),
            }
        )

    rank = {comment_id: i for i, comment_id in enumerate(order)}
    comments.sort(key=lambda c: (rank.get(c["id"], 10**9), c["id"]))
    return comments


COMMENT_SPAN_RE = re.compile(
    r'<span[^>]*class="comment-(?:start|end|reference)"[^>]*\bid="(\d+)"'
)


def comment_heading(comment: dict[str, str], index: int) -> str:
    heading = f"Comment {index} — {comment['author']}"
    if comment["date"]:
        heading += f" — {comment['date']}"
    return heading


def comment_block(comment: dict[str, str], index: int, indent: str = "") -> list[str]:
    """Render one comment as a blockquote at the given indentation."""
    lines = [f"{indent}> **{comment_heading(comment, index)}**", f"{indent}>"]
    anchor = comment["anchor"].strip()
    if anchor:
        if len(anchor) > 400:
            anchor = anchor[:400].rstrip() + "…"
        lines.extend([f"{indent}> **Attached to:** “{anchor}”", f"{indent}>"])
    for line in comment["text"].splitlines():
        lines.append(f"{indent}> {line}" if line.strip() else f"{indent}>")
    return lines


def place_inline_comment(
    line: str, comment_id: str, inline: str, pipe_table: bool
) -> str:
    """Insert a comment at the end of the cell containing its anchor span."""
    matches = [match for match in COMMENT_SPAN_RE.finditer(line) if match.group(1) == comment_id]
    if not matches:
        return line + inline
    position = matches[-1].start()
    if pipe_table:
        delimiter = re.search(r"(?<!\\)\|", line[position:])
        if delimiter:
            at = position + delimiter.start()
            return line[:at] + inline + line[at:]
        return line + inline
    cell_end = re.search(r"</(?:td|th)>", line[position:])
    if cell_end:
        at = position + cell_end.start()
        return line[:at] + inline + line[at:]
    return line + inline


def inline_comment(comment: dict[str, str], index: int, pipe_table: bool) -> str:
    """Render one comment inline, for a table cell that must stay intact."""
    text = " ".join(comment["text"].split())
    parts = [f"<br><strong>{html.escape(comment_heading(comment, index))}</strong>"]
    anchor = comment["anchor"].strip()
    if anchor:
        if len(anchor) > 200:
            anchor = anchor[:200].rstrip() + "…"
        parts.append(f" <em>“{html.escape(anchor)}”</em>")
    parts.append(f": {html.escape(text)}")
    inline = "".join(parts)
    return inline.replace("|", "\\|") if pipe_table else inline


def insert_comments_inline(
    markdown: str, comments: list[dict[str, str]]
) -> tuple[str, list[tuple[int, dict[str, str]]]]:
    """Move comments from the appendix to just after their anchored passage.

    Pandoc emits ``<span class="comment-start/end">`` markers around the text a
    comment is attached to. Use those positions to place the comment text at
    the end of the containing paragraph, list item or table cell.
    """
    if not comments:
        return markdown, []

    by_id = {comment["id"]: comment for comment in comments}
    number = {comment["id"]: index for index, comment in enumerate(comments, start=1)}
    lines = markdown.splitlines()

    last_line: dict[str, int] = {}
    line_is_table: list[bool] = []
    in_html_table = False
    for line_index, line in enumerate(lines):
        if "<table" in line:
            in_html_table = True
        line_is_table.append(in_html_table or line.lstrip().startswith("|"))
        for match in COMMENT_SPAN_RE.finditer(line):
            if match.group(1) in by_id:
                last_line[match.group(1)] = line_index
        if "</table>" in line:
            in_html_table = False

    insertions: dict[int, list[str]] = {}
    unplaced: list[tuple[int, dict[str, str]]] = []
    for comment in comments:
        line_index = last_line.get(comment["id"])
        if line_index is None:
            unplaced.append((number[comment["id"]], comment))
        else:
            insertions.setdefault(line_index, []).append(comment["id"])

    out: list[str] = []
    for line_index, line in enumerate(lines):
        ids = insertions.get(line_index)
        if not ids:
            out.append(line)
            continue

        if line_is_table[line_index]:
            updated = line
            pipe_table = updated.lstrip().startswith("|")
            for comment_id in ids:
                inline = inline_comment(
                    by_id[comment_id], number[comment_id], pipe_table
                )
                updated = place_inline_comment(updated, comment_id, inline, pipe_table)
            out.append(updated)
            continue

        out.append(line)
        leading = re.match(r"^[ \t]*", line).group(0)
        indent = leading
        if re.match(r"^[ \t]*(?:[-*+]|\d+[.)])\s+", line):
            indent = leading + "    "
        for comment_id in ids:
            out.extend(comment_block(by_id[comment_id], number[comment_id], indent))
        out.append("")

    return "\n".join(out), unplaced


def unplaced_comments_appendix(comments: list[tuple[int, dict[str, str]]]) -> str:
    """Append comments whose anchors do not appear in the converted Markdown."""
    if not comments:
        return ""
    out = [
        "---",
        "",
        "## Appendix: Comments not anchored in the body",
        "",
        (
            "> These comments had no visible anchor in the converted Markdown, "
            "so they are reproduced verbatim below with their author, date and "
            "the passage Word recorded."
        ),
    ]
    for index, comment in comments:
        out.extend(["", f"### {comment_heading(comment, index)}", ""])
        anchor = comment["anchor"].strip()
        if anchor:
            if len(anchor) > 400:
                anchor = anchor[:400].rstrip() + "…"
            out.extend([f"**Attached to:** “{anchor}”", ""])
        quoted = "\n".join(
            f"> {line}" if line.strip() else ">" for line in comment["text"].splitlines()
        )
        out.append(quoted)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Image OCR (shared by DOCX and PDF)
# ---------------------------------------------------------------------------


OCR_PSMS = (3, 4, 6, 11)
OCR_LANG = "eng"


def ocr_engine_label(use_ocr: bool) -> str:
    return f"tesseract {OCR_LANG}" if use_ocr else "disabled"


class OcrResult(NamedTuple):
    text: str
    confidence: float
    words: int


def tesseract_tsv(image: Path, psm: int) -> OcrResult:
    """Run Tesseract and return reconstructed text, confidence and word count.

    Confidence is weighted by word length, so a few long, low-confidence words
    cannot dominate the mean. No image preprocessing is applied.
    """
    cmd = ["tesseract", str(image), "stdout", "-l", OCR_LANG, "--psm", str(psm)]
    if (TESSDATA_DIR / "eng.traineddata").is_file():
        cmd.extend(["--tessdata-dir", str(TESSDATA_DIR)])
    cmd.append("tsv")
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        return OcrResult("", 0.0, 0)

    lines = proc.stdout.splitlines()
    if lines and lines[0].startswith("level"):
        lines = lines[1:]

    output: list[str] = []
    line_words: list[str] = []
    current_key: tuple[str, str, str, str] | None = None
    last_paragraph: str | None = None
    total = 0.0
    weight = 0
    word_count = 0

    for line in lines:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        word = parts[11].strip()
        if not word:
            continue
        key = (parts[1], parts[2], parts[3], parts[4])  # page, block, paragraph, line
        if current_key is not None and key != current_key:
            output.append(" ".join(line_words))
            line_words = []
            if last_paragraph is not None and key[2] != last_paragraph:
                output.append("")
            last_paragraph = current_key[2]
        current_key = key
        line_words.append(word)

        try:
            confidence = float(parts[10])
        except ValueError:
            continue
        if confidence >= 0:
            total += confidence * len(word)
            weight += len(word)
            word_count += 1

    if line_words:
        output.append(" ".join(line_words))

    text = "\n".join(output).replace("\x0c", "\n")
    mean_confidence = total / weight if weight else 0.0
    return OcrResult(text, mean_confidence, word_count)


def ocr_image_tesseract(path: Path) -> str:
    """OCR one image with the same four PSM modes for DOCX and PDF.

    Modes:
      3  automatic      mixed layouts and full pages
      4  single column  tables and row-wise content
      6  single block   cropped uniform text blocks
      11 sparse text    diagrams, labels and screenshots

    Raw confidence alone favours short, easy reads; sparse mode can score high
    while dropping table rows.  Each result is therefore discounted by the
    fraction of the best word coverage it captured:

        confidence * (words / max_words)

    This never rewards extra words (the largest result is not penalised), but
    a truncated result needs a clearly higher confidence to win.
    """
    runs: list[OcrResult] = []
    for psm in OCR_PSMS:
        result = tesseract_tsv(path, psm=psm)
        if result.text.strip():
            runs.append(result)
    if not runs:
        return ""

    max_words = max(run.words for run in runs)
    if max_words <= 0:
        best = max(runs, key=lambda run: run.confidence)
    else:
        best = max(
            runs,
            key=lambda run: (
                run.confidence * run.words / max_words,
                run.words,
                run.confidence,
            ),
        )

    text = best.text
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ocr_image(path: Path) -> str:
    """OCR one image with Tesseract."""
    return ocr_image_tesseract(path)


def save_bgr_png(bgr: np.ndarray, out_path: Path) -> bool:
    """Save a BGR uint8 array as PNG using PyMuPDF (no OpenCV)."""
    height, width = bgr.shape[:2]
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    pix = pymupdf.Pixmap(pymupdf.csRGB, width, height, rgb.tobytes(), 0)
    pix.save(str(out_path))
    return out_path.exists() and out_path.stat().st_size > 0


def extract_emf_plus_bitmap(emf_path: Path, out_png: Path) -> bool:
    """Extract the bitmap embedded in an EMF+ file and flatten it onto white.

    Word commonly stores screenshots/tables as EMF+ ``EmfPlusBitmap`` records
    rather than as separate PNG files. ``libemf2svg`` ignores EMF+ records
    entirely, so we read the raw BGRA pixels from the record directly.
    """
    data = emf_path.read_bytes()
    offset = 0
    plus_chunks: list[tuple[int, int]] = []
    while offset + 8 <= len(data):
        record_type, record_size = struct.unpack_from("<II", data, offset)
        if record_size < 8 or offset + record_size > len(data):
            break
        if record_type == 70:  # EMR_COMMENT
            _, comment_id = struct.unpack_from("<II", data, offset + 8)
            if comment_id == 0x2B464D45:  # "EMF+"
                plus_chunks.append((offset + 16, offset + record_size))
        offset += record_size

    for start, end in plus_chunks:
        cursor = start
        while cursor + 12 <= end:
            record_type, _flags, record_size, _data_size = struct.unpack_from(
                "<HHII", data, cursor
            )
            if record_type == 0x4008 and record_size > 40:  # EmfPlusBitmap
                width, height, stride = struct.unpack_from("<III", data, cursor + 20)
                payload = data[cursor + 40 : cursor + 40 + stride * height]
                if (
                    0 < width <= 10000
                    and 0 < height <= 10000
                    and stride in (width * 3, width * 4)
                    and len(payload) == stride * height
                ):
                    pixels = np.frombuffer(payload, dtype=np.uint8)
                    if stride == width * 4:
                        bgra = pixels.reshape(height, stride)[:, : width * 4].reshape(
                            height, width, 4
                        )
                        alpha = bgra[:, :, 3]
                        if int(alpha.max()) == 0:
                            bgr = bgra[:, :, :3]
                        else:
                            alpha_f = alpha.astype(np.float32) / 255.0
                            bgr = bgra[:, :, :3].astype(np.float32) + (
                                1.0 - alpha_f[:, :, None]
                            ) * 255.0
                            bgr = np.clip(bgr, 0, 255).astype(np.uint8)
                    else:
                        bgr = pixels.reshape(height, stride)[:, : width * 3].reshape(
                            height, width, 3
                        )
                    return save_bgr_png(bgr, out_png)
            if record_size < 12 or cursor + record_size > end:
                break
            cursor += record_size
    return False


def svg_to_png(svg_path: Path, out_png: Path, width: int = 2400) -> bool:
    helper = helper_command("rsvg-convert")
    logger.info("svg_to_png: helper=%r svg=%s", helper, svg_path)
    if helper is None:
        return False
    command, env = helper
    proc = subprocess.run(
        [*command, str(svg_path), "-o", str(out_png), "--width", str(width)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    ok = proc.returncode == 0 and out_png.exists() and out_png.stat().st_size > 0
    logger.info(
        "svg_to_png: rc=%d ok=%s stderr=%r",
        proc.returncode,
        ok,
        proc.stderr[:300],
    )
    return ok


def vector_to_png(source: Path, out_png: Path, workdir: Path) -> bool:
    """Convert EMF/EMF+/SVG to a raster image for OCR."""
    suffix = source.suffix.lower()
    if suffix == ".emf" and extract_emf_plus_bitmap(source, out_png):
        return True
    if suffix in {".emf", ".svg"}:
        svg_path = source if suffix == ".svg" else workdir / f"{source.stem}_vector.svg"
        if suffix == ".emf":
            helper = helper_command("emf2svg-conv")
            logger.info("vector_to_png: helper=%r source=%s", helper, source)
            if helper is None:
                return False
            command, env = helper
            proc = subprocess.run(
                [*command, "-p", "-i", str(source), "-o", str(svg_path)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            logger.info(
                "vector_to_png: emf rc=%d exists=%s stderr=%r",
                proc.returncode,
                svg_path.exists(),
                proc.stderr[:300],
            )
            if proc.returncode != 0 or not svg_path.exists():
                return False
            if svg_path.stat().st_size < 500:
                return False
            if all(token not in svg_path.read_text(errors="ignore") for token in ("<path", "<image", "<text")):
                return False
        return svg_to_png(svg_path, out_png)
    return False


def ocr_image_cached(
    path: Path,
    cache: dict[tuple, str] | None,
) -> str:
    """OCR a file, reusing text only for bit-identical input bytes."""
    if cache is None:
        return ocr_image(path)
    data = path.read_bytes()
    cache_key = (path.suffix.lower(), len(data), hashlib.sha256(data).digest())
    if cache_key in cache:
        return cache[cache_key]
    text = ocr_image(path)
    cache[cache_key] = text
    return text


def ocr_image_file(
    path: Path,
    workdir: Path,
    index: int,
    cache: dict[tuple, str] | None = None,
) -> str:
    """OCR a raster image, converting EMF/EMF+/SVG to PNG first if needed."""
    if path.suffix.lower() in {".emf", ".wmf", ".svg"}:
        png_path = workdir / f"vector_{index}.png"
        if not vector_to_png(path, png_path, workdir):
            return ""
        return ocr_image_cached(png_path, cache)
    return ocr_image_cached(path, cache)


def ocr_blockquote(text: str, label: str) -> str:
    lines = [f"> **Image text (OCR) — {label}**", ">"]
    lines.extend(f"> {line}" if line.strip() else ">" for line in text.splitlines())
    return "\n".join(lines)


def resolve_docx_media(source: str, media_dir: Path, workdir: Path) -> Path:
    """Resolve a Markdown or HTML image source against Pandoc's temp tree."""
    source = html.unescape(unquote(source.strip()))
    for base in (workdir, media_dir):
        candidate = (base / source).resolve()
        if candidate.exists():
            return candidate
    return (workdir / source).resolve()


def replace_docx_images(
    markdown: str,
    media_dir: Path,
    workdir: Path,
    cache: dict[tuple, str],
    use_ocr: bool = True,
) -> tuple[str, list[Path], set[Path]]:
    """Replace every DOCX image link or ``<img>`` tag with its OCR text."""
    failed_vectors: list[Path] = []
    referenced: set[Path] = set()
    counter = 0

    def image_text(source: str, alt: str) -> tuple[str, str, str | None]:
        nonlocal counter
        image_path = resolve_docx_media(source, media_dir, workdir)
        label = alt.strip() or Path(source).name
        if not image_path.exists():
            return "", label, f"<!-- image not extracted: {label} -->"

        counter += 1
        referenced.add(image_path)
        if not use_ocr:
            return "", label, f"<!-- image omitted: {label} (OCR disabled) -->"
        text = ocr_image_file(image_path, workdir, counter, cache)
        ocr_done()
        if text and useful_ocr(text):
            return text, label, None
        if image_path.suffix.lower() in {".emf", ".wmf", ".svg"}:
            failed_vectors.append(image_path)
            return "", label, f"<!-- vector image not OCR'd: {label} -->"
        return "", label, f"<!-- image omitted: {label} (no readable text detected) -->"

    def markdown_repl(match: re.Match[str]) -> str:
        alt = match.group(1).strip()
        source = match.group(2).strip()
        text, label, note = image_text(source, alt)
        if note:
            return note

        # Keep table rows intact: a multi-line blockquote would break the table.
        line_start = markdown.rfind("\n", 0, match.start()) + 1
        line_end = markdown.find("\n", match.end())
        line = markdown[line_start : line_end if line_end != -1 else len(markdown)]
        if line.lstrip().startswith("|"):
            escaped = text.replace("|", "\\|").replace("\n", "<br>")
            return f"<br>**Image text (OCR) — {label}:** {escaped}"

        return "\n\n" + ocr_blockquote(text, label) + "\n\n"

    def html_repl(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = HTML_IMG_SRC_RE.search(tag)
        if not src_match:
            return "<!-- image not extracted: missing src -->"
        alt_match = HTML_IMG_ALT_RE.search(tag)
        alt = html.unescape(alt_match.group(2)) if alt_match else ""
        text, label, note = image_text(src_match.group(2), alt)
        if note:
            return note

        line_start = markdown.rfind("\n", 0, match.start()) + 1
        line_end = markdown.find("\n", match.end())
        line = markdown[line_start : line_end if line_end != -1 else len(markdown)]
        prefix = markdown[: match.start()].lower()
        in_table = (
            prefix.count("<table") > prefix.count("</table>")
            or line.lstrip().startswith("|")
            or "<td" in line
            or "<th" in line
        )
        standalone = line.strip() == tag.strip()
        if standalone and not in_table:
            return "\n\n" + ocr_blockquote(text, label) + "\n\n"

        escaped = html.escape(text).replace("\n", "<br>")
        if in_table:
            escaped = escaped.replace("|", "\\|")
        return f"<br>**Image text (OCR) — {html.escape(label)}:** {escaped}"

    markdown = IMAGE_RE.sub(markdown_repl, markdown)
    return HTML_IMG_RE.sub(html_repl, markdown), failed_vectors, referenced


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def figures_section(
    media_dir: Path,
    referenced: set[Path],
    workdir: Path,
    cache: dict[tuple, str],
    use_ocr: bool = True,
) -> tuple[str, int, list[Path]]:
    """OCR images that Pandoc extracted but could not place in the body.

    Word floating/anchored drawings are extracted to ``media/`` but do not
    produce a Markdown image link. They often contain the most important
    diagrams in the document, so none may be dropped.
    """
    if not use_ocr:
        return "", 0, []
    image_suffixes = {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp",
        ".emf", ".wmf", ".svg",
    }
    figures: list[tuple[str, str]] = []
    failed_vectors: list[Path] = []
    index = 5000
    for image_path in sorted((p for p in media_dir.rglob("*") if p.is_file()), key=natural_key):
        if image_path.suffix.lower() not in image_suffixes:
            continue
        if image_path.resolve() in referenced:
            continue
        index += 1
        text = ocr_image_file(image_path, workdir, index, cache)
        ocr_done()
        if text and useful_ocr(text):
            figures.append((image_path.name, text))
        elif image_path.suffix.lower() in {".emf", ".wmf", ".svg"}:
            failed_vectors.append(image_path)
    if not figures:
        return "", 0, failed_vectors

    out = [
        "## Figures from the original document (OCR text)",
        "",
        (
            "> Pandoc could not position these floating/anchored drawings in the "
            "body, but the images were still present in the DOCX. Their text is "
            "reproduced below in image order and may contain recognition errors."
        ),
    ]
    for number, (name, text) in enumerate(figures, start=1):
        out.extend(["", f"### Figure {number} — {name}", ""])
        out.extend(f"> {line}" if line.strip() else ">" for line in text.splitlines())
    return "\n".join(out), len(figures), failed_vectors


def vector_note(vector_files: list[Path]) -> str:
    if not vector_files:
        return ""
    lines = [
        "## Vector images (not OCR'd)",
        "",
        (
            "> The original DOCX contains EMF/WMF/SVG vector drawings that could not "
            "be rendered to a raster image for OCR. They are listed below; consult "
            "the original DOCX if their visual detail is needed."
        ),
        "",
    ]
    lines.extend(f"- `{path.name}`" for path in vector_files)
    return "\n".join(lines)


def convert_docx(
    path: Path,
    out_path: Path,
    use_ocr: bool = True,
) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cp4ai_docx_") as tmp:
        workdir = Path(tmp)
        pandoc = pandoc_binary()
        cmd = [
            pandoc,
            str(path.resolve()),
            "--from=docx",
            "--to=gfm",
            "--wrap=none",
            "--markdown-headings=atx",
            "--track-changes=all",
            "--extract-media=media",
        ]
        proc = subprocess.run(
            cmd, check=False, cwd=workdir, capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Pandoc failed for {path}:\n{proc.stderr}")

        media_dir = workdir / "media"
        ocr_cache: dict[tuple, str] = {}
        body, failed_vectors, referenced = replace_docx_images(
            proc.stdout.strip(), media_dir, workdir, ocr_cache, use_ocr
        )
        figures, figure_count, more_failed = figures_section(
            media_dir, referenced, workdir, ocr_cache, use_ocr
        )
        vector_files = sorted(set(failed_vectors + more_failed), key=natural_key)
        header = extract_docx_header(path)
        comments = extract_docx_comments(path)
        body, unplaced_comments = insert_comments_inline(body, comments)
        page_done()

    pieces = [
        front_matter(
            path,
            "docx",
            converter=f"pandoc {pandoc_version()}",
            header=header or None,
            comments=len(comments) if comments else None,
            images_ocr=ocr_engine_label(use_ocr),
        ),
        "",
        f"# {md_title(path)}",
        "",
    ]
    if header:
        pieces.extend([f"> **Original document header:** {header}", ""])
    pieces.extend(
        [
            body,
            "",
            figures,
            unplaced_comments_appendix(unplaced_comments),
            vector_note(vector_files),
        ]
    )
    write_markdown(out_path, "\n".join(pieces))
    return (
        f"docx, {len(comments)} comment(s), {figure_count} floating figure(s), "
        f"{len(vector_files)} vector image(s) not OCR'd"
    )


# ---------------------------------------------------------------------------
# PPTX conversion
# ---------------------------------------------------------------------------


def _pptx_rels(zf: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Return rId -> (relationship type, absolute part name)."""
    rels_name = posixpath.join(
        posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
    )
    if rels_name not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_name))
    rels: dict[str, tuple[str, str]] = {}
    base = posixpath.dirname(part)
    for rel in root.findall(REL_NS + "Relationship"):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if not rel_id or not target:
            continue
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join(base, target))
        rels[rel_id] = (rel.get("Type", ""), resolved)
    return rels


def _pptx_slide_parts(zf: zipfile.ZipFile) -> list[str]:
    presentation = ET.fromstring(zf.read("ppt/presentation.xml"))
    rels = _pptx_rels(zf, "ppt/presentation.xml")
    parts: list[str] = []
    for slide_id in presentation.iter(P_NS + "sldId"):
        rel_id = slide_id.get(R_NS + "id")
        if rel_id in rels:
            parts.append(rels[rel_id][1])
    return parts


def _pptx_slide_size(zf: zipfile.ZipFile) -> tuple[int, int]:
    presentation = ET.fromstring(zf.read("ppt/presentation.xml"))
    slide_size = presentation.find(P_NS + "sldSz")
    if slide_size is None:
        return 0, 0
    try:
        return int(slide_size.get("cx", "0")), int(slide_size.get("cy", "0"))
    except ValueError:
        return 0, 0


def _pptx_authors(zf: zipfile.ZipFile) -> dict[str, str]:
    if "ppt/commentAuthors.xml" not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read("ppt/commentAuthors.xml"))
    return {
        author.get("id", ""): author.get("name", "Unknown")
        for author in root.iter(P_NS + "cmAuthor")
    }


def _pptx_notes_text(zf: zipfile.ZipFile, slide_part: str) -> str:
    rels = _pptx_rels(zf, slide_part)
    notes_part = next(
        (target for kind, target in rels.values() if kind.endswith("/notesSlide")),
        None,
    )
    if not notes_part or notes_part not in zf.namelist():
        return ""
    root = ET.fromstring(zf.read(notes_part))
    lines: list[str] = []
    for shape in root.iter(P_NS + "sp"):
        placeholder = shape.find(f"{P_NS}nvSpPr/{P_NS}nvPr/{P_NS}ph")
        if placeholder is not None and placeholder.get("type") != "body":
            continue
        text_body = shape.find(P_NS + "txBody")
        if text_body is None:
            continue
        for paragraph in text_body.iter(A_NS + "p"):
            text = "".join(node.text or "" for node in paragraph.iter(A_NS + "t")).strip()
            if text:
                lines.append(text)
    notes = "\n".join(lines).strip()
    if notes in {"-", "Click to add notes"}:
        return ""
    return notes


def _pptx_comments(zf: zipfile.ZipFile, slide_part: str) -> list[dict[str, str]]:
    rels = _pptx_rels(zf, slide_part)
    comments_part = next(
        (target for kind, target in rels.values() if kind.endswith("/comments")),
        None,
    )
    if not comments_part or comments_part not in zf.namelist():
        return []
    root = ET.fromstring(zf.read(comments_part))
    authors = _pptx_authors(zf)
    comments: list[dict[str, str]] = []
    for node in root.iter(P_NS + "cm"):
        text_node = node.find(P_NS + "text")
        if text_node is not None:
            text = "".join(text_node.itertext()).strip()
        else:
            text = "".join(t.text or "" for t in node.iter(A_NS + "t")).strip()
        if not text:
            continue
        comments.append(
            {
                "author": authors.get(node.get("authorId", ""), "Unknown"),
                "date": (node.get("dt") or "")[:10],
                "text": text,
            }
        )
    return comments


def _pptx_images(zf: zipfile.ZipFile, slide_part: str) -> dict[str, tuple[int, int, int, int]]:
    rels = _pptx_rels(zf, slide_part)
    root = ET.fromstring(zf.read(slide_part))
    images: dict[str, tuple[int, int, int, int]] = {}
    for picture in root.iter(P_NS + "pic"):
        name_node = picture.find(f"{P_NS}nvPicPr/{P_NS}cNvPr")
        shape_name = name_node.get("name", "") if name_node is not None else ""
        xfrm = picture.find(f"{P_NS}spPr/{A_NS}xfrm")
        if xfrm is None:
            continue
        offset = xfrm.find(A_NS + "off")
        extent = xfrm.find(A_NS + "ext")
        if offset is None or extent is None:
            continue
        try:
            coords = (
                int(offset.get("x", "0")),
                int(offset.get("y", "0")),
                int(extent.get("cx", "0")),
                int(extent.get("cy", "0")),
            )
        except ValueError:
            continue
        if shape_name:
            images[shape_name] = coords
        blip = picture.find(f"{P_NS}blipFill/{A_NS}blip")
        if blip is not None:
            rel_id = blip.get(R_NS + "embed")
            if rel_id in rels:
                images[posixpath.basename(rels[rel_id][1])] = coords
    return images


def _pptx_metadata(path: Path) -> list[dict[str, object]]:
    with zipfile.ZipFile(path) as zf:
        slide_width, slide_height = _pptx_slide_size(zf)
        return [
            {
                "notes": _pptx_notes_text(zf, part),
                "comments": _pptx_comments(zf, part),
                "images": _pptx_images(zf, part),
                "width": slide_width,
                "height": slide_height,
            }
            for part in _pptx_slide_parts(zf)
        ]


def _pptx_pct(value: int, total: int) -> int:
    if total <= 0:
        return 0
    percent = value / total * 100
    return max(0, min(100, int(percent / 10 + 0.5) * 10))


def _pptx_coords(
    coords: tuple[int, int, int, int] | None,
    slide_width: int,
    slide_height: int,
) -> str:
    if not coords or not slide_width or not slide_height:
        return ""
    x, y, width, height = coords
    top = _pptx_pct(y, slide_height)
    left = _pptx_pct(x, slide_width)
    box_width = _pptx_pct(width, slide_width)
    box_height = _pptx_pct(height, slide_height)
    return (
        f"[top: {top}%, left: {left}%, "
        f"width: {box_width}%, height: {box_height}%]"
    )


def _replace_pptx_images(
    markdown: str,
    media_dir: Path,
    workdir: Path,
    cache: dict[tuple, str],
    use_ocr: bool,
    image_coords: dict[str, tuple[int, int, int, int]],
    slide_width: int,
    slide_height: int,
) -> str:
    counter = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        alt = match.group(1).strip()
        source = match.group(2).strip()
        title = (match.group(3) or "").strip()
        image_path = resolve_docx_media(source, media_dir, workdir)
        label = title or alt or Path(source).name
        coords = image_coords.get(title) or image_coords.get(Path(source).name)
        coordinate_text = _pptx_coords(coords, slide_width, slide_height)

        if not image_path.exists():
            note = f"<!-- image not extracted: {label} -->"
            text = ""
        elif not use_ocr:
            note = f"<!-- image omitted: {label} (OCR disabled) -->"
            text = ""
        else:
            counter += 1
            text = ocr_image_file(
                image_path, workdir, counter, cache
            )
            ocr_done()
            note = "" if text.strip() else f"<!-- image omitted: {label} (no readable text detected) -->"

        line_start = markdown.rfind("\n", 0, match.start()) + 1
        line_end = markdown.find("\n", match.end())
        line = markdown[line_start : line_end if line_end != -1 else len(markdown)]

        coords_suffix = ""
        if coordinate_text:
            coords_suffix = f"<br>**Position:** {coordinate_text}"
        if line.lstrip().startswith("|"):
            parts = []
            if text:
                parts.append(f"<br>**Image text (OCR) — {label}:** {text.replace('|', '\\|').replace(chr(10), '<br>')}")
            if note:
                parts.append(note)
            if coords_suffix:
                parts.append(coords_suffix)
            return "".join(parts)

        pieces: list[str] = []
        if text:
            pieces.append(ocr_blockquote(text, label))
        if note:
            pieces.append(note)
        if coordinate_text:
            pieces.append(f"- **Position:** {coordinate_text}")
        if not pieces:
            return f"<!-- image omitted: {label} -->"
        return "\n\n" + "\n\n".join(pieces) + "\n\n"

    return PPTX_IMAGE_RE.sub(repl, markdown)


def _append_pptx_slide_meta(chunk: str, meta: dict[str, object]) -> str:
    pieces = [chunk.rstrip()]
    notes = str(meta.get("notes", "")).strip()
    if notes:
        pieces.extend(["", "### Speaker notes", ""])
        pieces.extend(f"> {line}" if line.strip() else ">" for line in notes.splitlines())

    comments = meta.get("comments", [])
    if comments:
        pieces.extend(["", "### Comments", ""])
        for index, comment in enumerate(comments):
            if index:
                pieces.append("")
            pieces.append(f"> **{comment['author']} — {comment['date']}**")
            pieces.append(">")
            pieces.extend(
                f"> {line}" if line.strip() else ">"
                for line in comment["text"].splitlines()
            )
    return "\n".join(pieces).strip()


def _split_pptx_slides(markdown: str) -> tuple[str, list[str]]:
    matches = list(re.finditer(r"(?m)^## ", markdown))
    if not matches:
        return "", [markdown.strip()] if markdown.strip() else []
    preamble = markdown[: matches[0].start()].strip()
    chunks: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        chunks.append(markdown[match.start() : end].strip())
    return preamble, chunks


def convert_pptx(
    path: Path,
    out_path: Path,
    use_ocr: bool = True,
) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = _pptx_metadata(path)
    with tempfile.TemporaryDirectory(prefix="cp4ai_pptx_") as tmp:
        workdir = Path(tmp)
        pandoc = pandoc_binary()
        proc = subprocess.run(
            [
                pandoc,
                str(path.resolve()),
                "--from=pptx",
                "--to=gfm",
                "--wrap=none",
                "--markdown-headings=atx",
                "--extract-media=media",
            ],
            check=False,
            cwd=workdir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Pandoc failed for {path}:\n{proc.stderr}")

        media_dir = workdir / "media"
        ocr_cache: dict[tuple, str] = {}
        preamble, chunks = _split_pptx_slides(proc.stdout.strip())
        if preamble and chunks:
            chunks[0] = preamble + "\n\n" + chunks[0]
        elif preamble:
            chunks = [preamble]

        total = max(len(chunks), len(metadata))
        rendered: list[str] = []
        for index in range(total):
            chunk = chunks[index] if index < len(chunks) else ""
            meta = metadata[index] if index < len(metadata) else {}
            image_coords = meta.get("images", {}) if meta else {}
            chunk = _replace_pptx_images(
                chunk,
                media_dir,
                workdir,
                ocr_cache,
                use_ocr,
                image_coords,
                int(meta.get("width", 0)),
                int(meta.get("height", 0)),
            )
            chunk = _append_pptx_slide_meta(chunk, meta)
            body = f"<!-- slide {index + 1} of {total} -->"
            if chunk:
                body += f"\n\n{chunk}"
            rendered.append(body)
            page_done()

    notes_count = sum(1 for meta in metadata if str(meta.get("notes", "")).strip())
    comments_count = sum(len(meta.get("comments", [])) for meta in metadata)
    header = front_matter(
        path,
        "pptx",
        converter=f"pandoc {pandoc_version()}",
        slides=len(metadata) or total,
        notes=notes_count or None,
        comments=comments_count or None,
        images_ocr=ocr_engine_label(use_ocr),
    )
    write_markdown(out_path, f"{header}\n\n# {md_title(path)}\n\n" + "\n\n".join(rendered))
    return (
        f"pptx, {len(metadata) or total} slide(s), {notes_count} note(s), "
        f"{comments_count} comment(s)"
    )


# ---------------------------------------------------------------------------
# PDF conversion + OCR
# ---------------------------------------------------------------------------


def merge_boxes(boxes: Iterable[pymupdf.Rect], gap: float = 6.0) -> list[pymupdf.Rect]:
    """Merge neighbouring image fragments (e.g. a table split across images)."""
    pending = [pymupdf.Rect(box) for box in boxes]
    pending = [box for box in pending if not box.is_empty]
    while True:
        merged: list[pymupdf.Rect] = []
        changed = False
        for box in pending:
            for index, existing in enumerate(merged):
                expanded = pymupdf.Rect(
                    existing.x0 - gap,
                    existing.y0 - gap,
                    existing.x1 + gap,
                    existing.y1 + gap,
                )
                if expanded.intersects(box):
                    merged[index] = existing | box
                    changed = True
                    break
            else:
                merged.append(box)
        if not changed:
            return merged
        pending = merged


def page_image_boxes(
    page: pymupdf.Page,
) -> tuple[list[pymupdf.Rect], list[pymupdf.Rect], int]:
    page_area = page.rect.get_area()
    foreground: list[pymupdf.Rect] = []
    background: list[pymupdf.Rect] = []
    infos = page.get_image_info(xrefs=True)
    for info in infos:
        box = pymupdf.Rect(info["bbox"]) & page.rect
        if box.is_empty or box.width < 80 or box.height < 40:
            continue
        if box.get_area() < page_area * 0.008:
            continue
        if box.width / max(box.height, 1) > 20 or box.height / max(box.width, 1) > 20:
            continue
        if box.get_area() >= page_area * 0.85:
            background.append(box)
        else:
            foreground.append(box)
    return foreground, background, len(infos)


def count_pages(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with pymupdf.open(path) as doc:
            return doc.page_count
    if suffix == ".pptx":
        with zipfile.ZipFile(path) as zf:
            return len(_pptx_slide_parts(zf))
    if suffix == ".docx":
        return 1
    return 0


def _count_pdf_ocr_jobs(path: Path) -> int:
    jobs = 0
    with pymupdf.open(path) as doc:
        for page in doc:
            foreground, background, _ = page_image_boxes(page)
            jobs += len(merge_boxes(foreground))
            if background and len(token_set(page.get_text())) < 40:
                jobs += len(merge_boxes(background))
    return jobs


def _count_docx_ocr_jobs(path: Path) -> int:
    image_suffixes = {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp",
        ".emf", ".wmf", ".svg",
    }
    with zipfile.ZipFile(path) as zf:
        return sum(
            1
            for name in zf.namelist()
            if name.startswith("word/media/")
            and Path(name).suffix.lower() in image_suffixes
        )


def _count_pptx_ocr_jobs(path: Path) -> int:
    total = 0
    with zipfile.ZipFile(path) as zf:
        for part in _pptx_slide_parts(zf):
            root = ET.fromstring(zf.read(part))
            for picture in root.iter(P_NS + "pic"):
                blip = picture.find(f"{P_NS}blipFill/{A_NS}blip")
                if blip is not None and blip.get(R_NS + "embed"):
                    total += 1
    return total


def count_ocr_jobs(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _count_pdf_ocr_jobs(path)
    if suffix == ".docx":
        return _count_docx_ocr_jobs(path)
    if suffix == ".pptx":
        return _count_pptx_ocr_jobs(path)
    return 0


def ocr_page_images(
    page: pymupdf.Page,
    page_number: int,
    workdir: Path,
    cache: dict[tuple, str],
    native_text: str,
) -> str:
    foreground, background, info_count = page_image_boxes(page)
    logger.info("page %d: image_infos=%d", page_number, info_count)

    def run_boxes(boxes: list[pymupdf.Rect], offset: int) -> list[str]:
        results: list[str] = []
        for index, box in enumerate(merge_boxes(boxes)):
            pix = page.get_pixmap(clip=box, dpi=300, alpha=False)
            # Reuse OCR only for a bit-identical rendered region. Image xrefs
            # and box size do not determine the pixels (vector text overlays
            # and shared backgrounds can differ), so hash the pixels instead.
            digest = hashlib.sha256(pix.samples).digest()
            cache_key = (pix.width, pix.height, len(pix.samples), digest)
            if cache_key in cache:
                text = cache[cache_key]
            else:
                image_path = workdir / f"page_{page_number:04d}_{offset + index}.png"
                pix.save(image_path)
                text = ocr_image(image_path)
                cache[cache_key] = text
            ocr_done()
            if text and useful_ocr(text):
                results.append(text)
        return results

    logger.info(
        "page %d: foreground=%d background=%d native_tokens=%d",
        page_number,
        len(foreground),
        len(background),
        len(token_set(native_text)),
    )
    native_tokens = len(token_set(native_text))
    regions: list[str] = []
    if foreground:
        # The common case: a real screenshot/table on a page whose full-page
        # image is just a background scan. OCR the foreground only.
        regions = run_boxes(foreground, 0)
        # A nearly blank native text layer plus almost no foreground text means
        # the page may be a full-page scan; fall back to the whole page.
        if native_tokens < 40 and len(token_set("\n".join(regions))) < 20 and background:
            regions = run_boxes(background, 50)
    elif background and native_tokens < 40:
        # Scanned page with no separate foreground images.
        regions = run_boxes(background, 50)
    return "\n\n".join(regions)


def convert_pdf(
    path: Path,
    out_path: Path,
    use_ocr: bool = True,
) -> str:
    doc = pymupdf.open(path)
    try:
        chunks = doc.to_markdown(
            page_chunks=True, show_progress=False, use_ocr=False
        )
        parts: list[str] = []
        ocr_pages = 0
        cache: dict[tuple, str] = {}
        with tempfile.TemporaryDirectory(prefix="cp4ai_pdf_") as tmp:
            workdir = Path(tmp)
            for page_index, chunk in enumerate(chunks):
                page = doc[page_index]
                native = clean_text(chunk.get("text", ""))
                ocr_text = ""
                if use_ocr:
                    ocr_text = ocr_page_images(
                        page, page_index + 1, workdir, cache, native
                    )
                    if ocr_text and is_ordered_subsequence_after_stripping_whitespace(
                        ocr_text, native
                    ):
                        ocr_text = ""
                if ocr_text:
                    ocr_pages += 1

                page_body = native
                if ocr_text:
                    page_body += f"\n\n{OCR_COMMENT}\n\n{ocr_text}"

                parts.append(
                    f"<!-- page {page_index + 1} of {len(chunks)} -->\n\n{page_body.strip()}"
                )
                page_done()
    finally:
        doc.close()

    header = front_matter(
        path,
        "pdf",
        pages=len(chunks),
        text_extraction=f"pymupdf {pymupdf.__version__}",
        images_ocr=(
            f"{ocr_engine_label(use_ocr)} ({ocr_pages} page(s) with OCR text)"
            if use_ocr
            else "disabled"
        ),
    )
    write_markdown(out_path, f"{header}\n\n# {md_title(path)}\n\n" + "\n\n".join(parts))
    return f"pdf, {len(chunks)} pages, OCR on {ocr_pages} page(s)"
