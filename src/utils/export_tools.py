"""Turn artifacts into publish-ready text / HTML."""
import html
import json

from src.utils.seo import slugify


def research_report_md(research: dict) -> str:
    if not research:
        return ""
    lines = [f"# Research brief: {research.get('topic', '')}", ""]
    if research.get("unverified"):
        lines += ["> **Unverified:** no live sources were available; treat facts as unchecked.", ""]
    lines += [research.get("summary", ""), ""]
    srcs = research.get("sources", [])
    if srcs:
        lines.append("## Sources")
        lines += [f"{i}. [{s.get('title') or s['link']}]({s['link']})" for i, s in enumerate(srcs, 1)]
    others = research.get("other_sources", [])
    if others:
        lines += ["", "## Further reading (found but not cited)"]
        lines += [f"- [{s.get('title') or s['link']}]({s['link']})" for s in others]
    return "\n".join(lines)


def blog_to_markdown(blog: dict) -> str:
    if not blog or not blog.get("body"):
        return ""
    kw = ", ".join([blog.get("primary_keyword", ""), *blog.get("keywords", [])]).strip(", ")
    front = ["---", f"title: \"{blog.get('title', '')}\"", f"slug: {slugify(blog.get('title', ''))}",
             f"description: \"{blog.get('meta_description', '')}\"", f"keywords: [{kw}]", "---", ""]
    return "\n".join(front) + blog["body"]


def blog_to_html(blog: dict, seo: dict, image: dict | None = None) -> str:
    """Self-contained HTML page with meta tags, Open Graph and JSON-LD, ready to drop into a CMS."""
    try:
        import markdown
        body_html = markdown.markdown(blog.get("body", ""), extensions=["extra"])
    except ImportError:  # pragma: no cover
        body_html = f"<pre>{html.escape(blog.get('body', ''))}</pre>"
    e = lambda s: html.escape(s or "", quote=True)  # noqa: E731
    og = "\n".join(f'<meta property="{k}" content="{e(v)}">' if k.startswith("og:")
                   else f'<meta name="{k}" content="{e(v)}">' for k, v in seo["og"].items())
    ld = "\n".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>' for x in seo["json_ld"])
    hero = ""
    if image and image.get("url"):
        hero = f'<img src="{e(image["url"])}" alt="{e(image.get("alt_text", ""))}">'
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n<title>{e(blog.get("title"))}</title>\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta name="description" content="{e(blog.get("meta_description"))}">\n'
            f'<link rel="canonical" href="/blog/{e(seo["slug"])}">\n{og}\n{ld}\n</head>\n<body>\n<article>\n{hero}\n{body_html}\n</article>\n</body></html>')


# ---------------- derived outputs + export bundle ----------------
import csv
import io
import re
import zipfile

import yaml

from src.utils.citations import CITE_RE, strip_sources_section
from src.utils.content_optimization import strip_markdown
from src.utils.seo import seo_package


def derive_blog_outputs(blog: dict, image: dict | None = None) -> dict:
    """Everything computed from the blog text. Pure, so the UI can recompute it after a manual edit."""
    if not blog or not blog.get("body"):
        return {}
    seo = seo_package(blog, (image or {}).get("url"))
    return {"markdown": blog_to_markdown(blog), "seo": seo, "html": blog_to_html(blog, seo, image),
            "plain_text": blog_plain_text(blog)}


def blog_plain_text(blog: dict) -> str:
    body = strip_sources_section(blog.get("body", ""))
    body = re.sub(r"^#{1,6}\s+(.*)$", lambda m: m.group(1).upper() if m.group(0).startswith("# ") else m.group(1), body, flags=re.M)
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    body = re.sub(r"\[INTERNAL LINK:\s*([^\]]*)\]", r"\1", body)
    body = re.sub(r"[*_`]", "", body)
    return CITE_RE.sub("", body).replace("  ", " ").strip()


def linkify_citations(text: str, sources: list[dict]) -> str:
    """Display-only: make [n] clickable (the whole '[n]' is the link target, which is easier to tap)."""
    def sub(m):
        ids = [int(x) for x in re.split(r"\s*,\s*", m.group(1))]
        if not all(1 <= i <= len(sources) for i in ids):
            return m.group(0)
        if len(ids) == 1:
            return f"[[{ids[0]}]]({sources[ids[0] - 1]['link']})"
        return "[" + ", ".join(f"[{i}]({sources[i - 1]['link']})" for i in ids) + "]"
    return CITE_RE.sub(sub, text)


def sources_csv(research: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["number", "cited", "title", "domain", "credibility", "provider", "url"])
    for i, s in enumerate(research.get("sources", []), 1):
        w.writerow([i, "yes", s.get("title", ""), s.get("domain", ""), s.get("credibility", ""), s.get("provider", ""), s["link"]])
    for s in research.get("other_sources", []):
        w.writerow(["", "no", s.get("title", ""), s.get("domain", ""), s.get("credibility", ""), s.get("provider", ""), s["link"]])
    return buf.getvalue()


def image_bytes_and_ext(image: dict) -> tuple[bytes | None, str]:
    data = image.get("data")
    if data:
        ext = "jpg" if data[:3] == b"\xff\xd8\xff" else "webp" if data[8:12] == b"WEBP" else "png"
        return data, ext
    if image.get("svg"):
        return image["svg"].encode("utf-8"), "svg"
    return None, ""


def build_export_zip(values: dict) -> bytes:
    """One zip with every artifact in publish-ready formats."""
    research, blog, li, image = (values.get(k) or {} for k in ("research", "blog", "linkedin", "image"))
    files: dict[str, bytes | str] = {}
    if research.get("summary"):
        files["research/brief.md"] = research_report_md(research)
        files["research/sources.csv"] = sources_csv(research)
    if blog.get("body"):
        out = derive_blog_outputs(blog, image)
        files.update({"blog/post.md": out["markdown"], "blog/post.html": out["html"], "blog/post.txt": out["plain_text"],
                      "blog/seo.json": __import__("json").dumps({k: out["seo"][k] for k in ("slug", "og", "json_ld")}, indent=2)})
    if li.get("text"):
        files["linkedin/post.txt"] = li["text"]
        if li.get("first_comment"):
            files["linkedin/first_comment.txt"] = li["first_comment"]
        if li.get("hook_variants"):
            files["linkedin/hook_options.txt"] = "\n".join(f"- {h}" for h in li["hook_variants"])
    data, ext = image_bytes_and_ext(image)
    if data:
        files[f"image/image.{ext}"] = data
    elif image.get("url"):
        files["image/image_url.txt"] = image["url"]
    if image.get("prompt"):
        files["image/prompt.txt"] = image["prompt"]
        files["image/alt_text.txt"] = image.get("alt_text", "")
    quality = {k: {x: values[k]["quality"][x] for x in ("score", "det_score", "llm_score", "failed", "checks")
                   if x in values[k]["quality"]} for k in ("blog", "linkedin") if (values.get(k) or {}).get("quality")}
    if quality:
        files["quality/report.json"] = __import__("json").dumps(quality, indent=2)
    if values.get("brand_profile"):
        files["brand/profile.yaml"] = yaml.safe_dump(values["brand_profile"], sort_keys=False, allow_unicode=True)
    files["README.txt"] = (f"ContentAlchemy export\nTopic: {values.get('topic', '')}\n\nFiles:\n"
                           + "\n".join(f"- {n}" for n in sorted(files)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in sorted(files.items()):
            z.writestr(name, content)
    return buf.getvalue()
