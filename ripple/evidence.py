"""Build the evidence package handed to the LLM.

The package is the LLM's entire world: it may only talk about components and
paths that appear here. Anything beyond it has to be labelled as a possible risk
or an unknown.
"""
from __future__ import annotations

import networkx as nx

from ripple import graph as g

MAX_AFFECTED = 16
MAX_DOCS = 4


def build_package(
    G: nx.DiGraph,
    component_id: str,
    change: str,
    store=None,
) -> dict:
    """Deterministic analysis + retrieved snippets, ready for the prompt."""
    analysis = g.analyse(G, component_id)

    items = []
    for a in analysis["affected"][:MAX_AFFECTED]:
        edge = G.edges[a["path"][-2], a["path"][-1]]
        ev = edge.get("evidence") or {}
        items.append(
            {
                **a,
                "owner": a["owner"] or "unknown",
                "tested": any(
                    G.nodes[t].get("type") == "test" for t in G.successors(a["id"])
                ),
                "evidence_doc": ev.get("doc", ""),
                "evidence_snippet": ev.get("snippet", ""),
            }
        )

    docs = []
    served_by = "local"
    if store is not None and change.strip():
        query = f"{analysis['component']['name']} {change}"
        docs = store.search(query, MAX_DOCS)
        served_by = getattr(store, "served_by", "local")

    return {
        "change": change.strip(),
        "component": analysis["component"],
        "affected": items,
        "truncated": len(analysis["affected"]) > MAX_AFFECTED,
        "risk": analysis["risk"],
        "gaps": analysis["gaps"],
        "docs": docs,
        "served_by": served_by,
        "allowed_components": sorted(
            {analysis["component"]["name"]} | {i["name"] for i in items}
        ),
    }


def render(pkg: dict) -> str:
    """Flatten the package into the text block the prompt receives."""
    c = pkg["component"]
    lines = [
        "PROPOSED CHANGE",
        f'  Component: {c["name"]} ({c["type"]}, criticality {c["criticality"]},'
        f' owner {c["owner"] or "unknown"})',
        f'  Change: {pkg["change"] or "(not described)"}',
        "",
        f'RISK (deterministic): {pkg["risk"]["level"]} '
        f'(score {pkg["risk"]["score"]}; {pkg["risk"]["formula"]})',
        "",
        "AFFECTED COMPONENTS (the only components you may treat as facts)",
    ]

    if not pkg["affected"]:
        lines.append("  (none - nothing in the graph depends on this component)")
    for a in pkg["affected"]:
        lines.append(
            f'  - {a["name"]} [{a["type"]}] hop {a["hop"]} | '
            f'owner {a["owner"]} | tests {"yes" if a["tested"] else "none"} | '
            f'risk {a["risk"]} | {a["confidence"]}'
        )
        lines.append(f'    path: {" -> ".join(a["path_names"])}')
        lines.append(f'    via {a["relationship"]}')
        if a["evidence_snippet"]:
            lines.append(f'    source [{a["evidence_doc"]}]: "{a["evidence_snippet"]}"')

    if pkg["gaps"]:
        lines += ["", "KNOWN GAPS IN THE GRAPH"]
        for gap in pkg["gaps"]:
            lines.append(f'  - {gap["name"]}: no {" and no ".join(gap["missing"])}')

    if pkg["docs"]:
        lines += ["", "RETRIEVED DOCUMENT SNIPPETS"]
        for d in pkg["docs"]:
            lines.append(f'  - [{d["doc"]}] "{d["snippet"]}"')

    if pkg["truncated"]:
        lines += ["", "(the affected list was truncated for length)"]

    return "\n".join(lines)
