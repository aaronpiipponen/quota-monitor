"""Runs every enabled provider once, isolating failures between providers."""

from __future__ import annotations

from typing import Callable, Optional

from .config import AppConfig
from .models import PollResult, utc_now
from .providers import PROVIDERS, Provider, ProviderError

ProviderFactory = Callable[[type[Provider], dict, AppConfig], Provider]


def _default_factory(cls: type[Provider], settings: dict, config: AppConfig) -> Provider:
    # Providers may keep small private files (e.g. a token cache) next to the database.
    return cls(settings, state_dir=config.db_path.parent)


def poll_all(config: AppConfig, registry: Optional[dict[str, type[Provider]]] = None,
             provider_factory: ProviderFactory = _default_factory) -> list[PollResult]:
    """Poll each enabled provider and return one result per config section.

    A failure in one provider is recorded as an error result and never prevents
    the remaining providers from being polled.
    """
    registry = PROVIDERS if registry is None else registry
    results: list[PollResult] = []
    for key, settings in config.providers.items():
        if not settings.get("enabled", False):
            continue
        polled_at = utc_now()
        type_name = str(settings.get("type", key))
        provider_cls = registry.get(type_name)
        if provider_cls is None:
            results.append(PollResult(key, polled_at, error=f"Unknown provider type '{type_name}'."))
            continue
        try:
            meters = provider_factory(provider_cls, settings, config).fetch()
            results.append(PollResult(key, polled_at, meters=meters))
        except ProviderError as exc:
            results.append(PollResult(key, polled_at, error=str(exc)))
        except Exception as exc:  # Deliberately broad: a provider bug must not stop the poll run.
            results.append(PollResult(key, polled_at, error=f"Unexpected {type(exc).__name__}: {exc}"))
    return results
