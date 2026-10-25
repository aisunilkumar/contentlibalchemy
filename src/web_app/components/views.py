"""One render function per workspace view. All state comes from the LangGraph checkpoint via `Ctx`."""
from dataclasses import dataclass
from typing import Callable

import streamlit as st

from src.core import session
from src.core.history import current_version, entries
from src.utils.diffing import change_stats, text_diff
from src.utils.export_tools import (build_export_zip, derive_blog_outputs, image_bytes_and_ext, linkify_citations,
                                    research_report_md, sources_csv)
from src.utils.quality_validation import rescore_deterministic
from src.web_app.components.media import render_image

VIEWS = ["Overview", "Research", "Blog", "LinkedIn", "Image", "Quality", "History", "Export"]
FOCUS = {"Research": "research", "Blog": "blog", "LinkedIn": "linkedin", "Image": "image"}
EXAMPLES = ["Write an SEO blog on cloud cost optimization", "Run a full campaign about remote team productivity",
            "Research the state of AI in healthcare"]


@dataclass
class Ctx:
    graph: object
    thread: str
    vals: dict
    submit: Callable[[str], None]   # queue a chat message and rerun

    def reload(self):
        self.vals = session.get_values(self.graph, self.thread)


# ---------------- shared widgets ----------------
def has(vals, kind) -> bool:
    a = vals.get(kind) or {}
    return bool({"research": a.get("summary"), "blog": a.get("body"), "linkedin": a.get("text"),
                 "image": a.get("prompt") or a.get("url") or a.get("svg")}[kind])


def empty_state(ctx: Ctx, what: str, label: str, prompt: str):
    st.info(f"No {what} yet.")
    if st.button(label, key=f"empty-{what}", type="primary"):
        ctx.submit(prompt)


def chips(ctx: Ctx, items: list[tuple[str, str]], prefix: str, before: Callable | None = None):
    """Quick-action buttons that send a clear, explicit chat message (no typing needed)."""
    cols = st.columns(len(items))
    for col, (label, prompt) in zip(cols, items):
        if col.button(label, key=f"{prefix}-{label}", width="stretch"):
            if before:
                before()
            ctx.submit(prompt)


def check_table(checks: list[dict]):
    """Status is spelled out in words, never colour alone."""
    st.dataframe([{"Status": "Pass" if c["passed"] else "Fix", "Check": c["name"].replace("_", " "),
                   "What to do": "" if c["passed"] else c["detail"]} for c in checks], hide_index=True, width="stretch")


def checks_summary(res: dict):
    n_bad = len(res["failed"])
    total = len(res["checks"])
    if n_bad:
        st.warning(f"{total - n_bad} of {total} rule checks pass. To fix: " + "; ".join(
            c["detail"] for c in res["checks"] if not c["passed"]))
    else:
        st.success(f"All {total} rule checks pass.")


def custom_change(ctx: Ctx, kind: str, key: str, before: Callable | None = None):
    col1, col2 = st.columns([4, 1])
    text = col1.text_input("Or describe any other change", key=f"custom-{key}",
                           placeholder={"blog": "e.g. add a section on tagging policies", "linkedin": "e.g. open with a story",
                                        "image": "e.g. show a calmer sunset palette"}[kind])
    if col2.button("Apply", key=f"apply-{key}", disabled=not text.strip(), width="stretch"):
        if before:
            before()
        ctx.submit(f"For the {'LinkedIn post' if kind == 'linkedin' else kind}: {text.strip()}")


def undo_button(ctx: Ctx, kind: str, key: str):
    can = len(entries(ctx.vals.get("history"), kind)) >= 2
    if st.button("↶ Undo last change", key=f"undo-{key}", disabled=not can,
                 help="Go back to the previous version of this item"):
        msg = session.undo(ctx.graph, ctx.thread, kind)
        st.toast(msg or "Nothing to undo.")
        st.rerun()


