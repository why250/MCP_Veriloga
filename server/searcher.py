"""
Searcher for VerilogA MCP Server (SQLite FTS5 version).

Uses SQLite's FTS5 extension to perform full-text search with BM25 ranking.
Extremely lightweight, no heavy ML dependencies.
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
    """Connects to the SQLite FTS5 index and executes queries."""

    def __init__(self) -> None:
        self._conn: Optional[sqlite3.Connection] = None

    def _ensure_connected(self) -> None:
        if self._conn is not None:
            return

        if not DB_FILE.exists():
            print("[searcher] Index not found, building now...")
            build_index(force=False)

        # Connect in read-only mode if possible, or standard mode
        try:
            self._conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            self._conn = sqlite3.connect(str(DB_FILE))
        
        # Enable row factory for dict-like access if needed, but tuple is fine for speed

    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """
        Return top_k most relevant chunks using FTS5 BM25 ranking.
        """
        self._ensure_connected()
        
        query = query.strip()
        if not query:
            return []

        # Sanitize query for FTS5 syntax
        # We treat the whole string as a phrase or set of tokens
        # Simple approach: escape double quotes and wrap in quotes for phrase search 
        # OR just split by space and AND them.
        # Let's try standard MATCH with simple sanitization.
        
        # Remove characters that might interfere with FTS5 syntax
        safe_query = re.sub(r'[^a-zA-Z0-9\s_\.]', ' ', query)
        tokens = safe_query.split()
        if not tokens:
            return []
            
        # Construct a query that looks for ALL tokens (AND logic)
        # fts5 string: "token1" AND "token2" ...
        # But for better recall, OR logic might be better, or just standard "token1 token2"
        # SQLite FTS5 default is that tokens are implicitly ANDed if just separated by space? 
        # No, default is usually phrase or NEAR. 
        # Actually, standard FTS5 syntax: 
        #   "foo bar" -> phrase
        #   foo bar -> implicit AND in recent versions? Let's verify.
        #   Actually, let's use the OR operator for broader recall, or just space.
        #   Let's stick to simple token matching.
        
        fts_query = " OR ".join(f'"{t}"' for t in tokens)
        
        # Execute query
        # bm25(chunks_fts) returns the score. Lower is better? No, FTS5 bm25() returns 
        # a negative value where more negative is better (magnitude is score).
        # Wait, documentation says: "The value returned by bm25() is a real number... 
        # The lower the value (more negative), the better the match."
        
        sql = """
            SELECT 
                text, 
                source, 
                title, 
                doc_type, 
                description, 
                bm25(chunks_fts) as rank
            FROM chunks_fts 
            WHERE chunks_fts MATCH ? 
            ORDER BY rank 
            LIMIT ?
        """
        
        try:
            cursor = self._conn.execute(sql, (fts_query, top_k))
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            # Fallback for syntax errors
            print(f"[searcher] Query error: {e}")
            return []

        results = []
        for row in rows:
            text, source, title, doc_type, description, rank = row
            # Convert rank to a positive score for display (just invert sign for intuition)
            score = -1.0 * rank
            
            results.append({
                "text": text,
                "source": source,
                "title": title,
                "doc_type": doc_type,
                "description": description,
                "score": round(score, 4),
            })
            
        return results

    def get_full_document(self, source_file: str) -> Optional[str]:
        """Return full text of a document by its relative path (from reference/)."""
        target = REFERENCE_DIR / source_file
        if not target.exists():
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
        """Return metadata for every indexed document (deduplicated by source)."""
        self._ensure_connected()
        
        # We can query the DB for distinct sources
        sql = "SELECT DISTINCT source, doc_type, description FROM chunks_fts ORDER BY source"
        cursor = self._conn.execute(sql)
        
        sources = []
        for row in cursor:
            src, dtype, desc = row
            sources.append({
                "source": src,
                "doc_type": dtype,
                "description": desc,
            })
        return sources


# ---------------------------------------------------------------------------
# Full-document text helpers
# ---------------------------------------------------------------------------

def _read_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i + 1} ---\n{text.strip()}")
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
