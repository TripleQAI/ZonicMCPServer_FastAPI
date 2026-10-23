"""Response envelope model for cached query results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CachedResult:
    """Envelope returned by ``BaseExecutor._read_through``.

    ``value`` is the cache key when ``is_cached`` is true, otherwise the raw result.
    ``variable`` is the flow key the result should be bound to (None when the result
    is not associated with a cache entry).
    """

    is_cached: bool
    variable: str | None
    value: Any

    @classmethod
    def cached(cls, variable: str, key: str) -> CachedResult:
        return cls(is_cached=True, variable=variable, value=key)

    @classmethod
    def inline(cls, raw: Any, variable: str | None = None) -> CachedResult:
        return cls(is_cached=False, variable=variable, value=raw)

    def to_dict(self) -> dict:
        return {
            'is_cached': self.is_cached,
            'data': {'variable': self.variable, 'value': self.value},
        }