# ---------------- Overview ----------------
def overview(ctx: Ctx):
    v = ctx.vals
    st.subheader("Overview")
    topic = v.get("topic")
    if not topic:
        st.markdown("Tell the assistant what you need in the chat, or start with an example:")
        for ex in EXAMPLES:
            if st.button(ex, key=f"ex-{ex}"):
                ctx.submit(ex)
        return
    st.markdown(f"**Topic:** {topic}")
    cards = [("research", "Research", f"Research {topic}"), ("blog", "SEO blog", f"Write an SEO blog on {topic}"),
             ("linkedin", "LinkedIn post", f"Write a LinkedIn post about {topic}"), ("image", "Image", f"Create an image about {topic}")]
    for col, (kind, label, prompt) in zip(st.columns(4), cards):
        with col.container(border=True):
            st.markdown(f"**{label}**")
            if has(v, kind):
                q = (v.get(kind) or {}).get("quality") or {}
                st.markdown("✅ Ready" + (f" · {q['score']}/10" if q.get("score") is not None else ""))
                ver = current_version(v.get("history"), kind)
                if ver:
                    st.caption(f"Version {ver['version']}")
            else:
                st.markdown("⬜ Not created yet")
                if st.button("Create", key=f"ov-{kind}", width="stretch"):
                    ctx.submit(prompt)
    f = v.get("final") or {}
    if f.get("flags"):
        st.warning("**Needs your attention**\n" + "\n".join(f"- {x}" for x in f["flags"]))
    vr = f.get("voice_report") or {}
    if vr.get("metrics"):
        st.markdown("**Brand voice:** " + ("✅ consistent across formats" if vr["consistent"] else "⚠️ " + "; ".join(vr["flags"])))
    if f.get("next_steps"):
        st.markdown("**Suggested next steps**")
        for x in f["next_steps"]:
            st.markdown(f"- {x}")
    u = f.get("usage") or {}
    if u:
        c1, c2, c3 = st.columns(3)
        c1.metric("Spend this session", f"${u['total_usd']:.3f}")
        c2.metric("Cache hits", u["cache_hits"])
        c3.metric("Turns", v.get("turn", 0))


