"""
Document indexer for VerilogA MCP Server (SQLite FTS5 version).

Parses all PDFs and HTML files under reference/, splits them into chunks,
and builds a SQLite FTS5 index for full-text search.

Everything is saved to index_cache/search.db.
"""

from __future__ import annotations

import concurrent.futures
import gc
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

INDEX_CACHE_DIR = Path(__file__).parent / "index_cache"
DB_FILE        = INDEX_CACHE_DIR / "search.db"

CHUNK_SIZE         = 500    # approximate characters per chunk
CHUNK_OVERLAP      = 80     # character overlap between adjacent chunks
MAX_PAGE_CHARS     = 15_000 # max characters extracted per PDF page (~30 printed pages worth)
_PDF_PAGE_TIMEOUT  = 20     # seconds before a single page extraction is abandoned

REFERENCE_DIR = Path(__file__).parent.parent / "reference"

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


@dataclass(slots=True)
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

def _get_page_text(page) -> str:
    """Extract text from a single pymupdf page with a hard timeout."""
    import fitz  # pymupdf

    flags = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(page.get_text, "text", flags=flags)
        try:
            return future.result(timeout=_PDF_PAGE_TIMEOUT) or ""
        except concurrent.futures.TimeoutError:
            return ""


def _parse_pdf(pdf_path: Path, max_pages: int = 0) -> List[tuple[str, str]]:
    """Return list of (page_title, page_text) from a PDF."""
    import fitz  # pymupdf

    pages = []
    doc = None
    try:
        doc = fitz.open(str(pdf_path))
        n = doc.page_count
        limit = min(n, max_pages) if max_pages > 0 else n
        for i in range(limit):
            print(f"    page {i+1}/{n}", end="\r", flush=True)
            try:
                raw = _get_page_text(doc[i])
            except MemoryError:
                print(f"\n    WARNING: MemoryError on page {i+1}/{n}, skipping")
                gc.collect()
                continue
            except Exception as exc:
                print(f"\n    WARNING: page {i+1}/{n} failed: {exc}")
                continue
            if not raw:
                print(f"\n    WARNING: page {i+1}/{n} timed out or empty, skipping")
                continue
            if len(raw) > MAX_PAGE_CHARS:
                raw = raw[:MAX_PAGE_CHARS]
            text = _clean_text(raw)
            del raw
            if text.strip():
                pages.append((f"Page {i+1}", text))
            if i % 50 == 49:
                gc.collect()
        print()  # end the \r progress line
    except Exception as exc:
        print(f"    WARNING: could not open {pdf_path.name}: {exc}")
    finally:
        if doc is not None:
            doc.close()
        gc.collect()
    return pages


MAX_HTML_READ_BYTES = 200_000   # max bytes read from each HTML file
MAX_HTML_TEXT_CHARS = 60_000    # max extracted plain-text chars per HTML file


class _HTMLTextExtractor(object):
    """Streaming HTML → plain text using Python's built-in html.parser."""
    from html.parser import HTMLParser as _HTMLParser

    _SKIP_TAGS  = frozenset({"script", "style", "nav", "header", "footer",
                              "noscript", "template"})
    _BLOCK_TAGS = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "h5",
                              "h6", "tr", "td", "th", "section", "article",
                              "br", "hr", "pre", "blockquote"})

    class _Parser(_HTMLParser):
        def __init__(self, skip_tags, block_tags):
            super().__init__(convert_charrefs=True)
            self._skip_tags  = skip_tags
            self._block_tags = block_tags
            self._skip_depth = 0
            self._in_title   = False
            self.title: str  = ""
            self._parts: List[str] = []

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag in self._skip_tags:
                self._skip_depth += 1
            elif tag == "title":
                self._in_title = True
            elif tag in self._block_tags:
                self._parts.append("\n")

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in self._skip_tags:
                self._skip_depth = max(0, self._skip_depth - 1)
            elif tag == "title":
                self._in_title = False
            elif tag in self._block_tags:
                self._parts.append("\n")

        def handle_data(self, data):
            if self._in_title:
                self.title += data
            elif self._skip_depth == 0:
                self._parts.append(data)

    @classmethod
    def extract(cls, html_str: str):
        """Return (title, plain_text) from an HTML string."""
        parser = cls._Parser(cls._SKIP_TAGS, cls._BLOCK_TAGS)
        try:
            parser.feed(html_str)
            parser.close()
        except Exception:
            pass  # best-effort: return whatever was collected
        return parser.title.strip() or "", "".join(parser._parts)


