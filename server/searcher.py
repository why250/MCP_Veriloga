"""
Hybrid searcher for VerilogA MCP Server.

Combines:
  - Semantic search  : TF-IDF + TruncatedSVD (LSA) vectors in FAISS
  - Keyword search   : BM25Okapi (rank-bm25)

Final score = ALPHA * lsa_score + (1 - ALPHA) * bm25_score

No torch or onnxruntime dependency — pure Python + numpy + sklearn + FAISS.
"""

from __future__ import annotations

import pickle
import re
from pathlib import Path
from typing import List, Optional

import faiss
import numpy as np

from indexer import (
    FAISS_FILE,
    BM25_FILE,
    PIPELINE_FILE,
    REFERENCE_DIR,
    Chunk,
    build_index,
    _tokenize,
)

ALPHA = 0.6   # weight for LSA/semantic score


class HybridSearcher:
    """Loads (or builds) the index once and serves repeated queries."""

    def __init__(self) -> None:
        self._chunks: Optional[List[Chunk]] = None
        self._faiss_index = None
        self._bm25 = None
        self._pipeline = None   # sklearn TF-IDF + SVD + Normalizer pipeline

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._chunks is not None:
            return

        self._chunks = build_index(force=False)
        self._faiss_index = faiss.read_index(str(FAISS_FILE))

        with open(BM25_FILE, "rb") as fh:
            self._bm25 = pickle.load(fh)

        with open(PIPELINE_FILE, "rb") as fh:
            self._pipeline = pickle.load(fh)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """
        Return top_k most relevant chunks as a list of dicts:
          {text, source, title, doc_type, description, score}
        """
        self._ensure_loaded()

        query = query.strip()
        if not query:
            return []

        n = len(self._chunks)
        k = min(top_k * 4, n)

        # --- LSA semantic scores ---
        q_vec = self._pipeline.transform([query]).astype("float32")
        sem_scores_raw, sem_indices = self._faiss_index.search(q_vec, k)
        sem_scores_raw = sem_scores_raw[0]
        sem_indices = sem_indices[0]

        # Normalize semantic scores to [0, 1]
        s_min, s_max = sem_scores_raw.min(), sem_scores_raw.max()
        if s_max > s_min:
            sem_norm = (sem_scores_raw - s_min) / (s_max - s_min)
        else:
            sem_norm = np.ones_like(sem_scores_raw)

        # --- BM25 scores (full corpus) ---
        tokens = _tokenize(query)
        bm25_all = np.array(self._bm25.get_scores(tokens), dtype="float32")
        bm25_max = bm25_all.max()
        if bm25_max > 0:
            bm25_all /= bm25_max

        # --- Combine on candidate set ---
        candidates: dict[int, float] = {}
        for i, sem_s in zip(sem_indices, sem_norm):
            if i < 0:
                continue
            combined = ALPHA * float(sem_s) + (1 - ALPHA) * float(bm25_all[i])
            candidates[int(i)] = combined

        # Also promote top BM25 hits that may have been outside FAISS top-k
        bm25_top_k_idx = np.argsort(bm25_all)[::-1][:k]
        for i in bm25_top_k_idx:
            if i not in candidates:
                candidates[int(i)] = (1 - ALPHA) * float(bm25_all[i])

        ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:top_k]

        results = []
        for idx, score in ranked:
            chunk = self._chunks[idx]
            results.append({
                "text": chunk.text,
                "source": chunk.source,
                "title": chunk.title,
                "doc_type": chunk.doc_type,
                "description": chunk.description,
                "score": round(float(score), 4),
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
        self._ensure_loaded()
        seen: dict[str, dict] = {}
        for chunk in self._chunks:
            if chunk.source not in seen:
                seen[chunk.source] = {
                    "source": chunk.source,
                    "doc_type": chunk.doc_type,
                    "description": chunk.description,
                }
        return list(seen.values())


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

_searcher: Optional[HybridSearcher] = None


def get_searcher() -> HybridSearcher:
    global _searcher
    if _searcher is None:
        _searcher = HybridSearcher()
    return _searcher
