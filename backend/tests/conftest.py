"""
Shared test fixtures for Member 3's backend tests.

We isolate the JSON store to a per-test temp directory so tests don't
clobber each other or leave droppings in the repo. We also force the
mock LLM provider (no network / no API key required) and disable the
WebSocket dev-user fallback so auth is actually tested.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Make the project root importable (so `backend` is a top-level package).
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """
    Fresh backend modules per test: JSON store points at a tmp dir, provider
    is reset to mock, WS dev fallback is disabled by default.
    """
    # Route JSON store at a per-test tmp dir.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_DEV_WS_FALLBACK", "0")

    # Reload the store module so it picks up the new DATA_DIR.
    import backend.storage.json_store as store
    importlib.reload(store)
    # Override DATA_DIR directly (the module resolves it at import time).
    store.DATA_DIR = data_dir

    # Reset the cached LLM provider so tests get a fresh one.
    from backend.services import llm_provider
    llm_provider.reset_provider()

    yield {
        "data_dir": data_dir,
        "monkeypatch": monkeypatch,
    }

    llm_provider.reset_provider()


@pytest.fixture
def client(app_env):
    """FastAPI TestClient with the isolated env."""
    from fastapi.testclient import TestClient
    # Re-import main so it picks up the fresh state.
    import importlib
    import backend.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def make_jwt(sub: str = "user-1", username: str = "alice", email: str = "alice@example.com") -> str:
    """Build an unsigned JWT the stub dep will decode happily."""
    import jwt as pyjwt
    return pyjwt.encode(
        {"sub": sub, "username": username, "email": email},
        "dev-secret",
        algorithm="HS256",
    )
