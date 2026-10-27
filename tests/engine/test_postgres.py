"""Named connections and PostgreSQL scripts (decision D-7 of the Node runtime's plan).

psycopg is faked, so these run without a database. ``test_live_sample_open_data`` runs
against the real ``sample_open_data`` schema only when LIVE_SAMPLE_OPEN_DATA_DSN is set.
"""
import asyncio
import os
import sys

import pytest
from pydantic import SecretStr

from app.engine.executors.database.executor import DatabaseExecutor
from app.engine.executors.database.query_mode import ScriptMode
from app.engine.executors.database.sql_encoders import PostgresBooleanEncoder, get_sql_encoder
from app.infra.connections import ConnectionRegistry, DatabaseConnection, kind_of
from app.models import ToolSpec

PG_DSN = 'postgresql://reader:pw@db.example.test/neondb'


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def named(default_settings, monkeypatch):
    monkeypatch.setattr(default_settings, 'database_connections',
                        {'sample_open_data': SecretStr(PG_DSN)})
    monkeypatch.setattr(default_settings, 'db_connection_string_override', SecretStr('Driver=x;'))
    registry = ConnectionRegistry()
    monkeypatch.setattr('app.infra.connections.get_connection_registry', lambda: registry)
    return registry


class FakeCursor:
    def __init__(self, sets):
        self.sets = sets
        self.index = 0
        self.executed = []

    @property
    def description(self):
        columns = self.sets[self.index][0] if self.index < len(self.sets) else None
        return [(c,) for c in columns] if columns else None

    def execute(self, sql, *args):
        self.executed.append(sql)

    def fetchmany(self, size):
        rows = self.sets[self.index][1]
        batch, self.sets[self.index] = rows[:size], (self.sets[self.index][0], rows[size:])
        return batch

    def nextset(self):
        self.index += 1
        return self.index < len(self.sets)


class FakePgConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.read_only = None
        self.committed = self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def psycopg(monkeypatch):
    made = {}

    def install(sets):
        cursor = FakeCursor(sets)
        conn = FakePgConnection(cursor)
        made.update(cursor=cursor, conn=conn)

        class FakePsycopg:
            @staticmethod
            def connect(dsn, autocommit=False, options=''):
                made.update(dsn=dsn, options=options)
                return conn

        monkeypatch.setitem(sys.modules, 'psycopg', FakePsycopg)
        return made

    return install


# ── the registry ──────────────────────────────────────────────────────────────

def test_kind_follows_the_dsn_scheme():
    assert kind_of(PG_DSN) == 'postgresql'
    assert kind_of('postgres://u@h/d') == 'postgresql'
    assert kind_of('Driver={ODBC Driver 18 for SQL Server};Server=x') == 'mssql'


def test_default_and_named_connections(named):
    assert named.get(None).kind == 'mssql'
    assert named.get('default').dsn == 'Driver=x;'
    connection = named.get('sample_open_data')
    assert (connection.kind, connection.dsn) == ('postgresql', PG_DSN)
    assert 'pw' not in repr(connection)


def test_unknown_connection_names_the_configured_ones(named):
    with pytest.raises(ValueError, match=r'Unknown database connection "nope".*sample_open_data'):
        named.get('nope')


def test_connections_parse_from_json_env(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv('DATABASE_CONNECTIONS', f'{{"sample_open_data": "{PG_DSN}"}}')
    settings = Settings(_env_file=None)
    assert settings.database_connections['sample_open_data'].get_secret_value() == PG_DSN


# ── PostgreSQL scripts ────────────────────────────────────────────────────────

def test_script_runs_read_only_with_a_statement_timeout(named, psycopg):
    made = psycopg([(['id', 'name'], [(1, 'A'), (2, 'B'), (3, 'C')])])
    config = {'script': "SELECT * FROM t WHERE x = '<x>' AND ok = <ok>", 'maxRows': 2,
              'queryTimeout': 5,
              'parameters': [{'name': 'x', 'placeholder': '<x>'},
                             {'name': 'ok', 'placeholder': '<ok>', 'dataType': 'boolean'}]}
    out = _run(ScriptMode().execute(named.get('sample_open_data'), config,
                                    {'x': "O'Neil", 'ok': 'yes'}, None))
    assert out == [{'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}]
    assert made['cursor'].executed == ["SELECT * FROM t WHERE x = 'O''Neil' AND ok = true"]
    assert made['options'] == '-c statement_timeout=5000'
    assert made['conn'].read_only is True
    assert made['conn'].committed and made['conn'].closed


def test_the_last_result_set_is_returned(named, psycopg):
    psycopg([(None, []), (['n'], [(1,)]), (['m'], [(2,)])])
    out = _run(ScriptMode().execute(named.get('sample_open_data'), {'script': 's'}, {}, None))
    assert out == [{'m': 2}]


def test_database_executor_uses_the_named_connection(named, psycopg):
    made = psycopg([(['v'], [(7,)])])
    tool = ToolSpec(id='q', type='database', config={
        'connection': 'sample_open_data', 'databaseType': 'postgresql', 'script': 'SELECT 7 AS v'})
    result = _run(DatabaseExecutor().execute(tool, _Ctx()))
    assert result.status == 'success', result.error
    assert result.output['data']['value'] == [{'v': 7}]
    assert made['dsn'] == PG_DSN


def test_database_type_must_match_the_connection(named, psycopg):
    psycopg([])
    tool = ToolSpec(id='q', type='database', config={
        'connectionName': 'sample_open_data', 'databaseType': 'mssql', 'script': 'SELECT 1'})
    result = _run(DatabaseExecutor().execute(tool, _Ctx()))
    assert result.status == 'error'
    assert 'is postgresql, not mssql' in result.error


def test_stored_procedures_are_refused_on_postgresql(named, psycopg):
    psycopg([])
    tool = ToolSpec(id='q', type='database', config={
        'connection': 'sample_open_data', 'queryType': 'stored_procedure', 'procedureName': 'p'})
    result = _run(DatabaseExecutor().execute(tool, _Ctx()))
    assert result.status == 'error'
    assert 'SQL Server connections only' in result.error


def test_postgres_booleans():
    assert isinstance(get_sql_encoder('boolean', 'postgresql'), PostgresBooleanEncoder)
    assert get_sql_encoder('bool', 'postgresql').encode('no') == 'false'
    assert get_sql_encoder('bool').encode('no') == '0'


class _Ctx:
    flow: dict = {}
    system: dict = {}
    results: dict = {}


@pytest.mark.skipif(not os.environ.get('LIVE_SAMPLE_OPEN_DATA_DSN'),
                    reason='LIVE_SAMPLE_OPEN_DATA_DSN is not set')
def test_live_sample_open_data(default_settings, monkeypatch):
    monkeypatch.setattr(default_settings, 'database_connections',
                        {'sample_open_data': SecretStr(os.environ['LIVE_SAMPLE_OPEN_DATA_DSN'])})
    monkeypatch.setattr('app.infra.connections.get_connection_registry', ConnectionRegistry)
    connection = ConnectionRegistry().get('sample_open_data')
    assert isinstance(connection, DatabaseConnection)
    out = _run(ScriptMode().execute(connection, {
        'script': 'SELECT count(*) AS facilities FROM sample_open_data.facility'}, {}, None))
    assert out == [{'facilities': 9}]
