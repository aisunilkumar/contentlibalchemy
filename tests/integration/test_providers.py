"""Vendor wrappers with mocked SDK/HTTP: verifies parsing, cost math and error translation."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import AuthenticationError, RateLimitError as OAIRate

from src.integrations import providers as P
from src.integrations.base import AuthError, ProviderError, RateLimitError
from src.core.config import settings
from src.integrations.services import Services


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://x"))


def test_openai_llm_parses_usage_and_cost():
    llm = P.OpenAICompatLLM("o", "k", "gpt-4o", price_in=2.5, price_out=10)
    llm.client = MagicMock()
    llm.client.chat.completions.create.return_value = NS(
        choices=[NS(message=NS(content="hi"))], model="gpt-4o", usage=NS(prompt_tokens=1000, completion_tokens=500))
    r = llm.complete([{"role": "user", "content": "x"}])
    assert r.text == "hi" and r.cost == pytest.approx((1000 * 2.5 + 500 * 10) / 1e6)


def test_openai_errors_are_translated():
    llm = P.OpenAICompatLLM("o", "k", "m")
    llm.client = MagicMock()
    llm.client.chat.completions.create.side_effect = OAIRate("slow", response=_resp(429), body=None)
    with pytest.raises(RateLimitError):
        llm.complete([])
    llm.client.chat.completions.create.side_effect = AuthenticationError("bad", response=_resp(401), body=None)
    with pytest.raises(AuthError):
        llm.complete([])
    llm.client.chat.completions.create.side_effect = RuntimeError("net")
    with pytest.raises(ProviderError):
        llm.complete([])


def test_serp_parsing_and_status_codes():
    s = P.SerpSearch("serp", "k")
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"organic_results": [{"title": "T", "link": "https://a", "snippet": "S"}]}
    with patch.object(P.requests, "get", return_value=ok):
        assert s.search("q").sources == [{"title": "T", "link": "https://a", "snippet": "S"}]
    for code, exc in ((429, RateLimitError), (401, AuthError)):
        with patch.object(P.requests, "get", return_value=MagicMock(status_code=code)):
            with pytest.raises(exc):
                s.search("q")
    with patch.object(P.requests, "get", side_effect=RuntimeError("dns")):
        with pytest.raises(ProviderError):
            s.search("q")


def test_openai_websearch_extracts_citations():
    w = P.OpenAIWebSearch("w", "k")
    ann = NS(type="url_citation", title="T", url="https://a")
    msg = NS(type="message", content=[NS(annotations=[ann, ann])])
    w.client = MagicMock()
    w.client.responses.create.return_value = NS(output=[msg], output_text="brief")
    r = w.search("q")
    assert r.summary == "brief" and r.sources == [{"title": "T", "link": "https://a", "snippet": ""}]


def test_perplexity_uses_citations():
    p = P.PerplexitySearch("p", "k")
    out = MagicMock()
    out.choices = [NS(message=NS(content="ans"))]
    out.model_dump.return_value = {"citations": ["https://x", "https://y"]}
    p.client = MagicMock()
    p.client.chat.completions.create.return_value = out
    assert [s["link"] for s in p.search("q").sources] == ["https://x", "https://y"]


def test_image_provider_handles_url_and_base64():
    img = P.OpenAIImage("i", "k")
    img.client = MagicMock()
    img.client.images.generate.return_value = NS(data=[NS(b64_json=None, url="https://img")])
    assert img.generate("p").url == "https://img"
    assert img.client.images.generate.call_args.kwargs["size"] == "1024x1024"
    assert img.generate("p", "landscape").size == "1792x1024"
    assert img.client.images.generate.call_args.kwargs["size"] == "1792x1024"
    gi = P.OpenAIImage("g", "k", model="gpt-image-1")
    gi.client = MagicMock()
    gi.client.images.generate.return_value = NS(data=[NS(b64_json="aGk=", url=None)])
    assert gi.generate("p", "landscape").size == "1536x1024" and gi.generate("p", "portrait").size == "1024x1536"
    img.client.images.generate.return_value = NS(data=[NS(b64_json="aGk=", url=None)])
    assert img.generate("p").data == b"hi"
    img.client.images.generate.side_effect = RuntimeError("x")
    with pytest.raises(ProviderError):
        img.generate("p")
    assert P.PromptOnlyImage().generate("p").prompt == "p"


def test_from_yaml_skips_providers_without_keys(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "SERPAPI_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert Services.from_yaml(settings.services_path).status() == {
        "llm_smart": [], "llm_cheap": [], "search": [], "image": []}


def test_from_yaml_openai_only(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "SERPAPI_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    st = Services.from_yaml(settings.services_path).status()
    assert st["llm_smart"] == ["openai_gpt4o", "openai_mini"]
    assert st["search"] == ["openai_websearch"] and st["image"] == ["openai_image"]


def test_all_keys_builds_every_provider(monkeypatch):
    pytest.importorskip("anthropic")
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "SERPAPI_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.setenv(k, "x")
    st = Services.from_yaml(settings.services_path).status()
    assert st["llm_smart"] == ["openai_gpt4o", "anthropic_sonnet", "gemini_flash", "openai_mini"]
    assert st["search"] == ["serpapi", "perplexity_sonar", "openai_websearch"]
