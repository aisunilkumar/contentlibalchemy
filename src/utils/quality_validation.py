"""Quality pipeline: deterministic checks (SEO, platform rules, citations, brand voice) + LLM judge,
blended into one score, with a revise-loop signal."""
import re
from dataclasses import asdict

from pydantic import BaseModel, Field

from src.agents import prompts
from src.core.brand import BrandProfile, brand_prompt, profile_from_state, voice_checks
from src.core.config import settings
from src.core.deps import get_services
from src.core.history import entries, make_entry
from src.core.router import should_revise
from src.utils.checks import Check
from src.utils.citations import citation_coverage, strip_sources_section
from src.utils.content_optimization import (count_words, extract_hashtags, first_words, headings, reading_ease,
                                            strip_markdown)
from src.utils.seo import faq_pairs, heading_hierarchy_ok, keyword_density
from src.workflow.node_utils import resilient

__all__ = ["Check", "Judge", "blog_checks", "linkedin_checks", "evaluate", "quality_node", "rescore_deterministic"]


class Judge(BaseModel):
    seo: float = Field(ge=0, le=10)
    voice: float = Field(ge=0, le=10)
    accuracy: float = Field(ge=0, le=10)
    readability: float = Field(ge=0, le=10)
    engagement: float = Field(ge=0, le=10)
    feedback: str = ""

    @property
    def score(self) -> float:
        return (self.seo + self.voice + self.accuracy + self.readability + self.engagement) / 5


