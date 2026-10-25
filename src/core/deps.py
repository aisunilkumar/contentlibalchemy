"""Lazy singleton for Services so tests can inject fakes."""
from src.core.config import settings
from src.integrations.services import Services

_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services.from_yaml(settings.services_path)
    return _services


def set_services(s: Services | None) -> None:
    global _services
    _services = s
