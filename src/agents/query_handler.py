"""Agent 1: Query Handler. Classifies intent, resolves follow-ups, builds the plan."""
from src.agents import prompts
from src.core.brand import load_brand_profile
from src.core.config import settings
from src.core.deps import get_services
from src.core.router import Route, heuristic_route, resolve_route
from src.utils.messages import last_user_text, to_chat
from src.workflow.node_utils import resilient


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
        route = svc.structured(Route, [{"role": "system", "content": prompts.ROUTER_SYSTEM + context}, *history],
                               tier="cheap")
    except Exception as exc:  # noqa: BLE001  all LLM providers down -> keyword routing
        route = heuristic_route(last_user_text(state.get("messages", [])), state)
        errors.append(f"router: LLM unavailable, used keyword routing ({type(exc).__name__})")
    update = resolve_route(route, state)
    update.update({"last_agent": "router", "errors": errors})
    if not state.get("brand_profile"):
        update["brand_profile"] = load_brand_profile().model_dump()
    return update
