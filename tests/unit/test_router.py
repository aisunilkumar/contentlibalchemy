import pytest

from src.core.history import make_entry
from src.core.router import Route, after_quality, heuristic_route, next_node, resolve_route


@pytest.mark.parametrize("text,intent", [
    ("Write an SEO blog on remote work", "blog"),
    ("Draft a LinkedIn post about AI in healthcare", "linkedin"),
    ("Create a header image for cloud security", "image"),
    ("Research quantum computing", "research"),
    ("Run a full campaign about FinOps", "series"),
    ("hello there", "chat"),
])
def test_heuristic_intents(text, intent):
    assert heuristic_route(text, {}).intent == intent


def test_heuristic_refine_needs_existing_content():
    assert heuristic_route("make it shorter", {}).intent == "chat"
    r = heuristic_route("make it shorter", {"blog": {"body": "x"}, "topic": "t"})
    assert (r.intent, r.refine_target) == ("refine", "none")   # 'it' is resolved later from focus / last artifact
    assert heuristic_route("make the blog shorter", {"blog": {"body": "x"}}).refine_target == "blog"
    assert heuristic_route("make the image brighter", {"image": {"prompt": "p"}}).refine_target == "image"


def test_plans_are_deterministic():
    assert resolve_route(Route(intent="series", topic="x"), {})["plan"] == ["research", "blog", "linkedin", "image"]
    assert resolve_route(Route(intent="image", topic="x"), {})["plan"] == ["image"]


def test_reuses_research_for_same_topic():
    st = {"topic": "ai ops", "research": {"topic": "AI Ops", "summary": "s"}}
    r = resolve_route(Route(intent="linkedin", topic="AI Ops", is_followup=True), st)
    assert r["plan"] == ["linkedin"]


def test_explicit_research_request_always_reruns_research():
    st = {"topic": "ai ops", "research": {"topic": "AI Ops", "summary": "s"}}
    assert resolve_route(Route(intent="research", topic="AI Ops", is_followup=True), st)["plan"] == ["research"]


def test_new_topic_clears_stale_artifacts():
    st = {"topic": "old", "blog": {"body": "x"}, "research": {"topic": "old", "summary": "s"}}
    r = resolve_route(Route(intent="blog", topic="brand new"), st)
    assert r["blog"] == {} and r["research"] == {} and r["plan"] == ["research", "blog"]


def test_missing_topic_becomes_clarifying_chat():
    r = resolve_route(Route(intent="blog", topic=""), {})
    assert r["intent"] == "chat" and "topic" in r["note"].lower()


def test_refine_without_content_becomes_chat():
    assert resolve_route(Route(intent="refine", refine_target="blog"), {})["intent"] == "chat"


def test_explicit_target_never_silently_switches_artifact():
    r = resolve_route(Route(intent="refine", refine_target="blog", instruction="x"), {"linkedin": {"text": "t"}})
    assert r["intent"] == "chat" and "no blog yet" in r["note"]


def test_pronoun_resolves_from_focus_then_last_artifact_then_any():
    st = {"blog": {"body": "b"}, "linkedin": {"text": "t"}, "image": {"prompt": "p"}}
    route = Route(intent="refine", refine_target="none", instruction="shorter")
    assert resolve_route(route, {**st, "focus": "linkedin", "last_artifact": "blog"})["plan"] == ["linkedin"]
    assert resolve_route(route, {**st, "focus": "research", "last_artifact": "image"})["plan"] == ["image"]
    assert resolve_route(route, {**st, "focus": "", "last_artifact": ""})["plan"] == ["blog"]
    assert resolve_route(route, {"linkedin": {"text": "t"}})["plan"] == ["linkedin"]


def test_undo_restores_previous_version_and_logs_it():
    h1 = make_entry("blog", {"body": "v1"}, [], 1, "First draft")
    h2 = make_entry("blog", {"body": "v2"}, [h1], 2, "Refined: shorter")
    st = {"blog": {"body": "v2"}, "history": [h1, h2], "turn": 2}
    r = resolve_route(Route(intent="undo", refine_target="blog"), st)
    assert r["intent"] == "undo" and r["blog"] == {"body": "v1"} and r["plan"] == []
    assert r["history"][0]["restored_from"] == 1 and "version 1" in r["note"] and "shorter" in r["note"]


def test_undo_with_nothing_earlier_explains_instead_of_failing():
    h1 = make_entry("blog", {"body": "v1"}, [], 1, "First draft")
    r = resolve_route(Route(intent="undo", refine_target="blog"), {"blog": {"body": "v1"}, "history": [h1]})
    assert r["intent"] == "chat" and "no earlier version" in r["note"]


def test_turn_counter_increments():
    assert resolve_route(Route(intent="chat"), {"turn": 4})["turn"] == 5


def test_edges():
    assert next_node({"plan": ["research", "blog"], "step": 1}) == "blog"
    assert next_node({"plan": ["blog"], "step": 1}) == "strategist"
    assert after_quality({"quality": {"revise": True}, "last_agent": "blog", "plan": ["blog"], "step": 0}) == "blog"
    assert after_quality({"quality": {"revise": False}, "last_agent": "blog", "plan": ["blog"], "step": 1}) == "strategist"
