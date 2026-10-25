"""Agent 3: SEO Blog Writer. Pass 1: structured SEO plan. Pass 2: grounded long-form draft.
Post-processing: citations are validated, renumbered and the Sources section is generated from real URLs."""
from pydantic import BaseModel, Field

from src.core.brand import brand_prompt, profile_from_state
from src.core.deps import get_services
from src.utils.citations import finalize_citations
from src.utils.content_optimization import trim_at_word
from src.utils.seo import slugify
from src.workflow.node_utils import resilient

SEO_PLAN = """Create an SEO plan for a blog post on: {topic}
Return: a primary keyword (2-4 words, what people actually search), 3-5 secondary keywords, a title (max 60 characters, starts with or contains the primary keyword), a meta description (110-155 characters, contains the primary keyword and a clear benefit), and a 5-6 item outline of H2 headings (at least one containing the primary or a secondary keyword)."""

BLOG_BODY = """Write the full blog post in Markdown.

TOPIC: {topic}
TITLE (use as the single H1): {title}
PRIMARY KEYWORD: {primary_keyword} (use naturally about once per 150 words; first 100 words; one H2; the conclusion; never stuff)
SECONDARY KEYWORDS: {secondary} (work most of them in naturally)
OUTLINE (H2 sections): {outline}

BRAND VOICE (follow exactly; these rules are checked automatically):
{brand}

RESEARCH (ground every factual claim here; cite as [n] using these numbers; never invent statistics):
{research}

STRUCTURE RULES: exactly one H1; 4-6 H2 sections; use H3 only under an H2 (never skip heading levels); 800-1200 words; a hook intro that states the benefit; short paragraphs; two [INTERNAL LINK: anchor text] placeholders.
FAQ RULES: an H2 titled "FAQ" followed by 3 questions, each as an H3 ending with "?" and a 1-3 sentence answer underneath.
End with a conclusion containing a call to action. Do NOT write a Sources section; it is added automatically.{unverified}
{revision}"""

USER_EDIT_BLOCK = """
USER REQUEST (apply exactly this change; keep everything else the same: structure, facts, citations, keywords, brand voice):
{feedback}

CURRENT DRAFT (this is the latest version, including any edits the user made by hand):
{previous}"""

REVISION_BLOCK = """
REVISION FEEDBACK (fix every point, keep what already works):
{feedback}

PREVIOUS DRAFT:
{previous}"""


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
        block = REVISION_BLOCK if loop else USER_EDIT_BLOCK
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
        plan = svc.structured(SeoPlan, [{"role": "user", "content": SEO_PLAN.format(topic=topic)}], tier="cheap")
    title, meta = trim_at_word(plan.title, 60), trim_at_word(plan.meta_description, 160)

    research = state.get("research") or {}
    unverified = ("\nNo verified sources exist: avoid specific statistics and mark claims needing checks with [VERIFY]."
                  if research.get("unverified") else "")
    body = svc.complete([{"role": "user", "content": BLOG_BODY.format(
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
