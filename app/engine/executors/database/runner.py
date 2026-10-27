from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from itertools import islice
from typing import TYPE_CHECKING, Any

from .row_mappers import DictRowMapper, RowMapper

if TYPE_CHECKING:
    from app.infra.connections import DatabaseConnection

#: Rows pulled from the cursor per fetch. Streaming in batches caps peak memory:
#: the whole result set is never held as raw pyodbc Row objects at once.
DEFAULT_FETCH_BATCH_SIZE = 1000


# ── Drivers: how a connection is opened ──────────────────────────────────────
def _connect_mssql(connection_string: str, timeout: float | None = None):
    import pyodbc
    conn = pyodbc.connect(connection_string, autocommit=False)
    if timeout:
        conn.timeout = int(timeout)
    return conn


def _connect_postgresql(dsn: str, timeout: float | None = None):
    """A psycopg connection with a statement timeout, read-only unless settings say otherwise."""
    import psycopg

    from app.config import get_settings
    settings = get_settings()
    seconds = timeout or settings.postgres_statement_timeout_seconds
    conn = psycopg.connect(dsn, autocommit=False,
                           options=f'-c statement_timeout={int(seconds * 1000)}')
    conn.read_only = settings.postgres_read_only
    return conn


def _opener(connection: str | DatabaseConnection, timeout: float | None) -> Callable[[], Any]:
    """The connect call of a connection; a bare string is an MSSQL connection string."""
    if isinstance(connection, str):
        return lambda: _connect_mssql(connection, timeout)
    from app.infra.connections import POSTGRESQL
    if connection.kind == POSTGRESQL:
        return lambda: _connect_postgresql(connection.dsn, timeout)
    return lambda: _connect_mssql(connection.dsn, timeout)


# ── Pattern 1: connection + transaction lifecycle as a context manager ─────────
@contextmanager
def _managed_connection(connect: str | Callable[[], Any]):
    """Open a connection, commit on success, roll back on error, always close.

    connect() runs before the try so a failed connect propagates its real error
    (the except/finally never reference an unbound conn). A string is an MSSQL
    connection string.
    """
    conn = _connect_mssql(connect) if isinstance(connect, str) else connect()
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


def _iter_result_sets(cursor, mapper: RowMapper, batch_size: int,
                      max_rows: int | None = None) -> Iterator[Any]:
    """Yield the mapped representation of each result set that carries columns.

    Result sets without a description (row-count-only statements such as
    DECLARE/INSERT/DROP in a batch) are skipped. ``max_rows`` caps each set.
    """
    while True:
        if cursor.description is not None:
            columns = [d[0] for d in cursor.description]
            rows = _iter_rows(cursor, batch_size)
            yield mapper.map_result_set(columns, islice(rows, max_rows) if max_rows else rows)
        if not cursor.nextset():
            break


# ── Pattern 4: shared execution core (Template Method via injected callables) ──
def _execute(connect: str | Callable[[], Any], prepare_cursor: Callable[[Any], None],
             collect: Callable[[Any], Any]) -> Any:
    """Run one query: manage the connection, let the caller prepare the cursor and
    collect the results. prepare_cursor and collect are the only varying steps."""
    with _managed_connection(connect) as conn:
        cursor = conn.cursor()
        prepare_cursor(cursor)
        return collect(cursor)


def _collect_last_result_set(cursor, mapper: RowMapper, batch_size: int,
                             max_rows: int | None = None) -> Any:
    """Keep only the final result set (a script's output follows its setup)."""
    result = mapper.empty()
    for mapped in _iter_result_sets(cursor, mapper, batch_size, max_rows):
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


def _script(connection: str | DatabaseConnection, sql: str, max_rows: int | None = None,
            timeout: float | None = None, mapper: RowMapper | None = None,
            batch_size: int = DEFAULT_FETCH_BATCH_SIZE) -> Any:
    """A script with its values inlined, on any connection: the last result set."""
    mapper = mapper or DictRowMapper()
    return _execute(_opener(connection, timeout), lambda cursor: cursor.execute(sql),
                    lambda cursor: _collect_last_result_set(cursor, mapper, batch_size, max_rows))


# ── Async facades (raw results; caching happens in BaseExecutor) ───────────────
async def run_script(connection: str | DatabaseConnection, sql: str, params: dict,
                     max_rows: int | None = None, timeout: float | None = None) -> Any:
    if isinstance(connection, str) and params:
        return await asyncio.to_thread(_pyodbc_script, connection, sql, params)
    return await asyncio.to_thread(_script, connection, sql, max_rows, timeout)


async def run_stored_procedure(connection: str | DatabaseConnection, procedure_name: str,
                               parameters: list[dict]) -> dict:
    from app.infra.connections import POSTGRESQL
    if not isinstance(connection, str):
        if connection.kind == POSTGRESQL:
            raise ValueError('Stored procedures run on SQL Server connections only; '
                             'use a script on a PostgreSQL connection.')
        connection = connection.dsn
    return await asyncio.to_thread(_pyodbc_stored_procedure, connection,
                                   procedure_name, parameters)
