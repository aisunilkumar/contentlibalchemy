"""Agent 1: Query Handler. Classifies intent, resolves follow-ups, builds the plan."""
from src.core.brand import load_brand_profile
from src.core.config import settings
from src.core.deps import get_services
from src.core.router import Route, heuristic_route, resolve_route
from src.utils.messages import last_user_text, to_chat
from src.workflow.node_utils import resilient

ROUTER_SYSTEM = """You are the routing brain of ContentAlchemy, a multi-agent content system.
Classify the user's latest message into exactly one intent:
- research: wants a research report or analysis on a topic
- blog: wants an SEO blog post or article
- linkedin: wants a LinkedIn post
- image: wants an image, visual or illustration
- series: wants a campaign, several formats, or "everything" on a topic
- refine: wants to change existing content ("shorter", "more casual", "add a section", "try again", "different style"); set refine_target to blog, linkedin or image and put the request in instruction
- undo: wants to revert a change ("undo that", "go back", "restore the previous version"); set refine_target if they say which artifact
- chat: greetings, questions about the system, or unclear requests
If the user does not say which artifact they mean, set refine_target to none: the app will use the artifact they are currently viewing.
Use the conversation history to resolve follow-ups like "now a LinkedIn post" or "make it shorter": reuse the previous topic and set is_followup=true.
topic must be a short noun phrase (max 12 words)."""


def _fallback(state, exc):
    return {"intent": "chat", "plan": [], "step": 0, "last_agent": "router",
            "note": "Sorry, I couldn't understand that request. Try: 'Write an SEO blog on <topic>'."}


@resilient("router", _fallback)
def query_handler(state):
    svc = get_services()
    artifacts = [k for k in ("research", "blog", "linkedin", "image") if state.get(k)]
    context = (f"\n\nCurrent topic: {state.get('topic') or 'none'}. Existing artifacts: {', '.join(artifacts) or 'none'}. "
               f"The user is currently viewing: {state.get('focus') or 'overview'}. Last artifact changed: {state.get('last_artifact') or 'none'}.")
    history = to_chat(state.get("messages", []))[-settings.history_window:]
    errors = []
    try:
        route = svc.structured(Route, [{"role": "system", "content": ROUTER_SYSTEM + context}, *history],
                               tier="cheap")
    except Exception as exc:  # noqa: BLE001  all LLM providers down -> keyword routing
        route = heuristic_route(last_user_text(state.get("messages", [])), state)
        errors.append(f"router: LLM unavailable, used keyword routing ({type(exc).__name__})")
    update = resolve_route(route, state)
    update.update({"last_agent": "router", "errors": errors})
    if not state.get("brand_profile"):
        update["brand_profile"] = load_brand_profile().model_dump()
    return update
