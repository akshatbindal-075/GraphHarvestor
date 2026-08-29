"""
resolution/merger.py
--------------------
Merge multiple NetworkX graphs into one, reconciling duplicate nodes that
were identified during entity resolution.

Usage
-----
    import networkx as nx
    from resolution.merger import merge_graphs

    g1 = nx.DiGraph()
    g2 = nx.DiGraph()
    # … populate graphs …
    combined = merge_graphs(g1, g2)
"""

from __future__ import annotations

import networkx as nx

from utils.logger import get_logger

log = get_logger(__name__)


def _merge_attrs(existing: dict, incoming: dict) -> dict:
    """Merge two attribute dicts, extending list fields and preferring
    non-empty values for scalar fields."""
    merged = dict(existing)
    for key, val in incoming.items():
        if key not in merged:
            merged[key] = val
        elif isinstance(merged[key], list):
            merged[key] = list(dict.fromkeys(merged[key] + (val if isinstance(val, list) else [val])))
        elif not merged[key] and val:
            merged[key] = val
    return merged


def merge_graphs(*graphs: nx.DiGraph) -> nx.DiGraph:
    """Merge two or more directed graphs into a single ``nx.DiGraph``.

    Nodes with the same ID are merged: their attributes are combined.
    Edges are unioned; parallel edges get their attributes combined.

    Parameters
    ----------
    *graphs:
        Two or more ``nx.DiGraph`` instances to merge.

    Returns
    -------
    nx.DiGraph
        A new graph containing all nodes and edges from the inputs.
    """
    combined = nx.DiGraph()

    for g in graphs:
        for node_id, attrs in g.nodes(data=True):
            if combined.has_node(node_id):
                existing_attrs = combined.nodes[node_id]
                combined.nodes[node_id].update(_merge_attrs(existing_attrs, attrs))
            else:
                combined.add_node(node_id, **attrs)

        for src, tgt, attrs in g.edges(data=True):
            if combined.has_edge(src, tgt):
                existing_attrs = combined.edges[src, tgt]
                combined.edges[src, tgt].update(_merge_attrs(existing_attrs, attrs))
            else:
                combined.add_edge(src, tgt, **attrs)

    log.info(
        "Merged {n_graphs} graphs → {nodes} nodes, {edges} edges",
        n_graphs=len(graphs),
        nodes=combined.number_of_nodes(),
        edges=combined.number_of_edges(),
    )
    return combined


def apply_resolution(graph: nx.DiGraph, resolved_entities: list[dict]) -> nx.DiGraph:
    """Re-map absorbed entity IDs in *graph* to their canonical node.

    After ``resolve_entities()`` runs, some node IDs were merged into others.
    This function rewires edges so they all point to the canonical node and
    removes the now-redundant alias nodes.

    Parameters
    ----------
    graph:
        A graph whose node IDs correspond to raw entity IDs.
    resolved_entities:
        Output of ``resolution.resolver.resolve_entities()``.

    Returns
    -------
    nx.DiGraph
        Graph with alias nodes collapsed into their canonical counterparts.
    """
    # Build mapping: absorbed_id → canonical_id
    id_map: dict[str, str] = {}
    for ent in resolved_entities:
        for merged_id in ent.get("merged_ids", []):
            id_map[merged_id] = ent["id"]

    if not id_map:
        return graph

    new_graph = nx.DiGraph()

    # Add canonical nodes
    for node_id, attrs in graph.nodes(data=True):
        canonical = id_map.get(node_id, node_id)
        if new_graph.has_node(canonical):
            new_graph.nodes[canonical].update(_merge_attrs(new_graph.nodes[canonical], attrs))
        else:
            new_graph.add_node(canonical, **attrs)

    # Rewire edges
    for src, tgt, attrs in graph.edges(data=True):
        c_src = id_map.get(src, src)
        c_tgt = id_map.get(tgt, tgt)
        if c_src == c_tgt:
            continue  # skip self-loops created by merging
        if new_graph.has_edge(c_src, c_tgt):
            new_graph.edges[c_src, c_tgt].update(_merge_attrs(new_graph.edges[c_src, c_tgt], attrs))
        else:
            new_graph.add_edge(c_src, c_tgt, **attrs)

    log.info(
        "Resolution applied: {before} → {after} nodes",
        before=graph.number_of_nodes(),
        after=new_graph.number_of_nodes(),
    )
    return new_graph
