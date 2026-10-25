import io
import json
import zipfile

from src.utils.export_tools import (blog_plain_text, build_export_zip, derive_blog_outputs, image_bytes_and_ext,
                                    linkify_citations, sources_csv)
from tests.fakes import good_blog

SRC = [{"title": "A", "link": "https://a.com", "domain": "a.com", "credibility": 1.4, "provider": "p"},
       {"title": "B", "link": "https://b.com", "domain": "b.com", "credibility": 1.1, "provider": "q"}]


def test_linkify_makes_citations_clickable_and_leaves_invalid_ones():
    assert linkify_citations("Fact [1].", SRC) == "Fact [[1]](https://a.com)."
    assert linkify_citations("Both [1, 2].", SRC) == "Both [[1](https://a.com), [2](https://b.com)]."
    assert linkify_citations("Bad [7].", SRC) == "Bad [7]."
    assert linkify_citations("Link [x](https://k.com) stays", SRC) == "Link [x](https://k.com) stays"


def test_sources_csv_marks_cited_and_further_reading():
    rows = sources_csv({"sources": SRC[:1], "other_sources": SRC[1:]}).splitlines()
    assert rows[0].startswith("number,cited") and rows[1].startswith("1,yes,A") and rows[2].startswith(",no,B")


def test_plain_text_export_has_no_markdown_or_citation_numbers():
    t = blog_plain_text({"body": "# Title\n\nSome **bold** claim [1] with [a link](https://x.com).\n\n## Sources\n1. [A](https://a.com)"})
    assert "#" not in t and "**" not in t and "[1]" not in t and "https" not in t and "Sources" not in t
    assert t.startswith("TITLE") and "a link" in t


def test_derived_outputs_recompute_from_text():
    blog = {"title": "T", "meta_description": "m", "primary_keyword": "cloud cost optimization", "keywords": [], "body": good_blog()}
    out = derive_blog_outputs(blog, {"url": "https://i/x.png", "alt_text": "alt"})
    assert set(out) == {"markdown", "seo", "html", "plain_text"} and "og:image" in out["seo"]["og"] and derive_blog_outputs({}) == {}


def test_image_bytes_detection():
    assert image_bytes_and_ext({"data": b"\xff\xd8\xff\xe0abc"})[1] == "jpg"
    assert image_bytes_and_ext({"data": b"\x89PNG....."})[1] == "png"
    assert image_bytes_and_ext({"svg": "<svg/>"}) == (b"<svg/>", "svg") and image_bytes_and_ext({"url": "u"}) == (None, "")


def test_export_zip_contains_every_artifact():
    vals = {"topic": "cloud", "research": {"summary": "s [1]", "sources": SRC, "other_sources": []},
            "blog": {"title": "T", "meta_description": "m", "primary_keyword": "cloud cost optimization", "keywords": [],
                     "body": good_blog(), "quality": {"score": 8, "det_score": 8, "llm_score": 8, "failed": [], "checks": []}},
            "linkedin": {"text": "hello?", "first_comment": "link", "hook_variants": ["h1", "h2"]},
            "image": {"svg": "<svg/>", "prompt": "p", "alt_text": "alt"}, "brand_profile": {"name": "Acme"}}
    z = zipfile.ZipFile(io.BytesIO(build_export_zip(vals)))
    names = set(z.namelist())
    assert {"README.txt", "research/brief.md", "research/sources.csv", "blog/post.md", "blog/post.html", "blog/post.txt",
            "blog/seo.json", "linkedin/post.txt", "linkedin/first_comment.txt", "linkedin/hook_options.txt", "image/image.svg",
            "image/alt_text.txt", "image/prompt.txt", "quality/report.json", "brand/profile.yaml"} <= names
    assert json.loads(z.read("blog/seo.json"))["slug"] and "blog/post.md" in z.read("README.txt").decode()


def test_export_zip_with_nothing_is_still_valid():
    assert zipfile.ZipFile(io.BytesIO(build_export_zip({}))).namelist() == ["README.txt"]
