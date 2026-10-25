"""Safety net + telemetry wrapper for every graph node."""
import functools
import logging
import time

from src.core.deps import get_services

log = logging.getLogger("nodes")


def resilient(name, fallback):
    """Wrap a node: never crash the graph, degrade via `fallback(state, exc)`, record trace + cost."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state):
            svc = get_services()
            t0, c0 = time.time(), svc.tracker.total
            try:
                out = dict(fn(state))
            except Exception as exc:  # noqa: BLE001
                log.exception("node %s failed", name)
                out = dict(fallback(state, exc))
                out["errors"] = [*out.get("errors", []), f"{name}: {type(exc).__name__}: {exc}"]
            out["trace"] = [{"node": name, "seconds": round(time.time() - t0, 3),
                             "cost_usd": round(svc.tracker.total - c0, 5)}]
            return out
        return wrapper
    return deco
