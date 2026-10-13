# Ripple Brain

A brain for your system's dependencies. Pick a component, describe a change, and
get a blast-radius graph plus an evidence-backed report.

Traversal is deterministic Python over a NetworkX graph. The LLM only explains
evidence it is handed, and is told it may not name a component that is not in
that evidence package. Everything works offline from `data/`.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # optional: both keys can stay empty
python -m uvicorn app:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. Pick **Authentication API**, type
*Remove support for the legacy token format*, press Enter.

On Windows, run Python with `-X utf8` if your console cannot print the arrows in
the report: `python -X utf8 -m uvicorn app:app --port 8000`.

## The two-minute demo

```bash
python reset_demo.py            # clean slate, demo report pre-cached
python -m uvicorn app:app --port 8000
```

1. "A change to one component can quietly break many others. Ripple Brain shows
   the blast radius before you ship."
2. Select **Authentication API**, type *Remove support for the legacy token
   format*, press Enter.
3. Watch the ripple spread hop by hop. Point out the Mobile App and its tests.
4. Read a finding, click its node: every claim traces back to a document.
5. Drag `data/samples/search-service.md` onto the canvas. New nodes pulse in,
   each edge carrying the sentence that supports it, tagged `Inferred`.
6. Point at Unknowns: the Notification Service has no owner and no tests, and
   the report says so.
7. Click **Copy as ticket**.

Say the limits out loud: potential impact, not guarantees; the tool never
changes code or deploys; inferred items stay separate from documented ones.

## Keys are optional

| Missing | What happens |
|---|---|
| `GEMINI_API_KEY` | The report is assembled deterministically from the evidence package, and doc extraction falls back to a rule-based reader. Both still cite sources. |
| `HYDRA_DB_API_KEY` | `LocalStore` serves retrieval with keyword search over `data/docs/`. |
| No network at all | Cached reports in `data/cache/` are served, and everything else is local. |

Keys live in `.env`, which is gitignored. Nothing is hardcoded.

## Edge direction

An edge points from the **dependency** to its **consumer**:

```
auth_api --> mobile_app        means "mobile_app depends on auth_api"
```

So the impact of a change is everything *reachable from* the changed node, and a
component's own dependencies are never in its blast radius. This is documented
at the top of `ripple/graph.py`, enforced by `tests/test_graph.py`, and applied
once at load time in `_add_edge` — nowhere else.

## Risk score

```
score = criticality_weight(target) / hop_distance      low 1, medium 2, high 3
```

The overall level is the highest score across affected components: `>= 2.5`
High, `>= 1.2` Medium, else Low. The formula is on hover over the risk badge.

## Layout

```text
app.py                  FastAPI app and static serving
reset_demo.py           put the demo back to a known state
static/index.html       the whole UI
data/components.json    17 components
data/dependencies.json  17 dependencies, each with a source snippet
data/docs/              7 sample documents
data/samples/           a doc to drop in during the demo
ripple/graph.py         graph, traversal, paths, risk
ripple/evidence.py      the evidence package handed to the LLM
ripple/llm.py           Gemini report and extraction, plus offline fallbacks
ripple/ingest.py        doc -> components/dependencies -> merge
ripple/store.py         Store interface, LocalStore, HydraStore
tests/test_graph.py     direction, cycle, leaf, risk and gap tests
```

Runtime state (`data/graph_overlay.json`, `data/uploads/`, `data/cache/`) is
generated, gitignored, and cleared by `python reset_demo.py`.

## API

| Route | Purpose |
|---|---|
| `GET /api/graph` | Nodes and edges, plus which backends are live |
| `POST /api/impact` | `{component_id, change}` to affected nodes, hops, paths, risk. No LLM, instant |
| `POST /api/report` | Same input, streamed report built from the evidence package |
| `POST /api/ingest` | Upload `.md`/`.txt`, merge extracted nodes and edges |
| `GET /api/source/{id}` | Snippets for a node, or for an edge as `source__target` |

Impact returns first so the graph animates while the report streams.

## HydraDB

HydraDB stores the source documents so evidence can be retrieved by meaning.
NetworkX stays the source of truth for traversal — HydraDB is never on the
critical path. Every HydraDB call lives in `ripple/store.py`.

Tested against **`hydradb-sdk==2.1.7`** (import name `hydra_db`), using
`client.databases.create` / `.status`, `client.context.ingest` and
`client.query`. To enable it, put a key in `.env` as `HYDRA_DB_API_KEY`; the
database (default `ripple-brain`) is created on first use.

If a call fails or takes more than 4 seconds, it is logged and `LocalStore`
serves the request instead. The "Powered by HydraDB" badge and the drawer footer
appear only when HydraDB actually served the result.

## Tests

```bash
python -m pytest tests/ -q
```

The direction test was written before the traversal code. The suite also covers
the real two-way edge between Billing and Notification, a tight `a -> b -> c -> a`
cycle, a self loop, leaf nodes, unknown ids, the risk thresholds, and the
deliberate gap (`Notification Service` has no owner and no tests, so the
Unknowns section has something real to say).

## Limits

- Results are *potential impact*, not guarantees. A missing edge does not mean
  there is no dependency.
- The tool never changes code and never deploys anything.
- Anything the LLM or the rule-based reader infers is labelled `Inferred` and
  kept separate from `Documented` dependencies.

## Dependencies

`fastapi`, `uvicorn`, `networkx`, `python-dotenv`, `python-multipart` (form
uploads), `google-genai`, `hydradb-sdk`, `pytest`. Tailwind, Cytoscape.js and
Inter load from CDNs in `static/index.html`. Versions are pinned in
`requirements.txt` to what was tested.
