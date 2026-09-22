"""Shared pytest fixtures for the whole `tests/` package.

Two things every test needs, regardless of which tool it exercises:

1. `tools._salt_common`'s module-level caches (client, webhook secret)
   must be reset between tests -- otherwise a client configured with one
   test's stubs would leak into the next test that happens to use the
   same host string.
2. The pending-ask file store must NEVER touch the real `~/.salt/...` on
   this machine -- `_state_dir()` is monkeypatched to a pytest `tmp_path`
   for every test, whether or not that particular test exercises
   ask_human/get_answer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from tools import _salt_common  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_salt_common_state(tmp_path, monkeypatch):
    _salt_common._client_cache.clear()
    _salt_common._webhook_secret_cache.clear()

    state_dir = tmp_path / "salt-dify-plugin-asks"

    def _fake_state_dir() -> Path:
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    monkeypatch.setattr(_salt_common, "_state_dir", _fake_state_dir)

    yield

    _salt_common._client_cache.clear()
    _salt_common._webhook_secret_cache.clear()


@pytest.fixture
def fake_client(monkeypatch):
    """Installs `FakeSaltClient` in place of the real `SaltClient` and
    returns the ready-to-configure instance `_salt_common.get_client`
    will hand back to every tool for the given credentials."""
    from tests.fakes import FakeSaltClient

    monkeypatch.setattr(_salt_common, "SaltClient", FakeSaltClient)

    def _make(credentials: dict) -> FakeSaltClient:
        return _salt_common.get_client(credentials)

    return _make
