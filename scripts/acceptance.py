"""Walk the section 11 acceptance checklist against a running server.

    python -m uvicorn app:app --port 8077      # in another shell
    python -X utf8 scripts/acceptance.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8077"

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    return ok


def post(path: str, payload: dict, raw: bool = False):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        return (body, dict(response.headers)) if raw else json.loads(body)


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


DEMO = "Remove support for the legacy token format"


# 1 -------------------------------------------------------------------------
proc = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-q"],
    cwd=ROOT, capture_output=True, text=True,
)
tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-120:]
check("Direction test and cycle test pass", proc.returncode == 0, tail)


# 2 -------------------------------------------------------------------------
impact = post("/api/impact", {"component_id": "auth_api", "change": DEMO})
direct = {a["name"] for a in impact["direct"]}
indirect = {a["name"] for a in impact["indirect"]}
expected_direct = {"Web App", "Mobile App", "Billing Service", "Partner Portal",
                   "Audit Log Service", "Legacy Token Runbook"}
expected_indirect = {"Web App E2E Suite", "Mobile App Test Suite",
                     "Billing Contract Tests", "Reporting Tool"}
upstream = {"User Database", "Session Cache", "Token Service"}
check(
    "Authentication API returns the expected direct and indirect set",
    expected_direct <= direct and expected_indirect <= indirect
    and not (direct | indirect) & upstream,
    f"direct {len(direct)}, indirect {len(indirect)}, no upstream leakage",
)


# 3 -------------------------------------------------------------------------
hops = sorted({a["hop"] for a in impact["affected"]})
paths_ok = all(a["path"][0] == "auth_api" and a["path"][-1] == a["id"]
               and len(a["path"]) - 1 == a["hop"] for a in impact["affected"])
check(
    "Hop-ordered waves are available to the UI",
    hops == list(range(1, max(hops) + 1)) and paths_ok,
    f"hops {hops}, every path starts at the changed node",
)


# 4 -------------------------------------------------------------------------
every_name = {c["name"] for c in json.loads(
    (ROOT / "data" / "components.json").read_text(encoding="utf-8"))}
overlay = ROOT / "data" / "graph_overlay.json"
if overlay.exists():
    every_name |= {c["name"] for c in
                   json.loads(overlay.read_text(encoding="utf-8")).get("components", [])}

leaks, sources = [], []
for component, change in [
    ("auth_api", DEMO),
    ("billing_service", "Switch the invoice stream to a new schema"),
    ("user_db", "Drop the deprecated profile columns"),
]:
    text, headers = post("/api/report",
                         {"component_id": component, "change": change}, raw=True)
    sources.append(headers.get("x-ripple-source") or headers.get("X-Ripple-Source"))
    allowed = {a["name"] for a in
               post("/api/impact", {"component_id": component, "change": change})["affected"]}
    allowed.add(get(f"/api/source/{component}")["title"])
    leaks += [n for n in every_name - allowed if n in text]

check("Report names only components in the evidence package (3 runs)",
      not leaks, f"leaked: {leaks}" if leaks else f"0 leaks across 3 runs, sources {sources}")


# 5 -------------------------------------------------------------------------
report, _ = post("/api/report", {"component_id": "auth_api", "change": DEMO}, raw=True)
unknowns = report.split("## Unknowns")[-1] if "## Unknowns" in report else ""
check("Unknowns calls out the component with no owner and no tests",
      "Notification Service" in unknowns and "owner" in unknowns.lower(),
      "Notification Service named under Unknowns")


# 6 -------------------------------------------------------------------------
sample = ROOT / "data" / "samples" / "search-service.md"
boundary = "----ripple"
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="files"; filename="{sample.name}"\r\n'
    "Content-Type: text/markdown\r\n\r\n"
    + sample.read_text(encoding="utf-8")
    + f"\r\n--{boundary}--\r\n"
).encode()
request = urllib.request.Request(
    BASE + "/api/ingest", data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
with urllib.request.urlopen(request, timeout=60) as response:
    ingested = json.loads(response.read().decode("utf-8"))

cited = all(d["evidence"]["snippet"] for d in ingested["dependencies"])
labelled = all(d["confidence"] == "inferred" for d in ingested["dependencies"])
check("Dropping a sample doc adds nodes and edges, each with a source snippet",
      ingested["added_components"] > 0 and ingested["added_dependencies"] > 0
      and cited and labelled,
      f'+{ingested["added_components"]} components, +{ingested["added_dependencies"]} '
      f'dependencies, all quoted and labelled inferred '
      f'(extractor: {ingested["extractor"]})')


# 7 -------------------------------------------------------------------------
node = get("/api/source/mobile_app")
edge = get("/api/source/auth_api__mobile_app")
meta = get("/api/graph")["meta"]
badge_ok = (meta["store"] == "hydradb") == (node["served_by"] == "hydradb")
check("Source drawer shows snippets; HydraDB badge only when HydraDB served",
      bool(node["snippets"]) and bool(edge["snippets"]) and badge_ok,
      f'node {len(node["snippets"])} snippets, edge {len(edge["snippets"])}, '
      f'store={meta["store"]}, served_by={node["served_by"]}')


# 8 -------------------------------------------------------------------------
from ripple import evidence, llm  # noqa: E402
from ripple import graph as g  # noqa: E402
from ripple.store import LocalStore  # noqa: E402

G = g.load_graph()
package = evidence.build_package(G, "auth_api", DEMO, LocalStore())
cache_file = llm._cache_path("auth_api", DEMO)

original = llm._gemini_report
llm._gemini_report = lambda pkg: (_ for _ in ()).throw(OSError("network down"))
try:
    import os
    os.environ["GEMINI_API_KEY"] = "offline-test"
    offline = "".join(llm.report_stream(package))
finally:
    llm._gemini_report = original
    os.environ.pop("GEMINI_API_KEY", None)

check("Works with no HydraDB key and with no network",
      meta["store"] == "local" and "## Summary" in offline
      and "Notification Service" in offline and cache_file.exists(),
      "store fell back to local; a network failure still produced a full report; "
      f"cache at data/cache/{cache_file.name}")


# 9 -------------------------------------------------------------------------
gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
secrets = []
for path in ROOT.rglob("*"):
    if path.is_dir() or any(p in path.parts for p in
                            ("__pycache__", ".git", "cache", "uploads", "scripts")):
        continue
    if path.suffix not in {".py", ".json", ".md", ".html", ".txt", ".example"}:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for pattern in (r"\bAIza[0-9A-Za-z_\-]{20,}", r"\bsk-[A-Za-z0-9]{20,}",
                    r"\bhdb_[A-Za-z0-9]{16,}", r"(?i)api_key\s*=\s*[\"'][^\"'\s]{12,}"):
        hit = re.search(pattern, text)
        if hit:
            secrets.append(f"{path.name}: {hit.group()[:12]}...")
env_file = ROOT / ".env"
env_empty = (not env_file.exists()) or all(
    not line.split("=", 1)[1].strip()
    for line in env_file.read_text(encoding="utf-8").splitlines()
    if "=" in line and not line.lstrip().startswith("#")
)
check(".env is gitignored and no keys are in the repo",
      ".env" in gitignore and not secrets,
      f"secrets found: {secrets}" if secrets else
      ".gitignore covers .env, no key-like strings in tracked files"
      + ("" if env_empty else "; .env holds values (gitignored)"))


# 10 ------------------------------------------------------------------------
start = time.time()
post("/api/impact", {"component_id": "auth_api", "change": DEMO})
post("/api/report", {"component_id": "auth_api", "change": DEMO}, raw=True)
get("/api/source/mobile_app")
get("/api/graph")
elapsed = time.time() - start
check("Demo runs end to end in under 2 minutes",
      elapsed < 120, f"server round trips for the whole script: {elapsed:.2f}s")


# ---------------------------------------------------------------------------
print()
for ok, name, detail in results:
    print(f'[{"PASS" if ok else "FAIL"}] {name}')
    if detail:
        print(f"         {detail}")
passed = sum(1 for ok, _, _ in results if ok)
print(f"\n{passed}/{len(results)} acceptance items pass")
sys.exit(0 if passed == len(results) else 1)
