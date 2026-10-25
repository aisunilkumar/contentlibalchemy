"""Deterministic fake providers so the whole graph can be tested with no API keys and no cost."""
import json
import re

from src.integrations.base import (ImageProvider, ImageResult, LLMProvider, LLMResult, ProviderError,
                                   SearchProvider, SearchResult, Usage)
from src.integrations.resilience import CostTracker, TTLCache
from src.integrations.services import Chain, Services


SENT = "Teams that track spend every week find savings faster than teams that guess [1]."


def good_blog(topic="cloud cost optimization", pk=None):
    pk = pk or topic
    block = " ".join([SENT] * 14)
    return (f"# {pk.title()} Guide\n\nIf you want better {pk}, start with visibility. You will see waste quickly, which is the core of FinOps. {block}\n\n"
            f"## Why {pk} matters\n{block}\n\n## How to start\n{block}\n\n## Common mistakes\n{block}\n\n"
            f"## FAQ\n\n### What is it?\nA weekly habit for your team. [INTERNAL LINK: cost tools]\n\n"
            f"### Where do you begin?\nWith tags and a simple report. [INTERNAL LINK: tagging guide]\n\n"
            f"## Conclusion\nGood {pk} starts with one habit. Pick a day and review your spend. Start today.")


GOOD_LI = ("3 reasons your cloud bill keeps growing.\n\nHere is a simple weekly habit that fixes it for you.\n\n"
           + "\n\n".join(["Track spend by team and tag every resource. Review the top five costs every Friday."] * 8)
           + "\n\nWhat is the first cost you would cut?\n\n#CloudCost #FinOps #DevOps")


class FakeLLM(LLMProvider):
    synth_text = "## Key findings\nCloud spend is rising [1]. Costs are driven by idle resources [2]."

    def __init__(self, name="fake", fail=None, bad_first=False, blog_text=None, synth_text=None):
        self.name, self.fail, self.bad_first, self.rpm = name, fail, bad_first, 10_000
        self.blog_text = blog_text
        if synth_text:
            self.synth_text = synth_text
        self.calls, self.prompts = 0, []

    def complete(self, messages, **kw):
        self.calls += 1
        if self.fail:
            raise self.fail
        sys_txt = " ".join(m["content"] for m in messages if m["role"] == "system")
        user = messages[-1]["content"] if messages[-1]["role"] == "user" else ""
        full = sys_txt + " " + " ".join(m["content"] for m in messages if m["role"] == "user")
        self.prompts.append(full)
        text = self._respond(sys_txt, user, full)
        return LLMResult(text, self.name, "fake-1", Usage(100, 100), 0.001, 0.0)

    def _respond(self, sys_txt, user, full):
        if '"title": "Route"' in sys_txt:
            return json.dumps(self._route(user))
        if '"title": "SeoPlan"' in sys_txt:
            return json.dumps({"title": "Cloud Cost Optimization: A Practical Guide To Cutting Spend Fast",
                               "meta_description": "Learn practical cloud cost optimization tactics that cut waste, improve visibility and keep your engineering team moving fast.",
                               "primary_keyword": "cloud cost optimization", "secondary_keywords": ["finops", "cloud savings"],
                               "outline": ["Why it matters", "How to start", "Common mistakes"]})
        if '"title": "Judge"' in sys_txt:
            low = "BADDRAFT" in full
            s = 4 if low else 9
            return json.dumps({"seo": s, "voice": s, "accuracy": s, "readability": s, "engagement": s,
                               "feedback": "Expand with concrete examples." if low else "Solid."})
        if '"title": "LinkedInPlan"' in sys_txt:
            return json.dumps({"angle": "Weekly review beats big projects", "format": "how_to", "audience": "engineering managers",
                               "hooks": ["Your cloud bill is a team habit problem.", "3 reasons your cloud bill keeps growing.",
                                         "Why does nobody own cloud spend?"],
                               "cta": "What is the first cost you would cut?", "hashtags": ["#CloudCost", "FinOps", "#DevOps"],
                               "first_comment": "Full guide link goes here."})
        if '"title": "ImageBrief"' in sys_txt:
            return json.dumps({"subject": "A cloud made of stacked coins above a calm city", "style": "x", "composition": "centered cloud, open sky",
                               "palette": "x", "lighting": "soft diffused light", "mood": "calm",
                               "alt_text": "Image of a cloud made of coins over a city"})
        if "search queries" in full:
            return "cloud cost overview\ncloud cost trends 2026\ncloud cost statistics\ncloud cost challenges"
        if "research brief" in full.lower() and "NUMBERED SOURCES" in full:
            return self.synth_text
        if "background brief" in full:
            return "Background on the topic. [VERIFY] figures."
        if "Write the full blog post" in full:
            if self.bad_first and "REVISION FEEDBACK" not in full:
                return "# Bad\n\nBADDRAFT too short."
            return self.blog_text or good_blog()
        if "Write a LinkedIn post" in full:
            return GOOD_LI
        return "Hi! I can write research briefs, blogs, LinkedIn posts and images. What topic?"

    @staticmethod
    def _route(q):
        q = q.lower()
        topic_m = re.search(r"\b(?:on|about|research)\s+([a-z ]+?)$", q)
        topic = topic_m.group(1).strip() if topic_m else ""
        base = {"topic": topic, "is_followup": not topic, "refine_target": "none", "instruction": ""}
        target = "blog" if "blog" in q else "linkedin" if "linkedin" in q else "image" if "image" in q else "none"
        if any(w in q for w in ("undo", "revert", "go back")):
            return {**base, "intent": "undo", "refine_target": target}
        if any(w in q for w in ("shorter", "punchier", "casual", "different style", "brighter")):
            return {**base, "intent": "refine", "refine_target": target, "instruction": q}
        if "campaign" in q or "series" in q:
            return {**base, "intent": "series"}
        if "linkedin" in q:
            return {**base, "intent": "linkedin"}
        if "blog" in q:
            return {**base, "intent": "blog"}
        if "image" in q:
            return {**base, "intent": "image"}
        if q.startswith("research"):
            return {**base, "intent": "research"}
        return {**base, "intent": "chat"}


