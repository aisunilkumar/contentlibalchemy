"""Image prompt engineering: structured brief -> brand-consistent prompt -> lint -> simplified retry -> SVG card."""
import textwrap
from xml.sax.saxutils import escape

from pydantic import BaseModel

from src.core.brand import Visual
from src.utils.content_optimization import count_words, trim_at_word

ASPECT_HINT = {"landscape": "wide 16:9 landscape composition with clear space for a headline",
               "square": "balanced square composition with the subject centered",
               "portrait": "tall portrait composition"}
DIMENSIONS = {"landscape": (1200, 630), "square": (1080, 1080), "portrait": (1080, 1350)}
NEGATIVE = "No text, letters, numbers, logos or watermarks. No real people or recognizable faces."


class ImageBrief(BaseModel):
    subject: str
    style: str = ""
    composition: str = ""
    palette: str = ""
    lighting: str = ""
    mood: str = ""
    alt_text: str = ""


def template_brief(topic: str, v: Visual) -> ImageBrief:
    """Deterministic brief used when the LLM can't produce one."""
    return ImageBrief(subject=f"A symbolic scene representing {topic}", style=v.style, palette=v.palette,
                      mood=v.mood, composition="one clear focal subject with generous negative space",
                      lighting="soft diffused light", alt_text=f"Illustration representing {topic}")


def assemble_image_prompt(b: ImageBrief, v: Visual, aspect: str = "landscape") -> str:
    """Brand identity wins over the model's style/palette so every image looks like the same brand."""
    parts = [b.subject.rstrip("."),
             f"Style: {v.style}", f"Composition: {b.composition or 'one clear focal subject'}, {ASPECT_HINT.get(aspect, '')}",
             f"Color palette: {v.palette}", f"Lighting: {b.lighting or 'soft diffused light'}",
             f"Mood: {b.mood or v.mood}"]
    if v.avoid:
        parts.append("Avoid: " + ", ".join(v.avoid))
    parts.append(NEGATIVE)
    return ". ".join(p.rstrip(".") for p in parts) + "."


def simplify_image_prompt(b: ImageBrief, v: Visual) -> str:
    """Short, conservative prompt for the retry after a rejection (e.g. content-policy or length errors)."""
    subject = trim_at_word(b.subject, 90)
    return f"{subject}. {trim_at_word(v.style, 60)}. {v.palette}. No text or logos."


def lint_image_prompt(prompt: str) -> list[str]:
    issues = []
    n = count_words(prompt)
    if n < 25:
        issues.append(f"prompt is very short ({n} words); add style, composition and lighting")
    if n > 220:
        issues.append(f"prompt is long ({n} words); some providers truncate or reject it")
    if "no text" not in prompt.lower():
        issues.append("missing the 'no text' constraint (generators often render garbled text)")
    return issues


def clean_alt_text(alt: str, fallback: str) -> str:
    alt = " ".join((alt or fallback).split())
    for lead in ("image of ", "picture of ", "photo of "):
        if alt.lower().startswith(lead):
            alt = alt[len(lead):].capitalize()
    return trim_at_word(alt, 125)


def make_svg_card(title: str, subtitle: str, colors: list[str], aspect: str = "landscape") -> str:
    """Branded placeholder that is always available, even if every image provider is down."""
    w, h = DIMENSIONS.get(aspect, DIMENSIONS["landscape"])
    c1, c2, c3 = (list(colors) + ["#0F766E", "#134E4A", "#F59E0B"])[:3]
    lines = textwrap.wrap(title, width=26 if aspect != "landscape" else 30)[:4] or [""]
    size = int(h * (0.085 if aspect == "landscape" else 0.06))
    y0 = h * 0.42 - (len(lines) - 1) * size * 0.6
    tspans = "".join(f'<tspan x="{w * 0.08:.0f}" y="{y0 + i * size * 1.25:.0f}">{escape(t)}</tspan>' for i, t in enumerate(lines))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{escape(title, {chr(34): "&quot;"})}">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/>'
            f'<stop offset="1" stop-color="{c2}"/></linearGradient></defs>'
            f'<rect width="{w}" height="{h}" fill="url(#g)"/>'
            f'<circle cx="{w * 0.86:.0f}" cy="{h * 0.2:.0f}" r="{h * 0.16:.0f}" fill="{c3}" opacity="0.9"/>'
            f'<circle cx="{w * 0.78:.0f}" cy="{h * 0.8:.0f}" r="{h * 0.3:.0f}" fill="{c3}" opacity="0.18"/>'
            f'<text font-family="Helvetica, Arial, sans-serif" font-weight="700" font-size="{size}" fill="#FFFFFF">{tspans}</text>'
            f'<text x="{w * 0.08:.0f}" y="{h * 0.9:.0f}" font-family="Helvetica, Arial, sans-serif" font-size="{int(h * 0.032)}" '
            f'fill="#FFFFFF" opacity="0.8">{escape(subtitle)}</text></svg>')
