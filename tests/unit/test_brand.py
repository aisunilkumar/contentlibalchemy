import pytest

from src.core.brand import (BrandProfile, banned_hits, brand_prompt, load_brand_profile, parse_brand_yaml,
                            voice_checks, voice_metrics, voice_report)

P = load_brand_profile()


def test_profile_loads_with_platform_rules_and_visual():
    assert P.name == "Acme Analytics" and P.rules("linkedin").max_emojis == 2 and P.rules("blog").max_emojis == 0
    assert P.visual.colors[0].startswith("#") and "unlock the power" in P.avoid_phrases


def test_prompt_is_platform_adapted_but_same_brand():
    blog, li = brand_prompt(P, "blog"), brand_prompt(P, "linkedin")
    assert "Acme Analytics" in blog and "Acme Analytics" in li
    assert "at most 22" in blog and "at most 16" in li and "NEVER use these phrases" in li
    assert "Long-form" in blog and "no Markdown" in li


def test_banned_phrases_handle_curly_apostrophes_and_case():
    assert banned_hits("In Today\u2019s Fast-Paced World, a Game-Changer", P) == ["game-changer", "in today's fast-paced world"]


def test_voice_checks_per_platform():
    text = "Cloud spend is simple. You can fix it. " * 3
    assert all(c.passed for c in voice_checks(text, P, "blog"))
    long = "This " + "very " * 40 + "long sentence goes on."
    assert not next(c for c in voice_checks(long, P, "linkedin") if c.name == "voice_sentence_length").passed
    assert not next(c for c in voice_checks("No second person here at all.", P, "blog") if c.name == "voice_pov").passed
    assert not next(c for c in voice_checks("Hi \U0001F680\U0001F525\U0001F4A1 you", P, "linkedin") if c.name == "voice_emojis").passed


def test_metrics():
    m = voice_metrics("## Heading\n\nYou can do it! Your team too.")
    assert m["exclamations"] == 1 and m["you_per_100"] > 10


def test_cross_format_voice_report():
    ok = voice_report("You can do this. Your team can too.", "You can start today. Your first step is small.", P)
    assert ok["consistent"] and set(ok["metrics"]) == {"blog", "linkedin"}
    bad = voice_report("This is a game-changer. " + "Facts. " * 30, "You you you you you. Wow! Wow! Wow!", P)
    assert not bad["consistent"] and any("banned" in f for f in bad["flags"])


def test_yaml_validation_gives_readable_errors():
    assert parse_brand_yaml("name: X\npov: second_person").name == "X"
    with pytest.raises(ValueError):
        parse_brand_yaml("pov: nonsense")
    with pytest.raises(ValueError):
        parse_brand_yaml("- a\n- b")
    assert isinstance(BrandProfile().rules("unknown").max_emojis, int)
