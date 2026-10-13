"""Gemini calls: the streamed impact report and doc extraction.

Two hard rules live here:
  1. The report prompt forbids naming any component outside the evidence package.
  2. If there is no key, no network, or the call fails, we fall back to a
     deterministic report built from the same evidence package. The fallback can
     only restate facts, so it cannot invent either.

Reports are cached by (component, change) under data/cache/ so the demo is
repeatable and offline-safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Iterator

from ripple import evidence

log = logging.getLogger("ripple.llm")

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"
DEFAULT_MODEL = "gemini-2.5-flash"
ARROW = "→"

REPORT_SYSTEM = (
    "You are a change-impact analyst. You receive a proposed change and an "
    "evidence package of components, dependency paths, owners, and source "
    "snippets. Use ONLY the components and paths in the evidence package as "
    "facts. Never add a dependency that is not listed. Return sections: "
    "Summary, Directly affected, Indirectly affected, Checks to run, Unknowns. "
    'Label anything you infer as "Possible risk (not in graph)". Reference each '
    "finding by its path. Be concise: under 250 words. If information such as "
    "owner or tests is missing, say so under Unknowns. Describe results as "
    "potential impact, not certainty. Format with markdown: each section as a "
    "level-2 heading, findings as bullets with the path in backticks."
)

EXTRACT_SYSTEM = (
    "You extract a dependency graph from an engineering document. Return STRICT "
    "JSON only, no prose and no code fences, shaped exactly as: "
    '{"components": [{"id": "snake_case", "name": "Human Name", '
    '"type": "api|service|database|app|test|doc|team", "owner": "", '
    '"criticality": "low|medium|high"}], '
    '"dependencies": [{"source": "id", "target": "id", '
    '"relationship": "CALLS|DEPENDS_ON|READS_FROM|TESTED_BY|DOCUMENTED_IN", '
    '"snippet": "exact sentence from the document"}]}. '
    "EDGE DIRECTION: source is the dependency, target is the consumer. "
    '"The mobile app calls the auth API" becomes {"source": "auth_api", '
    '"target": "mobile_app"}. Every dependency MUST include the exact '
    "supporting sentence from the document in `snippet`. Drop any dependency "
    "you cannot quote. Reuse an existing component id when the name matches one "
    "you are given. Leave owner as an empty string if the document does not say."
)


# --- key handling ----------------------------------------------------------

def _api_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def available() -> bool:
    return bool(_api_key())


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def _client():
    from google import genai  # imported lazily: optional dependency

    return genai.Client(api_key=_api_key())


# --- cache -----------------------------------------------------------------

def _cache_path(component_id: str, change: str) -> Path:
    key = hashlib.sha1(
        f"{component_id}|{change.strip().lower()}".encode("utf-8")
    ).hexdigest()[:16]
    return CACHE_DIR / f"{component_id}-{key}.md"


def _write_cache(path: Path, text: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        log.warning("could not cache report: %s", exc)


# --- report ----------------------------------------------------------------

def report_source(pkg: dict, use_cache: bool = True) -> str:
    """Where the next report will come from: cache, gemini or fallback."""
    if use_cache and _cache_path(pkg["component"]["id"], pkg["change"]).exists():
        return "cache"
    return "gemini" if available() else "fallback"


def report_stream(pkg: dict, use_cache: bool = True) -> Iterator[str]:
    """Yield the report in chunks. Source order: cache, Gemini, fallback."""
    cache = _cache_path(pkg["component"]["id"], pkg["change"])

    if use_cache and cache.exists():
        log.info("report served from cache: %s", cache.name)
        yield from _chunk(cache.read_text(encoding="utf-8"))
        return

    if available():
        collected = []
        try:
            for piece in _gemini_report(pkg):
                collected.append(piece)
                yield piece
        except Exception as exc:
            log.warning("gemini report failed (%s), using deterministic fallback", exc)
            if collected:
                yield "\n\n_The report was cut short by an upstream error._"
                return
            text = fallback_report(pkg)
            _write_cache(cache, text)
            yield from _chunk(text)
            return
        text = "".join(collected).strip()
        if text:
            _write_cache(cache, text)
        else:
            yield from _chunk(fallback_report(pkg))
        return

    log.info("no GEMINI_API_KEY, using deterministic fallback report")
    text = fallback_report(pkg)
    _write_cache(cache, text)
    yield from _chunk(text)


def _gemini_report(pkg: dict) -> Iterator[str]:
    from google.genai import types

    stream = _client().models.generate_content_stream(
        model=model_name(),
        contents=evidence.render(pkg),
        config=types.GenerateContentConfig(
            system_instruction=REPORT_SYSTEM,
            temperature=0.2,
            max_output_tokens=1200,
        ),
    )
    for event in stream:
        if event.text:
            yield event.text


def _chunk(text: str, size: int = 20) -> Iterator[str]:
    """Hand a cached report back in pieces so the UI still streams it."""
    for i in range(0, len(text), size):
        yield text[i : i + size]


def fallback_report(pkg: dict) -> str:
    """A grounded report assembled from the evidence package, with no LLM.

    It only restates the package, so by construction it cannot name a component
    that is not in the graph.
    """
    component = pkg["component"]
    direct = [a for a in pkg["affected"] if a["hop"] == 1]
    indirect = [a for a in pkg["affected"] if a["hop"] > 1]
    change = (
        '"{0}"'.format(pkg["change"]) if pkg["change"] else "the proposed change"
    )

    out = ["## Summary", ""]
    if pkg["affected"]:
        deepest = max(a["hop"] for a in pkg["affected"])
        out.append(
            "{n} components could be affected by {change} on {name}: "
            "{d} directly and {i} indirectly, reaching {deepest} hops out. "
            "Deterministic risk is {level}. These are potential impacts traced "
            "from documented dependencies, not guarantees.".format(
                n=len(pkg["affected"]),
                change=change,
                name=component["name"],
                d=len(direct),
                i=len(indirect),
                deepest=deepest,
                level=pkg["risk"]["level"],
            )
        )
    else:
        out.append(
            "Nothing in the graph depends on {name}, so no impact can be traced "
            "for this change. A missing edge is not proof that no dependency "
            "exists.".format(name=component["name"])
        )

    for title, rows in (("Directly affected", direct), ("Indirectly affected", indirect)):
        out += ["", "## " + title, ""]
        if not rows:
            out.append("- None found in the graph.")
            continue
        for a in rows:
            path = (" " + ARROW + " ").join(a["path_names"])
            relation = a["relationship"].lower().replace("_", " ")
            out.append(
                "- **{name}** ({relation}, risk {risk}) `{path}`".format(
                    name=a["name"], relation=relation, risk=a["risk"], path=path
                )
            )
            if a["evidence_snippet"]:
                out.append(
                    '  - Source [{doc}]: "{snippet}"'.format(
                        doc=a["evidence_doc"], snippet=a["evidence_snippet"]
                    )
                )

    out += ["", "## Checks to run", ""]
    checks = []
    for a in direct:
        owner = a["owner"] if a["owner"] != "unknown" else "no listed owner"
        path = (" " + ARROW + " ").join(a["path_names"])
        checks.append(
            "- Confirm with {owner} whether **{name}** still relies on the "
            "behaviour being changed (`{path}`).".format(
                owner=owner, name=a["name"], path=path
            )
        )
    for a in pkg["affected"]:
        if a["type"] == "test":
            checks.append(
                "- Re-run and update **{name}** before release.".format(name=a["name"])
            )
    for a in pkg["affected"]:
        if a["type"] == "doc":
            checks.append(
                "- Revise **{name}**, which documents the behaviour being "
                "changed.".format(name=a["name"])
            )
    out += checks[:8] or ["- No checks could be derived from the graph."]

    out += ["", "## Unknowns", ""]
    unknowns = []
    # An unowned component is the notable gap, so it gets its own line.
    for gap in pkg["gaps"]:
        if "owner" in gap["missing"]:
            unknowns.append(
                "- **{name}** has no recorded owner{tests}, so nobody can "
                "confirm its exposure to this change.".format(
                    name=gap["name"],
                    tests=" and no test suite" if "tests" in gap["missing"] else "",
                )
            )
    untested = [
        gap["name"]
        for gap in pkg["gaps"]
        if "tests" in gap["missing"] and "owner" not in gap["missing"]
    ]
    if untested:
        unknowns.append(
            "- No test suite is recorded for {names}, so regressions there "
            "would not be caught automatically.".format(names=", ".join(untested))
        )
    unknowns.append(
        "- The graph holds documented dependencies only. Anything undocumented "
        "is invisible here, so the absence of an edge is not evidence of safety."
    )
    out += unknowns

    return "\n".join(out)


# --- extraction ------------------------------------------------------------

def extract(doc_text: str, existing: list[dict]) -> dict:
    """Ask Gemini for components and dependencies. Raises if unavailable."""
    if not available():
        raise RuntimeError("GEMINI_API_KEY not set")

    from google.genai import types

    known = ", ".join("{id} ({name})".format(**c) for c in existing)
    response = _client().models.generate_content(
        model=model_name(),
        contents="EXISTING COMPONENT IDS: {known}\n\nDOCUMENT:\n{doc}".format(
            known=known, doc=doc_text[:20000]
        ),
        config=types.GenerateContentConfig(
            system_instruction=EXTRACT_SYSTEM,
            temperature=0.0,
            response_mime_type="application/json",
            max_output_tokens=2000,
        ),
    )
    return _parse_json(response.text or "")


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    data = json.loads(cleaned.strip())
    return {
        "components": data.get("components") or [],
        "dependencies": data.get("dependencies") or [],
    }
