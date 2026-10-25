"""Multi-turn conversation reliability: pronouns, focus, refinement, undo, hand edits, save/resume."""
import json

import pytest

from src.core.deps import set_services
from src.core.session import (apply_edit, get_values, parse_snapshot, restore_snapshot, restore_version, snapshot,
                              snapshot_json, undo, version_key)
from src.core.workflow import run_turn
from src.workflow.langgraph_workflow import build_graph
from tests.fakes import FakeImage, FakeLLM, err, good_blog, make_services

TOPIC = "cloud cost optimization"


class Refiner(FakeLLM):
    """Returns a visibly different blog when the user asks for a change."""
    def _respond(self, sys_txt, user, full):
        if "Write the full blog post" in full and "USER REQUEST" in full:
            return good_blog().replace("start with visibility", "start with one small habit")
        return super()._respond(sys_txt, user, full)


@pytest.fixture
def env():
    llm, img = Refiner(), FakeImage()
    set_services(make_services(llms=[llm], images=[img]))
    yield build_graph(), llm, img
    set_services(None)


def nodes(s):
    return [t["node"] for t in s["trace"]]


def test_refine_then_undo_by_chatting(env):
    g, llm, _ = env
    run_turn(g, f"Write an SEO blog on {TOPIC}", "u1")
    s = run_turn(g, "Make the blog shorter", "u1")
    assert "one small habit" in s["blog"]["body"] and len(s["history"]) == 2
    s = run_turn(g, "undo that", "u1")
    assert nodes(s) == ["router", "strategist"] and "start with visibility" in s["blog"]["body"]
    assert "Restored your blog to version 1" in s["final"]["reply"] and "Refined" in s["final"]["reply"]
    s = run_turn(g, "undo", "u1")
    assert "Nothing further to undo" in s["final"]["reply"] and "start with visibility" in s["blog"]["body"]


def test_make_it_shorter_follows_the_view_the_user_is_on(env):
    g, llm, _ = env
    run_turn(g, f"Run a full campaign about {TOPIC}", "f1")
    s = run_turn(g, "Make it punchier", "f1", focus="linkedin")
    assert s["intent"] == "refine" and s["refine_target"] == "linkedin" and nodes(s)[:2] == ["router", "linkedin"]
    assert len([h for h in s["history"] if h["kind"] == "linkedin"]) == 2 and len([h for h in s["history"] if h["kind"] == "blog"]) == 1
    s = run_turn(g, "Make it punchier", "f1")          # no focus: falls back to the last artifact touched
    assert s["refine_target"] == "linkedin"


def test_explicit_target_beats_focus(env):
    g, *_ = env
    run_turn(g, f"Run a full campaign about {TOPIC}", "f2")
    s = run_turn(g, "Make the blog shorter", "f2", focus="linkedin")
    assert s["refine_target"] == "blog"


def test_image_refinement_keeps_format_and_passes_the_request(env):
    g, llm, img = env
    run_turn(g, f"Create an image about {TOPIC}", "i1")
    s = run_turn(g, "Make the image brighter", "i1")
    assert len(img.prompts) == 2 and s["image"]["aspect"] == "landscape"
    assert any("USER REQUEST (apply this change to the previous direction)" in p for p in llm.prompts)
    assert [h["reason"].split(":")[0] for h in s["history"]] == ["First image", "Refined"]


def test_text_refinement_reports_what_changed(env):
    g, *_ = env
    run_turn(g, f"Write an SEO blog on {TOPIC}", "c1")
    s = run_turn(g, "Make the blog shorter", "c1")
    cs = s["final"]["change_summary"]
    assert cs["kind"] == "blog" and cs["added"] > 0 and cs["removed"] > 0 and "habit" in cs["diff"]
    assert "words" in s["final"]["reply"]


def test_hand_edits_become_the_base_for_the_next_refinement(env):
    g, llm, _ = env
    run_turn(g, f"Write an SEO blog on {TOPIC}", "e1")
    edited = good_blog().replace("start with visibility", "start with MY HAND EDIT")
    art = apply_edit(g, "e1", "blog", {"title": "My Own Title About Cloud Cost Optimization", "body": edited})
    assert art["slug"] == "own-title-about-cloud-cost-optimization" or art["slug"].startswith("my-own-title")
    assert art["quality"]["edited_by_user"] and art["quality"]["llm_score"] is None
    v = get_values(g, "e1")
    assert v["history"][-1]["reason"] == "Edited by you" and v["last_artifact"] == "blog"
    run_turn(g, "Make the blog shorter", "e1")
    assert any("USER REQUEST" in p and "MY HAND EDIT" in p for p in llm.prompts)


