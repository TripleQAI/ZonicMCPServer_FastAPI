"""Tests for the database runner: connection lifecycle, streaming result-set walk,
and row-mapping strategy.

pyodbc is not exercised — a fake cursor stands in, so these run without a database.
The fake serves rows through fetchmany() to mirror the runner's batched streaming.
"""
import pytest

from app.engine.executors.database import runner
from app.engine.executors.database.row_mappers import (
    ColumnarRowMapper,
    DictRowMapper,
)


class FakeCursor:
    """Replays a scripted sequence of result sets, mimicking pyodbc's cursor.

    Each entry is either None (a row count — no description) or (columns, rows).
    Rows are handed out through fetchmany() so batching is exercised; fetch_calls
    records each batch size requested for the current set.
    """

    def __init__(self, sets):
        self._sets = sets
        self._i = 0
        self._pos = 0
        self.executed = None
        self.fetch_calls = []

    @property
    def description(self):
        current = self._sets[self._i]
        return None if current is None else [(c,) for c in current[0]]

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchmany(self, size):
        self.fetch_calls.append(size)
        rows = self._sets[self._i][1]
        batch = rows[self._pos:self._pos + size]
        self._pos += len(batch)
        return batch

    def nextset(self):
        if self._i + 1 >= len(self._sets):
            return False
        self._i += 1
        self._pos = 0
        return True


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def connect(monkeypatch):
    """Patches pyodbc.connect to hand back a FakeConnection over the given sets."""
    made = {}

    def _install(sets):
        cursor = FakeCursor(sets)
        conn = FakeConnection(cursor)
        made['conn'] = conn
        made['cursor'] = cursor

        class FakePyodbc:
            @staticmethod
            def connect(connection_string, autocommit=False):
                return conn

        monkeypatch.setitem(__import__('sys').modules, 'pyodbc', FakePyodbc)
        return made

    return _install


# ── result-set selection (behaviour preserved from before the refactor) ───────

def test_returns_rows_from_a_plain_select(connect):
    made = connect([(['x'], [(1,)])])
    assert runner._pyodbc_script('cs', 'SELECT 1 AS x', {}) == [{'x': 1}]
    assert made['conn'].committed is True


def test_skips_leading_row_counts_to_reach_the_select(connect):
    """DECLARE/CREATE/INSERT report row counts with no description."""
    connect([None, None, None, (['a'], [(1,), (2,)])])
    assert runner._pyodbc_script('cs', 'batch', {}) == [{'a': 1}, {'a': 2}]


def test_ignores_trailing_row_counts_after_the_select(connect):
    """Trailing DROP TABLE statements must not wipe the collected rows."""
    connect([None, (['a'], [(1,)]), None, None])
    assert runner._pyodbc_script('cs', 'batch', {}) == [{'a': 1}]


def test_returns_the_last_result_set_when_several_exist(connect):
    connect([(['first'], [(1,)]), (['second'], [(2,)])])
    assert runner._pyodbc_script('cs', 'batch', {}) == [{'second': 2}]


def test_batch_with_no_result_set_returns_empty(connect):
    connect([None, None])
    assert runner._pyodbc_script('cs', 'DECLARE @x INT = 1;', {}) == []


def test_empty_result_set_returns_empty(connect):
    connect([None, (['a'], [])])
    assert runner._pyodbc_script('cs', 'batch', {}) == []


# ── parameter binding ─────────────────────────────────────────────────────────

def test_params_are_bound_positionally(connect):
    made = connect([(['x'], [(1,)])])
    runner._pyodbc_script('cs', 'SELECT ?', {'a': 7})
    assert made['cursor'].executed == ('SELECT ?', [7])


def test_no_params_executes_without_binding(connect):
    made = connect([(['x'], [(1,)])])
    runner._pyodbc_script('cs', 'SELECT 1', {})
    assert made['cursor'].executed == ('SELECT 1', None)


# ── connection lifecycle (Pattern 1) ──────────────────────────────────────────

def test_success_commits_and_closes(connect):
    made = connect([(['x'], [(1,)])])
    runner._pyodbc_script('cs', 'SELECT 1', {})
    assert made['conn'].committed is True
    assert made['conn'].rolled_back is False
    assert made['conn'].closed is True


