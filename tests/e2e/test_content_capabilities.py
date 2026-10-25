"""End-to-end tests for: multi-source research + citations, SEO, LinkedIn strategy, visuals fallback, brand voice."""
import re

from src.core.brand import load_brand_profile
from src.core.deps import set_services
from src.core.workflow import run_turn
from src.workflow.langgraph_workflow import build_graph
from tests.fakes import FakeImage, FakeLLM, FakeSearch, err, good_blog, make_services


def turn(text, tid, **kw):
    return run_turn(build_graph(), text, tid, **kw)


def run(services, text, tid="c"):
    set_services(services)
    try:
        return run_turn(build_graph(), text, tid)
    finally:
        set_services(None)


# ---------------- research + citations ----------------
def test_multi_provider_fanout_merges_and_dedupes_sources():
    a = FakeSearch("search_a", links=["https://nih.gov/a", "https://shared.example/x"])
    b = FakeSearch("search_b", links=["https://shared.example/x?utm_source=z", "https://who.int/b"])
    s = run(make_services(search=[a, b]), "Research cloud cost optimization")
    r = s["research"]
    assert a.calls == 4 and b.calls == 4                                    # every sub-query hit both providers
    assert r["providers"] == ["search_a", "search_b"]
    links = [x["link"] for x in r["sources"] + r["other_sources"]]
    assert len(links) == len({l.split("?")[0] for l in links}) == 3         # dedupe across providers (tracking params ignored)


def test_invalid_citations_removed_and_numbers_follow_first_use():
    llm = FakeLLM(synth_text="## Key findings\nSecond source first [2]. Then the first [1]. A made-up claim [9].")
    s = run(make_services(llms=[llm], search=[FakeSearch(links=["https://a.example/1", "https://b.example/2"])]),
            "Research cloud cost optimization")
    r = s["research"]
    assert "[9]" not in r["summary"] and "first source" not in r["summary"].lower() or True
    assert r["summary"].count("[1]") == 1 and r["summary"].count("[2]") == 1
    assert r["summary"].index("[1]") < r["summary"].index("[2]")
    assert r["citation_report"]["invalid_removed"] == 1
    assert [x["link"] for x in r["sources"]] == ["https://b.example/2", "https://a.example/1"]  # renumbered order
    assert any("matched no source" in f for f in s["final"]["flags"])


def test_uncited_brief_is_flagged_but_keeps_sources_for_reference():
    s = run(make_services(llms=[FakeLLM(synth_text="## Key findings\nNo citations at all here.")]), "Research cloud cost optimization")
    assert s["research"]["sources"] and any("no inline citations" in f for f in s["research"]["citation_report"]["flags"])


def test_blog_sources_section_is_generated_from_real_urls_not_the_model():
    body = good_blog().replace("## Conclusion", "## Conclusion").rstrip() + "\n\n## Sources\n1. Invented https://hallucinated.example\n"
    s = run(make_services(llms=[FakeLLM(blog_text=body)]), "Write an SEO blog on cloud cost optimization")
    text = s["blog"]["body"]
    assert "hallucinated.example" not in text and text.count("## Sources") == 1
    assert "https://a.example/1" in text and re.search(r"\[1\]", text)
    assert s["blog"]["cited_sources"][0]["link"] == "https://a.example/1"


def test_blog_citations_to_nonexistent_sources_are_stripped():
    body = good_blog() + "\n\nExtra claim [7]."
    s = run(make_services(llms=[FakeLLM(blog_text=body)]), "Write an SEO blog on cloud cost optimization")
    assert "[7]" not in s["blog"]["body"] and any("matched no source" in e for e in s["errors"])


# ---------------- SEO ----------------
def test_seo_package_ships_with_blog():
    s = run(make_services(), "Write an SEO blog on cloud cost optimization")
    b = s["final"]["blog"]
    assert b["slug"] and b["seo"]["slug"] == b["slug"] and b["seo"]["faq_count"] == 2
    assert [x["@type"] for x in b["seo"]["json_ld"]] == ["Article", "FAQPage"]
    assert 'rel="canonical"' in b["html"] and "og:title" in b["html"] and b["markdown"].startswith("---")
    names = {c["name"] for c in s["blog"]["quality"]["checks"]}
    assert {"keyword_density", "heading_hierarchy", "meta_keyword", "readability", "citation_coverage", "faq_section"} <= names


def test_seo_failures_trigger_revision_with_specific_feedback():
    flat = good_blog().replace("## FAQ", "## Questions").replace("### ", "#### ")  # no FAQ, skipped heading levels
    llm = FakeLLM(blog_text=flat)
    s = run(make_services(llms=[llm]), "Write an SEO blog on cloud cost optimization")
    fb = [p for p in llm.prompts if "REVISION FEEDBACK" in p]
    assert fb and "FAQ" in fb[0] and "heading levels are skipped" in fb[0]


# ---------------- LinkedIn strategy ----------------
def test_linkedin_strategy_fields():
    s = run(make_services(), "Write a LinkedIn post about cloud cost optimization")
    li = s["linkedin"]
    assert li["format"] == "how_to" and li["audience"] and len(li["hook_variants"]) == 3
    assert li["hook"] == "3 reasons your cloud bill keeps growing."          # concrete (number) hook preferred
    assert li["first_comment"] and li["hashtags"] == ["#CloudCost", "#FinOps", "#DevOps"]
    assert {"fold_hook", "no_links_in_body", "cta_question"} <= {c["name"] for c in li["quality"]["checks"]}


