"""Text extraction from an uploaded Gemini conversation PDF (in memory, never stored).

Uses PyMuPDF, which handles Devanagari (Hindi) shaping, emojis and line breaks better than
pure-Python PDF readers. Scanned/screenshot PDFs have no text layer: they are reported as
needing OCR instead of being guessed at.
"""

from __future__ import annotations

import re
import unicodedata

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PAGES = 500

OCR_HINT = ("This PDF has no selectable text (it looks like scanned pages or screenshots), so "
            "OCR would be required. In Gemini, export or print the conversation as a PDF "
            "(Share → Export / Print → Save as PDF) so the text stays selectable, or paste "
            "the response text instead.")


class PdfExtractionError(Exception):
    """User-facing problem with the uploaded PDF."""


def extract_pdf_text(raw: bytes) -> str:
    """Return the PDF's text (pages joined with blank lines). Raises PdfExtractionError."""
    if not raw:
        raise PdfExtractionError("The uploaded PDF is empty (0 bytes).")
    if len(raw) > MAX_PDF_BYTES:
        raise PdfExtractionError(
            f"The PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB.")
    if not raw.lstrip()[:5].startswith(b"%PDF"):
        raise PdfExtractionError("This file is not a PDF. Upload the Gemini conversation "
                                 "saved as .pdf.")
    try:
        import pymupdf
    except ImportError:  # older PyMuPDF releases
        import fitz as pymupdf  # type: ignore
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception:
        raise PdfExtractionError("The PDF could not be opened – it may be corrupted or "
                                 "incomplete. Export it again and re-upload.") from None
    try:
        if doc.needs_pass:
            raise PdfExtractionError("The PDF is password-protected. Remove the password "
                                     "and upload it again.")
        if doc.page_count == 0:
            raise PdfExtractionError("The PDF has no pages.")
        if doc.page_count > MAX_PAGES:
            raise PdfExtractionError(f"The PDF has more than {MAX_PAGES} pages.")
        pages, images = [], 0
        for page in doc:
            try:
                pages.append(page.get_text("text", sort=True))
                images += len(page.get_images(full=False))
            except Exception:
                raise PdfExtractionError("A page of the PDF is damaged and could not be "
                                         "read. Export it again and re-upload.") from None
    finally:
        doc.close()
    text = normalize_pdf_text("\n".join(pages))
    if len(re.sub(r"\s", "", text)) < 20:
        if images:
            raise PdfExtractionError(OCR_HINT)
        raise PdfExtractionError("No text was found in the PDF.")
    return text