# ---------------- Research ----------------
def research(ctx: Ctx):
    v = ctx.vals
    st.subheader("Research")
    r = v.get("research") or {}
    if not r.get("summary"):
        return empty_state(ctx, "research", "Research this topic", f"Research {v.get('topic') or 'my topic'}")
    if r.get("unverified"):
        st.warning("**Unverified.** No live sources were available, so facts below are not checked.")
    cr = r.get("citation_report") or {}
    if cr:
        for col, (label, val) in zip(st.columns(4), [("Sources found", cr["found"]), ("Cited", cr["cited"]),
                                                       ("Domains", cr["distinct_domains"]),
                                                       ("Paragraphs cited", f"{int(cr['coverage'] * 100)}%")]):
            col.metric(label, val)
        if cr.get("flags"):
            st.caption("Notes: " + "; ".join(cr["flags"]))
    st.markdown("#### Findings")
    st.markdown(linkify_citations(r["summary"], r.get("sources", [])))
    srcs = [(s, "Cited") for s in r.get("sources", [])] + [(s, "Further reading") for s in r.get("other_sources", [])]
    if srcs:
        st.markdown("#### Sources")
        show = st.radio("Show", ["All", "Cited", "Further reading"], horizontal=True, key="src-filter")
        rows = [{"#": i if status == "Cited" else None, "Status": status, "Title": s.get("title") or s["link"],
                 "Domain": s.get("domain", ""), "Credibility": s.get("credibility"), "Found by": s.get("provider", ""),
                 "Link": s["link"]} for i, (s, status) in enumerate(srcs, 1) if show in ("All", status)]
        st.dataframe(rows, hide_index=True, width="stretch",
                     column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open source")})
        st.caption("Credibility is a transparent heuristic (domain type and search rank), not a fact-check. "
                   "Citations point to real retrieved pages; open them to confirm the claim.")
    d1, d2, d3 = st.columns(3)
    d1.download_button("Brief (.md)", research_report_md(r), "research.md", width="stretch")
    d2.download_button("Sources (.csv)", sources_csv(r), "sources.csv", mime="text/csv", width="stretch")
    if d3.button("Re-run research", width="stretch"):
        ctx.submit(f"Research {v.get('topic')}")


# ---------------- Blog ----------------
def blog(ctx: Ctx):
    v = ctx.vals
    st.subheader("Blog post")
    b = v.get("blog") or {}
    if not b.get("body"):
        return empty_state(ctx, "blog post", "Write an SEO blog", f"Write an SEO blog on {v.get('topic') or 'my topic'}")
    key = session.version_key(v, "blog")
    title = st.text_input("Title (max 60 characters)", b.get("title", ""), key=f"bt-{key}")
    meta = st.text_input("Meta description (110 to 160 characters)", b.get("meta_description", ""), key=f"bm-{key}")
    st.caption(f"Title {len(title)}/60 · Meta {len(meta)}/160 · primary keyword: {b.get('primary_keyword')}")
    body = st.text_area("Article (Markdown)", b.get("body", ""), height=420, key=f"bb-{key}")
    edited = {**b, "title": title, "meta_description": meta, "body": body}
    changed = (title, meta, body) != (b.get("title", ""), b.get("meta_description", ""), b.get("body", ""))

    def save(reason="Edited by you"):
        session.apply_edit(ctx.graph, ctx.thread, "blog", {"title": title, "meta_description": meta, "body": body}, reason)

    live = rescore_deterministic("blog", edited, v) if changed else (b.get("quality") or rescore_deterministic("blog", b, v))
    c1, c2 = st.columns(2)
    if c1.button("Save my edits", key=f"bs-{key}", type="primary", disabled=not changed, width="stretch"):
        save()
        st.toast("Edits saved. Next changes will start from your version.")
        st.rerun()
    with c2:
        undo_button(ctx, "blog", "blog")
    if changed:
        st.info("You have unsaved edits. The rule check below updates instantly (no AI call).")
    checks_summary(live) if live.get("checks") else None
    with st.expander("All rule checks and score"):
        if live.get("checks"):
            st.caption(f"Score {live['score']}/10" + (f" (rules {live['det_score']}, AI judge {live['llm_score']})" if live.get("llm_score") is not None else " (rules only)"))
            check_table(live["checks"])

    st.markdown("#### Ask for a change")
    chips(ctx, [("Shorter", "Make the blog shorter"), ("More casual", "Make the blog more casual"),
                ("Punchier intro", "Rewrite the blog introduction to be punchier"), ("Add examples", "Add concrete examples to the blog")],
          "bc", before=save if changed else None)
    custom_change(ctx, "blog", "blog", before=save if changed else None)

    out = derive_blog_outputs(edited, v.get("image"))
    seo = out["seo"]
    st.markdown("#### SEO")
    for col, (label, val) in zip(st.columns(4), [("Keyword density", f"{seo['keyword_density']}%"), ("Reading ease", seo["reading_ease"]),
                                                   ("FAQ items", seo["faq_count"]), ("Internal links", seo["internal_link_placeholders"])]):
        col.metric(label, val)
    st.caption(f"URL slug: {seo['slug']}")
    with st.expander("Open Graph tags and structured data (JSON-LD)"):
        st.json({"og": seo["og"], "json_ld": seo["json_ld"]})
    with st.expander("Preview (citations are clickable)"):
        st.markdown(linkify_citations(body, (v.get("research") or {}).get("sources", [])))
    d1, d2, d3 = st.columns(3)
    d1.download_button("Markdown (.md)", out["markdown"], "post.md", width="stretch")
    d2.download_button("HTML with meta + JSON-LD", out["html"], "post.html", mime="text/html", width="stretch")
    d3.download_button("Plain text (.txt)", out["plain_text"], "post.txt", width="stretch")


# ---------------- LinkedIn ----------------
def linkedin(ctx: Ctx):
    v = ctx.vals
    st.subheader("LinkedIn post")
    li = v.get("linkedin") or {}
    if not li.get("text"):
        return empty_state(ctx, "LinkedIn post", "Write a LinkedIn post", f"Write a LinkedIn post about {v.get('topic') or 'my topic'}")
    key = session.version_key(v, "linkedin")
    st.caption(f"Format: {li.get('format') or 'n/a'} · Audience: {li.get('audience') or 'n/a'}")
    text = st.text_area("Post text (plain text)", li["text"], height=340, key=f"lt-{key}")
    changed = text != li["text"]
    edited = {**li, "text": text}

    def save(reason="Edited by you"):
        session.apply_edit(ctx.graph, ctx.thread, "linkedin", {"text": text}, reason)

    live = rescore_deterministic("linkedin", edited, v) if changed else (li.get("quality") or rescore_deterministic("linkedin", li, v))
    st.caption(f"{len(text)}/3000 characters")
    c1, c2 = st.columns(2)
    if c1.button("Save my edits", key=f"ls-{key}", type="primary", disabled=not changed, width="stretch"):
        save()
        st.toast("Edits saved.")
        st.rerun()
    with c2:
        undo_button(ctx, "linkedin", "linkedin")
    if changed:
        st.info("You have unsaved edits. The rule check below updates instantly (no AI call).")
    checks_summary(live) if live.get("checks") else None
    with st.expander("All rule checks and score"):
        if live.get("checks"):
            check_table(live["checks"])
    st.markdown("#### Ask for a change")
    chips(ctx, [("Shorter", "Make the LinkedIn post shorter"), ("Different hook", "Try a different hook for the LinkedIn post"),
                ("More conversational", "Make the LinkedIn post more conversational")], "lc", before=save if changed else None)
    custom_change(ctx, "linkedin", "linkedin", before=save if changed else None)
    st.markdown("#### Copy-ready")
    st.code(text, language=None, wrap_lines=True)
    if li.get("first_comment"):
        st.markdown("**First comment** (put the article link here, not in the post):")
        st.code(li["first_comment"], language=None, wrap_lines=True)
    if li.get("hook_variants"):
        with st.expander("Hook options to A/B test"):
            for h in li["hook_variants"]:
                st.markdown(f"- {h}" + ("  (used)" if h == li.get("hook") else ""))
    st.download_button("Download (.txt)", text, "linkedin.txt")


# ---------------- Image ----------------
def image(ctx: Ctx):
    v = ctx.vals
    st.subheader("Image")
    im = v.get("image") or {}
    if not (im.get("prompt") or im.get("svg") or im.get("url")):
        return empty_state(ctx, "image", "Create an image", f"Create an image about {v.get('topic') or 'my topic'}")
    render_image(im)
    status = {"none": "Generated", "simplified_prompt": "Generated from a simplified prompt",
              "svg_card": "Branded placeholder (image providers were unavailable)"}.get(im.get("fallback"), "")
    st.caption(f"{im.get('aspect', '')} · {status}" + (f" · {im['provider']}" if im.get("provider") else ""))
    if im.get("fallback") == "svg_card":
        st.warning("Image generation was unavailable, so this is a branded placeholder. The engineered prompt below can be reused elsewhere.")
    key = session.version_key(v, "image")
    alt = st.text_input("Alt text (describes the image for screen readers, max 125 characters)", im.get("alt_text", ""), key=f"ia-{key}", max_chars=125)
    if st.button("Save alt text", key=f"ias-{key}", disabled=alt == im.get("alt_text", "")):
        session.apply_edit(ctx.graph, ctx.thread, "image", {"alt_text": alt}, "Edited alt text")
        st.rerun()
    if im.get("lint"):
        st.warning("Prompt notes: " + "; ".join(im["lint"]))
    st.markdown("**Engineered prompt** (copy to reuse):")
    st.code(im.get("prompt", ""), language=None, wrap_lines=True)
    st.markdown("#### Ask for a change")
    chips(ctx, [("Different style", "Create a new image in a different style"), ("Brighter", "Make the image brighter and more colorful"),
                ("Simpler", "Make the image simpler with fewer elements")], "ic")
    custom_change(ctx, "image", "image")
    data, ext = image_bytes_and_ext(im)
    c1, c2 = st.columns(2)
    with c1:
        undo_button(ctx, "image", "image")
    if data:
        c2.download_button(f"Download image (.{ext})", data, f"image.{ext}", width="stretch")
    elif im.get("url"):
        c2.link_button("Open image", im["url"], width="stretch")


# ---------------- Quality ----------------
def quality(ctx: Ctx):
    v = ctx.vals
    st.subheader("Quality and brand voice")
    shown = False
    for kind, label in (("blog", "Blog"), ("linkedin", "LinkedIn")):
        q = (v.get(kind) or {}).get("quality")
        if q:
            shown = True
            judge = f", AI judge {q['llm_score']}" if q.get("llm_score") is not None else ", rules only"
            st.markdown(f"**{label}: {q['score']}/10** (rules {q['det_score']}{judge}) · {q.get('revisions_used', 0)} automatic revision(s)"
                        + (" · edited by you" if q.get("edited_by_user") else ""))
            check_table(q["checks"])
    vr = (v.get("final") or {}).get("voice_report") or {}
    if vr.get("metrics"):
        shown = True
        st.markdown("**Brand voice across formats**")
        st.dataframe([{"Format": k, "Avg sentence words": d["avg_sentence_words"], "'You' per 100 words": d["you_per_100"],
                       "Exclamations": d["exclamations"], "Emojis": d["emojis"], "Banned phrases": ", ".join(d["banned"]) or "none"}
                      for k, d in vr["metrics"].items()], hide_index=True, width="stretch")
        st.caption("Consistent." if vr["consistent"] else "Check: " + "; ".join(vr["flags"]))
    trace = (v.get("final") or {}).get("trace")
    if trace:
        shown = True
        st.markdown("**What each agent did this turn**")
        st.dataframe(trace, hide_index=True, width="stretch")
    if not shown:
        st.info("Scores and checks appear here after the first draft.")


# ---------------- History ----------------
def history(ctx: Ctx):
    v = ctx.vals
    st.subheader("Version history")
    hist, shown = v.get("history") or [], False
    for kind, label in (("blog", "Blog"), ("linkedin", "LinkedIn"), ("image", "Image")):
        es = entries(hist, kind)
        if not es:
            continue
        shown = True
        with st.expander(f"{label}: {len(es)} version(s)", expanded=len(es) > 1):
            st.dataframe([{"Version": e["version"], "Turn": e["turn"], "What happened": e["reason"], "Score": e.get("score"),
                           "Restored from": e.get("restored_from") or ""} for e in es], hide_index=True, width="stretch")
            vers = [e["version"] for e in es]
            c1, c2 = st.columns(2)
            a = c1.selectbox("Compare from", vers, index=max(len(vers) - 2, 0), key=f"hf-{kind}")
            b = c2.selectbox("to", vers, index=len(vers) - 1, key=f"ht-{kind}")
            ea, eb = (next(e for e in es if e["version"] == x)["artifact"] for x in (a, b))
            key = {"blog": "body", "linkedin": "text", "image": "prompt"}[kind]
            old, new = ea.get(key, ""), eb.get(key, "")
            st.caption("{words_before} → {words_after} words ({delta:+d}); +{added} added, −{removed} removed".format(**change_stats(old, new)))
            st.code(text_diff(old, new) or "No differences.", language="diff", wrap_lines=True)
            if st.button(f"Restore version {a}", key=f"hr-{kind}"):
                st.toast(session.restore_version(ctx.graph, ctx.thread, kind, a) or "Could not restore.")
                st.rerun()
    if not shown:
        st.info("Every draft, edit and restore is saved here, so you can compare and go back at any time.")


# ---------------- Export ----------------
def export(ctx: Ctx):
    v = ctx.vals
    st.subheader("Export")
    made = [k for k in ("research", "blog", "linkedin", "image") if has(v, k)]
    if not made:
        return st.info("Nothing to export yet.")
    st.download_button("Download everything (.zip)", build_export_zip(v), "contentalchemy-export.zip", mime="application/zip",
                       type="primary", help="Markdown, HTML with SEO tags, plain text, sources CSV, image, alt text, quality report, brand profile")
    st.caption("Includes: " + ", ".join(made) + ". Individual files are also on each tab.")
    st.markdown("#### Save this conversation")
    st.download_button("Session file (.json)", session.snapshot_json(v), "contentalchemy-session.json", mime="application/json",
                       help="Resume later from the sidebar: your drafts, history and chat come back exactly as they are")
    li = v.get("linkedin") or {}
    if li.get("text"):
        st.markdown("#### Copy-ready LinkedIn post")
        st.code(li["text"], language=None, wrap_lines=True)
    b = v.get("blog") or {}
    if b.get("body"):
        out = derive_blog_outputs(b, v.get("image"))
        with st.expander("Copy-ready blog (plain text)"):
            st.code(out["plain_text"], language=None, wrap_lines=True)


RENDER = {"Overview": overview, "Research": research, "Blog": blog, "LinkedIn": linkedin, "Image": image,
          "Quality": quality, "History": history, "Export": export}
