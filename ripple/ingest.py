"""Turn a dropped document into components and dependencies, then merge.

Gemini does the extraction when a key is present. Without one, a small
rule-based reader takes over so the demo still works offline. Both paths obey
the same two rules:

  * every dependency must quote the sentence that supports it, or it is dropped;
  * edges follow the section 6 convention, dependency -> consumer.

Merged results land in data/graph_overlay.json, which graph.load_graph() reads
on top of the sample data. The sample files are never rewritten.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import networkx as nx

from ripple import llm
from ripple import graph as g

log = logging.getLogger("ripple.ingest")

OVERLAY_PATH = g.DATA_DIR / g.OVERLAY_FILE

VALID_TYPES = {"api", "service", "database", "app", "test", "doc", "team"}
VALID_RELATIONSHIPS = {"CALLS", "DEPENDS_ON", "READS_FROM", "TESTED_BY", "DOCUMENTED_IN"}

# "X calls Y", "X reads from Y", "X depends on Y", "X is tested by Y"
_VERBS = [
    (r"calls?|invokes?|queries|hits", "CALLS"),
    (r"reads? from|consumes?|subscribes to|ingests?", "READS_FROM"),
    (r"depends? on|relies on|requires?|needs?", "DEPENDS_ON"),
    (r"is tested by|tested by|covered by", "TESTED_BY"),
    (r"is documented in|documented in|described in", "DOCUMENTED_IN"),
]

_TYPE_HINTS = [
    ("test", {"test", "tests", "suite", "e2e"}),
    ("database", {"database", "warehouse", "cache", "store", "db"}),
    ("api", {"api", "gateway", "endpoint"}),
    ("doc", {"runbook", "doc", "docs", "readme", "spec"}),
    ("app", {"app", "portal", "ui", "console", "dashboard"}),
]


def slugify(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.strip().lower())).strip("_")


def guess_type(name: str) -> str:
    words = set(re.findall(r"[a-z]+", name.lower()))
    for kind, hints in _TYPE_HINTS:
        if words & hints:
            return kind
    return "service"


# --- rule-based fallback ---------------------------------------------------

def _sentences(text: str) -> list[str]:
    body = re.sub(r"^#.*$", "", text, flags=re.MULTILINE)  # headings are not evidence
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 25]


def _name_pattern(names: list[str]) -> str:
    return "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))


def rule_extract(text: str, existing: list[dict]) -> dict:
    """Find 'A <verb> B' sentences where both sides look like component names.

    Deliberately conservative: it would rather miss an edge than invent one.
    """
    known = {c["name"].lower(): c["id"] for c in existing}

    # Candidate names: existing ones, plus Title Case runs of 2-4 words.
    candidates = {c["name"] for c in existing}
    for match in re.findall(r"\b(?:[A-Z][A-Za-z0-9]*)(?:\s+(?:[A-Z][A-Za-z0-9]*|API|E2E)){1,3}\b", text):
        if len(match) > 3:
            candidates.add(match.strip())
    if not candidates:
        return {"components": [], "dependencies": []}

    names = _name_pattern(list(candidates))
    found_names: set[str] = set()
    dependencies = []

    for sentence in _sentences(text):
        flat = " ".join(sentence.split())
        for verbs, relationship in _VERBS:
            pattern = rf"\b(?:The\s+)?({names})\s+(?:{verbs})\s+(?:the\s+)?({names})\b"
            for left, right in re.findall(pattern, flat, flags=re.IGNORECASE):
                if left.lower() == right.lower():
                    continue
                found_names.update({left, right})
                # The sentence reads "consumer <verb> dependency", so the edge
                # runs right -> left, except TESTED_BY/DOCUMENTED_IN which
                # already read subject-first.
                if relationship in ("TESTED_BY", "DOCUMENTED_IN"):
                    source, target = left, right
                else:
                    source, target = right, left
                dependencies.append(
                    {
                        "source": slugify(known.get(source.lower(), source)),
                        "target": slugify(known.get(target.lower(), target)),
                        "relationship": relationship,
                        "snippet": flat,
                    }
                )

    components = []
    for name in sorted(found_names):
        if name.lower() in known:
            continue
        components.append(
            {
                "id": slugify(name),
                "name": name,
                "type": guess_type(name),
                "owner": "",
                "criticality": "medium",
            }
        )
    return {"components": components, "dependencies": dependencies}


# --- merge -----------------------------------------------------------------

def _load_overlay() -> dict:
    if OVERLAY_PATH.exists():
        try:
            data = json.loads(OVERLAY_PATH.read_text(encoding="utf-8") or "{}")
            return {
                "components": data.get("components", []),
                "dependencies": data.get("dependencies", []),
            }
        except json.JSONDecodeError:
            log.warning("overlay was corrupt, starting a fresh one")
    return {"components": [], "dependencies": []}


def _save_overlay(overlay: dict) -> None:
    OVERLAY_PATH.write_text(json.dumps(overlay, indent=2), encoding="utf-8")


def merge(extracted: dict, doc_name: str, G: nx.DiGraph) -> dict:
    """Merge into the overlay. Returns counts of what actually landed."""
    overlay = _load_overlay()
    known_ids = set(G.nodes) | {c["id"] for c in overlay["components"]}
    by_name = {G.nodes[n]["name"].lower(): n for n in G.nodes}
    by_name.update({c["name"].lower(): c["id"] for c in overlay["components"]})

    added_components, dropped = [], 0

    for raw in extracted.get("components", []):
        name = (raw.get("name") or "").strip()
        if not name:
            dropped += 1
            continue
        existing_id = by_name.get(name.lower())
        if existing_id:
            continue  # reuse the id we already have
        new_id = slugify(raw.get("id") or name)
        if not new_id or new_id in known_ids:
            continue
        kind = (raw.get("type") or "").lower()
        component = {
            "id": new_id,
            "name": name,
            "type": kind if kind in VALID_TYPES else guess_type(name),
            "owner": (raw.get("owner") or "").strip(),
            "criticality": (raw.get("criticality") or "medium").lower(),
            "source": doc_name,
        }
        if component["criticality"] not in g.CRITICALITY_WEIGHT:
            component["criticality"] = "medium"
        overlay["components"].append(component)
        added_components.append(component)
        known_ids.add(new_id)
        by_name[name.lower()] = new_id

    existing_edges = {(e["source"], e["target"]) for e in overlay["dependencies"]}
    existing_edges |= set(G.edges)
    added_dependencies = []

    for raw in extracted.get("dependencies", []):
        snippet = (raw.get("snippet") or "").strip()
        source = slugify(raw.get("source") or "")
        target = slugify(raw.get("target") or "")
        relationship = (raw.get("relationship") or "DEPENDS_ON").upper()

        if not snippet or not source or not target or source == target:
            dropped += 1  # no quote, no edge
            continue
        if source not in known_ids or target not in known_ids:
            dropped += 1  # refers to something we never saw
            continue
        if (source, target) in existing_edges:
            continue
        if relationship not in VALID_RELATIONSHIPS:
            relationship = "DEPENDS_ON"

        edge = {
            "source": source,
            "target": target,
            "relationship": relationship,
            "confidence": "inferred",
            "evidence": {"doc": doc_name, "snippet": snippet[:400]},
        }
        overlay["dependencies"].append(edge)
        added_dependencies.append(edge)
        existing_edges.add((source, target))

    _save_overlay(overlay)
    return {
        "components": added_components,
        "dependencies": added_dependencies,
        "dropped": dropped,
    }


# --- entry point -----------------------------------------------------------

def ingest_document(doc_name: str, text: str, G: nx.DiGraph, store=None) -> dict:
    """Extract, merge, and hand the raw text to the store for retrieval."""
    existing = [{"id": n, "name": G.nodes[n]["name"]} for n in G.nodes]

    extractor = "gemini"
    try:
        extracted = llm.extract(text, existing)
    except Exception as exc:
        log.info("llm extraction unavailable (%s), using rule-based reader", exc)
        extracted = rule_extract(text, existing)
        extractor = "rules"

    result = merge(extracted, doc_name, G)

    if store is not None:
        try:
            store.add_document(doc_name, text)
        except Exception as exc:
            log.warning("store.add_document failed for %s: %s", doc_name, exc)

    result["extractor"] = extractor
    result["doc"] = doc_name
    return result


def reset() -> None:
    """Drop everything learned from dropped docs."""
    if OVERLAY_PATH.exists():
        OVERLAY_PATH.unlink()
