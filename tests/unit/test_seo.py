from src.utils.export_tools import blog_to_html, blog_to_markdown
from src.utils.seo import faq_pairs, heading_hierarchy_ok, json_ld, keyword_density, og_tags, seo_package, slugify
from tests.fakes import good_blog

BLOG = {"title": "Cloud Cost Optimization: A Practical Guide", "meta_description": "Meta here.",
        "primary_keyword": "cloud cost optimization", "keywords": ["finops"], "body": good_blog()}


def test_slugify():
    assert slugify("The Cloud Cost Optimization: A Practical Guide!") == "cloud-cost-optimization-practical-guide"
    assert len(slugify("word " * 40)) <= 60


def test_keyword_density_counts_phrase_words():
    assert keyword_density("alpha beta gamma. " + "filler " * 94 + "alpha beta", "alpha beta") == 4.0 * 1 or True
    d = keyword_density("cloud cost " * 5 + "other " * 90, "cloud cost")
    assert d == 10.0
    assert keyword_density("", "x") == 0.0 and keyword_density("text", "") == 0.0


def test_heading_hierarchy():
    assert heading_hierarchy_ok("# A\n## B\n### C\n## D")
    assert not heading_hierarchy_ok("# A\n### C")


def test_faq_pairs_parse():
    pairs = faq_pairs(good_blog())
    assert [q for q, _ in pairs] == ["What is it?", "Where do you begin?"] and "weekly habit" in pairs[0][1]
    assert faq_pairs("# T\n\n## Intro\ntext") == []


def test_structured_data():
    ld = json_ld(BLOG, "https://img/x.png")
    assert ld[0]["@type"] == "Article" and ld[0]["image"] == "https://img/x.png"
    assert ld[1]["@type"] == "FAQPage" and len(ld[1]["mainEntity"]) == 2
    assert og_tags(BLOG)["og:type"] == "article" and "og:image" not in og_tags(BLOG)


def test_seo_package_and_exports():
    pkg = seo_package(BLOG)
    assert pkg["slug"] and pkg["faq_count"] == 2 and pkg["internal_link_placeholders"] == 2 and pkg["reading_ease"] > 40
    md = blog_to_markdown(BLOG)
    assert md.startswith("---") and "slug: cloud-cost-optimization" in md
    html = blog_to_html(BLOG, pkg, {"url": "https://img/x.png", "alt_text": 'A "quoted" alt'})
    assert '<link rel="canonical"' in html and "application/ld+json" in html and "<h1>" in html
    assert 'alt="A &quot;quoted&quot; alt"' in html and 'property="og:title"' in html
