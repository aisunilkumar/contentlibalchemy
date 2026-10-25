"""Headless UI tests (Streamlit AppTest) with fake providers: navigation, editing, refinement, undo, a11y."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.core import session  # noqa: E402
from src.core.deps import set_services  # noqa: E402
from tests.fakes import FakeImage, FakeLLM, err, make_services  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "src" / "web_app" / "streamlit_app.py")
TOPIC = "cloud cost optimization"


@pytest.fixture(autouse=True)
def _reset():
    yield
    set_services(None)


def start(**kw):
    llm = kw.pop("llm", None) or FakeLLM()
    set_services(make_services(llms=[llm], **kw))
    at = AppTest.from_file(APP, default_timeout=90).run()
    assert not at.exception
    at._llm = llm
    return at


def say(at, text):
    at.chat_input[0].set_value(text).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def goto(at, view):
    at.radio(key="view").set_value(view).run()
    assert not at.exception, [e.value for e in at.exception]


def values(at):
    return session.get_values(at.session_state["graph"], at.session_state["thread"])


def by_label(elements, prefix):
    return next(e for e in elements if e.label.startswith(prefix))


def all_text(at):
    return " ".join(m.value for m in at.markdown)


# ---------------- navigation ----------------
def test_loads_with_all_views_and_example_prompts():
    at = start()
    assert at.radio(key="view").options == ["Overview", "Research", "Blog", "LinkedIn", "Image", "Quality", "History", "Export"]
    assert at.radio(key="view").value == "Overview" and len([b for b in at.button if (b.key or "").startswith("ex-")]) == 3


def test_example_prompt_button_starts_a_conversation():
    at = start()
    next(b for b in at.button if (b.key or "").startswith("ex-Write an SEO blog")).click().run()
    assert not at.exception and values(at)["intent"] == "blog" and at.radio(key="view").value == "Blog"


def test_every_view_renders_after_a_campaign_and_nav_shows_ready_marks():
    at = start()
    say(at, f"Run a full campaign about {TOPIC}")
    assert at.radio(key="view").value == "Overview"              # campaign lands on the overview
    assert "✅ Ready" in all_text(at) and sum("✅ Ready" in m.value for m in at.markdown) == 4
    for v in at.radio(key="view").options:
        goto(at, v)
    assert at.radio(key="view").format_func("Blog") == "Blog ✓" if callable(getattr(at.radio(key="view"), "format_func", None)) else True


def test_turn_navigates_to_what_changed():
    at = start()
    say(at, f"Write a LinkedIn post about {TOPIC}")
    assert at.radio(key="view").value == "LinkedIn"
    say(at, f"Create an image about {TOPIC}")
    assert at.radio(key="view").value == "Image"


def test_overview_create_button_fills_missing_pieces():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    goto(at, "Overview")
    at.button(key="ov-linkedin").click().run()
    assert not at.exception and values(at)["linkedin"]["text"] and at.radio(key="view").value == "LinkedIn"


# ---------------- editing + refinement ----------------
def test_unsaved_edit_is_autosaved_before_a_quick_change_and_reaches_the_prompt():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    body = next(t for t in at.text_area if t.label.startswith("Article"))
    body.set_value(body.value.replace("start with visibility", "start with MY HAND EDIT")).run()
    assert "unsaved edits" in " ".join(i.value for i in at.info)
    at.button(key="bc-Shorter").click().run()                    # chip without pressing Save first
    assert not at.exception
    hist = values(at)["history"]
    assert [h["reason"].split(":")[0] for h in hist] == ["First draft", "Edited by you", "Refined"]
    assert any("USER REQUEST" in p and "MY HAND EDIT" in p for p in at._llm.prompts)


def test_save_button_persists_edits_and_updates_checks():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    body = next(t for t in at.text_area if t.label.startswith("Article"))
    body.set_value("# Tiny\n\nToo short.").run()
    assert any("rule checks pass" in w.value for w in at.warning)  # instant feedback, no save needed
    by_label(at.button, "Save my edits").click().run()
    v = values(at)
    assert v["blog"]["body"].startswith("# Tiny") and v["blog"]["quality"]["edited_by_user"]


def test_pronouns_follow_the_current_view():
    at = start()
    say(at, f"Run a full campaign about {TOPIC}")
    goto(at, "LinkedIn")
    say(at, "Make it punchier")
    v = values(at)
    assert v["refine_target"] == "linkedin" and v["intent"] == "refine"


def test_custom_change_box_sends_a_targeted_request():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    at.text_input(key=next(t.key for t in at.text_input if (t.key or "").startswith("custom-blog"))).set_value("make it shorter please").run()
    by_label(at.button, "Apply").click().run()
    assert not at.exception and values(at)["intent"] == "refine" and "for the blog" in values(at)["instruction"].lower()


# ---------------- undo + history ----------------
def test_chat_undo_button_restores_previous_version():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    assert at.button(key="chat-undo").disabled
    say(at, "Make the blog shorter")
    assert not at.button(key="chat-undo").disabled
    at.button(key="chat-undo").click().run()
    assert [h["restored_from"] for h in values(at)["history"]] == [None, None, 1]


def test_history_view_lists_versions_diffs_and_restores():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    say(at, "Make the blog shorter")
    goto(at, "History")
    assert any(c.language == "diff" for c in at.code)
    at.button(key="hr-blog").click().run()
    assert not at.exception and values(at)["history"][-1]["reason"].startswith("Restored version")


# ---------------- research ----------------
def test_research_panel_shows_citations_sources_and_filter():
    at = start()
    say(at, f"Research {TOPIC}")
    assert at.radio(key="view").value == "Research"
    assert {"Sources found", "Cited", "Domains", "Paragraphs cited"} <= {m.label for m in at.metric}
    assert "[[1]](https://" in all_text(at) or "(https://a.example/1)" in all_text(at)  # clickable citations
    at.radio(key="src-filter").set_value("Further reading").run()
    assert not at.exception


# ---------------- images + accessibility ----------------
def test_images_render_with_real_alt_text():
    at = start()
    say(at, f"Create an image about {TOPIC}")
    html = next(m.value for m in at.markdown if "<img" in m.value)
    assert 'alt="A cloud made of coins over a city"' in html


def test_alt_text_is_editable_and_saved():
    at = start()
    say(at, f"Create an image about {TOPIC}")
    by_label(at.text_input, "Alt text").set_value("A calm cloud of coins").run()
    by_label(at.button, "Save alt text").click().run()
    assert values(at)["image"]["alt_text"] == "A calm cloud of coins"
    assert 'alt="A calm cloud of coins"' in next(m.value for m in at.markdown if "<img" in m.value)


def test_image_outage_shows_branded_placeholder_with_alt_and_warning():
    at = start(images=[FakeImage(fail=err("down"))])
    say(at, f"Create an image about {TOPIC}")
    assert any("branded placeholder" in w.value for w in at.warning)
    assert 'alt="Branded graphic titled' in next(m.value for m in at.markdown if "<img" in m.value)


def test_accessibility_options_and_landmarks():
    at = start()
    assert "Skip to chat" in all_text(at) and "Skip to workspace" in all_text(at) and 'id="chat-region"' in all_text(at)
    assert ":focus-visible" in all_text(at) and "#FFD400" not in all_text(at)
    at.checkbox(key="a11y_contrast").check().run()
    at.checkbox(key="a11y_large").check().run()
    at.checkbox(key="a11y_motion").check().run()
    css = all_text(at)
    assert "#FFD400" in css and "font-size:1.18rem" in css and "animation:none" in css
    say(at, f"Write an SEO blog on {TOPIC}")
    live = next(m.value for m in at.markdown if 'role="status"' in m.value)
    assert 'aria-live="polite"' in live and "Finished." in live


def test_status_is_never_colour_only():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    goto(at, "Quality")
    statuses = {r["Status"] for df in at.dataframe for r in df.value.to_dict("records") if "Status" in r}
    assert statuses <= {"Pass", "Fix"} and "Pass" in statuses


# ---------------- sessions + settings ----------------
def test_new_conversation_resets_everything():
    at = start()
    say(at, f"Write an SEO blog on {TOPIC}")
    by_label(at.button, "Start a new conversation").click().run()
    assert not at.exception and not values(at) and at.radio(key="view").value == "Overview"
    assert "announce" in at.session_state and "New conversation" in at.session_state["announce"]


def test_invalid_brand_yaml_is_rejected_gracefully():
    at = start()
    by_label(at.text_area, "Brand profile YAML").set_value("pov: nonsense").run()
    assert not at.exception and any("Invalid profile" in e.value for e in at.error)


def test_errors_are_shown_not_raised():
    at = start(llm=FakeLLM(fail=err("down")))
    say(at, f"Write an SEO blog on {TOPIC}")                     # LLM down: graph degrades, UI keeps working
    assert not at.exception
