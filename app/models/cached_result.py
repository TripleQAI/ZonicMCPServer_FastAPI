"""Response envelope model for cached query results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CachedResult:
    """Envelope returned by ``BaseExecutor._read_through``.

    ``value`` is the cache key when ``is_cached`` is true, otherwise the raw result.
    ``variable`` is the flow key the result should be bound to (None when the result
    is not associated with a cache entry). ``preview`` summarizes a result just cached
    when the tool asked for one (``config.result.preview``): its first rows, row count,
    columns, size and hash.
    """

    is_cached: bool
    variable: str | None
    value: Any
    preview: dict | None = None

    @classmethod
    def cached(cls, variable: str, key: str, preview: dict | None = None) -> CachedResult:
        return cls(is_cached=True, variable=variable, value=key, preview=preview)

    @classmethod
    def inline(cls, raw: Any, variable: str | None = None) -> CachedResult:
        return cls(is_cached=False, variable=variable, value=raw)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {'variable': self.variable, 'value': self.value}
        if self.preview is not None:
            data['preview'] = self.preview
        return {'is_cached': self.is_cached, 'data': data}
