from src.utils.citations import (cited_ids, citation_coverage, citation_report, credibility, domain, finalize_citations,
                                 normalize_url, rank_sources, renumber_citations, strip_sources_section)

SRC = [{"title": "A", "link": "https://a.com/x"}, {"title": "B", "link": "https://b.com/y"}, {"title": "C", "link": "https://c.org/z"}]


def test_normalize_url_strips_tracking_and_www():
    assert normalize_url("https://WWW.Example.com/a/?utm_source=x&id=2#frag") == "https://example.com/a?id=2"
    assert domain("https://www.nih.gov/x") == "nih.gov"


def test_rank_dedupes_caps_domain_and_prefers_credible():
    raw = [{"link": "https://reddit.com/r/a", "title": "r"}, {"link": "https://nih.gov/a", "title": "n"},
           {"link": "https://www.nih.gov/a?utm_source=x", "title": "dup"}, {"link": "https://site.com/1"},
           {"link": "https://site.com/2"}, {"link": "https://site.com/3"}, {"link": ""}]
    out = rank_sources(raw, max_per_domain=2, limit=10)
    links = [s["link"] for s in out]
    assert sum("site.com" in l for l in links) == 2 and sum("nih.gov" in l for l in links) == 1
    assert links[0].endswith("nih.gov/a") or "nih.gov" in links[0]
    assert "reddit.com" in links[-1]
    assert credibility("https://x.gov/a") > credibility("https://example.com/a") > credibility("https://reddit.com/a")


def test_renumber_by_first_use_and_drop_invalid():
    txt = "Late fact [3]. Early fact [1]. Combo [1, 3]. Bogus [9] and [0]."
    new, used, invalid = renumber_citations(txt, SRC)
    assert new.startswith("Late fact [1]. Early fact [2]. Combo [1, 2]. Bogus and")
    assert [s["title"] for s in used] == ["C", "A"] and sorted(invalid) == [0, 9]


def test_markdown_links_are_not_treated_as_citations():
    new, used, _ = renumber_citations("See [1](https://x.com) and [2].", SRC)
    assert "[1](https://x.com)" in new and [s["title"] for s in used] == ["B"] and cited_ids("a [2, 3] b [1]") == [2, 3, 1]


def test_finalize_replaces_model_written_sources_with_real_ones():
    body = "Intro [2].\n\nMore [2].\n\n## Sources\n1. Totally invented https://fake.com\n"
    out, used, invalid = finalize_citations(body, SRC)
    assert "fake.com" not in out and "[B](https://b.com/y)" in out and out.count("## Sources") == 1 and invalid == []
    assert strip_sources_section(out).endswith("More [1].")


def test_finalize_without_sources_removes_all_refs():
    out, used, invalid = finalize_citations("Claim [1]. Another [2].", [])
    assert "[1]" not in out and "## Sources" not in out and invalid == [1, 2]


def test_coverage_handles_bullets_and_empty_input():
    bullets = "## Key findings\n- " + "word " * 14 + "[1]\n- " + "word " * 14 + "\n- " + "word " * 14 + "[2]"
    assert citation_coverage(bullets) == 0.67
    assert citation_coverage("Short [1].") == 1.0 and citation_coverage("") == 1.0   # nothing to check


def test_coverage_and_report():
    cited = "word " * 25 + "[1]."
    plain = "word " * 25
    assert citation_coverage(f"# T\n\n{cited}\n\n{plain}") == 0.5
    r = citation_report(f"{cited}", SRC[:1], found=6, invalid=[9])
    assert r["cited"] == 1 and any("only 1 of 6" in f for f in r["flags"]) and any("removed 1" in f for f in r["flags"])
    assert "no inline citations" in citation_report("text", [], 3, [])["flags"][0]
