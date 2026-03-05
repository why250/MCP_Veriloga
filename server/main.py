"""
VerilogA MCP Server — main entry point.

Exposes 4 tools via FastMCP over HTTP SSE:
  1. search_veriloga     — hybrid RAG search over VerilogA documentation
  2. show_page           — return full text of a specific document
  3. list_sources        — list all indexed documents
  4. get_veriloga_template — return a ready-to-use VerilogA code template

Usage:
    python main.py                  # start SSE server on 0.0.0.0:8097
    python main.py --port 8096      # custom port
    python main.py --build-index    # force rebuild of document index, then exit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the server/ directory is on the Python path so relative imports work
sys.path.insert(0, str(Path(__file__).parent))

from fastmcp import FastMCP
from searcher import get_searcher
from templates import get_template, list_templates

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="veriloga-help",
    instructions=(
        "You are an expert assistant for Verilog-A analog hardware description language. "
        "You have access to the official OVI Verilog-A specification, the Keysight ADS 2025 "
        "Verilog-A help documentation, and a library of ready-to-use code templates. "
        "Use search_veriloga to look up language constructs, syntax, and best practices. "
        "Use get_veriloga_template to quickly scaffold new models. "
        "Always cite the source document and page when providing information."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1: search_veriloga
# ---------------------------------------------------------------------------

@mcp.tool()
def search_veriloga(query: str, top_k: int = 5) -> str:
    """Search VerilogA documentation using hybrid RAG (semantic + keyword).

    Args:
        query:  Natural language or keyword query, e.g. "analog operator ddt",
                "noise sources white_noise", "port declarations discipline",
                "limexp function usage".
        top_k:  Number of results to return (default 5, max 20).

    Returns:
        Formatted search results with title, source document, and content excerpt.
    """
    top_k = max(1, min(int(top_k), 20))
    searcher = get_searcher()
    results = searcher.search(query, top_k=top_k)

    if not results:
        return f"No results found for: {query!r}"

    lines = [f"Search results for: {query!r}  (top {len(results)})\n"]
    lines.append("=" * 70)

    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r['title']}")
        lines.append(f"    Source : {r['source']}")
        lines.append(f"    Doc    : {r['description']}")
        lines.append(f"    Score  : {r['score']:.4f}")
        lines.append(f"    Excerpt:")
        # Indent the excerpt for readability
        excerpt = r["text"][:600].replace("\n", "\n        ")
        lines.append(f"        {excerpt}")
        if len(r["text"]) > 600:
            lines.append("        [... truncated, use show_page() for full content ...]")
        lines.append("-" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: show_page
# ---------------------------------------------------------------------------

@mcp.tool()
def show_page(source_file: str) -> str:
    """Return the full extracted text of a VerilogA documentation page or PDF.

    Args:
        source_file: Relative path of the document inside the reference/ folder.
                     Use list_sources() to discover valid paths.
                     Examples:
                       "OVI_VerilogA.pdf"
                       "veriloga in ADS2025/veriloga/Condensed_Reference.html"

    Returns:
        Full plain-text content of the document.
    """
    searcher = get_searcher()
    text = searcher.get_full_document(source_file)

    if text is None:
        available = "\n".join(
            f"  {s['source']}" for s in searcher.list_sources()
        )
        return (
            f"Document not found: {source_file!r}\n\n"
            f"Available documents:\n{available}"
        )

    header = f"=== {source_file} ===\n\n"
    return header + text


# ---------------------------------------------------------------------------
# Tool 3: list_sources
# ---------------------------------------------------------------------------

@mcp.tool()
def list_sources() -> str:
    """List all VerilogA documentation sources that have been indexed.

    Returns a table of source files with their type (pdf/html) and description.
    Use the 'source' field as the argument to show_page().
    """
    searcher = get_searcher()
    sources = searcher.list_sources()

    if not sources:
        return "No documents indexed yet. The index will be built on first use."

    lines = ["Indexed VerilogA documentation sources:\n"]
    lines.append(f"{'#':<3}  {'Type':<5}  {'Source File':<65}  Description")
    lines.append("-" * 120)

    for i, s in enumerate(sources, 1):
        src = s["source"]
        dtype = s["doc_type"]
        desc = s["description"]
        # Wrap long source paths
        if len(src) > 63:
            src = "..." + src[-60:]
        lines.append(f"{i:<3}  {dtype:<5}  {src:<65}  {desc}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4: get_veriloga_template
# ---------------------------------------------------------------------------

@mcp.tool()
def get_veriloga_template(model_type: str) -> str:
    """Return a complete, ready-to-use VerilogA module template.

    Args:
        model_type: The type of model to generate.  Case-insensitive.
            Supported values (and common aliases):
              resistor   (r, res)
              capacitor  (c, cap)
              inductor   (l, ind)
              diode      (d)
              vccs       (gm)             — voltage-controlled current source
              vcvs       (gain)           — voltage-controlled voltage source
              nmos_simple (nmos, mosfet) — Level-1 NMOS transistor
              opamp_ideal (opamp)        — ideal single-pole op-amp
              vco        (oscillator)    — voltage-controlled oscillator
              transmission_line (tline) — lossless transmission line
              noise_source (noise)       — thermal noise resistor
              pll_phase_detector (pfd)  — phase-frequency detector

            Pass "list" to see all available templates.

    Returns:
        Complete, annotated VerilogA source code ready to use in ADS or other simulators.
    """
    if model_type.strip().lower() in ("list", "help", "?", ""):
        return list_templates()

    result = get_template(model_type)

    if result is None:
        return (
            f"No template found for: {model_type!r}\n\n"
            + list_templates()
        )

    lines = [
        f"// VerilogA Template: {result['title']}",
        f"// {result['description']}",
        "// " + "-" * 70,
        "",
        result["code"],
        "",
        "// Usage tips:",
        "//   1. Save as <module_name>.vams in your ADS component library",
        "//   2. Add `include \"disciplines.vams\" if not already at top",
        "//   3. Adjust parameter values to match your target specification",
        "//   4. Use search_veriloga() to look up specific constructs used above",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VerilogA MCP Server")
    parser.add_argument("--port", type=int, default=8097,
                        help="HTTP port to listen on (default: 8097)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Host/interface to bind to (default: 0.0.0.0)")
    parser.add_argument("--build-index", action="store_true",
                        help="Force-rebuild the document index, then exit")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.build_index:
        from indexer import build_index
        build_index(force=True)
        sys.exit(0)

    print(f"[veriloga-help] Starting MCP server on {args.host}:{args.port}")
    print("[veriloga-help] First request will trigger document indexing if cache is missing.")
    mcp.run(transport="sse", host=args.host, port=args.port)