class FakeSearch(SearchProvider):
    def __init__(self, name="fake_search", fail=None, links=None):
        self.name, self.fail, self.rpm, self.calls = name, fail, 10_000, 0
        self.links = links or ["https://a.example/1", "https://b.example/2"]

    def search(self, query, num=6):
        self.calls += 1
        if self.fail:
            raise self.fail
        return SearchResult("", [{"title": f"Source {i}", "link": l, "snippet": f"snippet {i}"}
                                 for i, l in enumerate(self.links)], self.name, 0.01)


class FakeImage(ImageProvider):
    def __init__(self, name="fake_image", fail=None, max_prompt_chars=None):
        self.name, self.fail, self.rpm, self.max_chars, self.prompts, self.aspects = name, fail, 10_000, max_prompt_chars, [], []

    def generate(self, prompt, aspect="square"):
        self.prompts.append(prompt)
        self.aspects.append(aspect)
        if self.fail:
            raise self.fail
        if self.max_chars and len(prompt) > self.max_chars:
            raise ProviderError("prompt rejected: too long")
        return ImageResult(self.name, "fake-img", prompt, url="https://img.example/x.png", cost=0.04, size="1792x1024")


def make_services(llms=None, search=None, images=None, budget=5.0):
    tracker = CostTracker(budget)
    llms = llms or [FakeLLM()]
    search = search if search is not None else [FakeSearch()]
    images = images if images is not None else [FakeImage()]
    mk = lambda cap, ps: Chain(cap, ps, tracker, retries=0, backoff=0)  # noqa: E731
    chains = {"llm_smart": mk("llm_smart", llms), "llm_cheap": mk("llm_cheap", llms),
              "search": mk("search", search), "image": mk("image", images)}
    return Services(chains, tracker, TTLCache(3600))


def err(msg="boom"):
    return ProviderError(msg)
