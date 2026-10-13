"""Put the demo back to a known state.

Removes everything the app learns at runtime: the ingested graph overlay, the
uploaded copies of dropped docs, and the cached reports. The sample data in
data/components.json, data/dependencies.json and data/docs/ is never touched.

    python reset_demo.py            # reset
    python reset_demo.py --check    # just report what is there
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

TARGETS = [
    DATA / "graph_overlay.json",
    DATA / "uploads",
    DATA / "cache",
]


def describe() -> None:
    graph_json = DATA / "components.json"
    print("sample data:", "present" if graph_json.exists() else "MISSING")
    for target in TARGETS:
        if not target.exists():
            print(f"  {target.name}: clean")
        elif target.is_dir():
            print(f"  {target.name}/: {len(list(target.iterdir()))} file(s)")
        else:
            print(f"  {target.name}: present")


DEMO_COMPONENT = "auth_api"
DEMO_CHANGE = "Remove support for the legacy token format"


def warm() -> None:
    """Pre-build the demo report so the rehearsal never waits on the network."""
    sys.path.insert(0, str(ROOT))
    from ripple import evidence, llm
    from ripple import graph as g
    from ripple.store import get_store

    package = evidence.build_package(
        g.load_graph(), DEMO_COMPONENT, DEMO_CHANGE, get_store()
    )
    text = "".join(llm.report_stream(package))
    print(f"cached the demo report ({len(text)} chars)")


def reset(do_warm: bool = True) -> None:
    for target in TARGETS:
        if target.is_dir():
            shutil.rmtree(target)
            print(f"removed {target.name}/")
        elif target.exists():
            target.unlink()
            print(f"removed {target.name}")
    if do_warm:
        warm()
    print("demo reset. Restart the server so it reloads the graph.")


if __name__ == "__main__":
    if "--check" in sys.argv:
        describe()
    else:
        reset(do_warm="--no-warm" not in sys.argv)
