import time

import pytest

from src.integrations.base import AuthError, ProviderError
from src.integrations.resilience import BudgetExceeded, CircuitBreaker, CostTracker, RateLimiter, TTLCache
from src.integrations.services import AllProvidersFailed, Chain
from tests.fakes import FakeLLM, err, make_services


def chain(*ps, budget=1.0, retries=0):
    return Chain("llm", list(ps), CostTracker(budget), retries=retries, backoff=0)


def test_fallback_order():
    assert chain(FakeLLM("a", err()), FakeLLM("b")).call("complete", [{"role": "user", "content": "x"}]).provider == "b"


def test_auth_error_disables_provider_permanently():
    a = FakeLLM("a", AuthError("bad key"))
    c = chain(a, FakeLLM("b"))
    m = [{"role": "user", "content": "x"}]
    c.call("complete", m); c.call("complete", m)
    assert a.calls == 1


def test_breaker_opens_after_three_failures():
    a = FakeLLM("a", err())
    c = chain(a, FakeLLM("b"))
    m = [{"role": "user", "content": "x"}]
    for _ in range(5):
        c.call("complete", m)
    assert a.calls == 3


def test_retries_stay_within_provider():
    a = FakeLLM("a", err())
    with pytest.raises(AllProvidersFailed):
        chain(a, retries=2).call("complete", [{"role": "user", "content": "x"}])
    assert a.calls == 3


def test_all_fail_and_empty_chain():
    with pytest.raises(AllProvidersFailed):
        chain(FakeLLM("a", err())).call("complete", [])
    with pytest.raises(AllProvidersFailed):
        chain().call("complete", [])


def test_rate_limiter():
    rl = RateLimiter(rpm=2)
    assert rl.acquire(0.05) and rl.acquire(0.05) and not rl.acquire(0.05)


def test_local_rate_limit_triggers_fallback():
    a = FakeLLM("a"); a.rpm = 1
    c = chain(a, FakeLLM("b"))
    for e in c.entries[:1]:
        e.limiter = RateLimiter(1)
    m = [{"role": "user", "content": "x"}]
    assert c.call("complete", m).provider == "a"
    c.entries[0].limiter.acquire = lambda timeout=30: False
    assert c.call("complete", m).provider == "b"


def test_budget_guard():
    c = chain(FakeLLM("a"), budget=0.0015)
    m = [{"role": "user", "content": "x"}]
    c.call("complete", m); c.call("complete", m)
    with pytest.raises(BudgetExceeded):
        c.call("complete", m)


def test_circuit_half_open_after_cooldown():
    b = CircuitBreaker(threshold=1, cooldown=0.05)
    b.failure()
    assert not b.allow()
    time.sleep(0.06)
    assert b.allow()


def test_cache_ttl_and_eviction():
    c = TTLCache(ttl=0.05, maxsize=2)
    c.set("a", 1); c.set("b", 2); c.set("c", 3)
    assert c.get("a") is None and c.get("c") == 3
    time.sleep(0.06)
    assert c.get("c") is None


def test_deterministic_calls_are_cached():
    llm = FakeLLM()
    s = make_services(llms=[llm])
    m = [{"role": "user", "content": "hi"}]
    s.complete(m, temperature=0); s.complete(m, temperature=0)
    assert llm.calls == 1 and s.cache_hits == 1
    s.complete(m, temperature=0.7); s.complete(m, temperature=0.7)
    assert llm.calls == 3


def test_structured_repairs_invalid_json():
    from pydantic import BaseModel

    class T(BaseModel):
        a: int

    class Flaky(FakeLLM):
        def _respond(self, sys_txt, user, full):
            return "not json" if self.calls == 1 else 'sure: {"a": 3}'

    assert make_services(llms=[Flaky()]).structured(T, [{"role": "user", "content": "x"}]).a == 3
    with pytest.raises(ProviderError):
        class Always(FakeLLM):
            def _respond(self, *a): return "nope"
        make_services(llms=[Always()]).structured(T, [{"role": "user", "content": "x"}])
