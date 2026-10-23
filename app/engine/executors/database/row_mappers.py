"""Row-representation strategies for database result sets.

A ``RowMapper`` turns a result set — a column list plus an iterable of raw row
tuples — into an output representation. Separating this from the cursor plumbing
in ``runner.py`` lets the representation be swapped (dict-per-row vs columnar)
without touching connection or traversal logic, and lets each mapper be unit
tested against fake rows with no database.

The row iterable is consumed lazily (the runner streams it via ``fetchmany``), so
a mapper must iterate it exactly once.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any


class RowMapper(ABC):
    """Strategy: convert (columns, rows) into an output representation."""

    @abstractmethod
    def map_result_set(self, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> Any:
        """Materialize one result set. ``rows`` is single-pass — iterate it once."""

    @abstractmethod
    def empty(self) -> Any:
        """The representation of 'no result set' (e.g. a batch with no SELECT)."""


class DictRowMapper(RowMapper):
    """Default: a list of ``{column: value}`` dicts — the historical shape."""

    def map_result_set(self, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[dict]:
        return [dict(zip(columns, row, strict=False)) for row in rows]

    def empty(self) -> list[dict]:
        return []


class ColumnarRowMapper(RowMapper):
    """Columnar ``{'columns': [...], 'rows': [[...]]}`` — avoids the per-row dict
    overhead (~2.8x less memory on wide results), at the cost of a shape the caller
    must know how to read."""

    def map_result_set(self, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> dict:
        return {'columns': list(columns), 'rows': [list(row) for row in rows]}

    def empty(self) -> dict:
        return {'columns': [], 'rows': []}