def test_hand_edit_gives_instant_rule_feedback_without_llm_calls(env):
    g, llm, _ = env
    run_turn(g, f"Write an SEO blog on {TOPIC}", "e2")
    calls = llm.calls
    art = apply_edit(g, "e2", "blog", {"body": "# Only a heading\n\nToo short."})
    assert llm.calls == calls and "word_count" in art["quality"]["failed"] and art["quality"]["score"] < 8


def test_undo_and_restore_helpers_for_ui_buttons(env):
    g, *_ = env
    run_turn(g, f"Write an SEO blog on {TOPIC}", "h1")
    assert undo(g, "h1", "blog") is None                       # only one version
    run_turn(g, "Make the blog shorter", "h1")
    assert "version 1" in undo(g, "h1", "blog") and "start with visibility" in get_values(g, "h1")["blog"]["body"]
    assert "version 2" in restore_version(g, "h1", "blog", 2) and "one small habit" in get_values(g, "h1")["blog"]["body"]
    assert restore_version(g, "h1", "blog", 99) is None


def test_version_key_changes_when_content_changes(env):
    g, *_ = env
    run_turn(g, f"Write an SEO blog on {TOPIC}", "k1")
    k1 = version_key(get_values(g, "k1"), "blog")
    apply_edit(g, "k1", "blog", {"body": good_blog() + "\n\nExtra."})
    assert version_key(get_values(g, "k1"), "blog") != k1


def test_save_and_resume_in_a_fresh_session(env):
    g, llm, _ = env
    run_turn(g, f"Run a full campaign about {TOPIC}", "s1")
    run_turn(g, "Make the blog shorter", "s1")
    raw = snapshot_json(get_values(g, "s1"))
    g2 = build_graph()                                          # brand-new graph + thread: like restarting the app
    restore_snapshot(g2, "s2", parse_snapshot(raw))
    v1, v2 = get_values(g, "s1"), get_values(g2, "s2")
    assert v2["blog"]["body"] == v1["blog"]["body"] and v2["topic"] == TOPIC and len(v2["messages"]) == len(v1["messages"])
    assert [h["version"] for h in v2["history"]] == [h["version"] for h in v1["history"]]
    s = run_turn(g2, "undo that", "s2")                          # history survived the round trip
    assert "start with visibility" in s["blog"]["body"]
    s = run_turn(g2, "Make the blog shorter", "s2")              # and context drives the next refinement
    assert s["refine_target"] == "blog" and any("USER REQUEST" in p for p in llm.prompts)


def test_snapshot_roundtrips_binary_images():
    from src.core.session import _dec, _enc
    assert _dec(json.loads(json.dumps(_enc({"image": {"data": b"\x89PNG\x00\xff"}})))) == {"image": {"data": b"\x89PNG\x00\xff"}}
    assert "messages" in snapshot({}) and snapshot({})["schema"] == 1


@pytest.mark.parametrize("raw,msg", [
    ("not json", "valid JSON"),
    (json.dumps({"schema": 99}), "schema"),
    (json.dumps([1, 2]), "schema"),
    (json.dumps({"schema": 1, "blog": "oops"}), "wrong type"),
    (json.dumps({"schema": 1, "messages": "x"}), "must be lists"),
    ("x" * 8_000_001, "too large"),
])
def test_imported_files_are_validated(raw, msg):
    with pytest.raises(ValueError, match=msg):
        parse_snapshot(raw)


def test_undo_still_works_when_every_llm_is_down(env):
    g, *_ = env
    run_turn(g, f"Write an SEO blog on {TOPIC}", "d1")
    run_turn(g, "Make the blog shorter", "d1")
    set_services(make_services(llms=[FakeLLM(fail=err("down"))]))
    s = run_turn(g, "undo that", "d1")                           # keyword routing handles it
    assert s["intent"] == "undo" and "start with visibility" in s["blog"]["body"]
