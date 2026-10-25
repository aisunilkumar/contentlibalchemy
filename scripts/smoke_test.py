"""Live check of your keys through the abstraction layer (costs a few cents)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.deps import get_services  # noqa: E402

svc = get_services()
print("status:", svc.status())
print("llm  :", svc.complete([{"role": "user", "content": "say ok"}], tier="cheap", max_tokens=5).text)
try:
    r = svc.search("one headline about AI today")
    print("search:", r.provider, len(r.sources), "sources")
except Exception as e:  # noqa: BLE001
    print("search FAILED:", e)
r = svc.image("a simple blue circle on white")
print("image:", r.provider, "(prompt-only fallback)" if not (r.url or r.data) else "ok")
print("spend:", svc.stats())
