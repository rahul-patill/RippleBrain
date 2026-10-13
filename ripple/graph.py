"""Graph building, traversal, paths and risk scoring.

EDGE DIRECTION CONVENTION
-------------------------
An edge points from the DEPENDENCY to its CONSUMER.

    auth_api --> mobile_app        means "mobile_app depends on auth_api"

So the impact of changing a node is everything REACHABLE FROM it:
    impact(G, "auth_api") -> web_app, mobile_app, ... and their tests.
Its own dependencies (user_db, token_service) are PREDECESSORS and are never
part of the blast radius.

If a source file ever stores edges the other way round, reverse them ONCE here
at load time (see _add_edge) and nowhere else in the codebase.

Everything in this module is deterministic. No LLM is involved.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

CRITICALITY_WEIGHT = {"low": 1, "medium": 2, "high": 3}
RISK_FORMULA = "risk = criticality weight (low 1, medium 2, high 3) / hop distance"

#: Overlay of nodes/edges learned from dropped docs. Written by ripple.ingest.
OVERLAY_FILE = "graph_overlay.json"


# --- loading ---------------------------------------------------------------

def _add_node(G: nx.DiGraph, c: dict) -> None:
    G.add_node(
        c["id"],
        id=c["id"],
        name=c.get("name") or c["id"],
        type=c.get("type") or "service",
        owner=c.get("owner") or "",
        criticality=(c.get("criticality") or "medium").lower(),
        source=c.get("source") or "",
    )


def _add_edge(G: nx.DiGraph, e: dict) -> None:
    # Source data already follows the dependency -> consumer convention.
    # Reversing, if ever needed, belongs right here.
    G.add_edge(
        e["source"],
        e["target"],
        relationship=e.get("relationship") or "DEPENDS_ON",
        confidence=(e.get("confidence") or "documented").lower(),
        evidence=e.get("evidence") or {"doc": "", "snippet": ""},
    )


def load_graph(
    data_dir: Path | str | None = None, include_overlay: bool = True
) -> nx.DiGraph:
    """Build the graph from data/, including any ingested overlay.

    Pass include_overlay=False for the pristine sample graph (the tests do).
    """
    root = Path(data_dir) if data_dir else DATA_DIR
    G = nx.DiGraph()

    for c in json.loads((root / "components.json").read_text(encoding="utf-8")):
        _add_node(G, c)
    for e in json.loads((root / "dependencies.json").read_text(encoding="utf-8")):
        _add_edge(G, e)

    overlay_path = root / OVERLAY_FILE
    if include_overlay and overlay_path.exists():
        overlay = json.loads(overlay_path.read_text(encoding="utf-8") or "{}")
        for c in overlay.get("components", []):
            if c["id"] not in G:
                _add_node(G, c)
        for e in overlay.get("dependencies", []):
            if e["source"] in G and e["target"] in G:
                _add_edge(G, e)

    # Drop edges that reference a component we never loaded.
    for u, v in [(u, v) for u, v in G.edges if u not in G or v not in G]:
        G.remove_edge(u, v)
    return G


# --- neighbours ------------------------------------------------------------

def consumers(G: nx.DiGraph, node_id: str) -> list[str]:
    """Things that depend on node_id (downstream, i.e. at risk)."""
    return list(G.successors(node_id))


def dependencies(G: nx.DiGraph, node_id: str) -> list[str]:
    """Things node_id depends on (upstream, NOT part of its blast radius)."""
    return list(G.predecessors(node_id))


# --- traversal -------------------------------------------------------------

def impact(G: nx.DiGraph, node_id: str) -> dict[str, dict]:
    """Everything reachable from node_id, with hop distance and shortest path.

    Cycles are safe: shortest-path BFS visits each node once.
    """
    if node_id not in G:
        raise KeyError(f"unknown component: {node_id}")

    paths = nx.single_source_shortest_path(G, node_id)
    return {
        target: {"hop": len(path) - 1, "path": path}
        for target, path in paths.items()
        if target != node_id
    }


# --- risk ------------------------------------------------------------------

def risk_score(criticality: str, hop: int) -> float:
    weight = CRITICALITY_WEIGHT.get((criticality or "medium").lower(), 2)
    return weight / max(hop, 1)


def risk_level(score: float) -> str:
    if score >= 2.5:
        return "High"
    if score >= 1.2:
        return "Medium"
    return "Low"


# --- analysis --------------------------------------------------------------

def _is_tested(G: nx.DiGraph, node_id: str) -> bool:
    return any(G.nodes[t].get("type") == "test" for t in G.successors(node_id))


def _gaps(G: nx.DiGraph, node_ids: list[str]) -> list[dict]:
    """Affected components missing an owner or a test suite."""
    out = []
    for n in node_ids:
        attrs = G.nodes[n]
        missing = []
        if not attrs.get("owner"):
            missing.append("owner")
        if attrs.get("type") not in ("test", "doc") and not _is_tested(G, n):
            missing.append("tests")
        if missing:
            out.append({"id": n, "name": attrs["name"], "missing": missing})
    return out


def analyse(G: nx.DiGraph, node_id: str) -> dict:
    """Full deterministic result for one changed component."""
    reached = impact(G, node_id)

    affected = []
    for target, info in reached.items():
        attrs = G.nodes[target]
        path = info["path"]
        last_edge = G.edges[path[-2], path[-1]]
        score = risk_score(attrs["criticality"], info["hop"])
        affected.append(
            {
                "id": target,
                "name": attrs["name"],
                "type": attrs["type"],
                "owner": attrs["owner"],
                "criticality": attrs["criticality"],
                "hop": info["hop"],
                "path": path,
                "path_names": [G.nodes[p]["name"] for p in path],
                "relationship": last_edge["relationship"],
                "confidence": last_edge["confidence"],
                "risk": round(score, 2),
            }
        )
    affected.sort(key=lambda a: (a["hop"], a["name"]))

    top = max((a["risk"] for a in affected), default=0.0)
    changed = G.nodes[node_id]

    return {
        "component": {
            "id": node_id,
            "name": changed["name"],
            "type": changed["type"],
            "owner": changed["owner"],
            "criticality": changed["criticality"],
        },
        "affected": affected,
        "direct": [a for a in affected if a["hop"] == 1],
        "indirect": [a for a in affected if a["hop"] > 1],
        "max_hop": max((a["hop"] for a in affected), default=0),
        "risk": {
            "score": round(top, 2),
            "level": risk_level(top),
            "formula": RISK_FORMULA,
        },
        "gaps": _gaps(G, [node_id] + list(reached)),
    }


def to_cytoscape(G: nx.DiGraph) -> dict:
    """Nodes and edges in the shape the UI wants."""
    return {
        "nodes": [
            {"data": {**G.nodes[n], "tested": _is_tested(G, n)}} for n in G.nodes
        ],
        "edges": [
            {
                "data": {
                    "id": f"{u}__{v}",
                    "source": u,
                    "target": v,
                    "relationship": d["relationship"],
                    "confidence": d["confidence"],
                    "evidence": d["evidence"],
                }
            }
            for u, v, d in G.edges(data=True)
        ],
    }
