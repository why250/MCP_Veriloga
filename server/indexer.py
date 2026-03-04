"""
Document indexer for VerilogA MCP Server.

Parses all PDFs and HTML files under reference/, splits them into chunks,
and builds:
  - A FAISS vector index (TF-IDF + TruncatedSVD / LSA, no torch/onnx needed)
  - A BM25 keyword index (rank-bm25)
  - A serialized sklearn pipeline (TfidfVectorizer + TruncatedSVD)

Everything is saved to index_cache/ and reloaded on subsequent starts.
"""

from __future__ import annotations

import json
import pickle
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

INDEX_CACHE_DIR = Path(__file__).parent / "index_cache"
CHUNKS_FILE    = INDEX_CACHE_DIR / "chunks.json"
FAISS_FILE     = INDEX_CACHE_DIR / "faiss.index"
BM25_FILE      = INDEX_CACHE_DIR / "bm25.pkl"
PIPELINE_FILE  = INDEX_CACHE_DIR / "tfidf_svd_pipeline.pkl"

LSA_COMPONENTS = 256   # SVD latent dimensions
CHUNK_SIZE     = 500   # approximate characters per chunk
CHUNK_OVERLAP  = 80    # character overlap between adjacent chunks

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
    """Return list of (page_title, page_text) from a PDF."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = _clean_text(text)
        if text.strip():
            pages.append((f"Page {i + 1}", text))
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

def build_index(force: bool = False) -> List[Chunk]:
    """
    Parse documents, build FAISS + BM25 indexes, save to index_cache/.
    If cache exists and force=False, load from cache instead.
    """
    INDEX_CACHE_DIR.mkdir(exist_ok=True)

    cache_complete = (
        CHUNKS_FILE.exists()
        and FAISS_FILE.exists()
        and BM25_FILE.exists()
        and PIPELINE_FILE.exists()
    )
    if not force and cache_complete:
        print("[indexer] Loading from cache...")
        return load_chunks()

    print("[indexer] Building index from documents...")
    chunks = _collect_chunks()
    if not chunks:
        raise RuntimeError("No chunks extracted — check that reference/ contains PDF/HTML files.")

    _build_lsa_faiss(chunks)
    _build_bm25(chunks)

    with open(CHUNKS_FILE, "w", encoding="utf-8") as fh:
        json.dump([asdict(c) for c in chunks], fh, ensure_ascii=False, indent=2)

    print(f"[indexer] Done. {len(chunks)} chunks indexed.")
    return chunks


def load_chunks() -> List[Chunk]:
    with open(CHUNKS_FILE, encoding="utf-8") as fh:
        return [Chunk(**d) for d in json.load(fh)]


def _collect_chunks() -> List[Chunk]:
    chunks: List[Chunk] = []

    for pdf_path in sorted(REFERENCE_DIR.glob("*.pdf")):
        description = DOC_DESCRIPTIONS.get(pdf_path.name, pdf_path.name)
        rel_path = str(pdf_path.relative_to(REFERENCE_DIR))
        print(f"  [pdf] {pdf_path.name}")
        try:
            sections = _parse_pdf(pdf_path)
        except Exception as exc:
            print(f"    WARNING: {exc}")
            continue
        for title, text in sections:
            for idx, chunk_text in enumerate(_split_text(text)):
                chunks.append(Chunk(
                    text=chunk_text, source=rel_path, title=title,
                    doc_type="pdf", description=description, chunk_idx=idx,
                ))

    html_dir = REFERENCE_DIR / "veriloga in ADS2025" / "veriloga"
    for html_path in sorted(html_dir.glob("*.html")):
        description = DOC_DESCRIPTIONS.get(html_path.name, html_path.name)
        rel_path = str(html_path.relative_to(REFERENCE_DIR))
        print(f"  [html] {html_path.name}")
        try:
            sections = _parse_html(html_path)
        except Exception as exc:
            print(f"    WARNING: {exc}")
            continue
        for title, text in sections:
            for idx, chunk_text in enumerate(_split_text(text)):
                chunks.append(Chunk(
                    text=chunk_text, source=rel_path, title=title,
                    doc_type="html", description=description, chunk_idx=idx,
                ))

    return chunks


# ---------------------------------------------------------------------------
# LSA (TF-IDF + TruncatedSVD) → FAISS index
# ---------------------------------------------------------------------------

def _build_lsa_faiss(chunks: List[Chunk]) -> None:
    import faiss
    import numpy as np
    from sklearn.decomposition import TruncatedSVD
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import Normalizer
    from sklearn.feature_extraction.text import TfidfVectorizer

    print("[indexer] Building TF-IDF + LSA pipeline...")
    texts = [c.text for c in chunks]

    n_components = min(LSA_COMPONENTS, len(texts) - 1)
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer="word",
            token_pattern=r"[a-zA-Z_$][\w$]*",
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.95,
            sublinear_tf=True,
        )),
        ("svd",  TruncatedSVD(n_components=n_components, random_state=42)),
        ("norm", Normalizer(copy=False)),
    ])

    print(f"[indexer] Fitting pipeline on {len(texts)} chunks (LSA dims={n_components})...")
    embeddings = pipeline.fit_transform(texts).astype("float32")

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    faiss.write_index(index, str(FAISS_FILE))

    with open(PIPELINE_FILE, "wb") as fh:
        pickle.dump(pipeline, fh)

    print(f"[indexer] FAISS index saved ({dim}d, {len(chunks)} vectors).")


# ---------------------------------------------------------------------------
# BM25 index
# ---------------------------------------------------------------------------

def _build_bm25(chunks: List[Chunk]) -> None:
    from rank_bm25 import BM25Okapi

    tokenized = [_tokenize(c.text) for c in chunks]
    bm25 = BM25Okapi(tokenized)
    with open(BM25_FILE, "wb") as fh:
        pickle.dump(bm25, fh)
    print("[indexer] BM25 index saved.")


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z_$][\w$]*|[0-9]+(?:\.[0-9]+)?", text.lower())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    build_index(force=True)
