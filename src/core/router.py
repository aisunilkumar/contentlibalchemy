"""Routing logic: Route schema, heuristic fallback, plan resolution, and graph edge functions.

The LLM only classifies intent/topic. The *plan* is derived deterministically, so a model
slip can never produce an invalid agent sequence.
"""
import copy
import re
from typing import Literal

from pydantic import BaseModel

from src.core.config import settings
from src.core.history import current_version, make_entry, undo_target

Intent = Literal["research", "blog", "linkedin", "image", "series", "refine", "undo", "chat"]
ARTIFACTS = ("blog", "linkedin", "image")
CONTENT_INTENTS = {"research", "blog", "linkedin", "image", "series"}
PLANS = {
    "research": ["research"],
    "blog": ["research", "blog"],
    "linkedin": ["research", "linkedin"],
    "image": ["image"],
    "series": ["research", "blog", "linkedin", "image"],
    "chat": [],
}
ROUTABLE = ["research", "blog", "linkedin", "image", "strategist"]


class Route(BaseModel):
    intent: Intent
    topic: str = ""
    is_followup: bool = False
    refine_target: Literal["blog", "linkedin", "image", "none"] = "none"
    instruction: str = ""


def has_artifact(state: dict, kind: str) -> bool:
    a = state.get(kind) or {}
    if kind == "blog":
        return bool(a.get("body"))
    if kind == "linkedin":
        return bool(a.get("text"))
    return bool(a.get("prompt") or a.get("url") or a.get("data") or a.get("svg"))


def pick_target(requested: str | None, state: dict) -> str | None:
    """Which artifact does 'it' mean? Explicit wins; else the view the user is on; else the last one touched."""
    if requested in ARTIFACTS:  # explicit: never silently switch to another artifact
        return requested if has_artifact(state, requested) else None
    for cand in (state.get("focus"), state.get("last_artifact")):
        if cand in ARTIFACTS and has_artifact(state, cand):
            return cand
    return next((k for k in ARTIFACTS if has_artifact(state, k)), None)


# ---------- heuristic fallback (used when every LLM provider is down) ----------
_REFINE = re.compile(r"\b(shorter|longer|rewrite|rephrase|tone|casual|formal|punchier|tweak|revise|brighter|"
                     r"different|try again|another version|add a|remove|change|make it|make the|more \w+|less \w+)\b", re.I)
_UNDO = re.compile(r"\b(undo|revert|go back|roll ?back|previous version|restore the previous)\b", re.I)


def heuristic_route(text: str, state: dict) -> Route:
    t = text.lower()
    topic = ""
    m = re.search(r"\b(?:about|on|for|regarding|around)\s+(.+?)[.?!]*$", text, re.I)
    if m:
        topic = m.group(1).strip()
    mentioned = next((k for k in ARTIFACTS if re.search(rf"\b{k}\b|\b{'article' if k == 'blog' else k}\b", t)), "none")
    if _UNDO.search(t):
        return Route(intent="undo", topic=state.get("topic", ""), is_followup=True, refine_target=mentioned)
    kind = None
    if re.search(r"\b(series|campaign|everything|all formats)\b", t):
        kind = "series"
    elif "linkedin" in t:
        kind = "linkedin"
    elif re.search(r"\b(blog|article|seo post)\b", t):
        kind = "blog"
    elif re.search(r"\b(image|picture|visual|illustration|graphic|banner|header)\b", t):
        kind = "image"
    elif re.search(r"\b(research|report|analy[sz]e|analysis|brief)\b", t):
        kind = "research"
    have_any = any(has_artifact(state, k) for k in ARTIFACTS)
    refine_like = bool(_REFINE.search(t)) and have_any
    if refine_like and (kind is None or (not topic and kind in ("blog", "linkedin", "image"))):
        return Route(intent="refine", topic=state.get("topic", ""), is_followup=True,
                     refine_target=mentioned, instruction=text)
    if kind:
        return Route(intent=kind, topic=topic or state.get("topic", ""), is_followup=not topic)
    return Route(intent="chat", topic=state.get("topic", ""), is_followup=True)