def blog_checks(blog: dict, research: dict, profile: BrandProfile, min_words: int) -> list[Check]:
    body = strip_sources_section(blog.get("body", ""))
    title, meta = blog.get("title", ""), blog.get("meta_description", "")
    pk = (blog.get("primary_keyword") or "").lower()
    sec = [k.lower() for k in blog.get("keywords", []) if k]
    prose = strip_markdown(body)
    words, dens = count_words(prose), keyword_density(body, pk)
    h2 = headings(body, 2)
    sec_hits = sum(k in prose.lower() for k in sec)
    ease = reading_ease(prose)
    c = [
        Check("title_length", 0 < len(title) <= 60, f"title is {len(title)} chars (max 60)"),
        Check("title_keyword", bool(pk) and pk in title.lower(), f"title should contain the primary keyword '{pk}'"),
        Check("meta_length", 110 <= len(meta) <= 160, f"meta description is {len(meta)} chars (aim 110-155)"),
        Check("meta_keyword", bool(pk) and pk in meta.lower(), f"meta description should contain '{pk}'"),
        Check("word_count", words >= min_words, f"{words} words (min {min_words}); expand with concrete detail"),
        Check("keyword_in_intro", bool(pk) and pk in first_words(prose, 100).lower(),
              f"primary keyword '{pk}' missing from the first 100 words"),
        Check("keyword_density", 0.4 <= dens <= 3.0,
              f"primary keyword density is {dens}% (aim 0.5-2.5%; {'add' if dens < 0.4 else 'reduce'} mentions)"),
        Check("secondary_keywords", not sec or sec_hits >= max(1, len(sec) // 2),
              f"only {sec_hits}/{len(sec)} secondary keywords used"),
        Check("single_h1", len(headings(body, 1)) == 1, f"found {len(headings(body, 1))} H1 headings (need exactly 1)", critical=True),
        Check("h2_structure", len(h2) >= 3, f"only {len(h2)} H2 sections (need 3+)"),
        Check("heading_hierarchy", heading_hierarchy_ok(body), "heading levels are skipped (e.g. H1 -> H3)"),
        Check("keyword_in_h2", any(pk in h.lower() or any(k in h.lower() for k in sec) for h in h2),
              "work the primary or a secondary keyword into at least one H2"),
        Check("faq_section", len(faq_pairs(body)) >= 2, "add an H2 'FAQ' with 2+ questions as H3 headings ending in '?'"),
        Check("readability", ease >= profile.reading_ease_min,
              f"reading ease is {ease} (brand minimum {profile.reading_ease_min:g}); use shorter sentences and simpler words"),
    ]
    if research and research.get("sources") and not research.get("unverified"):
        cov = citation_coverage(body)
        c.append(Check("citations", bool(re.search(r"\[\d+\]", body)), "cite research sources inline as [n]"))
        c.append(Check("citation_coverage", cov >= 0.4, f"only {int(cov * 100)}% of paragraphs cite a source; ground claims in the research"))
    return c + voice_checks(prose, profile, "blog")


def linkedin_checks(li: dict, profile: BrandProfile) -> list[Check]:
    text = li.get("text", "")
    tags = extract_hashtags(text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    fold = settings.linkedin_fold_chars
    first_two = " ".join(lines[:2])
    body_no_tags = "\n".join(ln for ln in lines if not re.fullmatch(r"(?:\s*#\w+)+\s*", ln))
    prose = body_no_tags
    return [
        Check("length", 300 <= len(text) <= 3000, f"{len(text)} chars (LinkedIn limit 3000; aim 900-1500)", critical=True),
        Check("hashtags", 3 <= len(tags) <= 5, f"{len(tags)} hashtags (use 3-5)"),
        Check("hook", bool(lines) and len(lines[0]) <= 140, "first line (hook) should be under 140 characters"),
        Check("fold_hook", len(first_two) <= fold, f"first two lines are {len(first_two)} chars; keep under ~{fold} so the hook shows before 'see more'"),
        Check("cta_question", "?" in text[-500:], "end with one question as a call to action"),
        Check("short_paragraphs", all(len(p) <= 450 for p in paras), "break up paragraphs over 450 characters"),
        Check("plain_text", "**" not in text and not re.search(r"^#{1,6}\s", text, re.M), "remove Markdown formatting", critical=True),
        Check("no_links_in_body", "http" not in text.lower(), "no links in the body; put the link in the first comment"),
    ] + voice_checks(prose, profile, "linkedin")


def evaluate(kind: str, content: dict, state: dict) -> dict:
    svc, profile = get_services(), profile_from_state(state)
    research = state.get("research") or {}
    checks = blog_checks(content, research, profile, settings.min_blog_words) if kind == "blog" \
        else linkedin_checks(content, profile)
    det = round(10 * sum(c.passed for c in checks) / len(checks), 2)
    text = content.get("body") or content.get("text", "")
    fixes = [f"- {c.detail}" for c in checks if not c.passed]
    llm, judge_feedback, judge_error = None, "", None
    try:
        j = svc.structured(Judge, [{"role": "user", "content": prompts.JUDGE.format(
            kind="blog post" if kind == "blog" else "LinkedIn post", kind_upper=kind.upper(),
            brand=brand_prompt(profile, kind), research=(research.get("summary") or "(none)")[:2500],
            content=text[:9000])}], tier="cheap")
        llm, judge_feedback = round(j.score, 2), j.feedback
    except Exception as exc:  # noqa: BLE001  judge down -> deterministic score only
        judge_error = f"{type(exc).__name__}: {exc}"
    w = settings.det_weight
    score = round(det if llm is None else w * det + (1 - w) * llm, 2)
    failed = [c.name for c in checks if not c.passed]
    critical = [c.name for c in checks if c.critical and not c.passed]
    if critical:  # hard rules gate the output: the judge cannot average them away
        score = min(score, round(settings.quality_threshold - 0.5, 2))
    feedback = "\n".join(fixes + ([f"- {judge_feedback}"] if judge_feedback else []))
    return {"score": score, "det_score": det, "llm_score": llm, "checks": [asdict(c) for c in checks],
            "feedback": feedback, "judge_error": judge_error, "failed": failed, "critical_failed": critical}


def _fallback(state, exc):  # evaluation crashed: accept the draft and move on
    return {"quality": {"score": 0, "accepted": True, "revise": False, "skipped": True},
            "step": state.get("step", 0) + 1, "revisions": 0}


@resilient("quality", _fallback)
def quality_node(state):
    kind = state["last_agent"]
    content = state.get(kind) or {}
    if not (content.get("body") or content.get("text")):  # writer failed -> nothing to score
        return {"quality": {"score": 0, "accepted": True, "revise": False, "skipped": True},
                "step": state.get("step", 0) + 1, "revisions": 0,
                "errors": [f"quality: no {kind} content to evaluate"]}
    res = evaluate(kind, content, state)
    used = state.get("revisions", 0)
    revise = should_revise(res["score"], used, len(res["failed"]), bool(res["critical_failed"]))
    needs_work = bool(res["critical_failed"]) or res["score"] < settings.quality_threshold or len(res["failed"]) > settings.max_failed_checks
    q = {**res, "kind": kind, "revise": revise, "accepted": not revise, "revisions_used": used,
         "below_threshold": (not revise) and needs_work}
    update = {"quality": q, kind: {**content, "quality": {k: q[k] for k in
              ("score", "det_score", "llm_score", "checks", "feedback", "revisions_used", "below_threshold", "failed")}}}
    if not revise:
        update.update(step=state.get("step", 0) + 1, revisions=0, last_artifact=kind)
        hist = state.get("history") or []
        instr = (state.get("instruction") or "").strip()
        reason = (f"Refined: {instr[:70]}" if instr and state.get("intent") == "refine"
                  else "First draft" if not entries(hist, kind) else "Regenerated")
        update["history"] = [make_entry(kind, update[kind], hist, state.get("turn", 0), reason, q["score"])]
    return update


def rescore_deterministic(kind: str, artifact: dict, values: dict) -> dict:
    """Instant, free re-check of hand-edited content (rules only: no LLM call)."""
    profile = profile_from_state(values)
    research = values.get("research") or {}
    checks = blog_checks(artifact, research, profile, settings.min_blog_words) if kind == "blog" \
        else linkedin_checks(artifact, profile)
    det = round(10 * sum(c.passed for c in checks) / len(checks), 2)
    return {"score": det, "det_score": det, "llm_score": None, "checks": [asdict(c) for c in checks],
            "feedback": "\n".join(f"- {c.detail}" for c in checks if not c.passed),
            "failed": [c.name for c in checks if not c.passed], "revisions_used": 0, "below_threshold": False,
            "edited_by_user": True}
