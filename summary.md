# Ripple Brain — Build Plan

> A brain for your system's dependencies. Ask "what breaks if I change this?" and get a blast-radius graph plus an evidence-backed report.

**For the agent:** Read this whole file first. Build in the order given in section 9. Keep the product minimal and the code simple. Do not add features that are not listed here. After each milestone, run it and confirm it works before moving on.

---

## 1. Product in one paragraph

Ripple Brain turns scattered engineering knowledge (architecture notes, API specs, runbooks, READMEs) into a dependency graph. The user picks a component and describes a change. The app traces direct and indirect dependents, animates the "ripple" across the graph, and produces a short report: what is affected, why (with the exact path), what to check, and what is unknown. Every claim links back to a source document.

**Pitch line:** *A company brain that answers "what breaks if we change this", not just "what do we know".*

## 2. Principles

1. **Graph first, AI second.** Traversal is deterministic Python. The LLM only explains evidence it is given.
2. **Never invent.** The LLM may not name a dependency that is not in the evidence package. Anything else goes under "Possible risks (not in graph)" or "Unknowns".
3. **Show the evidence.** Every finding shows its path and its source snippet.
4. **Minimal and premium.** Few elements, lots of whitespace, one accent color, smooth motion. No dashboards full of widgets.
5. **Always demoable.** Everything works offline from local data. HydraDB and the LLM are layered on top with fallbacks.

## 3. Features (MVP)

| # | Feature | Notes |
|---|---|---|
| 1 | **Ask a change** | Component dropdown (searchable) + one-line change description. |
| 2 | **Ripple graph** | Interactive graph. Affected nodes light up in waves by hop distance (hop 1, then 2, then 3). |
| 3 | **Impact report** | Streamed. Sections: Summary, Directly affected, Indirectly affected, Checks to run, Unknowns. |
| 4 | **Evidence paths** | Each affected item shows its path, e.g. `Auth API → Mobile App → Mobile Tests`. |
| 5 | **Confidence tags** | Each edge/finding is tagged `Documented` (from a source doc), `Inferred` (LLM suggestion), or `Unknown`. |
| 6 | **Risk score** | Deterministic: based on criticality and hop distance. Shown as Low / Medium / High, with the formula visible on hover. |
| 7 | **Learn from docs** | Drag in `.md`/`.txt` docs. The LLM extracts components and relationships, each with a source citation, and merges them into the graph. New nodes pulse once. |
| 8 | **Source drawer** | Click any node or edge to see its source snippet(s) and owner. |
| 9 | **Copy as ticket** | One button: copies the report as a Markdown change-review checklist. |

**Explicitly out of scope:** auth, user accounts, real repo scanning, deployments, multi-tenant UI, settings pages.

## 4. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.10+, **FastAPI** | Simple, async, streaming responses |
| Graph | **NetworkX** | Traversal and path finding |
| Frontend | Single `index.html`, **Tailwind (CDN)**, **Cytoscape.js** (CDN), vanilla JS | Fast to build, looks far more premium than default Streamlit |
| LLM | **Gemini** via `google-genai` (model name from env `GEMINI_MODEL`) | Fits Antigravity; swap if needed |
| Knowledge store | **HydraDB** (`hydradb-sdk`) behind an adapter | See section 7 |
| Local fallback | JSON files in `data/` | Demo never depends on the network |

If the frontend takes too long, fallback is Streamlit + PyVis. Do not switch unless blocked.

## 5. Project structure

```text
ripple-brain/
├── app.py                    # FastAPI app + static file serving
├── static/
│   └── index.html            # the whole UI
├── data/
│   ├── components.json       # nodes
│   ├── dependencies.json     # edges (with source refs)
│   └── docs/                 # 6–8 short sample docs (md)
├── ripple/
│   ├── graph.py              # build graph, traversal, paths, risk score
│   ├── evidence.py           # build the evidence package for the LLM
│   ├── llm.py                # Gemini calls: report + extraction
│   ├── ingest.py             # doc -> entities/edges -> merge
│   └── store.py              # Store interface + LocalStore + HydraStore
├── tests/
│   └── test_graph.py
├── .env.example
├── .gitignore                # includes .env
├── requirements.txt
└── README.md
```

## 6. Data model

**Edge direction convention (document this in `graph.py`):** an edge points from the **dependency** to its **consumer**. `auth_api → mobile_app` means "mobile_app depends on auth_api". Impact = everything reachable from the changed node. If source data stores it the other way, reverse it once at load time, never elsewhere.

