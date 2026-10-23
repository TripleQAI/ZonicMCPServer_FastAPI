"""Tool-definition parameters and placeholder substitution.

Sibling of :mod:`infra.resolver`: that module resolves ``{{flow.x}}`` tokens against
the execution context; this one substitutes a tool definition's ``<placeholder>``
markers with its parameter values.

The substitution walk here is transport-agnostic. How a value is turned into text
is delegated to a :class:`ValueEncoder`, supplied by the caller — so a SQL script
inlines SQL literals while an API tool could inline URL-encoded values, without
either concern leaking into this module.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolParameter:
    """One entry of a tool definition's ``parameters`` array."""

    name: str = ''
    placeholder: str = ''
    source: str = ''
    source_path: Any = ''
    data_type: str = 'string'
    required: bool = False
    default_value: Any = ''
    validation_rule: str = ''
    description: str = ''

    @classmethod
    def from_dict(cls, d: dict) -> ToolParameter:
        return cls(
            name=d.get('name', ''),
            placeholder=d.get('placeholder', ''),
            source=d.get('source', ''),
            source_path=d.get('sourcePath', ''),
            data_type=d.get('dataType', 'string'),
            required=d.get('required', False),
            default_value=d.get('defaultValue', ''),
            validation_rule=d.get('validationRule', ''),
            description=d.get('description', ''),
        )

    @classmethod
    def from_list(cls, items: Iterable[dict]) -> list[ToolParameter]:
        return [cls.from_dict(i) for i in items]

    def resolve(self, values: dict[str, Any]) -> Any:
        """Pick this parameter's value: input → sourcePath → defaultValue.

        ``sourcePath`` arrives already resolved — ``BaseExecutor.execute`` runs the
        whole config through ``resolve_dict`` before the executor sees it.
        """
        if self.name in values:
            value = values[self.name]
        elif self.source_path not in ('', None):
            value = self.source_path
        else:
            value = self.default_value

        if self.required and value in (None, ''):
            raise ValueError(f'Required parameter "{self.name}" has no value.')
        return value


class ValueEncoder(ABC):
    """Turns a resolved parameter value into text safe for its target syntax."""

    @abstractmethod
    def encode(self, value: Any) -> str: ...


#: Resolves a parameter's ``data_type`` to the encoder that should render it.
EncoderFactory = Callable[[str], ValueEncoder]


def substitute(template: str, parameters: Iterable[ToolParameter],
               values: dict[str, Any], encoder_for: EncoderFactory) -> str:
    """Replace every parameter's ``placeholder`` in *template* with its encoded value.

    Placeholders are delimited (``<name>``), so a shorter one cannot match inside a
    longer one and substitution order does not matter.
    """
    for param in parameters:
        if not param.placeholder:
            continue
        encoded = encoder_for(param.data_type).encode(param.resolve(values))
        template = template.replace(param.placeholder, encoded)
    return template
