from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from .row_mappers import DictRowMapper, RowMapper

#: Rows pulled from the cursor per fetch. Streaming in batches caps peak memory:
#: the whole result set is never held as raw pyodbc Row objects at once.
DEFAULT_FETCH_BATCH_SIZE = 1000


# ── Pattern 1: connection + transaction lifecycle as a context manager ─────────
@contextmanager
def _managed_connection(connection_string: str):
    """Open a connection, commit on success, roll back on error, always close.

    connect() runs before the try so a failed connect propagates its real error
    (the except/finally never reference an unbound conn).
    """
    import pyodbc
    conn = pyodbc.connect(connection_string, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Pattern 3: streaming traversal (Iterator) ──────────────────────────────────
def _iter_rows(cursor, batch_size: int) -> Iterator[Any]:
    """Yield rows one at a time, fetched from the cursor in batches."""
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        yield from batch


def _iter_result_sets(cursor, mapper: RowMapper, batch_size: int) -> Iterator[Any]:
    """Yield the mapped representation of each result set that carries columns.

    Result sets without a description (row-count-only statements such as
    DECLARE/INSERT/DROP in a batch) are skipped.
    """
    while True:
        if cursor.description is not None:
            columns = [d[0] for d in cursor.description]
            yield mapper.map_result_set(columns, _iter_rows(cursor, batch_size))
        if not cursor.nextset():
            break


# ── Pattern 4: shared execution core (Template Method via injected callables) ──
def _execute(connection_string: str, prepare_cursor: Callable[[Any], None],
             collect: Callable[[Any], Any]) -> Any:
    """Run one query: manage the connection, let the caller prepare the cursor and
    collect the results. prepare_cursor and collect are the only varying steps."""
    with _managed_connection(connection_string) as conn:
        cursor = conn.cursor()
        prepare_cursor(cursor)
        return collect(cursor)


def _collect_last_result_set(cursor, mapper: RowMapper, batch_size: int) -> Any:
    """Keep only the final result set (a script's output follows its setup)."""
    result = mapper.empty()
    for mapped in _iter_result_sets(cursor, mapper, batch_size):
        result = mapped
    return result


def _collect_all_result_sets(cursor, mapper: RowMapper, batch_size: int) -> dict:
    """Keep every result set (a stored procedure may return several)."""
    return {'resultSets': list(_iter_result_sets(cursor, mapper, batch_size))}


# ── Public sync runners (executed off-thread by the async facades below) ───────
def _pyodbc_script(connection_string: str, sql: str, params: dict[str, Any],
                   mapper: RowMapper | None = None,
                   batch_size: int = DEFAULT_FETCH_BATCH_SIZE) -> Any:
    mapper = mapper or DictRowMapper()

    def prepare(cursor) -> None:
        if params:
            cursor.execute(sql, list(params.values()))
        else:
            cursor.execute(sql)

    return _execute(connection_string, prepare, lambda cursor: _collect_last_result_set(cursor, mapper, batch_size))


def _pyodbc_stored_procedure(connection_string: str, procedure_name: str,
                             parameters: list[dict], mapper: RowMapper | None = None,
                             batch_size: int = DEFAULT_FETCH_BATCH_SIZE) -> dict[str, Any]:
    mapper = mapper or DictRowMapper()

    def prepare(cursor) -> None:
        in_params = [p for p in parameters if p.get('direction', 'IN') in ('IN', 'INOUT')]
        call_args = [p.get('value') for p in in_params]
        call = f"EXEC {procedure_name} " + ', '.join('?' for _ in call_args)
        cursor.execute(call, call_args)

    return _execute(connection_string, prepare, lambda cursor: _collect_all_result_sets(cursor, mapper, batch_size))


# ── Async facades (raw results; caching happens in BaseExecutor) ───────────────
async def run_script(connection_string: str, sql: str, params: dict) -> Any:
    return await asyncio.to_thread(_pyodbc_script, connection_string, sql, params)


async def run_stored_procedure(connection_string: str, procedure_name: str,
                               parameters: list[dict]) -> dict:
    return await asyncio.to_thread(_pyodbc_stored_procedure, connection_string,
                                   procedure_name, parameters)
