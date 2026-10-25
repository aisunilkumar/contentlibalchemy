"""Source ranking and citation integrity: every [n] must map to a real retrieved source."""
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\](?!\()")
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ref"}
# Heuristic credibility signals (not a fact-check). Tune freely.
TRUSTED = {"who.int", "nature.com", "science.org", "reuters.com", "apnews.com", "oecd.org", "worldbank.org",
           "imf.org", "nih.gov", "arxiv.org", "ieee.org", "acm.org", "mckinsey.com", "gartner.com", "hbr.org"}
LOW_QUALITY = {"pinterest.com", "quora.com", "reddit.com", "facebook.com", "x.com", "twitter.com",
               "tiktok.com", "instagram.com", "answers.com", "ehow.com"}


def normalize_url(url: str) -> str:
    u = urlparse((url or "").strip())
    q = urlencode([(k, v) for k, v in parse_qsl(u.query) if k.lower() not in _TRACKING])
    host = u.netloc.lower().removeprefix("www.")
    return urlunparse((u.scheme.lower() or "https", host, u.path.rstrip("/") or "", "", q, ""))


def domain(url: str) -> str:
    return urlparse(normalize_url(url)).netloc


def _on(host: str, names) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def credibility(url: str, rank: int = 0) -> float:
    h = domain(url)
    score = 1.0
    if h.endswith((".gov", ".edu", ".int")) or ".gov." in h or ".ac." in h:
        score += 0.5
    if _on(h, TRUSTED):
        score += 0.3
    if _on(h, LOW_QUALITY):
        score -= 0.7
    if url.startswith("https"):
        score += 0.1
    return round(score + 0.3 / (1 + rank), 3)  # earlier search rank = small bonus


def rank_sources(raw: list[dict], max_per_domain: int = 2, limit: int = 12) -> list[dict]:
    """Dedupe by normalized URL, score, cap per domain, keep the best `limit`."""
    seen: dict[str, dict] = {}
    for i, s in enumerate(raw):
        link = s.get("link")
        if not link:
            continue
        key = normalize_url(link)
        cand = {**s, "link": link, "domain": domain(link), "credibility": credibility(link, s.get("rank", i))}
        if key not in seen or cand["credibility"] > seen[key]["credibility"]:
            seen[key] = cand
    out, per = [], {}
    for s in sorted(seen.values(), key=lambda x: -x["credibility"]):
        if per.get(s["domain"], 0) < max_per_domain:
            per[s["domain"]] = per.get(s["domain"], 0) + 1
            out.append(s)
    return out[:limit]


def cited_ids(text: str) -> list[int]:
    ids = []
    for m in CITE_RE.finditer(text):
        ids += [int(x) for x in re.split(r"\s*,\s*", m.group(1))]
    return ids


def strip_sources_section(text: str) -> str:
    """Remove any model-written Sources/References section: we regenerate it from real URLs."""
    return re.sub(r"\n#{1,3}\s*(?:Sources|References|Citations)\b.*\Z", "", text.rstrip(), flags=re.S | re.I).rstrip()


def renumber_citations(text: str, sources: list[dict]) -> tuple[str, list[dict], list[int]]:
    """Number citations by first appearance, drop refs that match no source.
    Returns (new_text, used_sources_in_new_order, invalid_ids_removed)."""
    mapping: dict[int, int] = {}
    invalid: list[int] = []

    def sub(m):
        out = []
        for raw in re.split(r"\s*,\s*", m.group(1)):
            old = int(raw)
            if not (1 <= old <= len(sources)):
                invalid.append(old)
                continue
            if old not in mapping:
                mapping[old] = len(mapping) + 1
            if mapping[old] not in out:
                out.append(mapping[old])
        return f"[{', '.join(map(str, sorted(out)))}]" if out else ""

    new = CITE_RE.sub(sub, text)
    new = re.sub(r"[ \t]+([.,;:!?])", r"\1", new)       # tidy gaps left by removed refs
    new = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", new)
    used = [sources[old - 1] for old, _ in sorted(mapping.items(), key=lambda kv: kv[1])]
    return new, used, invalid


def sources_section(used: list[dict], heading: str = "## Sources") -> str:
    if not used:
        return ""
    lines = [heading, ""] + [f"{i}. [{s.get('title') or s['link']}]({s['link']})" for i, s in enumerate(used, 1)]
    return "\n".join(lines)


def finalize_citations(body: str, sources: list[dict]) -> tuple[str, list[dict], list[int]]:
    """Strip model Sources -> renumber -> append a deterministic Sources section."""
    clean, used, invalid = renumber_citations(strip_sources_section(body), sources)
    sec = sources_section(used)
    return (clean + ("\n\n" + sec if sec else "")), used, invalid


_BULLET = re.compile(r"\s*(?:[-*\u2022]|\d+[.)])\s+")


def _blocks(text: str) -> list[str]:
    """Paragraphs, with bullet/numbered lists split into one block per item."""
    out = []
    for para in re.split(r"\n\s*\n", strip_sources_section(text)):
        lines = para.split("\n")
        out += lines if sum(bool(_BULLET.match(ln)) for ln in lines) >= 2 else [para]
    return [b for b in out if b.strip() and not b.lstrip().startswith("#")]


def citation_coverage(text: str, min_words: int = 12) -> float:
    """Share of substantive blocks (paragraphs / list items of >= min_words) that carry a citation.
    Returns 1.0 when there is nothing substantive to check."""
    blocks = [b for b in _blocks(text) if len(b.split()) >= min_words]
    return round(sum(bool(CITE_RE.search(b)) for b in blocks) / len(blocks), 2) if blocks else 1.0


def citation_report(text: str, used: list[dict], found: int, invalid: list[int]) -> dict:
    domains = {s["domain"] for s in used if s.get("domain")}
    flags = []
    if not used:
        flags.append("brief has no inline citations")
    elif len(used) < 3 and found >= 3:
        flags.append(f"brief cites only {len(used)} of {found} sources found")
    if invalid:
        flags.append(f"removed {len(invalid)} citation(s) that matched no source")
    cov = citation_coverage(text)
    if used and cov < 0.5:
        flags.append(f"only {int(cov * 100)}% of paragraphs carry a citation")
    return {"found": found, "cited": len(used), "distinct_domains": len(domains), "invalid_removed": len(invalid),
            "coverage": cov, "flags": flags}
