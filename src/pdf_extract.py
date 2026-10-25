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
                pages.append(page.get_text("text", sort=False))
                images += len(page.get_images(full=False))
            except Exception:
                raise PdfExtractionError("A page of the PDF is damaged and could not be "
                                         "read. Export it again and re-upload.") from None
    finally:
        doc.close()
    text = clean_text("\n\n".join(pages))
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

