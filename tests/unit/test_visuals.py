from src.core.brand import Visual
from src.utils.visuals import (ImageBrief, assemble_image_prompt, clean_alt_text, lint_image_prompt, make_svg_card,
                               simplify_image_prompt, template_brief)

V = Visual()
B = ImageBrief(subject="A cloud made of stacked coins above a calm city", style="neon cyberpunk", palette="red and black",
               composition="centered cloud", lighting="golden hour", mood="dramatic", alt_text="x")


def test_brand_visual_identity_overrides_model_style_and_palette():
    p = assemble_image_prompt(B, V, "landscape")
    assert V.style in p and V.palette in p and "cyberpunk" not in p and "red and black" not in p
    assert "16:9" in p and "No text" in p and "golden hour" in p
    assert "square" in assemble_image_prompt(B, V, "square")


def test_lint_flags_short_long_and_missing_negative():
    assert lint_image_prompt(assemble_image_prompt(B, V)) == []
    assert any("short" in i for i in lint_image_prompt("a cloud"))
    assert any("long" in i for i in lint_image_prompt("word " * 230 + "no text"))
    assert any("no text" in i for i in lint_image_prompt("a " * 40))


def test_simplified_prompt_is_much_shorter():
    full, simple = assemble_image_prompt(B, V), simplify_image_prompt(B, V)
    assert len(simple) < len(full) * 0.7 and "No text" in simple


def test_alt_text_cleanup():
    assert clean_alt_text("Image of a cloud", "fb") == "A cloud"
    assert len(clean_alt_text("word " * 60, "fb")) <= 125 and clean_alt_text("", "fallback text") == "fallback text"


def test_template_brief_is_usable_without_llm():
    b = template_brief("FinOps", V)
    assert "FinOps" in b.subject and b.alt_text


def test_svg_card_is_valid_escaped_and_branded():
    import xml.etree.ElementTree as ET
    svg = make_svg_card('Cut <cloud> costs & "waste" fast: a practical guide for teams', "Acme", ["#111111", "#222222", "#ff0000"], "square")
    root = ET.fromstring(svg)  # raises if invalid XML
    assert root.tag.endswith("svg") and 'viewBox="0 0 1080 1080"' in svg and "#ff0000" in svg and "&lt;cloud&gt;" in svg
    assert 'viewBox="0 0 1200 630"' in make_svg_card("t", "s", [], "landscape")