```json
// components.json
{ "id": "auth_api", "name": "Authentication API", "type": "api",
  "owner": "Identity Team", "criticality": "high" }

// dependencies.json
{ "source": "auth_api", "target": "mobile_app", "relationship": "CALLS",
  "confidence": "documented",
  "evidence": { "doc": "auth-overview.md", "snippet": "The mobile app calls /token on every launch." } }
```

Types: `api, service, database, app, test, doc, team`. Relationships: `CALLS, DEPENDS_ON, READS_FROM, TESTED_BY, DOCUMENTED_IN`. Criticality: `low, medium, high`.

**Sample graph:** about 15 nodes, at least one path 3 hops deep, at least one node with no consumers, and one deliberate gap (a component with no owner and no tests) so the "Unknowns" section has something real to say.

Demo scenario: **Authentication API — "Remove support for the legacy token format."**
Affected: Web App, Mobile App, Billing Service, Reporting Tool, plus their tests, plus the runbook that references legacy tokens.

## 7. HydraDB integration

HydraDB is a managed retrieval layer (vector + full-text + knowledge graph) with a Python SDK.

**Role in this project:**
- Store the ingested source documents so evidence snippets can be retrieved by meaning (e.g. "which docs mention legacy tokens?").
- Power the Source drawer and the "Learn from docs" step.
- **NetworkX remains the source of truth for traversal.** HydraDB is not on the critical path.

**Setup (do this at the start; budget 20 minutes):**
1. Create an account at hydradb.com and generate an API key from the dashboard.
2. `pip install hydradb-sdk` (the import name is `hydra_db`, not `hydradb`).
3. Put the key in `.env` as `HYDRA_DB_API_KEY`. Never commit it.
4. Smoke test:
   ```python
   import os
   from hydra_db import HydraDB
   client = HydraDB(token=os.environ["HYDRA_DB_API_KEY"])
   # default base URL is https://api.hydradb.com
   ```
5. Read the current docs at **https://docs.hydradb.com** and confirm the exact calls for: creating a tenant/database, ingesting text or files, and querying. **Do not guess method names.** The SDK changed between versions (older releases used tenant-based `recall`; the 2.x releases use `query` with a `database` argument), so pin the version you tested in `requirements.txt`.
6. Create one tenant/database named `ripple-brain`.

**Adapter rule:** all HydraDB calls live in `ripple/store.py` behind this interface:

```python
class Store:
    def add_document(self, doc_id: str, text: str) -> None: ...
    def search(self, query: str, k: int = 3) -> list[dict]: ...   # [{doc, snippet}]
```

`LocalStore` (keyword search over `data/docs/`) is the default. `HydraStore` is enabled when `HYDRA_DB_API_KEY` is set. If any HydraDB call fails or takes over 4 seconds, log it and fall back to `LocalStore`. The UI shows a small "Powered by HydraDB" badge only when HydraStore actually served the result.

**If setup is not working after 25 minutes, ship with LocalStore and revisit later.** Do not let it block the demo.

## 8. API (FastAPI)

| Route | Purpose |
|---|---|
| `GET /api/graph` | Nodes and edges for the UI |
| `POST /api/impact` | `{component_id, change}` → affected nodes, hops, paths, risk (JSON, instant, no LLM) |
| `POST /api/report` | Same input → streamed LLM report built from the evidence package |
| `POST /api/ingest` | Upload docs → extracted nodes/edges merged into graph |
| `GET /api/source/{id}` | Snippets for a node or edge |

Impact is computed first and returned immediately so the graph animates while the report streams.

**Risk score (deterministic):** `score = criticality_weight(target) / hop_distance`, with weights low=1, medium=2, high=3. Overall level is the max across affected nodes: ≥2.5 High, ≥1.2 Medium, else Low. Show the formula on hover.

### LLM prompts

**Report prompt (system):**
> You are a change-impact analyst. You receive a proposed change and an evidence package of components, dependency paths, owners, and source snippets. Use ONLY the components and paths in the evidence package as facts. Never add a dependency that is not listed. Return sections: Summary, Directly affected, Indirectly affected, Checks to run, Unknowns. Label anything you infer as "Possible risk (not in graph)". Reference each finding by its path. Be concise: under 250 words. If information such as owner or tests is missing, say so under Unknowns. Describe results as potential impact, not certainty.

**Extraction prompt (ingest):** return strict JSON `{components: [...], dependencies: [{source, target, relationship, snippet}]}`. Every dependency must include the exact snippet from the document that supports it. Use the edge direction convention from section 6. Drop any edge without a supporting snippet. Reuse existing component ids when the name matches.

## 9. Build order (4 hours)

