"""PDF content extraction for crawled PDF documents.

Feature #12: Extract text from PDF URLs using PyMuPDF (fitz).
Falls back gracefully when PyMuPDF is not installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PDFContent:
    """Extracted content from a PDF document."""

    text: str
    title: str
    page_count: int
    metadata: dict[str, str]


def is_pdf_url(url: str) -> bool:
    """Check if URL likely points to a PDF."""
    lower = url.lower().rstrip("/")
    return lower.endswith(".pdf") or "application/pdf" in lower


def extract_pdf_text(data: bytes, *, max_pages: int = 50) -> PDFContent | None:
    """Extract text from PDF binary data.

    Args:
        data: Raw PDF bytes.
        max_pages: Maximum pages to extract.

    Returns:
        PDFContent or None if extraction fails.
    """
    try:
        import fitz  # PyMuPDF  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("pymupdf_not_installed")
        return None

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        pages = min(doc.page_count, max_pages)
        parts: list[str] = []
        for i in range(pages):
            page = doc[i]
            parts.append(page.get_text())

        text = "\n\n".join(parts).strip()
        if not text:
            return None

        meta = doc.metadata or {}
        title = meta.get("title", "") or ""
        doc.close()

        return PDFContent(
            text=text,
            title=title,
            page_count=pages,
            metadata={k: str(v) for k, v in meta.items() if v},
        )
    except Exception:  # noqa: BLE001
        logger.debug("pdf_extraction_failed")
        return None
