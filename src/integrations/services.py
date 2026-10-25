"""Fallback chains + the `Services` facade that agents use. Agents never import a vendor SDK."""
import json
import logging
import os
import random
import time
from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from .base import AuthError, LLMResult, ProviderError, SearchResult, ImageResult
from .providers import (GEMINI_URL, AnthropicLLM, OpenAICompatLLM, OpenAIImage, OpenAIWebSearch,
                        PerplexitySearch, PromptOnlyImage, SerpSearch)
from .resilience import CircuitBreaker, CostTracker, RateLimiter, TTLCache

log = logging.getLogger("services")


class AllProvidersFailed(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Entry:
    p: object
    limiter: RateLimiter
    breaker: CircuitBreaker


class Chain:
    """Ordered providers for one capability. Retries stay inside a provider; fallback moves across."""

    def __init__(self, capability, providers, tracker, retries=2, backoff=1.0, breaker_cooldown=60):
        self.capability, self.tracker = capability, tracker
        self.retries, self.backoff = retries, backoff
        self.entries = [Entry(p, RateLimiter(getattr(p, "rpm", 60)),
                              CircuitBreaker(cooldown=breaker_cooldown)) for p in providers]

    def _attempt(self, fn, *a, **kw):
        delay = self.backoff
        for i in range(self.retries + 1):
            try:
                return fn(*a, **kw)
            except AuthError:
                raise
            except ProviderError:
                if i == self.retries:
                    raise
                time.sleep(delay + random.random() * 0.1 * delay)  # backoff + jitter
                delay *= 2

    def _invoke(self, e, method, args, kwargs):
        """One provider, with all guards. Returns (result, error_message)."""
        n = e.p.name
        if not e.breaker.allow():
            return None, f"{n}: circuit open"
        if not e.limiter.acquire():
            return None, f"{n}: local rate limit"
        try:
            res = self._attempt(getattr(e.p, method), *args, **kwargs)
        except AuthError as ex:
            e.breaker.trip_permanent()
            log.error("%s disabled: bad credentials", n)
            return None, f"{n}: auth ({ex})"
        except ProviderError as ex:
            e.breaker.failure()
            log.warning("%s failed, falling back: %s", n, ex)
            return None, f"{n}: {ex}"
        e.breaker.success()
        self.tracker.add(res.provider, getattr(res, "cost", 0.0))
        return res, None

    def call(self, method, *args, **kwargs):
        """First provider that succeeds (fallback semantics)."""
        self.tracker.check()
        errors = []
        for e in self.entries:
            res, err = self._invoke(e, method, args, kwargs)
            if res is not None:
                return res
            errors.append(err)
        raise AllProvidersFailed(errors or [f"no providers configured for {self.capability}"])

    def call_all(self, method, *args, max_providers=None, **kwargs):
        """Fan-out: results from several providers (multi-source). Fails only if none succeed."""
        self.tracker.check()
        results, errors = [], []
        for e in self.entries:
            if max_providers and len(results) >= max_providers:
                break
            res, err = self._invoke(e, method, args, kwargs)
            if res is not None:
                results.append(res)
            else:
                errors.append(err)
        if not results:
            raise AllProvidersFailed(errors or [f"no providers configured for {self.capability}"])
        return results


def _extract_json(text: str) -> str:
    t = text.strip()
    s, e = t.find("{"), t.rfind("}")
    return t[s:e + 1] if s != -1 and e > s else t


class Services:
    def __init__(self, chains, tracker, cache):
        self.chains, self.tracker, self.cache = chains, tracker, cache
        self.cache_hits = 0

    # ---- construction ----
    @classmethod
    def from_yaml(cls, path="config/services.yaml"):
        with open(path) as f:
            cfg = yaml.safe_load(f)
        provs = {n: _build({**c, "_name": n}) for n, c in cfg["providers"].items()}
        tracker = CostTracker(cfg.get("budget_usd", 5.0))
        chains = {
            cap: Chain(cap, [provs[n] for n in names if provs.get(n)], tracker,
                       retries=cfg.get("retries", 2), backoff=cfg.get("backoff_seconds", 1.0))
            for cap, names in cfg["chains"].items()
        }
        return cls(chains, tracker, TTLCache(cfg.get("cache_ttl_seconds", 3600)))

    def status(self):
        return {c: [e.p.name for e in ch.entries] for c, ch in self.chains.items()}

    def stats(self):
        return {**self.tracker.summary(), "cache_hits": self.cache_hits}

    # ---- facade used by agents ----
    def complete(self, messages, *, tier="smart", **kw) -> LLMResult:
        key = None
        if kw.get("temperature", 0.4) == 0:  # deterministic -> cacheable
            key = TTLCache.key("llm", tier, messages, kw)
            if (hit := self.cache.get(key)) is not None:
                self.cache_hits += 1
                return hit
        res = self.chains[f"llm_{tier}"].call("complete", messages, **kw)
        if key:
            self.cache.set(key, res)
        return res

    def search(self, query, num=6) -> SearchResult:
        key = TTLCache.key("search", query, num)
        if (hit := self.cache.get(key)) is not None:
            self.cache_hits += 1
            return hit
        res = self.chains["search"].call("search", query, num)
        self.cache.set(key, res)
        return res

    def search_all(self, query, num=6, max_providers=2) -> list:
        """Query up to `max_providers` search providers and return every result (broader source coverage)."""
        key = TTLCache.key("search_all", query, num, max_providers)
        if (hit := self.cache.get(key)) is not None:
            self.cache_hits += 1
            return hit
        res = self.chains["search"].call_all("search", query, num, max_providers=max_providers)
        self.cache.set(key, res)
        return res

    def image(self, prompt, aspect="square") -> ImageResult:
        return self.chains["image"].call("generate", prompt, aspect)

    def structured(self, schema, messages, *, tier="cheap", retries=1, max_tokens=800):
        """JSON-mode via prompt + Pydantic validation + one repair attempt. Works on any provider."""
        sys = {"role": "system", "content":
               "Return ONLY valid JSON matching this schema. No prose, no code fences.\n"
               + json.dumps(schema.model_json_schema())}
        msgs = [sys, *messages]
        for _ in range(retries + 1):
            r = self.complete(msgs, tier=tier, temperature=0, max_tokens=max_tokens)
            try:
                return schema.model_validate_json(_extract_json(r.text))
            except ValidationError as e:
                msgs = [*msgs, {"role": "assistant", "content": r.text},
                        {"role": "user", "content": f"Invalid JSON ({e.error_count()} errors). Return corrected JSON only."}]
        raise ProviderError("structured output failed validation")


def _build(cfg):
    t = cfg["type"]
    key = os.getenv(cfg["key_env"]) if cfg.get("key_env") else None
    if cfg.get("key_env") and not key:
        return None  # key missing -> provider skipped
    pi, po, rpm, name = cfg.get("price_in", 0), cfg.get("price_out", 0), cfg.get("rpm", 60), cfg["_name"]
    return {
        "openai_llm": lambda: OpenAICompatLLM(name, key, cfg["model"], None, pi, po, rpm),
        "gemini_llm": lambda: OpenAICompatLLM(name, key, cfg["model"], GEMINI_URL, pi, po, rpm),
        "anthropic_llm": lambda: AnthropicLLM(name, key, cfg["model"], pi, po, rpm),
        "serp": lambda: SerpSearch(name, key, rpm),
        "perplexity": lambda: PerplexitySearch(name, key, cfg.get("model", "sonar"), rpm),
        "openai_websearch": lambda: OpenAIWebSearch(name, key, cfg.get("model", "gpt-4o"), rpm,
                                                   cfg.get("tool_type", "web_search_preview")),
        "openai_image": lambda: OpenAIImage(name, key, cfg.get("model", "dall-e-3"), rpm),
        "prompt_only": lambda: PromptOnlyImage(),
    }[t]()
