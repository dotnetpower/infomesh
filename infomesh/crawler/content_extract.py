"""Code block and table extraction from HTML.

Features:
- #17: Code block extraction with language tagging
- #18: HTML table → structured data conversion
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── #17: Code block extraction ─────────────────────────────────────

_CODE_BLOCK_RE = re.compile(
    r"<pre[^>]*>\s*<code[^>]*(?:class=[\"']"
    r"(?:language-|lang-|highlight-)?([\w+#.-]+)[\"'][^>]*)?>"
    r"(.*?)</code>\s*</pre>",
    re.DOTALL | re.IGNORECASE,
)

_INLINE_CODE_RE = re.compile(
    r"<code[^>]*>(.*?)</code>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class CodeBlock:
    """An extracted code block with optional language tag."""

    code: str
    language: str = ""
    line_count: int = 0


def extract_code_blocks(
    html: str,
    *,
    min_lines: int = 2,
    max_blocks: int = 50,
) -> list[CodeBlock]:
    """Extract ``<pre><code>`` blocks from HTML.

    Args:
        html: Raw HTML string.
        min_lines: Minimum lines for a code block.
        max_blocks: Maximum blocks to extract.

    Returns:
        List of CodeBlock objects.
    """
    blocks: list[CodeBlock] = []

    for m in _CODE_BLOCK_RE.finditer(html):
        if len(blocks) >= max_blocks:
            break
        lang = m.group(1) or ""
        raw = _clean_html(m.group(2))
        lines = raw.count("\n") + 1
        if lines >= min_lines:
            blocks.append(
                CodeBlock(
                    code=raw,
                    language=lang.lower(),
                    line_count=lines,
                )
            )

    return blocks


# ── #18: HTML table extraction ─────────────────────────────────────


_TABLE_RE = re.compile(
    r"<table[^>]*>(.*?)</table>",
    re.DOTALL | re.IGNORECASE,
)

_TR_RE = re.compile(
    r"<tr[^>]*>(.*?)</tr>",
    re.DOTALL | re.IGNORECASE,
)

_TH_TD_RE = re.compile(
    r"<(?:th|td)[^>]*>(.*?)</(?:th|td)>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class ExtractedTable:
    """An extracted HTML table as structured data."""

    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    caption: str = ""

    def to_csv(self) -> str:
        """Convert to CSV string."""
        lines: list[str] = []
        if self.headers:
            lines.append(",".join(f'"{h}"' for h in self.headers))
        for row in self.rows:
            lines.append(",".join(f'"{c}"' for c in row))
        return "\n".join(lines)

    def to_dict_list(self) -> list[dict[str, str]]:
        """Convert to list of dicts (using headers as keys)."""
        if not self.headers:
            return []
        result: list[dict[str, str]] = []
        for row in self.rows:
            d: dict[str, str] = {}
            for i, h in enumerate(self.headers):
                d[h] = row[i] if i < len(row) else ""
            result.append(d)
        return result


def extract_tables(
    html: str,
    *,
    max_tables: int = 20,
    min_rows: int = 1,
) -> list[ExtractedTable]:
    """Extract HTML tables into structured data.

    Args:
        html: Raw HTML string.
        max_tables: Maximum tables to extract.
        min_rows: Minimum data rows for a table.

    Returns:
        List of ExtractedTable objects.
    """
    tables: list[ExtractedTable] = []

    for tm in _TABLE_RE.finditer(html):
        if len(tables) >= max_tables:
            break
        table_html = tm.group(1)

        # Extract caption
        caption = ""
        cap_m = re.search(
            r"<caption[^>]*>(.*?)</caption>",
            table_html,
            re.DOTALL | re.IGNORECASE,
        )
        if cap_m:
            caption = _clean_html(cap_m.group(1)).strip()

        rows_data: list[list[str]] = []
        headers: list[str] = []

        for tr_m in _TR_RE.finditer(table_html):
            tr_html = tr_m.group(1)
            cells = [
                _clean_html(c.group(1)).strip() for c in _TH_TD_RE.finditer(tr_html)
            ]
            if not cells:
                continue

            # First row with <th> is headers
            if not headers and "<th" in tr_html.lower():
                headers = cells
            else:
                rows_data.append(cells)

        if len(rows_data) >= min_rows:
            tables.append(
                ExtractedTable(
                    headers=headers,
                    rows=rows_data,
                    caption=caption,
                )
            )

    return tables


# ── Shared helpers ─────────────────────────────────────────────────


def _clean_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&amp;", "&")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")
    return text
