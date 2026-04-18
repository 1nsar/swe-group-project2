"""
Tests for the LLM provider abstraction (Assignment 2 §3.4).

We verify:
  1. Mock provider streams tokens and fills StreamResult.
  2. Cancellation is respected mid-stream.
  3. The factory honours LLM_PROVIDER for every supported provider and falls
     back to mock when a required key is missing or placeholdered.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.services.llm_provider import (
    ChatMessage,
    GeminiProvider,
    MockProvider,
    OllamaProvider,
    StreamResult,
    get_provider,
    reset_provider,
)


@pytest.mark.asyncio
async def test_mock_provider_streams_tokens_and_fills_result():
    provider = MockProvider(delay=0)
    result = StreamResult()

    tokens: list[str] = []
    async for chunk in provider.stream(
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hello"),
        ],
        result=result,
    ):
        tokens.append(chunk)

    assert len(tokens) > 1, "should stream multiple chunks, not one blob"
    assert result.finished is True
    assert result.error is None
    assert result.output_tokens > 0
    assert "".join(tokens) == result.text


@pytest.mark.asyncio
async def test_mock_provider_respects_cancellation():
    provider = MockProvider(delay=0.01)
    cancel = asyncio.Event()
    result = StreamResult()

    collected: list[str] = []

    async def consume():
        async for chunk in provider.stream(
            [ChatMessage(role="user", content="anything")],
            cancel_event=cancel,
            result=result,
        ):
            collected.append(chunk)
            if len(collected) == 2:
                cancel.set()

    await consume()

    assert result.finished is False
    assert result.error == "cancelled"
    assert len(collected) == 2


def test_factory_falls_back_to_mock_without_api_key(monkeypatch):
    reset_provider()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    provider = get_provider()
    assert provider.name == "mock", "should fall back to mock when no API key"


def test_factory_forces_mock(monkeypatch):
    reset_provider()
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    # Even with a fake key present, LLM_PROVIDER=mock wins.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-real-looking")
    provider = get_provider()
    assert provider.name == "mock"
    reset_provider()


def test_placeholder_api_key_is_ignored(monkeypatch):
    reset_provider()
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-...")  # the .env.example default
    provider = get_provider()
    assert provider.name == "mock", ".env.example placeholder should not trigger the real adapter"
    reset_provider()


# ── multi-provider factory ──────────────────────────────────────────────────


def test_factory_selects_ollama_without_key(monkeypatch):
    """Ollama is keyless — choosing it just wires up the HTTP adapter."""
    reset_provider()
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")

    provider = get_provider()
    assert provider.name == "ollama"
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://localhost:11434"
    assert provider.default_model == "llama3.1"
    reset_provider()


def test_factory_selects_gemini_with_key(monkeypatch):
    reset_provider()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-fake-but-real-looking-key")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-2.0-flash")

    provider = get_provider()
    assert provider.name == "gemini"
    assert isinstance(provider, GeminiProvider)
    assert provider.default_model == "gemini-2.0-flash"
    reset_provider()


def test_factory_falls_back_to_mock_when_gemini_key_missing(monkeypatch):
    reset_provider()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert get_provider().name == "mock"
    reset_provider()


def test_factory_falls_back_to_mock_when_gemini_key_is_placeholder(monkeypatch):
    reset_provider()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "your-google-api-key")
    assert get_provider().name == "mock"
    reset_provider()


def test_factory_falls_back_to_mock_on_unknown_provider(monkeypatch):
    reset_provider()
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
    assert get_provider().name == "mock"
    reset_provider()


def test_gemini_message_split_translates_roles():
    """Gemini wants 'model' instead of 'assistant' and system as a separate field."""
    msgs = [
        ChatMessage(role="system", content="SYS-A"),
        ChatMessage(role="system", content="SYS-B"),
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="hello"),
        ChatMessage(role="user", content="again"),
    ]
    system_text, contents = GeminiProvider._split(msgs)
    assert system_text == "SYS-A\n\nSYS-B"
    roles = [c["role"] for c in contents]
    assert roles == ["user", "model", "user"]
    assert contents[0]["parts"][0]["text"] == "hi"
    assert contents[1]["parts"][0]["text"] == "hello"


def test_ollama_trims_trailing_slash_on_base_url():
    p = OllamaProvider(base_url="http://localhost:11434/")
    assert p.base_url == "http://localhost:11434"


@pytest.mark.asyncio
async def test_ollama_provider_parses_ndjson_stream(monkeypatch):
    """Verify OllamaProvider decodes Ollama's newline-delimited JSON frames
    and fills StreamResult with the real prompt_eval_count / eval_count."""
    import json as _json
    import backend.services.llm_provider as lp

    frames = [
        _json.dumps({"message": {"content": "Hello"}, "done": False}),
        _json.dumps({"message": {"content": " world"}, "done": False}),
        _json.dumps({
            "message": {"content": "!"},
            "done": True,
            "prompt_eval_count": 7,
            "eval_count": 11,
        }),
    ]

    class _FakeResponse:
        def __init__(self, lines):
            self._lines = lines
            self.status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aiter_lines(self):
            for l in self._lines:
                yield l

        async def aread(self):
            return b""

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None):
            assert method == "POST"
            assert url.endswith("/api/chat")
            assert json["model"] == "llama3.1"
            return _FakeResponse(frames)

    class _FakeHttpx:
        AsyncClient = _FakeClient

    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)

    provider = OllamaProvider()
    result = StreamResult()
    collected: list[str] = []
    async for tok in provider.stream(
        [ChatMessage(role="user", content="hi")],
        result=result,
    ):
        collected.append(tok)

    assert "".join(collected) == "Hello world!"
    assert result.finished is True
    assert result.input_tokens == 7
    assert result.output_tokens == 11
    assert result.model == "llama3.1"