| Time | Task | Milestone (verify before moving on) |
|---|---|---|
| 0:00–0:25 | Project skeleton, sample data, `.env.example`, HydraDB account + smoke test (in parallel) | `data/` loads; HydraDB client connects or fallback decided |
| 0:25–1:00 | `graph.py` + `tests/test_graph.py` | **Direction test passes first**: changing `auth_api` returns its consumers, not its dependencies. Cycles don't hang. Leaf node returns empty. |
| 1:00–1:30 | `evidence.py`, `llm.py`, `/api/impact`, `/api/report` | Curl a request, get a grounded report with no invented components |
| 1:30–2:30 | `index.html`: layout, graph, ripple animation, streamed report, evidence paths, risk badge | Full flow works in the browser |
| 2:30–3:10 | `ingest.py`, `store.py` (Local + Hydra), "Learn from docs" UI, Source drawer | Dropping a doc adds nodes with citations |
| 3:10–3:40 | Copy-as-ticket, empty/error states, cache the demo report, polish | Demo runs 3 times in a row without surprises |
| 3:40–4:00 | README, reset-to-known-state script, rehearse 2-minute demo | Repeatable demo |

## 10. UI spec: minimal and premium

**Layout:** one screen. Left 62%: graph canvas. Right 38%: report panel. A slim top bar with the logo (text only: **Ripple Brain**) and the two inputs.

**Look:**
- Dark theme by default: background `#0B0D10`, surface `#12151A`, hairline borders `rgba(255,255,255,0.06)`, text `#E8EAED`, muted `#8A8F98`.
- **One accent:** electric violet `#7C5CFF`. Used only for the changed node, the ripple, and the primary button. Risk uses a small amber/red dot, nothing else colorful.
- Font: **Inter** (Google Fonts), tight tracking on headings. Sizes: 13px body, 11px labels in uppercase with letter-spacing.
- Rounded corners (12px), no heavy shadows, subtle 1px borders, generous padding (24px+).
- Graph nodes: small circles with labels below, thin grey edges. Unaffected nodes dim to 25% opacity when an analysis is active.
- **Ripple animation:** the changed node pulses with an expanding ring; hop 1 nodes light up at ~250ms, hop 2 at ~500ms, hop 3 at ~750ms, edges draw in along the path. Ease-out, never bouncy.
- Report text streams in with a soft fade. Section titles are small uppercase labels, not big headers.
- Each finding shows its path as a quiet breadcrumb, with a small `Documented` / `Inferred` pill.
- Source drawer slides in from the right, 200ms.
- Empty state: a centered line, "Pick a component and describe a change to see the ripple." No illustrations.
- No emojis in the UI.

**Inputs:** a searchable select for the component and a single text input for the change (placeholder: *Remove support for the legacy token format*). One primary button: **Trace impact**. Pressing Enter submits.

## 11. Acceptance checklist

- [ ] Direction test and cycle test pass
- [ ] Selecting `Authentication API` + a change returns the expected direct and indirect set
- [ ] Graph highlights affected nodes in hop-ordered waves
- [ ] Report mentions only components present in the evidence package (spot-check 3 runs)
- [ ] Unknowns section calls out the component with no owner/tests
- [ ] Dropping a sample doc adds nodes/edges, each with a source snippet
- [ ] Source drawer shows snippets; HydraDB badge appears only when HydraDB served the result
- [ ] App still works with no HydraDB key and with no network (cached report)
- [ ] `.env` is gitignored; no keys in the repo
- [ ] Demo runs end to end in under 2 minutes

## 12. Demo script (2 minutes)

1. "A change to one component can quietly break many others. Ripple Brain shows the blast radius before you ship."
2. Show the graph. Select **Authentication API**, type *Remove support for the legacy token format*, press Enter.
3. Watch the ripple spread. Point out the mobile app and its tests.
4. Read one finding and click its source: "Every claim traces back to a document."
5. Drag in a new doc. Show new nodes appear with citations.
6. Point at Unknowns: "It tells you what it doesn't know."
7. Click **Copy as ticket**. Close: "Not just what we know, but what breaks."

## 13. Safety and limits (say these out loud in the demo)

- Results are *potential impact*, not guarantees. A missing edge does not mean no dependency.
- The tool never changes code or deploys anything.
- Inferred items are always labeled and kept separate from documented dependencies.

## 14. Rules for the agent

- Keep files small and functions short. No frameworks beyond those listed.
- No new dependencies without a reason; list any you add in the README.
- Never hardcode secrets. Read from `.env`.
- Write the direction test before the traversal code.
- If a step is blocked for more than 15 minutes, take the fallback and move on.
- After each milestone, run the app or tests and report what passed.