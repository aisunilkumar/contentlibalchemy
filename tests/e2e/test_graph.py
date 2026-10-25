"""End-to-end graph tests with fake providers: routing, state, loops, memory, degradation."""
import uuid

from src.core.workflow import run_turn
from src.integrations.base import ProviderError
from src.core.deps import set_services
from src.workflow.langgraph_workflow import build_graph
from tests.fakes import FakeImage, FakeLLM, FakeSearch, err, make_services


def turn(graph, text, tid, **kw):
    return run_turn(graph, text, tid, **kw)


def nodes(state):
    return [t["node"] for t in state["trace"]]


def test_blog_flow_order_and_artifacts(graph):
    s = turn(graph, "Write an SEO blog on cloud cost optimization", "t1")
    assert nodes(s) == ["router", "research", "blog", "quality", "strategist"]
    assert s["intent"] == "blog" and s["topic"] == "cloud cost optimization"
    assert s["research"]["sources"] and not s["research"]["unverified"]
    assert s["blog"]["title"] and len(s["blog"]["title"]) <= 60
    assert s["blog"]["quality"]["score"] >= 7.5
    assert s["final"]["blog"]["markdown"].startswith("---")
    assert s["final"]["usage"]["total_usd"] > 0


def test_series_runs_all_six_agents(graph):
    s = turn(graph, "Run a full campaign about cloud cost optimization", "t2")
    assert nodes(s) == ["router", "research", "blog", "quality", "linkedin", "quality", "image", "strategist"]
    f = s["final"]
    assert f["blog"]["body"] and f["linkedin"]["text"] and f["image"]["url"] and f["research"]["report_md"]
    assert f["next_steps"]


def test_revision_loop_improves_bad_first_draft():
    llm = FakeLLM(bad_first=True)
    set_services(make_services(llms=[llm]))
    s = turn(build_graph(), "Write an SEO blog on cloud cost optimization", "t3")
    assert nodes(s).count("blog") == 2 and nodes(s).count("quality") == 2
    assert s["blog"]["quality"]["revisions_used"] == 1
    assert s["blog"]["quality"]["score"] >= 7.5 and "BADDRAFT" not in s["blog"]["body"]
    assert any("REVISION FEEDBACK" in p for p in llm.prompts)
    set_services(None)


def test_revision_cap_prevents_infinite_loop():
    class AlwaysBad(FakeLLM):
        def _respond(self, sys_txt, user, full):
            if "Write the full blog post" in full:
                return "# Bad\n\nBADDRAFT"
            return super()._respond(sys_txt, user, full)
    set_services(make_services(llms=[AlwaysBad()]))
    s = turn(build_graph(), "Write an SEO blog on cloud cost optimization", "t4")
    assert nodes(s).count("blog") == 3  # first draft + max_revisions(2)
    assert s["blog"]["quality"]["below_threshold"] and any("after 2 revision" in f for f in s["final"]["flags"])
    set_services(None)


def test_multi_turn_followup_reuses_topic_and_research(graph):
    tid = str(uuid.uuid4())
    turn(graph, "Write an SEO blog on cloud cost optimization", tid)
    s = turn(graph, "Now a LinkedIn post", tid)
    assert nodes(s) == ["router", "linkedin", "quality", "strategist"]  # research skipped
    assert s["topic"] == "cloud cost optimization"
    assert s["linkedin"]["text"] and s["blog"]["body"]          # earlier blog still available
    assert len(s["messages"]) == 4                              # conversation memory kept


def test_refine_reruns_writer_with_instruction(graph, llm):
    tid = "t6"
    turn(graph, "Write an SEO blog on cloud cost optimization", tid)
    s = turn(graph, "Make the blog shorter", tid)
    assert s["intent"] == "refine" and nodes(s)[:3] == ["router", "blog", "quality"]
    assert any("USER REQUEST (apply exactly this change" in p and "shorter" in p for p in llm.prompts)


def test_new_topic_clears_old_artifacts(graph):
    tid = "t7"
    turn(graph, "Write a LinkedIn post about cloud cost optimization", tid)
    s = turn(graph, "Research quantum computing", tid)
    assert s["topic"] == "quantum computing" and not s["linkedin"] and s["research"]["topic"] == "quantum computing"


def test_chat_and_missing_topic(graph):
    s = turn(graph, "hello", "t8")
    assert s["intent"] == "chat" and nodes(s) == ["router", "strategist"] and s["final"]["reply"]
    s = turn(graph, "Write a linkedin post", "t9")
    assert "topic" in s["final"]["reply"].lower()


def test_errors_and_trace_reset_each_turn(graph):
    tid = "t10"
    turn(graph, "Write an SEO blog on cloud cost optimization", tid)
    s = turn(graph, "hello", tid)
    assert nodes(s) == ["router", "strategist"]


# ---------------- degradation ----------------
def test_primary_llm_down_falls_back_to_second_provider():
    a, b = FakeLLM("primary", fail=err("outage")), FakeLLM("secondary")
    set_services(make_services(llms=[a, b]))
    s = turn(build_graph(), "Write an SEO blog on cloud cost optimization", "d1")
    assert s["blog"]["body"] and b.calls > 0
    assert "secondary" in s["final"]["usage"]["by_provider"] and "primary" not in s["final"]["usage"]["by_provider"]
    set_services(None)


def test_all_search_down_flags_research_unverified():
    set_services(make_services(search=[FakeSearch(fail=err("down"))]))
    s = turn(build_graph(), "Write an SEO blog on cloud cost optimization", "d2")
    assert s["research"]["unverified"] and s["blog"]["body"]
    assert any("unverified" in f.lower() for f in s["final"]["flags"])
    set_services(None)


def test_image_outage_degrades_to_branded_svg_card():
    set_services(make_services(images=[FakeImage(fail=err("down"))]))
    s = turn(build_graph(), "Create an image about cloud cost optimization", "d3")
    im = s["image"]
    assert im["fallback"] == "svg_card" and im["svg"].startswith("<svg") and im["prompt"] and im["error"]
    assert im["alt_text"] and any("placeholder" in f for f in s["final"]["flags"])
    set_services(None)


def test_router_llm_down_uses_keyword_routing():
    set_services(make_services(llms=[FakeLLM(fail=err("down"))]))
    s = turn(build_graph(), "Write a LinkedIn post about cloud cost optimization", "d4")
    # all LLMs down: the router degrades; downstream nodes degrade without crashing the graph
    assert s["intent"] == "linkedin" and "strategist" in nodes(s)
    assert any("keyword routing" in e for e in s["errors"])
    set_services(None)


def test_node_crash_never_kills_the_graph():
    class Boom(FakeSearch):
        def search(self, q, num=6):
            raise RuntimeError("unexpected bug")
    set_services(make_services(search=[Boom()]))
    s = turn(build_graph(), "Write an SEO blog on cloud cost optimization", "d5")
    assert s["blog"]["body"] and "strategist" in nodes(s)
    set_services(None)


def test_budget_exhaustion_degrades_gracefully():
    set_services(make_services(budget=0.0005))
    s = turn(build_graph(), "Write an SEO blog on cloud cost optimization", "d6")
    assert "strategist" in nodes(s) and s["final"]["reply"]
    assert any("budget" in e.lower() for e in s["errors"])
    set_services(None)


def test_stream_emits_per_node_updates(graph):
    seen = []
    run_turn(graph, "Write an SEO blog on cloud cost optimization", "s1", on_update=lambda n, u: seen.append(n))
    assert seen[0] == "router" and seen[-1] == "strategist" and "quality" in seen
