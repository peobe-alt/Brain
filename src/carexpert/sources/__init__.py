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


def source_for_url(url: str) -> str | None:
    """Which configured source a pasted URL belongs to.

    Matched on the registrable name rather than the full host, because the
    same site is `autoscout24.fr`, `.de`, `.it` and `www.` or not, and a user
    pasting a German search should not have to say so.
    """
    from urllib.parse import urlparse

    host = (urlparse(url).netloc or "").lower().split(":")[0]
    if not host:
        return None
    labels = [part for part in host.split(".") if part not in ("www", "m")]
    if not labels:
        return None

    best: tuple[int, str] | None = None
    for name, config in load_site_configs().items():
        candidates = {name.replace("-", "")}
        base = (config.get("base_url") or "").lower()
        if base:
            base_host = urlparse(base).netloc.split(":")[0]
            candidates |= {
                part for part in base_host.split(".") if part not in ("www", "com", "fr", "de")
            }
        for candidate in candidates:
            if candidate and candidate in {label.replace("-", "") for label in labels}:
                # Le nom le plus long l'emporte: "autoscout24" plutot que "auto".
                if best is None or len(candidate) > best[0]:
                    best = (len(candidate), name)
    return best[1] if best else None


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
    "source_for_url",
]
