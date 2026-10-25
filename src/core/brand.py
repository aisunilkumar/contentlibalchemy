"""Brand profile: one structured source of truth for tone, vocabulary, per-platform rules and visual identity."""
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from src.core.config import settings
from src.utils.checks import Check
from src.utils.content_optimization import (EMOJI_RE, avg_sentence_words, count_words, strip_markdown)


class PlatformRules(BaseModel):
    max_avg_sentence_words: float = 22
    max_emojis: int = 0
    max_exclamations: int = 1


class Visual(BaseModel):
    style: str = "clean modern editorial illustration with flat shapes and subtle gradients"
    palette: str = "deep teal and warm amber on a soft neutral background"
    colors: list[str] = Field(default_factory=lambda: ["#0F766E", "#134E4A", "#F59E0B"])
    mood: str = "calm, confident, optimistic"
    avoid: list[str] = Field(default_factory=lambda: ["text", "logos", "real people", "stock-photo cliches"])


class BrandProfile(BaseModel):
    name: str = "Your Brand"
    tone: str = "Clear, friendly and professional."
    persona: str = "A knowledgeable colleague."
    pov: Literal["second_person", "first_person_plural", "third_person"] = "second_person"
    use: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    avoid_phrases: list[str] = Field(default_factory=list)
    preferred_terms: list[str] = Field(default_factory=list)
    cta_style: str = "Invite a concrete next step."
    sample: str = ""
    reading_ease_min: float = 40
    platforms: dict[str, PlatformRules] = Field(default_factory=lambda: {
        "blog": PlatformRules(), "linkedin": PlatformRules(max_avg_sentence_words=16, max_emojis=2)})
    visual: Visual = Field(default_factory=Visual)

    def rules(self, platform: str) -> PlatformRules:
        return self.platforms.get(platform) or PlatformRules()


def parse_brand_yaml(text: str) -> BrandProfile:
    """Validate user-edited YAML. Raises ValueError with a readable message."""
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("brand profile must be a YAML mapping")
        return BrandProfile.model_validate(data)
    except Exception as e:  # noqa: BLE001
        raise ValueError(str(e).splitlines()[0] if str(e) else "invalid brand profile") from e


def load_brand_profile(path: str | None = None) -> BrandProfile:
    p = Path(path or settings.brand_voice_path)
    return parse_brand_yaml(p.read_text()) if p.exists() else BrandProfile()


def profile_from_state(state: dict) -> BrandProfile:
    d = state.get("brand_profile")
    return BrandProfile.model_validate(d) if d else load_brand_profile()


_POV = {"second_person": 'Address the reader as "you".',
        "first_person_plural": 'Speak as "we" (the brand) to "you" (the reader).',
        "third_person": "Write in the third person; avoid addressing the reader directly."}
_PLATFORM_HINT = {"blog": "Long-form: explain with examples, vary sentence length, keep paragraphs short.",
                  "linkedin": "Short, scannable lines with white space; conversational but professional; no Markdown."}


def brand_prompt(p: BrandProfile, platform: str) -> str:
    """The same brand, adapted to the platform. Injected into every writer and the judge."""
    r = p.rules(platform)
    lines = [f"Brand: {p.name}. Tone: {p.tone} Persona: {p.persona}", _POV[p.pov], _PLATFORM_HINT.get(platform, "")]
    if p.use:
        lines.append("Do: " + "; ".join(p.use))
    if p.avoid:
        lines.append("Avoid: " + "; ".join(p.avoid))
    if p.avoid_phrases:
        lines.append("NEVER use these phrases: " + ", ".join(f'"{x}"' for x in p.avoid_phrases))
    if p.preferred_terms:
        lines.append("Preferred terms (use instead of synonyms): " + ", ".join(p.preferred_terms))
    lines.append(f"Average sentence length: at most {r.max_avg_sentence_words:g} words. "
                 f"Emojis: at most {r.max_emojis}. Exclamation marks: at most {r.max_exclamations}.")
    lines.append(f"Call to action style: {p.cta_style}")
    if p.sample:
        lines.append("Style sample:\n" + p.sample.strip())
    return "\n".join(x for x in lines if x)


# ---------- deterministic voice checks (same rules applied to every format) ----------
def voice_metrics(text: str) -> dict:
    prose = strip_markdown(text)
    w = max(count_words(prose), 1)
    return {"words": count_words(prose), "avg_sentence_words": round(avg_sentence_words(prose), 1),
            "you_per_100": round(100 * len(re.findall(r"\byou(?:r|rs)?\b", prose, re.I)) / w, 2),
            "exclamations": prose.count("!"), "emojis": len(EMOJI_RE.findall(text))}


def banned_hits(text: str, p: BrandProfile) -> list[str]:
    low = text.lower().replace("\u2019", "'")
    return [x for x in p.avoid_phrases if x.lower() in low]


def voice_checks(text: str, p: BrandProfile, platform: str) -> list[Check]:
    r, m = p.rules(platform), voice_metrics(text)
    hits = banned_hits(text, p)
    checks = [
        Check("voice_banned_phrases", not hits, f"remove banned phrases: {', '.join(hits)}", critical=True),
        Check("voice_sentence_length", m["avg_sentence_words"] <= r.max_avg_sentence_words,
              f"average sentence is {m['avg_sentence_words']} words (brand max {r.max_avg_sentence_words:g}); shorten sentences"),
        Check("voice_emojis", m["emojis"] <= r.max_emojis, f"{m['emojis']} emojis (brand max {r.max_emojis})"),
        Check("voice_exclamations", m["exclamations"] <= r.max_exclamations,
              f"{m['exclamations']} exclamation marks (brand max {r.max_exclamations})"),
    ]
    if p.pov == "second_person":
        checks.append(Check("voice_pov", m["you_per_100"] > 0, 'address the reader directly as "you"'))
    return checks


def voice_report(blog_text: str, linkedin_text: str, p: BrandProfile) -> dict:
    """Cross-format consistency (informational): same brand rules applied to every artifact."""
    metrics, flags = {}, []
    for name, text in (("blog", blog_text), ("linkedin", linkedin_text)):
        if text:
            metrics[name] = {**voice_metrics(text), "banned": banned_hits(text, p)}
            if metrics[name]["banned"]:
                flags.append(f"{name}: uses banned phrases {metrics[name]['banned']}")
    if len(metrics) == 2:
        a, b = metrics["blog"], metrics["linkedin"]
        if abs(a["you_per_100"] - b["you_per_100"]) > 4:
            flags.append("reader address ('you') differs a lot between blog and LinkedIn")
        if abs(a["exclamations"] / max(a["words"], 1) - b["exclamations"] / max(b["words"], 1)) * 100 > 1.0:
            flags.append("energy (exclamation marks) differs between blog and LinkedIn")
    return {"metrics": metrics, "flags": flags, "consistent": not flags}
