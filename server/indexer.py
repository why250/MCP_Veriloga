"""
Document indexer for VerilogA MCP Server — SQLite FTS5 edition.

Parses all PDFs and HTML files under reference/, splits them into chunks,
and builds a single SQLite database with an FTS5 virtual table for BM25
full-text search.  No heavy ML dependencies required.

Everything is saved to index_cache/search.db; subsequent starts load
directly from this file (~3 s vs ~30–90 s for a fresh build).
"""

from __future__ import annotations

import gc
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List

INDEX_CACHE_DIR = Path(__file__).parent / "index_cache"
DB_FILE         = INDEX_CACHE_DIR / "search.db"

CHUNK_SIZE      = 500    # approximate characters per chunk
CHUNK_OVERLAP   = 80     # character overlap between adjacent chunks
MAX_PAGE_CHARS  = 15_000 # max characters kept per PDF page

MEM_LIMIT_PCT   = 80     # pause indexing when system RAM use exceeds this %
MEM_POLL_SEC    = 5      # seconds to wait between memory re-checks

REFERENCE_DIR = Path(__file__).parent.parent / "reference"


# ---------------------------------------------------------------------------
# Memory guard
# ---------------------------------------------------------------------------

def _wait_for_memory(label: str = "") -> None:
    """
    Block until system RAM usage drops below MEM_LIMIT_PCT (default 80 %).
    Calls gc.collect() on each iteration to release Python-held memory.
    Prints a single warning line and then a completion line when unblocked.
    psutil is optional: if unavailable the function is a no-op.
    """
    try:
        import psutil
    except ImportError:
        return

    mem = psutil.virtual_memory()
    if mem.percent < MEM_LIMIT_PCT:
        return  # fast path — nothing to do

    tag = f" [{label}]" if label else ""
    print(
        f"\n[memory]{tag} RAM usage {mem.percent:.1f}% > {MEM_LIMIT_PCT}% limit "
        f"({mem.used / 1e9:.1f}/{mem.total / 1e9:.1f} GB). Waiting...",
        flush=True,
    )
    while True:
        gc.collect()
        time.sleep(MEM_POLL_SEC)
        mem = psutil.virtual_memory()
        if mem.percent < MEM_LIMIT_PCT:
            print(
                f"[memory]{tag} RAM back to {mem.percent:.1f}%, resuming.",
                flush=True,
            )
            return
        print(
            f"[memory]{tag} still {mem.percent:.1f}% — waiting {MEM_POLL_SEC}s...",
            flush=True,
        )


DOC_DESCRIPTIONS = {
    "OVI_VerilogA.pdf":
        "OVI Verilog-A Language Reference Manual — formal language specification",
    "VerilogA Modeling.pdf":
        "Verilog-A Modeling Guide — practical modeling tutorials and examples",
    "veriaref.pdf":
        "Condensed Verilog-A Quick Reference",
    "About_Model_Development_in_Verilog-A.html":
        "ADS 2025 — About Model Development in Verilog-A",
    "Condensed_Reference.html":
        "ADS 2025 — Condensed Verilog-A Reference",
    "Getting_Started_with_Verilog-A_in_the_Advanced_Design_System.html":
        "ADS 2025 — Getting Started with Verilog-A",
    "Migrating_from_the_SDD_and_UCM.html":
        "ADS 2025 — Migrating from SDD and UCM to Verilog-A",
    "Using_Verilog-A_in_Advanced_Design_System.html":
        "ADS 2025 — Using Verilog-A in ADS",
    "Using_Verilog-A_with_the_ADS_Analog_RF_Simulator_(ADSsim).html":
        "ADS 2025 — Verilog-A with ADSsim Analog/RF Simulator",
    "Verilog-A_in_ADS_Design_Kits.html":
        "ADS 2025 — Verilog-A in ADS Design Kits",
}


@dataclass
class Chunk:
    text: str
    source: str       # relative path from REFERENCE_DIR
    title: str        # section/page title
    doc_type: str     # "pdf" or "html"
    description: str  # human-readable doc description
    chunk_idx: int    # index within the document


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_pdf(pdf_path: Path) -> List[tuple[str, str]]:
    """Return list of (page_title, page_text) using pymupdf (fitz)."""
    import fitz  # pymupdf

    pages = []
    doc = None
    try:
        doc = fitz.open(str(pdf_path))
        n = doc.page_count
        for i in range(n):
            print(f"    page {i + 1}/{n}", end="\r", flush=True)
            # Check memory every 20 pages and wait if system RAM > 80 %
            if i % 20 == 0:
                _wait_for_memory(f"{pdf_path.name} p{i + 1}")
            try:
                raw = doc[i].get_text() or ""
            except MemoryError:
                print(f"\n    WARNING: MemoryError on page {i + 1}/{n}, skipping")
                gc.collect()
                continue
            except Exception as exc:
                print(f"\n    WARNING: page {i + 1}/{n} failed: {exc}")
                continue
            if len(raw) > MAX_PAGE_CHARS:
                raw = raw[:MAX_PAGE_CHARS]
            text = _clean_text(raw)
            del raw
            if text.strip():
                pages.append((f"Page {i + 1}", text))
            if i % 50 == 49:
                gc.collect()
        print()  # end the \r progress line
    except Exception as exc:
        print(f"\n    WARNING: could not open {pdf_path.name}: {exc}")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
        gc.collect()
    return pages


