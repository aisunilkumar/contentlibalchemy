"""Session utilities: save/restore a conversation, apply hand edits, undo/restore versions.
All changes go through the LangGraph checkpointer (update_state), so there is one source of truth."""
import base64
import copy
import json

from src.core.history import current_version, entries, make_entry, undo_target
from src.utils.quality_validation import rescore_deterministic
from src.utils.seo import slugify
from src.utils.messages import to_chat

SCHEMA = 1
MAX_SNAPSHOT_BYTES = 8_000_000
_KEEP = ("topic", "turn", "last_artifact", "brand_profile", "research", "blog", "linkedin", "image", "history")


def cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def get_values(graph, thread_id: str) -> dict:
    return dict(graph.get_state(cfg(thread_id)).values or {})


def _commit(graph, thread_id: str, update: dict) -> None:
    graph.update_state(cfg(thread_id), update, as_node="strategist")


# ---------------- snapshot / restore ----------------
def _enc(obj):
    if isinstance(obj, bytes):
        return {"__b64__": base64.b64encode(obj).decode()}
    if isinstance(obj, dict):
        return {k: _enc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_enc(v) for v in obj]
    return obj


def _dec(obj):
    if isinstance(obj, dict):
        if set(obj) == {"__b64__"}:
            return base64.b64decode(obj["__b64__"])
        return {k: _dec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec(v) for v in obj]
    return obj


def snapshot(values: dict) -> dict:
    snap = {"schema": SCHEMA, "messages": to_chat(values.get("messages", []))}
    snap.update({k: _enc(copy.deepcopy(values[k])) for k in _KEEP if k in values})
    return snap


def snapshot_json(values: dict) -> str:
    return json.dumps(snapshot(values), ensure_ascii=False)


def parse_snapshot(raw: str | bytes) -> dict:
    """Validate an imported file. Raises ValueError with a readable message. Never executes anything."""
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("file is too large to be a ContentAlchemy session")
    try:
        snap = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise ValueError("not a valid JSON file") from e
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        raise ValueError("not a ContentAlchemy session file (wrong or missing schema)")
    for k in ("research", "blog", "linkedin", "image", "brand_profile"):
        if k in snap and not isinstance(snap[k], dict):
            raise ValueError(f"'{k}' has the wrong type")
    if not isinstance(snap.get("messages", []), list) or not isinstance(snap.get("history", []), list):
        raise ValueError("'messages' and 'history' must be lists")
    return snap


def restore_snapshot(graph, thread_id: str, snap: dict) -> None:
    update = {k: _dec(snap[k]) for k in _KEEP if k in snap}
    update["messages"] = [(m.get("role", "user"), str(m.get("content", ""))) for m in snap.get("messages", [])
                          if isinstance(m, dict)]
    update["final"] = {}
    _commit(graph, thread_id, update)


# ---------------- hand edits ----------------
def apply_edit(graph, thread_id: str, kind: str, changes: dict, reason: str = "Edited by you") -> dict:
    """Persist the user's own edits so later refinements start from what they actually have."""
    values = get_values(graph, thread_id)
    art = {**(values.get(kind) or {}), **changes}
    if kind == "blog":
        art["slug"] = slugify(art.get("title", ""))
    if kind == "linkedin":
        from src.utils.content_optimization import extract_hashtags
        art["hashtags"] = extract_hashtags(art.get("text", ""))
    if kind in ("blog", "linkedin"):
        art["quality"] = rescore_deterministic(kind, art, values)
    hist = values.get("history") or []
    entry = make_entry(kind, art, hist, values.get("turn", 0), reason, (art.get("quality") or {}).get("score"))
    _commit(graph, thread_id, {kind: art, "history": [entry], "last_artifact": kind})
    return art


# ---------------- undo / restore ----------------
def undo(graph, thread_id: str, kind: str) -> str | None:
    """Returns a message, or None if there is nothing to undo."""
    values = get_values(graph, thread_id)
    hist = values.get("history") or []
    prev = undo_target(hist, kind)
    if prev is None:
        return None
    return restore_version(graph, thread_id, kind, prev["version"])


def restore_version(graph, thread_id: str, kind: str, version: int) -> str | None:
    values = get_values(graph, thread_id)
    hist = values.get("history") or []
    target = next((h for h in entries(hist, kind) if h["version"] == version), None)
    if target is None:
        return None
    entry = make_entry(kind, target["artifact"], hist, values.get("turn", 0), f"Restored version {version}",
                       target.get("score"), restored_from=version)
    _commit(graph, thread_id, {kind: copy.deepcopy(target["artifact"]), "history": [entry], "last_artifact": kind})
    return f"Restored {kind} to version {version}."


def version_key(values: dict, kind: str) -> str:
    """Changes whenever an artifact changes, so UI widgets re-initialize with the new text."""
    cur = current_version(values.get("history"), kind)
    return f"{kind}-{len(entries(values.get('history'), kind))}-{(cur or {}).get('version', 0)}"
