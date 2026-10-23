from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.engine.executors.base import BaseExecutor

log = logging.getLogger(__name__)
_registry: dict[str, BaseExecutor] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    from app.config import get_settings
    for type_name, class_path in get_settings().executor_types.items():
        try:
            module_path, class_name = class_path.rsplit('.', 1)
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            _registry[type_name] = cls()
            log.debug('Registered executor %s -> %s', type_name, class_path)
        except Exception as exc:
            log.error('Failed to load executor %s (%s): %s', type_name, class_path, exc)
    _loaded = True


def get_executor(tool_type: str) -> BaseExecutor:
    _load()
    exc = _registry.get(tool_type)
    if exc is None:
        raise KeyError(f'No executor registered for tool type "{tool_type}".')
    return exc


def registered_types() -> list[str]:
    _load()
    return list(_registry.keys())
