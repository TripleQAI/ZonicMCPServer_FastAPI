"""Install a custom package so its module becomes importable — a Strategy.

The registry calls ``ensure_installed`` after it has authorized the package (§security).
The pip implementation is idempotent (skips if already importable), runs off the event
loop in a subprocess, and invalidates Python's import caches so the fresh module resolves.
Tests inject a fake installer.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
from abc import ABC, abstractmethod

from .config_source import PackageConfig

log = logging.getLogger(__name__)


def _module_importable(module_path: str) -> bool:
    try:
        return importlib.util.find_spec(module_path) is not None
    except Exception:
        return False


class PackageInstaller(ABC):
    @abstractmethod
    async def ensure_installed(self, cfg: PackageConfig) -> bool:
        """Make cfg.module_path importable. Return True on success (or already present)."""


class PipPackageInstaller(PackageInstaller):
    """Install via ``pip`` — pinned, hash-verified, from an allowed index."""

    async def ensure_installed(self, cfg: PackageConfig) -> bool:
        if _module_importable(cfg.module_path):
            return True

        cmd = [sys.executable, '-m', 'pip', 'install', '--no-input', '--disable-pip-version-check']
        index = cfg.index_url or self._default_index()
        if index:
            cmd += ['--index-url', index]
        if cfg.hashes:
            cmd.append('--require-hashes')
            for h in cfg.hashes:
                cmd += ['--hash', h]
        cmd.append(cfg.distribution)

        ok = await self._run(cmd, cfg.distribution)
        if ok:
            importlib.invalidate_caches()
            ok = _module_importable(cfg.module_path)
        return ok

    @staticmethod
    def _default_index() -> str:
        from app.config import get_settings
        return get_settings().function_package_index_url

    @staticmethod
    async def _run(cmd: list[str], label: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                log.error('pip install failed for %s (rc=%s): %s',
                          label, proc.returncode, (out or b'').decode(errors='replace')[-500:])
                return False
            return True
        except Exception as exc:
            log.error('pip install error for %s: %s', label, exc)
            return False