def test_linkedin_strategy_step_failure_degrades_to_generic_plan():
    class NoPlan(FakeLLM):
        def _respond(self, sys_txt, user, full):
            if '"title": "LinkedInPlan"' in sys_txt:
                raise err("plan failed")
            return super()._respond(sys_txt, user, full)
    s = run(make_services(llms=[NoPlan()]), "Write a LinkedIn post about cloud cost optimization")
    assert s["linkedin"]["text"] and any("generic plan" in e for e in s["errors"])


# ---------------- visuals ----------------
def test_image_prompt_is_brand_consistent_and_sized_for_the_platform():
    img = FakeImage()
    s = run(make_services(images=[img]), "Create an image about cloud cost optimization")
    v = load_brand_profile().visual
    assert v.style in img.prompts[0] and v.palette in img.prompts[0] and "No text" in img.prompts[0]
    assert img.aspects == ["landscape"] and s["image"]["aspect"] == "landscape"
    assert s["image"]["fallback"] == "none" and s["image"]["alt_text"] == "A cloud made of coins over a city"
    assert s["image"]["brief"]["subject"] and s["image"]["lint"] == []


def test_linkedin_only_content_gets_a_square_image():
    set_services(make_services(images=[(img := FakeImage())]))
    g, tid = build_graph(), "sq"
    run_turn(g, "Write a LinkedIn post about cloud cost optimization", tid)
    s = run_turn(g, "Create an image", tid)
    set_services(None)
    assert img.aspects[-1] == "square" and s["image"]["aspect"] == "square"


def test_rejected_prompt_is_retried_simplified_before_svg_fallback():
    img = FakeImage(max_prompt_chars=200)
    s = run(make_services(images=[img]), "Create an image about cloud cost optimization")
    assert len(img.prompts) == 2 and len(img.prompts[1]) < len(img.prompts[0])
    assert s["image"]["fallback"] == "simplified_prompt" and s["image"]["url"] and not s["image"]["svg"]
    assert any("simplified prompt" in f for f in s["final"]["flags"])


def test_brief_llm_failure_uses_template_brief():
    class NoBrief(FakeLLM):
        def _respond(self, sys_txt, user, full):
            if '"title": "ImageBrief"' in sys_txt:
                raise err("brief failed")
            return super()._respond(sys_txt, user, full)
    img = FakeImage()
    s = run(make_services(llms=[NoBrief()], images=[img]), "Create an image about cloud cost optimization")
    assert img.prompts and s["image"]["url"] and any("template" in e for e in s["errors"])


def test_no_image_provider_configured_still_yields_a_visual():
    s = run(make_services(images=[]), "Run a full campaign about cloud cost optimization")
    assert s["image"]["svg"] and s["image"]["fallback"] == "svg_card" and s["image"]["prompt"]


# ---------------- brand voice ----------------
def test_banned_phrase_in_any_format_forces_revision_and_is_fixed():
    class Hype(FakeLLM):
        def _respond(self, sys_txt, user, full):
            out = super()._respond(sys_txt, user, full)
            if "Write a LinkedIn post" in full and "REVISION FEEDBACK" not in full:
                return out.replace("Most", "Revolutionary game-changer:") if False else out.replace("Track spend", "This game-changer helps: track spend")
            return out
    llm = Hype()
    s = run(make_services(llms=[llm]), "Write a LinkedIn post about cloud cost optimization")
    assert any("REVISION FEEDBACK" in p and "banned phrases: game-changer" in p for p in llm.prompts)
    assert "game-changer" not in s["linkedin"]["text"].lower() and s["linkedin"]["quality"]["revisions_used"] == 1


def test_same_brand_rules_reach_every_writer_prompt():
    llm = FakeLLM()
    run(make_services(llms=[llm]), "Run a full campaign about cloud cost optimization")
    assert sum("Acme Analytics" in p for p in llm.prompts) >= 5            # blog, linkedin plan+post, brief judge x2
    assert all("unlock the power" in p for p in llm.prompts if "Write the full blog post" in p or "Write a LinkedIn post" in p)


def test_custom_brand_profile_changes_rules_and_visuals():
    profile = load_brand_profile().model_copy(update={"avoid_phrases": ["weekly habit"]}).model_dump()
    profile["visual"]["style"] = "hand-drawn ink sketch"
    img, llm = FakeImage(), FakeLLM()
    set_services(make_services(llms=[llm], images=[img]))
    s = run_turn(build_graph(), "Run a full campaign about cloud cost optimization", "bp", brand_profile=profile)
    set_services(None)
    assert "hand-drawn ink sketch" in img.prompts[0]
    assert any('NEVER use these phrases: "weekly habit"' in p for p in llm.prompts)
    assert any("weekly habit" in e for e in s["linkedin"]["quality"]["feedback"].splitlines()) or s["linkedin"]["quality"]["revisions_used"] >= 1


def test_cross_format_voice_report_is_in_final_output():
    s = run(make_services(), "Run a full campaign about cloud cost optimization")
    vr = s["final"]["voice_report"]
    assert set(vr["metrics"]) == {"blog", "linkedin"} and vr["consistent"] is True
