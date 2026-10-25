"""Rate limiting, circuit breaking, caching and cost tracking."""
import hashlib
import json
import threading
import time
from collections import Counter, defaultdict


class RateLimiter:
    """Token bucket: `rpm` requests per minute, burst = rpm."""

    def __init__(self, rpm):
        self.cap, self.tokens, self.rate = rpm, float(rpm), rpm / 60
        self.t, self.lock = time.monotonic(), threading.Lock()

    def acquire(self, timeout=30) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return True
                wait = (1 - self.tokens) / self.rate
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(wait)


class CircuitBreaker:
    def __init__(self, threshold=3, cooldown=60):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures, self.opened_at = 0, None

    def allow(self):  # after cooldown: half-open, let a probe through
        return self.opened_at is None or time.monotonic() - self.opened_at >= self.cooldown

    def success(self):
        self.failures, self.opened_at = 0, None

    def failure(self):
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()

    def trip_permanent(self):  # bad API key
        self.opened_at = float("inf")


class TTLCache:
    def __init__(self, ttl=3600, maxsize=512):
        self.ttl, self.maxsize, self.d, self.lock = ttl, maxsize, {}, threading.Lock()

    def get(self, k):
        with self.lock:
            v = self.d.get(k)
            if v and time.time() - v[0] < self.ttl:
                return v[1]
            self.d.pop(k, None)
            return None

    def set(self, k, val):
        with self.lock:
            if len(self.d) >= self.maxsize:
                self.d.pop(min(self.d, key=lambda x: self.d[x][0]))
            self.d[k] = (time.time(), val)

    @staticmethod
    def key(*parts):
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class BudgetExceeded(Exception):
    pass


class CostTracker:
    def __init__(self, budget):
        self.budget, self.total = budget, 0.0
        self.by_provider, self.calls = defaultdict(float), Counter()
        self.lock = threading.Lock()

    def check(self):
        if self.total >= self.budget:
            raise BudgetExceeded(f"budget ${self.budget:.2f} reached (spent ${self.total:.4f})")

    def add(self, provider, cost):
        with self.lock:
            self.total += cost
            self.by_provider[provider] += cost
            self.calls[provider] += 1

    def summary(self):
        return {
            "total_usd": round(self.total, 4),
            "budget_usd": self.budget,
            "by_provider": {k: round(v, 4) for k, v in self.by_provider.items()},
            "calls": dict(self.calls),
        }
