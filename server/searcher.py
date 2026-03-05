"""
Searcher for VerilogA MCP Server — SQLite FTS5 edition.

Uses SQLite's built-in FTS5 extension with BM25 ranking.
Peak runtime memory: ~10–20 MB (SQLite page cache only).
No heavy ML dependencies required.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from indexer import (
    DB_FILE,
    REFERENCE_DIR,
    build_index,
)


class SQLiteSearcher:
    """Connects to the SQLite FTS5 index and executes BM25 queries."""

    def __init__(self) -> None:
        self._conn: Optional[sqlite3.Connection] = None

    def _ensure_connected(self) -> None:
        if self._conn is not None:
            return

        if not DB_FILE.exists() or DB_FILE.stat().st_size == 0:
            print("[searcher] Index not found, building now...")
            build_index(force=False)

        # Open read-only if possible, fall back to read-write
        try:
            self._conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            self._conn = sqlite3.connect(str(DB_FILE))

    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """Return top_k most relevant chunks using FTS5 BM25 ranking."""
        self._ensure_connected()

        query = query.strip()
        if not query:
            return []

        # Strip characters that break FTS5 syntax; keep alphanumeric, underscore, dot
        safe_query = re.sub(r'[^a-zA-Z0-9\s_\.\-]', ' ', query)
        tokens = safe_query.split()
        if not tokens:
            return []

        # OR-join quoted tokens for broad recall across all search terms
        fts_query = " OR ".join(f'"{t}"' for t in tokens)

        sql = """
            SELECT
                text,
                source,
                title,
                doc_type,
                description,
                bm25(chunks_fts) AS rank
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """

        try:
            rows = self._conn.execute(sql, (fts_query, top_k)).fetchall()
        except sqlite3.OperationalError as exc:
            print(f"[searcher] Query error ({exc}), retrying with plain tokens")
            # Fall back to a simpler query with individual unquoted tokens
            plain_query = " OR ".join(tokens)
            try:
                rows = self._conn.execute(sql, (plain_query, top_k)).fetchall()
            except sqlite3.OperationalError:
                return []

        results = []
        for text, source, title, doc_type, description, rank in rows:
            results.append({
                "text": text,
                "source": source,
                "title": title,
                "doc_type": doc_type,
                "description": description,
                "score": round(-rank, 4),  # FTS5 bm25() returns negative; invert for display
            })
        return results

    def get_full_document(self, source_file: str) -> Optional[str]:
        """Return the full extracted text of a document by its relative path."""
        target = REFERENCE_DIR / source_file
        if not target.exists():
            # Case-insensitive fallback search
            for candidate in REFERENCE_DIR.rglob("*"):
                if (candidate.is_file()
                        and candidate.name.lower() == Path(source_file).name.lower()):
                    target = candidate
                    break
            else:
                return None

        suffix = target.suffix.lower()
        if suffix == ".pdf":
            return _read_pdf_text(target)
        elif suffix in (".html", ".htm"):
            return _read_html_text(target)
        else:
            try:
                return target.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return None

    def list_sources(self) -> List[dict]:
        """Return metadata for every indexed document (deduplicated by source path)."""
        self._ensure_connected()
        rows = self._conn.execute(
            "SELECT DISTINCT source, doc_type, description FROM chunks_fts ORDER BY source"
        ).fetchall()
        return [
            {"source": src, "doc_type": dtype, "description": desc}
            for src, dtype, desc in rows
        ]


# ---------------------------------------------------------------------------
# Full-document text helpers
# ---------------------------------------------------------------------------

def _read_pdf_text(path: Path) -> str:
    """Extract full text from a PDF using pymupdf."""
    import fitz  # pymupdf

    pages = []
    try:
        doc = fitz.open(str(path))
        for i in range(doc.page_count):
            try:
                text = doc[i].get_text() or ""
            except Exception:
                continue
            text = text.strip()
            if text:
                pages.append(f"--- Page {i + 1} ---\n{text}")
        doc.close()
    except Exception as exc:
        return f"[Error reading PDF: {exc}]"
    return "\n\n".join(pages)


def _read_html_text(path: Path) -> str:
    from bs4 import BeautifulSoup

    with open(path, encoding="utf-8", errors="ignore") as fh:
        soup = BeautifulSoup(fh, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "noscript", "link", "meta"]):
        tag.decompose()
    body = (
        soup.find("div", class_="body-container")
        or soup.find("div", id="mc-main-content")
        or soup.find("body")
    )
    if body is None:
        return soup.get_text(separator="\n", strip=True)
    text = body.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_searcher: Optional[SQLiteSearcher] = None


def get_searcher() -> SQLiteSearcher:
    global _searcher
    if _searcher is None:
        _searcher = SQLiteSearcher()
    return _searcher
