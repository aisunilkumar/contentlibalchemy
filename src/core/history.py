"""Version history for artifacts: append-only log + an undo stack derived from it."""
import copy

KINDS = ("blog", "linkedin", "image")
MAX_PER_KIND = 12


def entries(history, kind):
    return [h for h in (history or []) if h.get("kind") == kind]


def next_version(history, kind) -> int:
    return max([h["version"] for h in entries(history, kind)] or [0]) + 1


def make_entry(kind, artifact, history, turn, reason, score=None, restored_from=None) -> dict:
    return {"kind": kind, "version": next_version(history, kind), "turn": turn, "reason": reason,
            "score": score, "restored_from": restored_from, "artifact": copy.deepcopy(artifact)}


def append_history(old, new):
    """LangGraph reducer: append, keep the newest MAX_PER_KIND entries per kind."""
    merged = [*(old or []), *(new or [])]
    keep = []
    for kind in {h.get("kind") for h in merged}:
        keep += entries(merged, kind)[-MAX_PER_KIND:]
    return sorted(keep, key=lambda h: merged.index(h))


def current_stack(es):
    """Replay the log: normal entries push, restores pop back to the version they restored."""
    stack = []
    for e in es:
        r = e.get("restored_from")
        if r is not None:
            idx = next((i for i, x in enumerate(stack) if x["version"] == r), None)
            if idx is not None:
                stack = stack[:idx + 1]
                continue
        stack.append(e)
    return stack


def undo_target(history, kind):
    """The version to go back to, or None. Repeated undo keeps walking back (never toggles)."""
    stack = current_stack(entries(history, kind))
    return stack[-2] if len(stack) >= 2 else None


def current_version(history, kind):
    stack = current_stack(entries(history, kind))
    return stack[-1] if stack else None
