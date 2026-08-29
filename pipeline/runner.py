"""
pipeline/runner.py
------------------
Core orchestration: Scrape → Chunk → Extract → Resolve → Build Graph → Export.

Usage
-----
    from pipeline.runner import run_pipeline
    from llm.groq_client import GroqClient

    run_pipeline(
        urls=["https://en.wikipedia.org/wiki/Knowledge_graph"],
        llm_client=GroqClient(),
        output_dir="output",
    )
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx
from tqdm import tqdm

from llm.extractor import extract_graph
from resolution.resolver import resolve_entities
from resolution.merger import merge_graphs, apply_resolution
from scrapers.http_scraper import HttpScraper
from scrapers.playwright_scraper import PlaywrightScraper
from utils.logger import get_logger
from utils.text import chunk_text, count_tokens

log = get_logger(__name__)

_MAX_CHUNK_TOKENS = 1_500


# ── Graph builder ─────────────────────────────────────────────────────────────

def _build_graph(extraction: dict[str, list[dict]]) -> nx.DiGraph:
    """Convert a single LLM extraction result into a NetworkX DiGraph."""
    g = nx.DiGraph()
    for ent in extraction.get("entities", []):
        g.add_node(ent["id"], name=ent.get("name", ""), type=ent.get("type", ""))
    for rel in extraction.get("relations", []):
        g.add_edge(rel["source"], rel["target"], relation=rel.get("relation", ""))
    return g


# ── Export ────────────────────────────────────────────────────────────────────

def _export(graph: nx.DiGraph, output_dir: Path, stem: str) -> None:
    """Write graph to GraphML and JSON-LD formats."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # GraphML
    graphml_path = output_dir / f"{stem}.graphml"
    nx.write_graphml(graph, str(graphml_path))
    log.info("Saved GraphML → {path}", path=graphml_path)

    # JSON-LD (lightweight representation)
    json_ld: dict[str, Any] = {
        "@context": {"@vocab": "https://schema.org/", "relation": "https://schema.org/relatedTo"},
        "@graph": [],
    }
    for node_id, attrs in graph.nodes(data=True):
        json_ld["@graph"].append({"@id": node_id, **attrs})
    for src, tgt, attrs in graph.edges(data=True):
        json_ld["@graph"].append({
            "@type": "Relation",
            "source": src,
            "target": tgt,
            **attrs,
        })
    jsonld_path = output_dir / f"{stem}.jsonld"
    jsonld_path.write_text(json.dumps(json_ld, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved JSON-LD → {path}", path=jsonld_path)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    urls: list[str],
    llm_client: Any,
    *,
    output_dir: str | Path = "output",
    model: str | None = None,
    use_playwright: bool = False,
    resolution_threshold: float = 85.0,
    stem: str = "graph",
) -> nx.DiGraph:
    """Run the full GraphHarvestor pipeline.

    Parameters
    ----------
    urls:
        List of URLs to scrape.
    llm_client:
        Any LLM client with a ``chat()`` method (GroqClient, OpenRouterClient …).
    output_dir:
        Directory where output files are written.
    model:
        Optional model name forwarded to the LLM extractor.
    use_playwright:
        Use the headless browser scraper instead of the HTTP scraper.
    resolution_threshold:
        Fuzzy-matching threshold (0–100) for entity deduplication.
    stem:
        Base filename for output files (without extension).

    Returns
    -------
    nx.DiGraph
        The final merged, resolved knowledge graph.
    """
    output_dir = Path(output_dir)
    scraper = PlaywrightScraper() if use_playwright else HttpScraper()

    all_entities: list[dict] = []
    graphs: list[nx.DiGraph] = []

    for url in tqdm(urls, desc="Scraping URLs", unit="url"):
        log.info("Processing {url}", url=url)
        try:
            chunks = scraper.scrape(url)
        except Exception as exc:
            log.error("Failed to scrape {url}: {err}", url=url, err=exc)
            continue

        if not chunks:
            log.warning("No content extracted from {url}", url=url)
            continue

        # Join chunks into full text then re-chunk to token limit
        full_text = " ".join(chunks)
        token_chunks = list(chunk_text(full_text, max_tokens=_MAX_CHUNK_TOKENS))
        log.info("  {n} token-chunks to process", n=len(token_chunks))

        for chunk in tqdm(token_chunks, desc="  Extracting", leave=False, unit="chunk"):
            try:
                extraction = extract_graph(chunk, llm_client, model=model)
            except Exception as exc:
                log.error("Extraction failed: {err}", err=exc)
                continue

            all_entities.extend(extraction.get("entities", []))
            graphs.append(_build_graph(extraction))

    if not graphs:
        log.warning("Pipeline produced no graph data.")
        return nx.DiGraph()

    # Merge all per-chunk graphs
    log.info("Merging {n} graphs …", n=len(graphs))
    merged = merge_graphs(*graphs)

    # Resolve duplicate entities
    log.info("Resolving entities (threshold={t}) …", t=resolution_threshold)
    resolved = resolve_entities(all_entities, threshold=resolution_threshold)
    final_graph = apply_resolution(merged, resolved)

    # Export
    _export(final_graph, output_dir, stem)

    log.info(
        "Pipeline complete: {nodes} nodes, {edges} edges",
        nodes=final_graph.number_of_nodes(),
        edges=final_graph.number_of_edges(),
    )
    return final_graph
