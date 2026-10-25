"""ContentAlchemy UI. Run: streamlit run src/web_app/streamlit_app.py

Layout: chat on the left, a multi-view workspace on the right (stacks vertically on phones).
All content lives in the LangGraph checkpoint, so the chat, the views, undo and export never disagree."""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # so `src.*` imports work from any cwd

import streamlit as st  # noqa: E402
import yaml  # noqa: E402

from src.core import session  # noqa: E402
from src.core.brand import load_brand_profile, parse_brand_yaml  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.core.deps import get_services  # noqa: E402
from src.core.history import entries  # noqa: E402
from src.core.workflow import run_turn  # noqa: E402
from src.utils.content_optimization import strip_markdown  # noqa: E402
from src.utils.messages import to_chat  # noqa: E402
from src.web_app.components.styles import SKIP_LINKS, anchor, build_css, live_announcement  # noqa: E402
from src.web_app.components.views import FOCUS, RENDER, VIEWS, Ctx, has  # noqa: E402
from src.workflow.langgraph_workflow import build_graph  # noqa: E402

LABELS = {"router": "Understanding your request", "research": "Researching (multi-source)",
          "blog": "Writing the SEO blog", "linkedin": "Planning + writing the LinkedIn post",
          "image": "Engineering the image prompt", "quality": "Scoring quality + brand voice",
          "strategist": "Assembling results"}
VIEW_AFTER = {"blog": "Blog", "linkedin": "LinkedIn", "image": "Image", "research": "Research", "series": "Overview"}

st.set_page_config(page_title="ContentAlchemy", page_icon="⚗️", layout="wide", initial_sidebar_state="expanded")
ss = st.session_state
if "graph" not in ss:
    ss.graph, ss.thread, ss.view = build_graph(), str(uuid.uuid4()), "Overview"
    ss.brand_yaml = yaml.safe_dump(load_brand_profile().model_dump(), sort_keys=False, allow_unicode=True)
    ss.brand_ok = load_brand_profile().model_dump()


def submit(text: str) -> None:
    """Queue a chat message (from a button) and rerun; it is processed at the top of the next run."""
    ss.pending = text
    st.rerun()


def values() -> dict:
    return session.get_values(ss.graph, ss.thread)


def view_after(v: dict) -> str | None:
    intent = v.get("intent")
    if intent in ("refine", "undo"):
        return VIEW_AFTER.get(v.get("refine_target"))
    return VIEW_AFTER.get(intent)


# ---------------- global styles + accessibility ----------------
st.markdown(build_css(ss.get("a11y_contrast", False), ss.get("a11y_large", False), ss.get("a11y_motion", False)),
            unsafe_allow_html=True)
st.markdown(SKIP_LINKS, unsafe_allow_html=True)
st.markdown(live_announcement(ss.get("announce", "")), unsafe_allow_html=True)
st.title("⚗️ ContentAlchemy")

# ---------------- sidebar ----------------
with st.sidebar:
    with st.expander("Brand profile (applies to every format)"):
        text = st.text_area("Brand profile YAML", ss.brand_yaml, height=300,
                            help="Tone, banned phrases, per-platform rules and visual identity. Checked automatically.")
        ss.brand_yaml = text
        try:
            ss.brand_ok = parse_brand_yaml(text).model_dump()
            st.caption("✓ Valid profile")
        except ValueError as e:
            st.error(f"Invalid profile, using the last valid one: {e}")
    with st.expander("Accessibility and display"):
        st.checkbox("High contrast", key="a11y_contrast")
        st.checkbox("Larger text", key="a11y_large")
        st.checkbox("Reduce motion", key="a11y_motion", help="Also follows your system setting automatically")
        st.caption("Keyboard: Tab to move, Enter or Space to press, arrow keys to switch workspace views.")
    with st.expander("Session"):
        up = st.file_uploader("Resume a saved session (.json)", type=["json"], key="uploader")
        if up is not None and ss.get("last_import") != (up.name, up.size):
            try:
                snap = session.parse_snapshot(up.getvalue())
                ss.thread = str(uuid.uuid4())
                session.restore_snapshot(ss.graph, ss.thread, snap)
                ss.last_import, ss.view = (up.name, up.size), "Overview"
                ss.announce = "Session restored."
                st.rerun()
            except ValueError as e:
                st.error(f"Could not load that file: {e}")
        if st.button("Start a new conversation", width="stretch"):
            ss.thread, ss.view, ss.pending, ss.announce = str(uuid.uuid4()), "Overview", None, "New conversation started."
            st.rerun()
    try:
        svc = get_services()
        with st.expander("Providers and spend"):
            for cap, names in svc.status().items():
                st.write(f"**{cap}**: {' → '.join(names) or 'none configured'}")
            u = svc.stats()
            st.metric("Spend this session", f"${u['total_usd']:.3f}", help=f"Budget ${u['budget_usd']:.2f}")
            st.caption(f"Cache hits: {u['cache_hits']} · Quality threshold: {settings.quality_threshold}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Service setup failed: {e}")