def test_failure_rolls_back_and_closes(connect, monkeypatch):
    made = connect([(['x'], [(1,)])])

    def boom(*a, **kw):
        raise RuntimeError('query failed')

    monkeypatch.setattr(made['cursor'], 'execute', boom)
    with pytest.raises(RuntimeError, match='query failed'):
        runner._pyodbc_script('cs', 'SELECT 1', {})
    assert made['conn'].rolled_back is True
    assert made['conn'].committed is False
    assert made['conn'].closed is True


# ── streaming / batching (Tier 2, Pattern 3) ──────────────────────────────────

def test_all_rows_returned_across_multiple_batches(connect):
    rows = [(i,) for i in range(2500)]
    connect([(['n'], rows)])
    out = runner._pyodbc_script('cs', 'SELECT n', {}, batch_size=1000)
    assert out == [{'n': i} for i in range(2500)]


def test_fetch_happens_in_batches_of_batch_size(connect):
    made = connect([(['n'], [(i,) for i in range(2500)])])
    runner._pyodbc_script('cs', 'SELECT n', {}, batch_size=1000)
    # 1000, 1000, 500 -> then a final empty fetch that returns [] and stops
    assert made['cursor'].fetch_calls == [1000, 1000, 1000, 1000]


def test_custom_batch_size_is_honoured(connect):
    made = connect([(['n'], [(i,) for i in range(5)])])
    runner._pyodbc_script('cs', 'SELECT n', {}, batch_size=2)
    assert made['cursor'].fetch_calls == [2, 2, 2, 2]  # 2,2,1,then empty


# ── row-mapping strategy (Pattern 2) ──────────────────────────────────────────

def test_dict_mapper_is_the_default(connect):
    connect([(['a', 'b'], [(1, 2), (3, 4)])])
    assert runner._pyodbc_script('cs', 'q', {}) == [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]


def test_columnar_mapper_changes_representation(connect):
    connect([(['a', 'b'], [(1, 2), (3, 4)])])
    out = runner._pyodbc_script('cs', 'q', {}, mapper=ColumnarRowMapper())
    assert out == {'columns': ['a', 'b'], 'rows': [[1, 2], [3, 4]]}


def test_columnar_mapper_empty_result_set(connect):
    connect([None])
    out = runner._pyodbc_script('cs', 'q', {}, mapper=ColumnarRowMapper())
    assert out == {'columns': [], 'rows': []}


# ── stored procedure: all result sets (Pattern 4, _collect_all) ───────────────

def test_stored_procedure_collects_all_result_sets(connect):
    connect([(['a'], [(1,)]), (['b'], [(2,), (3,)])])
    out = runner._pyodbc_stored_procedure('cs', 'dbo.Proc', [])
    assert out == {'resultSets': [[{'a': 1}], [{'b': 2}, {'b': 3}]]}


def test_stored_procedure_binds_only_in_and_inout_params(connect):
    made = connect([(['x'], [(1,)])])
    params = [
        {'name': 'a', 'direction': 'IN', 'value': 10},
        {'name': 'b', 'direction': 'OUT', 'value': None},
        {'name': 'c', 'direction': 'INOUT', 'value': 30},
    ]
    runner._pyodbc_stored_procedure('cs', 'dbo.Proc', params)
    sql, bound = made['cursor'].executed
    assert sql == 'EXEC dbo.Proc ?, ?'
    assert bound == [10, 30]


def test_stored_procedure_columnar_mapper(connect):
    connect([(['a'], [(1,)])])
    out = runner._pyodbc_stored_procedure('cs', 'dbo.Proc', [], mapper=ColumnarRowMapper())
    assert out == {'resultSets': [{'columns': ['a'], 'rows': [[1]]}]}


# ── row_mappers unit tests (no cursor at all) ─────────────────────────────────

def test_dict_row_mapper_maps_and_empties():
    m = DictRowMapper()
    assert m.map_result_set(['a', 'b'], [(1, 2), (3, 4)]) == [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]
    assert m.empty() == []


def test_columnar_row_mapper_maps_and_empties():
    m = ColumnarRowMapper()
    assert m.map_result_set(['a', 'b'], [(1, 2)]) == {'columns': ['a', 'b'], 'rows': [[1, 2]]}
    assert m.empty() == {'columns': [], 'rows': []}


def test_mapper_consumes_a_single_pass_iterator():
    """map_result_set must iterate the row stream exactly once (it's a generator)."""
    def gen():
        yield (1,)
        yield (2,)
    assert DictRowMapper().map_result_set(['n'], gen()) == [{'n': 1}, {'n': 2}]
