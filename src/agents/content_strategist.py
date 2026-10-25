"""Agent 6: Content Strategist. Assembles everything into the dashboard payload + chat reply."""
from src.core.brand import profile_from_state, voice_report
from src.core.deps import get_services
from src.utils.content_optimization import strip_markdown
from src.utils.citations import strip_sources_section
from src.core.history import entries
from src.utils.diffing import change_stats, text_diff
from src.utils.export_tools import derive_blog_outputs, research_report_md
from src.utils.messages import to_chat
from src.workflow.node_utils import resilient

CHAT_SYSTEM = ("You are ContentAlchemy's assistant. You help create research reports, SEO blog posts, LinkedIn posts and "
               "images, and you can run a full campaign on a topic. Answer briefly. If the user's request is unclear, "
               "ask which topic and format they want.")


def next_steps(state) -> list[str]:
    have = {k: bool(state.get(k)) for k in ("research", "blog", "linkedin", "image")}
    out = []
    if have["blog"] and not have["linkedin"]:
        out.append("Turn this into a LinkedIn post")
    if have["blog"] and not have["image"]:
        out.append("Create a header image for the blog")
    if have["linkedin"] and have["blog"] and (state.get("image") or {}).get("aspect") == "landscape":
        out.append("Create a square image for the LinkedIn post")
    if have["linkedin"] and not have["blog"]:
        out.append("Expand this into an SEO blog post")
    if have["research"] and not have["blog"]:
        out.append("Write an SEO blog from this research")
    if have["blog"]:
        out.append("Ask me to make the blog shorter, punchier, or more casual (or say 'undo that')")
        out.append("Plan a follow-up post on a related sub-topic")
    return out[:4]


def _score(a): return (a.get("quality") or {}).get("score")


def _reply(state, flags) -> str:
    produced = [p for p in (state.get("plan") or [])]
    if state.get("intent") == "refine":
        produced = [state.get("refine_target", "draft")]
    parts = []
    names = {"research": "research brief", "blog": "SEO blog post", "linkedin": "LinkedIn post", "image": "image"}
    for p in produced:
        a = state.get(p) or {}
        s = _score(a)
        parts.append(f"- **{names.get(p, p).capitalize()}**" + (f" (quality {s}/10)" if s is not None else ""))
    reused = "research" not in (state.get("plan") or []) and state.get("research") and state.get("intent") in ("blog", "linkedin")
    head = f"Here is what I produced for **{state.get('topic', '')}**:" if state.get("intent") != "refine" \
        else f"Updated your {state.get('refine_target')} based on: \"{state.get('instruction', '')}\"."
    msg = head + ("\n" + "\n".join(parts) if parts and state.get("intent") != "refine" else "")
    if reused:
        msg += "\n- Reused the research from earlier in this conversation."
    if flags:
        msg += "\n\nHeads up:\n" + "\n".join(f"- {f}" for f in flags)
    msg += "\n\nOpen the tabs on the right to review and edit."
    return msg


def _change_summary(state):
    """For text refinements: what changed versus the previous version."""
    if state.get("intent") != "refine" or state.get("refine_target") not in ("blog", "linkedin"):
        return None
    kind = state["refine_target"]
    es = entries(state.get("history"), kind)
    if len(es) < 2:
        return None
    key = "body" if kind == "blog" else "text"
    old, new = es[-2]["artifact"].get(key, ""), es[-1]["artifact"].get(key, "")
    return {"kind": kind, **change_stats(old, new), "diff": text_diff(old, new)}


def _fallback(state, exc):
    reply = "Something went wrong while assembling the results, but any drafts created are still in the tabs."
    return {"final": {"reply": reply, "errors": state.get("errors", [])}, "messages": [("assistant", reply)],
            "last_agent": "strategist"}


@resilient("strategist", _fallback)
def content_strategist(state):
    svc = get_services()
    research, blog, li, image = (state.get(k) or {} for k in ("research", "blog", "linkedin", "image"))
    flags = []
    if research.get("unverified"):
        flags.append("Research is unverified (no live sources were available).")
    for kind, art in (("Blog", blog), ("LinkedIn", li)):
        q = art.get("quality") or {}
        if q.get("below_threshold"):
            flags.append(f"{kind} still has issues after {q.get('revisions_used', 0)} revision(s) "
                         f"(score {q.get('score')}/10): {', '.join(q.get('failed', [])) or 'low score'}.")
    cr = research.get("citation_report") or {}
    flags += [f"Research: {f}" for f in cr.get("flags", [])]
    if image.get("fallback") == "simplified_prompt":
        flags.append("Image generated from a simplified prompt after the first attempt was rejected.")
    if image.get("fallback") == "svg_card":
        flags.append("Image providers unavailable: showing a branded placeholder; the optimized prompt is kept so you can retry.")
    profile = profile_from_state(state)
    vr = voice_report(strip_markdown(strip_sources_section(blog.get("body", ""))), li.get("text", ""), profile)
    flags += [f"Brand voice: {f}" for f in vr["flags"]]
    flags += [e for e in state.get("errors", []) if e not in flags and not e.startswith("research:")][:4]

    if state.get("intent") == "undo":
        reply, flags = state.get("note") or "Restored the previous version.", []
    elif state.get("intent") == "chat":
        reply = state.get("note") or ""
        if not reply:
            try:
                reply = svc.complete([{"role": "system", "content": CHAT_SYSTEM},
                                      *to_chat(state.get("messages", []))[-6:]],
                                     tier="cheap", temperature=0.3, max_tokens=250).text
            except Exception:  # noqa: BLE001
                reply = ("I can write research briefs, SEO blog posts, LinkedIn posts and images, or a full campaign. "
                         "Tell me a topic and format.")
        flags = []
    else:
        reply = _reply(state, flags)
    change = _change_summary(state)
    if change:
        reply += f"\n\nChange: {change['words_before']} → {change['words_after']} words ({change['delta']:+d}); see the History tab for the full diff."

    final = {
        "intent": state.get("intent"), "topic": state.get("topic"), "reply": reply,
        "research": {**research, "report_md": research_report_md(research)} if research else {},
        "blog": ({**blog, **derive_blog_outputs(blog, image)} if blog.get("body") else {}),
        "linkedin": li, "image": image, "voice_report": vr,
        "next_steps": next_steps(state) if state.get("intent") not in ("chat", "undo") else [],
        "change_summary": change,
        "flags": flags, "errors": state.get("errors", []), "trace": state.get("trace", []),
        "usage": svc.stats(),
    }
    return {"final": final, "messages": [("assistant", reply)], "last_agent": "strategist"}