vals = values()
chat_col, work_col = st.columns([1, 1.7], gap="large")

# ---------------- chat ----------------
with chat_col:
    st.markdown(anchor("chat-region"), unsafe_allow_html=True)
    st.subheader("Chat")
    box = st.container(height=520, border=True)
    typed = st.chat_input("Ask for content, or a change (e.g. 'make it shorter')")
    prompt = ss.pop("pending", None) or typed
    if prompt:
        ss.last_error = None
        try:
            with box:
                st.chat_message("user").markdown(prompt)
                with st.status("Agents working…", expanded=True) as status:
                    def on_update(node, upd):
                        status.write(f"✓ {LABELS.get(node, node)}")
                        q = upd.get("quality") or {}
                        if node == "quality" and q.get("revise"):
                            status.write(f"↻ Score {q['score']}/10, fixing: {', '.join(q.get('failed', [])) or 'low score'}")
                    after = run_turn(ss.graph, prompt, ss.thread, brand_profile=ss.brand_ok,
                                     focus=FOCUS.get(ss.view, ""), on_update=on_update)
                    status.update(label="Done", state="complete", expanded=False)
            nav = view_after(after)
            if nav:
                ss.view = nav  # take the user to what just changed
            ss.announce = "Finished. " + strip_markdown((after.get("final") or {}).get("reply", ""))[:200]
        except Exception as e:  # noqa: BLE001
            ss.last_error, ss.announce = str(e), "Something went wrong."
        st.rerun()

    with box:
        if ss.get("last_error"):
            st.error(f"Something went wrong: {ss.last_error}")
        msgs = to_chat(vals.get("messages", []))
        if not msgs:
            st.info("Try: *Write an SEO blog on cloud cost optimization*, then *Now a LinkedIn post*, then "
                    "*Make it shorter*. Say *undo that* to go back.")
        for m in msgs:
            st.chat_message(m["role"]).markdown(m["content"])

    target = vals.get("last_artifact")
    can_undo = bool(target) and len(entries(vals.get("history"), target)) >= 2
    if st.button("↶ Undo last change", disabled=not can_undo, key="chat-undo",
                 help=f"Go back to the previous {target}" if target else "Nothing to undo yet"):
        st.toast(session.undo(ss.graph, ss.thread, target) or "Nothing to undo.")
        st.rerun()
    steps = ((vals.get("final") or {}).get("next_steps") or [])[:3]
    if steps:
        st.caption("Suggested next steps")
        for i, s_ in enumerate(steps):
            if st.button(s_, key=f"step-{i}"):
                submit(s_)

# ---------------- workspace ----------------
with work_col:
    st.markdown(anchor("workspace-region"), unsafe_allow_html=True)
    st.subheader("Workspace")
    ready = {"Research": has(vals, "research"), "Blog": has(vals, "blog"), "LinkedIn": has(vals, "linkedin"), "Image": has(vals, "image")}
    view = st.radio("Workspace view", VIEWS, horizontal=True, key="view", label_visibility="collapsed",
                    format_func=lambda x: f"{x} ✓" if ready.get(x) else x)
    RENDER[view](Ctx(ss.graph, ss.thread, vals, submit))