def clean_text(text: str) -> str:
    """Normalise PDF artefacts without changing the wording."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "").replace("\ufeff", "").replace("\x0c", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ------------------------------------------------------------------ layout normalisation
# Zero-width / invisible characters PDFs insert. ZWJ/ZWNJ (U+200C/U+200D) are kept: Hindi
# conjuncts and emoji sequences need them.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u2060\u00ad\ufeff\u200e\u200f"), None)
_SPACES = dict.fromkeys(map(ord, "\u00a0\u2007\u202f\u2009\u200a\u3000\t"), " ")

_PLATFORM_HEADING = r"(?:YOU\s?TUBE\s+SHORTS?|INSTAGRAM\s+REELS?|FACEBOOK\s+REELS?)"
_KNOWN_LABEL = (
    r"(?:(?:Instagram|Facebook|YouTube|Reels?|IG|FB)\s+)?"
    r"(?:Caption|Hashtags?|Description|Hook|SEO\s+Title|Title|Reel\s+Cover\s+(?:Title|Text)|"
    r"Primary\s+SEO\s+Keyword|Secondary\s+SEO\s+Keywords|Search\s+Phrases|Share\s+Trigger|"
    r"Save\s+Trigger|Comment\s+Trigger|On-Screen\s+Text|Optimized\s+Transcript|"
    r"Content\s+Category|YouTube\s+Tags|Search-Intent\s+Questions|Shorts\s+Description)"
)
# A platform heading in the middle of a line ("Response: 🟣 INSTAGRAM REELS 1. Hook").
# Upper-case headings always split; mixed case only when a field label/number follows,
# so prose such as "post it on Instagram reels" is left alone.
_INLINE_HEADING_UPPER = re.compile(
    r"[ \t]*(?P<h>(?:[^\w\s#]{1,3}[ \t]*)?(?-i:YOUTUBE\s+SHORTS|INSTAGRAM\s+REELS|"
    r"FACEBOOK\s+REELS))[ \t]*")
_INLINE_HEADING_LABEL = re.compile(
    r"[ \t]*(?P<h>(?:[^\w\s#]{1,3}[ \t]*)?" + _PLATFORM_HEADING + r")[ \t]*:?[ \t]*"
    r"(?=\d{1,2}[.)]\s|" + _KNOWN_LABEL + r"\b)", re.I)
# A known field label after other text on the same line ("…text. Hashtags: #a #b").
_INLINE_LABEL = re.compile(
    r"(?<=\S)[ \t]+(?=(?:\d{1,2}[.)][ \t]*)?" + _KNOWN_LABEL + r"[ \t]*[:：])", re.I)
# "6. Hashtags #a #b" / "5. Instagram Caption क्या…" (numbered label, value on same line)
_NUMBERED_LABEL_VALUE = re.compile(
    r"^(?P<l>[ \t]*\d{1,2}[.)][ \t]*" + _KNOWN_LABEL + r")[ \t]+(?P<v>[^:：\s].*)$", re.I)
# Print artefacts repeated on every page.
_ARTEFACT_LINE = re.compile(
    r"^\s*(?:(?:page\s*)?\d{1,4}\s*(?:/|of)\s*\d{1,4}|\d{1,4}|"
    r"https?://\S+|(?:gemini\.google\.com|share\.gemini\.google)\S*|"
    r"\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?:\s*[AP]M)?(?:\s+\S.*)?|"
    r"Gemini(?:\s*-\s*.*)?|Google Gemini)\s*$", re.I)


def _fold_styled(text: str) -> str:
    """Fold 'mathematical bold' letters, ligatures and full-width punctuation to plain
    characters (NFKC per character). Hindi and emojis are left untouched."""
    out = []
    for ch in text:
        o = ord(ch)
        if 0x1D400 <= o <= 0x1D7FF or 0xFB00 <= o <= 0xFB06 or 0xFF01 <= o <= 0xFF5E:
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


def _split_mixed_heading(line: str) -> str:
    """'Response: Instagram Reels Caption: …' -> heading on its own line. A heading at the
    start of a line or after just a number ('5. Facebook Reel Description') is a field label
    and is left for the parser."""
    def repl(m: re.Match) -> str:
        before = line[:m.start()].strip()
        if not before or re.fullmatch(r"\d{1,2}[.)]?", before):
            return m.group(0)
        return "\n" + m.group("h") + "\n"
    return _INLINE_HEADING_LABEL.sub(repl, line)


def normalize_pdf_text(text: str) -> str:
    """Turn PDF text into the line structure the Gemini parser expects.

    - removes invisible characters, normalises odd spaces and styled letters (NFC kept)
    - drops repeated page artefacts (page numbers, print URLs/timestamps) so a response that
      continues on the next page is joined seamlessly
    - puts platform headings and field labels that share a line with other text on their own
      line, so headings next to 'Response:' / 'Hook' are detected
    """
    text = clean_text(_fold_styled(text).translate(_INVISIBLE).translate(_SPACES))
    lines: list[str] = []
    for line in text.split("\n"):
        if _ARTEFACT_LINE.match(line):
            continue
        if line.strip() in (":", "："):  # label and colon split into separate spans
            if lines:
                lines[-1] = lines[-1].rstrip() + ":"
            continue
        line = _INLINE_HEADING_UPPER.sub(lambda m: "\n" + m.group("h") + "\n", line)
        line = _INLINE_HEADING_LABEL.sub(lambda m: "\n" + m.group("h") + "\n", line)
        line = _INLINE_LABEL.sub("\n", line)
        for part in line.split("\n"):
            m = _NUMBERED_LABEL_VALUE.match(part)
            if m:
                part = f"{m.group('l')}: {m.group('v')}"
            lines.append(part.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


