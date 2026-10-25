"""Try the graph in a terminal with your real keys:  python scripts/run_cli.py"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.deps import get_services  # noqa: E402
from src.core.workflow import run_turn  # noqa: E402
from src.workflow.langgraph_workflow import build_graph  # noqa: E402

graph, tid = build_graph(), str(uuid.uuid4())
print("Providers:", get_services().status(), "\nType a request (Ctrl-C to quit).")
while True:
    try:
        text = input("\n> ").strip()
    except (KeyboardInterrupt, EOFError):
        break
    if not text:
        continue
    st = run_turn(graph, text, tid, on_update=lambda n, u: print(f"  ✓ {n}"))
    f = st["final"]
    print("\n" + f["reply"])
    for k in ("blog", "linkedin"):
        if f.get(k):
            print(f"\n--- {k} ---\n{f[k].get('markdown') or f[k].get('text')}")
    print("\nUsage:", f["usage"])
