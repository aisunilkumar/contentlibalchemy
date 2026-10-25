"""Technical SEO helpers: slug, keyword density, FAQ extraction, Open Graph, JSON-LD."""
import re

from src.utils.citations import strip_sources_section
from src.utils.content_optimization import count_words, headings, reading_ease, strip_markdown

_STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "for", "on", "with", "is", "your", "how", "why", "what"}


def slugify(title: str, max_len: int = 60) -> str:
    words = [w for w in re.sub(r"[^a-z0-9\s-]", "", title.lower()).split() if w]
    core = [w for w in words if w not in _STOP] or words
    slug = "-".join(core)
    return slug[:max_len].rstrip("-")


def keyword_density(text: str, keyword: str) -> float:
    """Occurrences of the exact phrase x its word count / total words, as a percentage."""
    kw = (keyword or "").lower().strip()
    prose = strip_markdown(text).lower()
    total = count_words(prose)
    if not kw or not total:
        return 0.0
    return round(100 * len(re.findall(re.escape(kw), prose)) * count_words(kw) / total, 2)


def heading_hierarchy_ok(md: str) -> bool:
    """No skipped levels (H1 -> H3) after the first heading."""
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6})\s+\S", md, flags=re.M)]
    return all(b - a <= 1 for a, b in zip(levels, levels[1:]))


def faq_pairs(md: str) -> list[tuple[str, str]]:
    m = re.search(r"^##\s+(?:FAQ|Frequently Asked Questions)[^\n]*\n(.*?)(?=^##\s|\Z)", md, re.S | re.M | re.I)
    if not m:
        return []
    return [(q.strip(), " ".join(a.split()))
            for q, a in re.findall(r"^###\s+(.+?\?)\s*\n+(.*?)(?=^###\s|\Z)", m.group(1), re.S | re.M)]


def og_tags(blog: dict, image_url: str | None = None) -> dict:
    tags = {"og:type": "article", "og:title": blog.get("title", ""), "og:description": blog.get("meta_description", ""),
            "twitter:card": "summary_large_image", "twitter:title": blog.get("title", ""),
            "twitter:description": blog.get("meta_description", "")}
    if image_url:
        tags["og:image"] = tags["twitter:image"] = image_url
    return tags


def json_ld(blog: dict, image_url: str | None = None) -> list[dict]:
    art = {"@context": "https://schema.org", "@type": "Article", "headline": blog.get("title", ""),
           "description": blog.get("meta_description", ""),
           "keywords": ", ".join([blog.get("primary_keyword", ""), *blog.get("keywords", [])]).strip(", ")}
    if image_url:
        art["image"] = image_url
    out = [art]
    pairs = faq_pairs(blog.get("body", ""))
    if pairs:
        out.append({"@context": "https://schema.org", "@type": "FAQPage",
                    "mainEntity": [{"@type": "Question", "name": q,
                                    "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs]})
    return out


def seo_package(blog: dict, image_url: str | None = None) -> dict:
    body = strip_sources_section(blog.get("body", ""))
    return {"slug": slugify(blog.get("title", "")), "og": og_tags(blog, image_url), "json_ld": json_ld(blog, image_url),
            "keyword_density": keyword_density(body, blog.get("primary_keyword", "")),
            "reading_ease": reading_ease(strip_markdown(body)), "faq_count": len(faq_pairs(body)),
            "outline": [("H1", h) for h in headings(body, 1)] + [("H2", h) for h in headings(body, 2)],
            "internal_link_placeholders": len(re.findall(r"\[INTERNAL LINK:", body))}
