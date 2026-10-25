"""Agent 4: LinkedIn Post Writer. Strategy first (format, hooks, CTA, hashtags, first comment), then the post."""
import re
from typing import Literal

from pydantic import BaseModel, Field

from src.agents import prompts
from src.agents.blog_writer import research_context, revision_block
from src.core.brand import brand_prompt, profile_from_state
from src.core.config import settings
from src.core.deps import get_services
from src.utils.content_optimization import extract_hashtags
from src.workflow.node_utils import resilient


class LinkedInPlan(BaseModel):
    angle: str
    format: Literal["story", "how_to", "listicle", "contrarian", "data_led", "question"] = "how_to"
    audience: str = ""
    hooks: list[str] = Field(default_factory=list)
    cta: str = "What has worked for you?"
    hashtags: list[str] = Field(default_factory=list)
    first_comment: str = ""


def pick_hook(hooks: list[str]) -> str:
    """Prefer a short hook with a number (concrete); otherwise the shortest. Deterministic."""
    ok = [h.strip() for h in hooks if h and h.strip()]
    if not ok:
        return ""
    short = [h for h in ok if len(h) <= 140] or ok
    return sorted(short, key=lambda h: (not re.search(r"\d", h), len(h)))[0]


def clean_hashtags(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        t = "#" + re.sub(r"\W+", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:5]


def _fallback(state, exc):
    return {"linkedin": state.get("linkedin") or {}, "last_agent": "linkedin"}


@resilient("linkedin", _fallback)
def linkedin_writer(state):
    svc, topic = get_services(), state["topic"]
    profile = profile_from_state(state)
    brand = brand_prompt(profile, "linkedin")
    existing = state.get("linkedin") or {}
    revision, is_loop = revision_block(state, "linkedin")
    errors = []

    if existing.get("plan") and revision:  # revising: keep the strategy
        plan = LinkedInPlan.model_validate(existing["plan"])
    else:
        try:
            plan = svc.structured(LinkedInPlan, [{"role": "user", "content": prompts.LINKEDIN_PLAN.format(
                topic=topic, brand=brand, research=research_context(state, 2500))}], tier="cheap", max_tokens=700)
        except Exception as exc:  # noqa: BLE001  strategy step is optional: degrade to a generic plan
            plan = LinkedInPlan(angle=f"A practical take on {topic}", hooks=[f"Most teams get {topic} wrong."])
            errors.append(f"linkedin: strategy step failed, used a generic plan ({type(exc).__name__})")
    hook, tags = pick_hook(plan.hooks) or f"Most teams get {topic} wrong.", clean_hashtags(plan.hashtags)

    text = svc.complete([{"role": "user", "content": prompts.LINKEDIN_POST.format(
        topic=topic, format=plan.format, angle=plan.angle, audience=plan.audience, hook=hook, cta=plan.cta,
        hashtags=" ".join(tags) or "(3-5 relevant CamelCase hashtags)", brand=brand,
        research=research_context(state, 2500), fold=settings.linkedin_fold_chars, revision=revision)}],
        tier="smart", temperature=0.7, max_tokens=700).text.strip()
    return {"linkedin": {"text": text, "hashtags": extract_hashtags(text), "hook": hook, "hook_variants": plan.hooks,
                         "format": plan.format, "audience": plan.audience, "cta": plan.cta,
                         "first_comment": plan.first_comment, "plan": plan.model_dump()},
            "last_agent": "linkedin", "revisions": state.get("revisions", 0) + (1 if is_loop else 0), "errors": errors}
