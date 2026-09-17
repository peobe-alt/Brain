"""Source registry: built-in adapters plus everything declared in `sites/`."""

from __future__ import annotations

from typing import Any

from .base import SourceAdapter, SourceInfo
from .configured import ConfiguredSource, load_site_configs
from .demo import DemoSource
from .fetcher import FetchError, PoliteFetcher, RobotsDisallowed

BUILTIN = {"demo": DemoSource}


def available_sources() -> dict[str, SourceInfo]:
    """Every source that can be used right now, keyed by name."""
    sources: dict[str, SourceInfo] = {"demo": DemoSource().info}
    for name, config in load_site_configs().items():
        sources[name] = SourceInfo(
            name=name,
            label=config.get("label", name),
            countries=config.get("countries", []),
            requires_js=bool(config.get("requires_js", False)),
            notes=config.get("notes", ""),
        )
    return sources


def get_source(name: str, **kwargs: Any) -> SourceAdapter:
    if name in BUILTIN:
        return BUILTIN[name](**kwargs)
    configs = load_site_configs()
    if name not in configs:
        known = ", ".join(sorted(available_sources()))
        raise KeyError(f"source inconnue '{name}'. Sources disponibles: {known}")
    return ConfiguredSource(configs[name], **kwargs)


__all__ = [
    "ConfiguredSource",
    "DemoSource",
    "FetchError",
    "PoliteFetcher",
    "RobotsDisallowed",
    "SourceAdapter",
    "SourceInfo",
    "available_sources",
    "get_source",
]
