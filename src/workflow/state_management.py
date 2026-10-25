"""The shared contract between all agents. Agents never call each other: they read/write this state."""
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from src.core.history import append_history

RESET = "__reset__"


def append_or_reset(old, new):
    """Reducer for per-turn logs: append, or start fresh when new begins with the RESET sentinel."""
    old = old or []
    new = new or []
    if new and new[0] == RESET:
        return list(new[1:])
    return [*old, *new]


class ContentState(TypedDict, total=False):
    # --- conversation memory (persisted by the checkpointer across turns) ---
    messages: Annotated[list, add_messages]
    brand_profile: dict    # BrandProfile as dict: one source of truth for all formats
    topic: str
    turn: int              # conversation turn counter
    last_artifact: str     # blog|linkedin|image: what the user touched last (resolves 'make it shorter')
    focus: str             # UI hint for this turn: the view the user is looking at

    # --- routing (written by router) ---
    intent: str            # research|blog|linkedin|image|series|refine|chat
    plan: list             # e.g. ["research","blog","linkedin","image"]
    step: int              # pointer into plan
    instruction: str       # refine instruction ("make it shorter")
    refine_target: str
    note: str              # router -> strategist message for chat/clarification

    # --- artifacts (persist across turns so follow-ups can reuse them) ---
    research: dict         # {topic, summary, sources[], unverified}
    blog: dict             # {title, meta_description, primary_keyword, keywords[], body, slug, cited_sources, quality}
    linkedin: dict         # {text, hashtags[], hook, hook_variants[], format, first_comment, quality}
    image: dict            # {prompt, brief, alt_text, aspect, url|data|svg, provider, fallback, error}

    # --- control (per turn) ---
    last_agent: str
    quality: dict          # latest evaluation {score, det_score, llm_score, checks[], feedback, revise, accepted}
    revisions: int
    final: dict

    # --- version history (append-only, per-kind cap): powers undo, restore, diff ---
    history: Annotated[list, append_history]

    # --- observability (per turn, append-only with reset) ---
    errors: Annotated[list, append_or_reset]
    trace: Annotated[list, append_or_reset]


def initial_turn_input(text: str, brand_profile: dict | None = None, focus: str | None = None) -> dict:
    """What we send into the graph each turn: new message + per-turn resets."""
    inp = {
        "messages": [("user", text)],
        "step": 0,
        "revisions": 0,
        "quality": {},
        "instruction": "",
        "note": "",
        "last_agent": "",
        "focus": focus or "",
        "final": {},
        "errors": [RESET],
        "trace": [RESET],
    }
    if brand_profile:
        inp["brand_profile"] = brand_profile
    return inp
