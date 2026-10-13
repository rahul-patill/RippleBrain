"""Ripple Brain: FastAPI app and static file serving.

Impact is deterministic and returned immediately so the graph can animate while
the report streams in behind it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # before anything reads os.environ

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from ripple import evidence, ingest, llm  # noqa: E402
from ripple import graph as g  # noqa: E402
from ripple.store import get_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ripple.app")

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="Ripple Brain", docs_url="/api/docs", redoc_url=None)

_graph = None


def graph():
    """Cached graph. Reloaded after an ingest."""
    global _graph
    if _graph is None:
        _graph = g.load_graph()
    return _graph


def reload_graph():
    global _graph
    _graph = g.load_graph()
    return _graph


class ImpactRequest(BaseModel):
    component_id: str = Field(min_length=1)
    change: str = ""


# --- graph -----------------------------------------------------------------

@app.get("/api/graph")
def api_graph():
    G = graph()
    store = get_store()
    return {
        **g.to_cytoscape(G),
        "meta": {
            "store": store.name,
            "llm": "gemini" if llm.available() else "offline",
            "model": llm.model_name() if llm.available() else None,
            "formula": g.RISK_FORMULA,
        },
    }


# --- impact ----------------------------------------------------------------

@app.post("/api/impact")
def api_impact(req: ImpactRequest):
    try:
        return g.analyse(graph(), req.component_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown component: {req.component_id}")


# --- report ----------------------------------------------------------------

@app.post("/api/report")
def api_report(req: ImpactRequest):
    G = graph()
    if req.component_id not in G:
        raise HTTPException(status_code=404, detail=f"unknown component: {req.component_id}")

    store = get_store()
    pkg = evidence.build_package(G, req.component_id, req.change, store)
    source = llm.report_source(pkg)

    def body():
        try:
            yield from llm.report_stream(pkg)
        except Exception as exc:  # never leave the panel hanging
            log.exception("report stream failed")
            yield f"\n\n_Report unavailable: {exc}_"

    return StreamingResponse(
        body(),
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Ripple-Source": source,
            "X-Ripple-Store": pkg["served_by"],
            "Cache-Control": "no-store",
        },
    )


# --- learn from docs -------------------------------------------------------

MAX_DOC_BYTES = 400_000
ALLOWED_SUFFIXES = (".md", ".txt", ".markdown")


@app.post("/api/ingest")
async def api_ingest(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")

    store = get_store()
    added_components, added_dependencies, dropped = [], [], 0
    extractors, read = set(), 0

    for upload in files:
        name = Path(upload.filename or "dropped.md").name
        if not name.lower().endswith(ALLOWED_SUFFIXES):
            raise HTTPException(status_code=415, detail=f"{name}: only .md and .txt are supported")

        raw = await upload.read()
        if len(raw) > MAX_DOC_BYTES:
            raise HTTPException(status_code=413, detail=f"{name} is too large")
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue

        result = ingest.ingest_document(name, text, graph(), store)
        added_components += result["components"]
        added_dependencies += result["dependencies"]
        dropped += result["dropped"]
        extractors.add(result["extractor"])
        read += 1

    reload_graph()
    return {
        "docs": read,
        "added_components": len(added_components),
        "added_dependencies": len(added_dependencies),
        "dropped": dropped,
        "extractor": "gemini" if "gemini" in extractors else "rules",
        "components": added_components,
        "dependencies": added_dependencies,
    }


# --- source drawer ---------------------------------------------------------

@app.get("/api/source/{item_id}")
def api_source(item_id: str):
    G = graph()
    store = get_store()

    if "__" in item_id:
        source, target = item_id.split("__", 1)
        if not G.has_edge(source, target):
            raise HTTPException(status_code=404, detail=f"unknown edge: {item_id}")
        edge = G.edges[source, target]
        return {
            "kind": "edge",
            "id": item_id,
            "title": f'{G.nodes[source]["name"]} → {G.nodes[target]["name"]}',
            "subtitle": edge["relationship"],
            "confidence": edge["confidence"],
            "owner": G.nodes[source].get("owner") or "",
            "snippets": [edge["evidence"]] if edge["evidence"].get("snippet") else [],
            "served_by": "graph",
        }

    if item_id not in G:
        raise HTTPException(status_code=404, detail=f"unknown component: {item_id}")

    node = G.nodes[item_id]
    snippets = []
    for u, v, data in list(G.in_edges(item_id, data=True)) + list(
        G.out_edges(item_id, data=True)
    ):
        ev = data.get("evidence") or {}
        if ev.get("snippet"):
            snippets.append(
                {
                    "doc": ev["doc"],
                    "snippet": ev["snippet"],
                    "via": f'{G.nodes[u]["name"]} → {G.nodes[v]["name"]}',
                    "confidence": data["confidence"],
                }
            )

    retrieved = store.search(node["name"], 2)
    served_by = getattr(store, "served_by", "local") if retrieved else "graph"

    return {
        "kind": "node",
        "id": item_id,
        "title": node["name"],
        "subtitle": node["type"],
        "owner": node.get("owner") or "",
        "criticality": node.get("criticality"),
        "confidence": "documented",
        "snippets": snippets,
        "retrieved": retrieved,
        "served_by": served_by,
    }


# --- static ----------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
