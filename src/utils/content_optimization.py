"""Deterministic text helpers for SEO + platform formatting."""
import re


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def trim_at_word(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:- ")


def first_words(text: str, n: int) -> str:
    return " ".join(re.findall(r"\S+", text)[:n])


def headings(md: str, level: int) -> list[str]:
    return re.findall(rf"^{'#' * level}\s+(.+)$", md, flags=re.M)


def extract_hashtags(text: str) -> list[str]:
    seen, out = set(), []
    for h in re.findall(r"#\w+", text):
        if h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out


def parse_lines(text: str, limit: int | None = None) -> list[str]:
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"^\s*(?:[-*\u2022]+|\d+[.)])\s*", "", ln).strip().strip('"')
        if ln:
            lines.append(ln)
    return lines[:limit] if limit else lines


# ---------- readability / voice helpers ----------
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B06]")


def strip_markdown(md: str) -> str:
    """Prose only: drop headings, links syntax, emphasis, bullets, code, HTML comments."""
    t = re.sub(r"```.*?```", " ", md, flags=re.S)
    t = re.sub(r"^#{1,6}\s+.*$", " ", t, flags=re.M)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]|\[INTERNAL LINK:[^\]]*\]|\[VERIFY\]", "", t)
    t = re.sub(r"[*_`>]|^\s*[-+]\s+|^\s*\d+\.\s+", "", t, flags=re.M)
    return " ".join(t.split())


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if re.search(r"\w", s)]


def avg_sentence_words(text: str) -> float:
    ss = sentences(text)
    return count_words(text) / len(ss) if ss else 0.0


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith("le") and n > 1:
        n -= 1
    return max(n, 1)


def reading_ease(text: str) -> float:
    """Flesch reading ease (approximate; heuristic syllable counter)."""
    words = re.findall(r"[A-Za-z']+", text)
    ss = sentences(text)
    if not words or not ss:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return round(206.835 - 1.015 * (len(words) / len(ss)) - 84.6 * (syl / len(words)), 1)