# ---------- plan resolution ----------
def _same_topic(a: str, b: str) -> bool:
    n = lambda s: re.sub(r"\W+", " ", (s or "").lower()).strip()  # noqa: E731
    return bool(n(a)) and n(a) == n(b)


def _chat(note: str, **extra) -> dict:
    return {"intent": "chat", "plan": [], "step": 0, "refine_target": "", "instruction": "", "note": note, **extra}


def resolve_route(route: Route, state: dict) -> dict:
    """Route (+ current state) -> state update. Pure function: easy to unit test."""
    intent = route.intent
    topic = (route.topic or "").strip() or state.get("topic", "")
    req = route.refine_target if route.refine_target in ARTIFACTS else None
    turn = {"turn": state.get("turn", 0) + 1}

    if intent in ("refine", "undo"):
        target = pick_target(req, state)
        if target is None:
            if req:
                return {**_chat(f"There is no {req} yet. Ask me to create one first, e.g. 'Write an SEO blog on <topic>'."), **turn}
            return {**_chat("There's nothing to change yet. Tell me a topic and format, e.g. 'Write an SEO blog on remote work'."), **turn}
        if intent == "refine":
            return {"intent": "refine", "topic": topic, "plan": [target], "step": 0, "refine_target": target,
                    "instruction": route.instruction or "Improve this draft.", "note": "", "last_artifact": target, **turn}
        prev = undo_target(state.get("history"), target)
        if prev is None:
            return {**_chat(f"Nothing further to undo for the {target}: there is no earlier version."), **turn}
        cur = current_version(state.get("history"), target)
        entry = make_entry(target, prev["artifact"], state.get("history"), state.get("turn", 0) + 1,
                           f"Restored version {prev['version']}", prev.get("score"), restored_from=prev["version"])
        return {"intent": "undo", "topic": topic, "plan": [], "step": 0, "refine_target": target, "instruction": "",
                "note": f"Restored your {target} to version {prev['version']} (undid: {cur['reason'] if cur else 'last change'}).",
                target: copy.deepcopy(prev["artifact"]), "history": [entry], "last_artifact": target, **turn}

    reset: dict = {}
    note = ""
    if intent in CONTENT_INTENTS:
        if not topic:
            return {**_chat("What topic should I cover? For example: 'Write a LinkedIn post about cloud cost optimization'."), **turn}
        plan = list(PLANS[intent])
        new_topic = not route.is_followup and not _same_topic(topic, state.get("topic", ""))
        if new_topic and state.get("topic"):
            reset = {"research": {}, "blog": {}, "linkedin": {}, "image": {}, "last_artifact": ""}  # stale artifacts
        elif intent != "research" and "research" in plan and _same_topic((state.get("research") or {}).get("topic", ""), topic) \
                and ((state.get("research") or {}).get("summary")):
            plan.remove("research")  # reuse research from an earlier turn
    else:
        plan = []
    return {"intent": intent, "topic": topic, "plan": plan, "step": 0, "refine_target": "",
            "instruction": "", "note": note, **reset, **turn}


# ---------- graph edge functions ----------
def next_node(state: dict) -> str:
    plan, i = state.get("plan") or [], state.get("step", 0)
    return plan[i] if i < len(plan) else "strategist"


def after_quality(state: dict) -> str:
    q = state.get("quality") or {}
    if q.get("revise"):
        return state["last_agent"]  # loop back to the same writer with feedback
    return next_node(state)


def should_revise(score: float, revisions: int, n_failed: int = 0, critical: bool = False) -> bool:
    """Revise when the blended score is low, a hard rule failed, or too many rule checks fail
    (a generous LLM judge must not be able to hide rule violations)."""
    needs = score < settings.quality_threshold or critical or n_failed > settings.max_failed_checks
    return needs and revisions < settings.max_revisions
