import pytest

from app.engine.executors.database.query_mode import (
    ScriptMode,
    StoredProcedureMode,
    get_query_mode,
)
from app.engine.executors.database.sql_encoders import (
    BooleanEncoder,
    DateEncoder,
    NumericEncoder,
    StringEncoder,
    get_sql_encoder,
)
from app.infra.parameters import ToolParameter, substitute


def _param(**kw) -> ToolParameter:
    base = {
        'name': 'userID', 'source': 'flow', 'dataType': 'string', 'required': False,
        'sourcePath': '', 'description': '', 'placeholder': '<userID>',
        'defaultValue': '', 'validationRule': '',
    }
    base.update(kw)
    return ToolParameter.from_dict(base)


# ── encoder selection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('data_type, expected', [
    ('string', StringEncoder), ('text', StringEncoder),
    ('int', NumericEncoder), ('integer', NumericEncoder), ('decimal', NumericEncoder),
    ('date', DateEncoder), ('datetime', DateEncoder),
    ('bool', BooleanEncoder), ('boolean', BooleanEncoder),
])
def test_encoder_selected_by_data_type(data_type, expected):
    assert isinstance(get_sql_encoder(data_type), expected)


def test_encoder_selection_is_case_insensitive():
    assert isinstance(get_sql_encoder('  DateTime '), DateEncoder)


def test_unknown_data_type_falls_back_to_string_encoder():
    assert isinstance(get_sql_encoder('nonsense'), StringEncoder)
    assert isinstance(get_sql_encoder(''), StringEncoder)


# ── StringEncoder ─────────────────────────────────────────────────────────────

def test_string_encoder_doubles_single_quotes():
    assert StringEncoder().encode("O'Brien") == "O''Brien"


def test_string_encoder_none_becomes_empty():
    assert StringEncoder().encode(None) == ''


def test_string_encoder_casts_non_strings():
    assert StringEncoder().encode(42) == '42'


# ── NumericEncoder ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('value', ['42', 42, '-7', '3.14', ' 8 '])
def test_numeric_encoder_accepts_numbers(value):
    assert NumericEncoder().encode(value) == str(value).strip()


@pytest.mark.parametrize('value', ['1 OR 1=1', '42; DROP TABLE t', 'abc', '1e5'])
def test_numeric_encoder_rejects_non_numbers(value):
    with pytest.raises(ValueError, match='not a valid number'):
        NumericEncoder().encode(value)


def test_numeric_encoder_passes_empty_through():
    assert NumericEncoder().encode('') == ''


# ── DateEncoder ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('value', ['2026-04-12', '2026-04-12 13:45', '2026-04-12T13:45:30'])
def test_date_encoder_accepts_iso_dates(value):
    assert DateEncoder().encode(value) == value


@pytest.mark.parametrize('value', ["2026-04-12'; DROP TABLE t--", '12/04/2026', 'yesterday'])
def test_date_encoder_rejects_non_dates(value):
    with pytest.raises(ValueError, match='not a valid ISO-8601 date'):
        DateEncoder().encode(value)


# ── BooleanEncoder ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('value, expected', [
    ('true', '1'), ('TRUE', '1'), (True, '1'), ('yes', '1'), ('1', '1'),
    ('false', '0'), (False, '0'), ('no', '0'), ('0', '0'), ('', '0'),
])
def test_boolean_encoder(value, expected):
    assert BooleanEncoder().encode(value) == expected


def test_boolean_encoder_rejects_junk():
    with pytest.raises(ValueError, match='not a valid boolean'):
        BooleanEncoder().encode('maybe')


# ── injection, end to end through substitute + SQL encoders ───────────────────

def test_string_injection_payload_stays_inside_the_literal():
    out = substitute("WHERE ID = '<userID>'",
                     [_param(sourcePath="x'; DROP TABLE dbo.Bill--")], {}, get_sql_encoder)
    assert out == "WHERE ID = 'x''; DROP TABLE dbo.Bill--'"
    assert out.count("'") % 2 == 0   # every quote balanced → the payload stays data


def test_string_injection_payload_from_input_is_escaped_too():
    out = substitute("WHERE ID = '<userID>'", [_param()], {'userID': "a' OR '1'='1"},
                     get_sql_encoder)
    assert out == "WHERE ID = 'a'' OR ''1''=''1'"


def test_numeric_injection_into_unquoted_position_is_rejected():
    """An unquoted placeholder gets no quoting protection, so the encoder must refuse."""
    with pytest.raises(ValueError, match='not a valid number'):
        substitute('SELECT TOP <maxRows> *',
                   [_param(name='maxRows', placeholder='<maxRows>',
                           dataType='int', sourcePath='10; DROP TABLE t')],
                   {}, get_sql_encoder)


def test_realistic_script_substitution():
    script = (
        "DECLARE @UserIDRaw NVARCHAR(100) = '<userID>';\n"
        "DECLARE @Start NVARCHAR(50) = '<newPeriodStart>';\n"
        'SELECT TOP <maxRows> * FROM dbo.Bill WHERE Region = <region>;'
    )
    params = [
        _param(sourcePath='u-42', required=True),
        _param(name='newPeriodStart', placeholder='<newPeriodStart>',
               dataType='date', sourcePath='2026-04-12'),
        _param(name='maxRows', placeholder='<maxRows>', dataType='int', sourcePath=100),
        _param(name='region', placeholder='<region>', dataType='int', sourcePath='7'),
    ]
    assert substitute(script, params, {}, get_sql_encoder) == (
        "DECLARE @UserIDRaw NVARCHAR(100) = 'u-42';\n"
        "DECLARE @Start NVARCHAR(50) = '2026-04-12';\n"
        'SELECT TOP 100 * FROM dbo.Bill WHERE Region = 7;'
    )


# ── query mode registry ───────────────────────────────────────────────────────

def test_get_query_mode_returns_expected_types():
    assert isinstance(get_query_mode('script'), ScriptMode)
    assert isinstance(get_query_mode('stored_procedure'), StoredProcedureMode)


def test_get_query_mode_rejects_unknown():
    with pytest.raises(KeyError, match='Unknown queryType'):
        get_query_mode('nope')