def _parse_html(html_path: Path) -> List[tuple[str, str]]:
    """Return list of (section_title, section_text) from an HTML file."""
    with open(html_path, encoding="utf-8", errors="ignore") as fh:
        raw = fh.read(MAX_HTML_READ_BYTES)

    title, text = _HTMLTextExtractor.extract(raw)
    del raw

    page_title = title if title else html_path.stem

    if len(text) > MAX_HTML_TEXT_CHARS:
        text = text[:MAX_HTML_TEXT_CHARS]

    full_text = _clean_text(text)
    del text
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

def build_index(force: bool = False, low_memory: bool = False,
                max_pages: int = 0) -> None:
    """
    Parse documents and build SQLite FTS5 index.
    
    Args:
        force: Rebuild index even if it exists.
        low_memory: (Ignored for SQLite implementation, kept for CLI compatibility)
        max_pages: Only index first N pages per PDF (for testing).
    """
    INDEX_CACHE_DIR.mkdir(exist_ok=True)

    if not force and DB_FILE.exists():
        print("[indexer] Index already exists (use --build-index to force rebuild).")
        return

    mode_tag = ""
    if max_pages > 0:
        mode_tag += f" [test mode: first {max_pages} page(s) per PDF]"
    print(f"[indexer] Building SQLite index from documents...{mode_tag}")
    
    chunks = _collect_chunks(max_pages=max_pages)
    if not chunks:
        raise RuntimeError("No chunks extracted — check that reference/ contains PDF/HTML files.")

    _build_sqlite_index(chunks)
    
    print(f"[indexer] Done. {len(chunks)} chunks indexed.")


def _collect_chunks(max_pages: int = 0) -> List[Chunk]:
    chunks: List[Chunk] = []

    for pdf_path in sorted(REFERENCE_DIR.glob("*.pdf")):
        description = DOC_DESCRIPTIONS.get(pdf_path.name, pdf_path.name)
        rel_path = str(pdf_path.relative_to(REFERENCE_DIR))
        print(f"  [pdf] {pdf_path.name}")
        try:
            sections = _parse_pdf(pdf_path, max_pages=max_pages)
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
                print(f"    WARNING: MemoryError splitting '{title}', skipping page")
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
    html_files = sorted(html_dir.glob("*.html"), key=lambda p: p.stat().st_size)
    if max_pages > 0:
        # test mode: only process the smallest HTML file to verify the pipeline
        html_files = html_files[:1]
        print(f"  [html] test mode: only processing smallest file ({html_files[0].name})")
    for html_path in html_files:
        description = DOC_DESCRIPTIONS.get(html_path.name, html_path.name)
        rel_path = str(html_path.relative_to(REFERENCE_DIR))
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


def _build_sqlite_index(chunks: List[Chunk]) -> None:
    if DB_FILE.exists():
        DB_FILE.unlink()
        
    conn = sqlite3.connect(str(DB_FILE))
    
    # Enable FTS5
    # Create a virtual table for full-text search
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts 
        USING fts5(text, title, source, doc_type, description, chunk_idx UNINDEXED);
    """)
    
    print(f"[indexer] Inserting {len(chunks)} chunks into SQLite...")
    
    # Batch insert
    data = [
        (c.text, c.title, c.source, c.doc_type, c.description, c.chunk_idx)
        for c in chunks
    ]
    
    conn.executemany(
        "INSERT INTO chunks_fts(text, title, source, doc_type, description, chunk_idx) VALUES (?, ?, ?, ?, ?, ?)",
        data
    )
    
    conn.commit()
    
    # Optimize the FTS index
    print("[indexer] Optimizing FTS index...")
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize');")
    conn.commit()
    conn.close()
    
    print(f"[indexer] SQLite index saved to {DB_FILE}")


if __name__ == "__main__":
    build_index(force=True)
