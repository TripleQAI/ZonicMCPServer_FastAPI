import pytest

from app.infra.parameters import ToolParameter, ValueEncoder, substitute


class PassthroughEncoder(ValueEncoder):
    """Encoder that does nothing — isolates the substitution walk from encoding."""

    def encode(self, value):
        return '' if value is None else str(value)


class MarkerEncoder(ValueEncoder):
    """Encoder that brackets its output, proving the factory is actually consulted."""

    def encode(self, value):
        return f'[{value}]'


def _passthrough(data_type: str) -> ValueEncoder:
    return PassthroughEncoder()


def _param(**kw) -> ToolParameter:
    base = {
        'name': 'userID', 'source': 'flow', 'dataType': 'string', 'required': False,
        'sourcePath': '', 'description': '', 'placeholder': '<userID>',
        'defaultValue': '', 'validationRule': '',
    }
    base.update(kw)
    return ToolParameter.from_dict(base)


# ── ToolParameter.from_dict ───────────────────────────────────────────────────

def test_from_dict_maps_camel_case_keys():
    p = ToolParameter.from_dict({
        'name': 'newPeriodStart', 'source': 'flow', 'dataType': 'string',
        'required': True, 'sourcePath': '2026-04-12', 'placeholder': '<newPeriodStart>',
        'defaultValue': 'x', 'validationRule': 'rule', 'description': 'd',
    })
    assert p.name == 'newPeriodStart'
    assert p.source_path == '2026-04-12'
    assert p.data_type == 'string'
    assert p.required is True
    assert p.default_value == 'x'
    assert p.validation_rule == 'rule'


def test_from_dict_applies_defaults_for_missing_keys():
    p = ToolParameter.from_dict({'name': 'x'})
    assert p.placeholder == ''
    assert p.data_type == 'string'
    assert p.required is False


def test_from_list_builds_every_entry():
    params = ToolParameter.from_list([{'name': 'a'}, {'name': 'b'}])
    assert [p.name for p in params] == ['a', 'b']


# ── value precedence ──────────────────────────────────────────────────────────

def test_input_wins_over_source_path():
    assert _param(sourcePath='from-flow').resolve({'userID': 'from-input'}) == 'from-input'


def test_source_path_used_when_no_input():
    assert _param(sourcePath='2026-04-12').resolve({}) == '2026-04-12'


def test_default_value_used_when_source_path_empty():
    assert _param(sourcePath='', defaultValue='fallback').resolve({}) == 'fallback'


def test_required_parameter_without_value_raises():
    with pytest.raises(ValueError, match='Required parameter "userID"'):
        _param(required=True).resolve({})


def test_required_parameter_satisfied_by_input():
    assert _param(required=True).resolve({'userID': 'u-42'}) == 'u-42'


# ── substitute ────────────────────────────────────────────────────────────────

def test_substitutes_into_pre_quoted_template():
    out = substitute("DECLARE @U NVARCHAR(100) = '<userID>';",
                     [_param(sourcePath='u-42')], {}, _passthrough)
    assert out == "DECLARE @U NVARCHAR(100) = 'u-42';"


def test_substitutes_every_occurrence():
    out = substitute("WHERE A = '<userID>' OR B = '<userID>'",
                     [_param(sourcePath='u-42')], {}, _passthrough)
    assert out == "WHERE A = 'u-42' OR B = 'u-42'"


def test_multiple_parameters():
    params = [
        _param(sourcePath='u-42'),
        _param(name='newPeriodStart', placeholder='<newPeriodStart>', sourcePath='2026-04-12'),
    ]
    out = substitute("WHERE ID = '<userID>' AND Start > '<newPeriodStart>'",
                     params, {}, _passthrough)
    assert out == "WHERE ID = 'u-42' AND Start > '2026-04-12'"


def test_placeholder_with_no_matching_parameter_is_left_alone():
    out = substitute("WHERE ID = '<unknown>'", [_param(sourcePath='u-42')], {}, _passthrough)
    assert out == "WHERE ID = '<unknown>'"


def test_parameter_whose_placeholder_is_absent_is_ignored():
    assert substitute('SELECT 1', [_param(sourcePath='u-42')], {}, _passthrough) == 'SELECT 1'


def test_parameter_without_placeholder_key_is_skipped():
    out = substitute("WHERE ID = '<userID>'", [_param(placeholder='')], {}, _passthrough)
    assert out == "WHERE ID = '<userID>'"


def test_template_without_placeholders_is_unchanged():
    assert substitute('SELECT 1', [], {}, _passthrough) == 'SELECT 1'


def test_prefix_placeholder_does_not_corrupt_longer_one():
    params = [
        _param(name='user', placeholder='<user>', sourcePath='short'),
        _param(sourcePath='long'),
    ]
    out = substitute("WHERE A = '<user>' AND B = '<userID>'", params, {}, _passthrough)
    assert out == "WHERE A = 'short' AND B = 'long'"


def test_encoder_factory_is_consulted_per_parameter():
    out = substitute("ID = <userID>", [_param(sourcePath='u-42')], {},
                     lambda data_type: MarkerEncoder())
    assert out == 'ID = [u-42]'


def test_encoder_factory_receives_the_parameter_data_type():
    seen: list[str] = []

    def factory(data_type: str) -> ValueEncoder:
        seen.append(data_type)
        return PassthroughEncoder()

    params = [_param(sourcePath='1', dataType='int'),
              _param(name='d', placeholder='<d>', sourcePath='x', dataType='date')]
    substitute('<userID> <d>', params, {}, factory)
    assert seen == ['int', 'date']
