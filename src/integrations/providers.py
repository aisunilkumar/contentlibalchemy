"""One class per vendor. Every vendor failure is translated to ProviderError subclasses."""
import base64
import time

import requests
from openai import AuthenticationError as OAIAuth
from openai import OpenAI
from openai import RateLimitError as OAIRate

from .base import (AuthError, ImageProvider, ImageResult, LLMProvider, LLMResult,
                   ProviderError, RateLimitError, SearchProvider, SearchResult, Usage)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
PERPLEXITY_URL = "https://api.perplexity.ai"


def _wrap(e):
    if isinstance(e, OAIRate):
        return RateLimitError(str(e))
    if isinstance(e, OAIAuth):
        return AuthError(str(e))
    return ProviderError(str(e))


def _cost(u: Usage, p_in, p_out):
    return (u.input_tokens * p_in + u.output_tokens * p_out) / 1_000_000


# ---------------- LLMs ----------------
class OpenAICompatLLM(LLMProvider):
    """Covers OpenAI and Gemini (OpenAI-compatible endpoint)."""

    def __init__(self, name, api_key, model, base_url=None, price_in=0, price_out=0, rpm=60):
        self.name, self.model, self.rpm = name, model, rpm
        self.price = (price_in, price_out)
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=0)

    def complete(self, messages, *, temperature=0.4, max_tokens=1500, model=None):
        t0 = time.time()
        try:
            r = self.client.chat.completions.create(
                model=model or self.model, messages=messages,
                temperature=temperature, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001
            raise _wrap(e) from e
        usage = r.usage
        u = Usage(getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)
        return LLMResult(r.choices[0].message.content or "", self.name, r.model,
                         u, _cost(u, *self.price), time.time() - t0)


class AnthropicLLM(LLMProvider):
    def __init__(self, name, api_key, model, price_in=0, price_out=0, rpm=50):
        import anthropic  # optional dependency
        self._a, self.name, self.model, self.rpm = anthropic, name, model, rpm
        self.price = (price_in, price_out)
        self.client = anthropic.Anthropic(api_key=api_key, timeout=60, max_retries=0)

    def complete(self, messages, *, temperature=0.4, max_tokens=1500, model=None):
        t0 = time.time()
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [m for m in messages if m["role"] != "system"]
        kw = {"system": system} if system else {}
        try:
            r = self.client.messages.create(model=model or self.model, messages=msgs,
                                            max_tokens=max_tokens, temperature=temperature, **kw)
        except self._a.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except self._a.AuthenticationError as e:
            raise AuthError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(str(e)) from e
        u = Usage(r.usage.input_tokens, r.usage.output_tokens)
        text = "".join(b.text for b in r.content if b.type == "text")
        return LLMResult(text, self.name, r.model, u, _cost(u, *self.price), time.time() - t0)


# ---------------- Search ----------------
class SerpSearch(SearchProvider):
    def __init__(self, name, api_key, rpm=30):
        self.name, self.key, self.rpm = name, api_key, rpm

    def search(self, query, num=6):
        try:
            r = requests.get("https://serpapi.com/search.json",
                             params={"q": query, "api_key": self.key, "num": num}, timeout=20)
            if r.status_code == 429:
                raise RateLimitError("serpapi 429")
            if r.status_code in (401, 403):
                raise AuthError("serpapi auth")
            r.raise_for_status()
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(str(e)) from e
        src = [{"title": x.get("title"), "link": x.get("link"), "snippet": x.get("snippet")}
               for x in r.json().get("organic_results", [])]
        return SearchResult("", src, self.name, cost=0.01)  # approx per-search cost


class PerplexitySearch(SearchProvider):
    def __init__(self, name, api_key, model="sonar", rpm=50):
        self.name, self.model, self.rpm = name, model, rpm
        self.client = OpenAI(api_key=api_key, base_url=PERPLEXITY_URL, timeout=60, max_retries=0)

    def search(self, query, num=6):
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": f"Research with sources: {query}"}])
        except Exception as e:  # noqa: BLE001
            raise _wrap(e) from e
        d = r.model_dump()
        urls = d.get("citations") or [x.get("url") for x in (d.get("search_results") or [])]
        src = [{"title": u, "link": u, "snippet": ""} for u in urls if u]
        return SearchResult(r.choices[0].message.content or "", src, self.name, cost=0.01)


class OpenAIWebSearch(SearchProvider):
    def __init__(self, name, api_key, model="gpt-4o", rpm=30, tool_type="web_search_preview"):
        self.name, self.model, self.rpm, self.tool_type = name, model, rpm, tool_type
        self.client = OpenAI(api_key=api_key, timeout=90, max_retries=0)

    def search(self, query, num=6):
        try:
            r = self.client.responses.create(
                model=self.model, tools=[{"type": self.tool_type}],  # verify tool name in current docs
                input=f"Research: {query}. Give a factual brief and cite sources.")
        except Exception as e:  # noqa: BLE001
            raise _wrap(e) from e
        src = []
        for item in r.output:
            if item.type == "message":
                for part in item.content:
                    for a in getattr(part, "annotations", None) or []:
                        if a.type == "url_citation":
                            src.append({"title": a.title, "link": a.url, "snippet": ""})
        src = list({s["link"]: s for s in src}.values())
        return SearchResult(r.output_text, src, self.name, cost=0.03)  # approx; verify pricing


# ---------------- Images ----------------
class OpenAIImage(ImageProvider):
    SIZES = {"dalle": {"square": "1024x1024", "landscape": "1792x1024", "portrait": "1024x1792"},
             "gpt-image": {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}}

    def __init__(self, name, api_key, model="dall-e-3", rpm=5):
        self.name, self.model, self.rpm = name, model, rpm
        self.client = OpenAI(api_key=api_key, timeout=120, max_retries=0)

    def _size(self, aspect):
        family = "dalle" if "dall-e" in self.model else "gpt-image"
        return self.SIZES[family].get(aspect, "1024x1024")

    def generate(self, prompt, aspect="square"):
        size = self._size(aspect)
        try:
            r = self.client.images.generate(model=self.model, prompt=prompt, size=size)
        except Exception as e:  # noqa: BLE001
            raise _wrap(e) from e
        d = r.data[0]
        if getattr(d, "b64_json", None):
            return ImageResult(self.name, self.model, prompt, data=base64.b64decode(d.b64_json), cost=0.04, size=size)
        return ImageResult(self.name, self.model, prompt, url=d.url, cost=0.04, size=size)


class PromptOnlyImage(ImageProvider):
    """Optional last resort: returns no image (the agent then builds a branded SVG card)."""
    name = "prompt_only"
    rpm = 10_000

    def generate(self, prompt, aspect="square"):
        return ImageResult(self.name, "none", prompt)