def _parse_html(html_path: Path) -> List[tuple[str, str]]:
    """Return list of (section_title, section_text) from an HTML file."""
    from bs4 import BeautifulSoup

    with open(html_path, encoding="utf-8", errors="ignore") as fh:
        soup = BeautifulSoup(fh, "lxml")

    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else html_path.stem

    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "noscript", "link", "meta"]):
        tag.decompose()

    body = (
        soup.find("div", class_="body-container")
        or soup.find("div", id="mc-main-content")
        or soup.find("div", class_=re.compile(r"body|content|main", re.I))
        or soup.find("body")
    )

    if body is None:
        return []

    full_text = _clean_text(body.get_text(separator="\n", strip=True))
    return [(page_title, full_text)] if full_text.strip() else []


def _clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _split_text(text: str, size: int = CHUNK_SIZE,
                overlap: int = CHUNK_OVERLAP) -> List[str]:
    if len(text) <= size:
        return [text]

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):
                idx = text.rfind(sep, start + size // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break
        chunks.append(text[start:end].strip())
        start = end - overlap
        if start >= len(text):
            break
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_index(force: bool = False) -> None:
    """
    Parse documents, build SQLite FTS5 index, save to index_cache/search.db.
    If the DB already exists and force=False, skip building.
    """
    INDEX_CACHE_DIR.mkdir(exist_ok=True)

    if not force and DB_FILE.exists() and DB_FILE.stat().st_size > 0:
        print("[indexer] Index already exists, skipping build.")
        return

    print("[indexer] Building FTS5 index from documents...")
    chunks = _collect_chunks()
    if not chunks:
        raise RuntimeError(
            "No chunks extracted — check that reference/ contains PDF/HTML files."
        )

    _build_fts5(chunks)
    print(f"[indexer] Done. {len(chunks)} chunks indexed → {DB_FILE}")


def _collect_chunks() -> List[Chunk]:
    chunks: List[Chunk] = []

    for pdf_path in sorted(REFERENCE_DIR.glob("*.pdf")):
        description = DOC_DESCRIPTIONS.get(pdf_path.name, pdf_path.name)
        rel_path = str(pdf_path.relative_to(REFERENCE_DIR))
        _wait_for_memory(pdf_path.name)
        print(f"  [pdf] {pdf_path.name}")
        try:
            sections = _parse_pdf(pdf_path)
        except MemoryError:
            print(f"    WARNING: MemoryError parsing {pdf_path.name}, skipping")
            gc.collect()
            continue
        except Exception as exc:
            print(f"    WARNING: {exc}")
            continue
        for title, text in sections:
            try:
                page_chunks = _split_text(text)
            except MemoryError:
                print(f"    WARNING: MemoryError splitting '{title}', skipping")
                gc.collect()
                continue
            for idx, chunk_text in enumerate(page_chunks):
                chunks.append(Chunk(
                    text=chunk_text, source=rel_path, title=title,
                    doc_type="pdf", description=description, chunk_idx=idx,
                ))
        del sections
        gc.collect()

    html_dir = REFERENCE_DIR / "veriloga in ADS2025" / "veriloga"
    for html_path in sorted(html_dir.glob("*.html")):
        description = DOC_DESCRIPTIONS.get(html_path.name, html_path.name)
        rel_path = str(html_path.relative_to(REFERENCE_DIR))
        _wait_for_memory(html_path.name)
        print(f"  [html] {html_path.name}")
        try:
            sections = _parse_html(html_path)
        except MemoryError:
            print(f"    WARNING: MemoryError parsing {html_path.name}, skipping")
            gc.collect()
            continue
        except Exception as exc:
            print(f"    WARNING: {exc}")
            continue
        for title, text in sections:
            try:
                page_chunks = _split_text(text)
            except MemoryError:
                print(f"    WARNING: MemoryError splitting '{html_path.name}', skipping")
                gc.collect()
                continue
            for idx, chunk_text in enumerate(page_chunks):
                chunks.append(Chunk(
                    text=chunk_text, source=rel_path, title=title,
                    doc_type="html", description=description, chunk_idx=idx,
                ))
        del sections
        gc.collect()

    return chunks


# ---------------------------------------------------------------------------
# SQLite FTS5 index
# ---------------------------------------------------------------------------

def _build_fts5(chunks: List[Chunk]) -> None:
    """Write all chunks into a SQLite FTS5 virtual table for BM25 search."""
    _wait_for_memory("fts5-build")
    print(f"[indexer] Writing {len(chunks)} chunks to SQLite FTS5...")

    # Remove any stale DB first so the CREATE is always clean
    if DB_FILE.exists():
        DB_FILE.unlink()

    conn = sqlite3.connect(str(DB_FILE))
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_idx   UNINDEXED,
                source      UNINDEXED,
                title       UNINDEXED,
                doc_type    UNINDEXED,
                description UNINDEXED,
                text,
                tokenize = 'porter ascii'
            )
        """)
        conn.executemany(
            "INSERT INTO chunks_fts VALUES (?,?,?,?,?,?)",
            [
                (c.chunk_idx, c.source, c.title,
                 c.doc_type, c.description, c.text)
                for c in chunks
            ],
        )
        conn.commit()
        print("[indexer] FTS5 index saved.")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    build_index(force=True)
