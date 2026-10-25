"""Agent 3: SEO Blog Writer. Pass 1: structured SEO plan. Pass 2: grounded long-form draft.
Post-processing: citations are validated, renumbered and the Sources section is generated from real URLs."""
from pydantic import BaseModel, Field

from src.agents import prompts
from src.core.brand import brand_prompt, profile_from_state
from src.core.deps import get_services
from src.utils.citations import finalize_citations
from src.utils.content_optimization import trim_at_word
from src.utils.seo import slugify
from src.workflow.node_utils import resilient


class SeoPlan(BaseModel):
    title: str
    meta_description: str
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)


def research_context(state, limit=7000) -> str:
    r = state.get("research") or {}
    if not r.get("summary"):
        return "(no research available)"
    srcs = "\n".join(f"[{i}] {s.get('title') or s['link']} ({s['link']})" for i, s in enumerate(r.get("sources", []), 1))
    return f"{r['summary'][:limit]}\n\nSOURCES:\n{srcs}" if srcs else r["summary"][:limit]


def revision_block(state, kind) -> tuple[str, bool]:
    """Feedback from the quality loop, or the user's refine instruction. Returns (prompt block, is_loop)."""
    prev = (state.get(kind) or {})
    previous = prev.get("body") or prev.get("text") or ""
    q = state.get("quality") or {}
    loop = bool(q.get("revise"))
    fb = q.get("feedback") if loop else state.get("instruction", "")
    if fb and previous:
        block = prompts.REVISION_BLOCK if loop else prompts.USER_EDIT_BLOCK
        return block.format(feedback=fb, previous=previous), loop
    return "", False


def _fallback(state, exc):
    return {"blog": state.get("blog") or {}, "last_agent": "blog"}


@resilient("blog", _fallback)
def blog_writer(state):
    svc, topic = get_services(), state["topic"]
    profile = profile_from_state(state)
    existing = state.get("blog") or {}
    revision, is_loop = revision_block(state, "blog")

    if existing.get("title") and revision:  # revising: keep the SEO plan, don't pay for pass 1 again
        plan = SeoPlan(title=existing["title"], meta_description=existing.get("meta_description", ""),
                       primary_keyword=existing.get("primary_keyword", topic),
                       secondary_keywords=existing.get("keywords", []), outline=existing.get("outline", []))
    else:
        plan = svc.structured(SeoPlan, [{"role": "user", "content": prompts.SEO_PLAN.format(topic=topic)}], tier="cheap")
    title, meta = trim_at_word(plan.title, 60), trim_at_word(plan.meta_description, 160)

    research = state.get("research") or {}
    unverified = ("\nNo verified sources exist: avoid specific statistics and mark claims needing checks with [VERIFY]."
                  if research.get("unverified") else "")
    body = svc.complete([{"role": "user", "content": prompts.BLOG_BODY.format(
        topic=topic, title=title, primary_keyword=plan.primary_keyword,
        secondary=", ".join(plan.secondary_keywords), outline="; ".join(plan.outline),
        brand=brand_prompt(profile, "blog"), research=research_context(state),
        unverified=unverified, revision=revision)}], tier="smart", temperature=0.5, max_tokens=2800).text.strip()

    # citation integrity: renumber by first use, drop refs matching no source, rebuild Sources from real URLs
    body, used, invalid = finalize_citations(body, research.get("sources", []))
    errors = [f"blog: removed {len(invalid)} citation(s) that matched no source"] if invalid else []

    return {"blog": {"title": title, "slug": slugify(title), "meta_description": meta,
                     "primary_keyword": plan.primary_keyword, "keywords": plan.secondary_keywords,
                     "outline": plan.outline, "body": body, "cited_sources": used},
            "last_agent": "blog", "revisions": state.get("revisions", 0) + (1 if is_loop else 0), "errors": errors}
