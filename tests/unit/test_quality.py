from src.core.brand import BrandProfile, load_brand_profile
from src.utils.quality_validation import blog_checks, linkedin_checks
from tests.fakes import GOOD_LI, good_blog

PROFILE = load_brand_profile()
RES = {"sources": [{"link": "a"}], "unverified": False}


def blog(**kw):
    d = {"title": "Cloud Cost Optimization Guide", "meta_description": "Learn practical cloud cost optimization tactics that cut waste and improve visibility for your whole engineering team.",
         "primary_keyword": "cloud cost optimization", "keywords": ["finops", "cloud savings"], "body": good_blog()}
    d.update(kw)
    return d


def failed(checks):
    return {c.name for c in checks if not c.passed}


def test_good_blog_passes_everything():
    assert failed(blog_checks(blog(), RES, PROFILE, 600)) == set()


def test_blog_failures_detected():
    bad = blog(title="T" * 80, meta_description="short", body="# A\n# B\n#### skipped\nshort text")
    f = failed(blog_checks(bad, {}, PROFILE, 600))
    assert {"title_length", "title_keyword", "meta_length", "meta_keyword", "word_count", "keyword_in_intro", "single_h1",
            "h2_structure", "heading_hierarchy", "faq_section", "keyword_in_h2"} <= f


def test_keyword_stuffing_and_density():
    stuffed = blog(body=good_blog().replace("start with visibility", "cloud cost optimization cloud cost optimization " * 20))
    assert "keyword_density" in failed(blog_checks(stuffed, RES, PROFILE, 600))
    thin = blog(body=good_blog().replace("cloud cost optimization", "it"))
    assert "keyword_density" in failed(blog_checks(thin, RES, PROFILE, 600))


def test_citation_checks_only_when_sources_verified():
    body = good_blog().replace("[1]", "")
    assert {"citations", "citation_coverage"} <= failed(blog_checks(blog(body=body), RES, PROFILE, 600))
    assert not ({"citations", "citation_coverage"} & failed(blog_checks(blog(body=body), {"sources": [], "unverified": True}, PROFILE, 600)))


def test_brand_voice_rules_are_enforced_in_blog():
    body = good_blog().replace("start with visibility", "unlock the power of cutting-edge visibility!!")
    f = failed(blog_checks(blog(body=body), RES, PROFILE, 600))
    assert {"voice_banned_phrases", "voice_exclamations"} <= f


def test_readability_threshold_comes_from_brand_profile():
    strict = BrandProfile(reading_ease_min=99)
    assert "readability" in failed(blog_checks(blog(), RES, strict, 600))


def test_linkedin_checks():
    ok = {"text": GOOD_LI}
    assert failed(linkedin_checks(ok, PROFILE)) == set()
    bad = {"text": "**Bold** " + "x" * 3100 + " https://x.com Revolutionary game-changer!!! \U0001F680\U0001F525\U0001F4A1"}
    f = failed(linkedin_checks(bad, PROFILE))
    assert {"length", "hashtags", "hook", "cta_question", "plain_text", "no_links_in_body", "voice_banned_phrases", "voice_emojis"} <= f


def test_linkedin_fold_check():
    long_hook_text = "Short line one that is okay.\n" + "y" * 220 + "\n\nWhat do you think?\n\n#A #B #C"
    assert "fold_hook" in failed(linkedin_checks({"text": long_hook_text}, PROFILE))


def test_critical_check_caps_score_even_with_a_generous_judge(svc):
    from src.utils.quality_validation import evaluate
    body = good_blog().replace("start with visibility", "unlock the power of visibility")
    res = evaluate("blog", blog(body=body), {"research": RES})
    assert res["critical_failed"] == ["voice_banned_phrases"] and res["llm_score"] == 9 and res["score"] < 7.5


def test_should_revise_rules():
    from src.core.router import should_revise
    assert not should_revise(9, 0, n_failed=1)           # one soft failure is tolerated
    assert should_revise(9, 0, n_failed=2)               # two soft failures force a revision
    assert should_revise(9, 0, critical=True)            # hard rule always revises
    assert not should_revise(9, 2, n_failed=5, critical=True)  # but never beyond the revision cap
    assert should_revise(5, 1)
