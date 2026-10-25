"""Provider-agnostic types, errors and interfaces."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ProviderError(Exception):
    """Transient or unknown provider failure: retry, then fall back."""


class RateLimitError(ProviderError):
    """Provider said 429."""


class AuthError(ProviderError):
    """Bad credentials: never retry, disable the provider."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    cost: float = 0.0
    latency: float = 0.0


@dataclass
class SearchResult:
    summary: str  # may be "" (SERP returns links only)
    sources: list  # [{title, link, snippet}]
    provider: str
    cost: float = 0.0


@dataclass
class ImageResult:
    provider: str
    model: str
    prompt: str
    url: str | None = None
    data: bytes | None = None
    cost: float = 0.0
    size: str | None = None


class LLMProvider(ABC):
    name: str
    rpm: int = 60

    @abstractmethod
    def complete(self, messages, *, temperature=0.4, max_tokens=1500, model=None) -> LLMResult: ...


class SearchProvider(ABC):
    name: str
    rpm: int = 30

    @abstractmethod
    def search(self, query: str, num: int = 6) -> SearchResult: ...


class ImageProvider(ABC):
    name: str
    rpm: int = 5

    @abstractmethod
    def generate(self, prompt: str, aspect: str = "square") -> ImageResult: ...
