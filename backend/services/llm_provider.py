"""
LLM provider abstraction.

Assignment 2 §3.4: "Abstract the LLM provider behind an interface — swapping
providers should require changes in one place."

This module provides:

- ``LLMProvider`` protocol: every provider must expose ``stream(messages, ...)``
  which yields tokens (strings) asynchronously and fills a ``StreamResult``.
- ``AnthropicProvider``: real Claude adapter using the Messages API.
- ``OllamaProvider``:   local LLM via the Ollama REST API (free, offline).
- ``GeminiProvider``:   Google Gemini via the ``google-genai`` SDK.
- ``MockProvider``:     deterministic fake that streams tokens from a canned
  response. Used by tests and when no real provider can be configured.
- ``get_provider()``: factory chosen by environment variables. Changing the
  default provider is a one-line env change.

Cancellation
------------
Each ``stream`` call takes a ``cancel_event`` (``asyncio.Event``). The caller
sets it when the client disconnects / cancels; providers check it between
tokens and stop cleanly.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class StreamResult:
    """Aggregated result of a streaming call — used by callers for logging."""

    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    finished: bool = False
    error: str | None = None


class LLMProvider(Protocol):
    name: str
    default_model: str

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.4,
        cancel_event: asyncio.Event | None = None,
        result: StreamResult | None = None,
    ) -> AsyncIterator[str]:
        ...  # pragma: no cover


# ── Mock provider ────────────────────────────────────────────────────────────


class MockProvider:
    """
    Deterministic provider used in tests and when no API key is configured.

    Produces a response built from the most-recent user message, streamed
    word-by-word with a small delay so the frontend can actually render
    token-by-token. The response format is obvious ("[mock] ...") so demo
    viewers can tell when no real LLM is wired up.
    """

    name = "mock"
    default_model = "mock-1"

    def __init__(self, delay: float = 0.02) -> None:
        self.delay = delay

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.4,
        cancel_event: asyncio.Event | None = None,
        result: StreamResult | None = None,
    ) -> AsyncIterator[str]:
        user_text = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        system_hint = next(
            (m.content for m in messages if m.role == "system"),
            "",
        )
        response = self._canned_response(system_hint, user_text)
        tokens = response.split(" ")

        out_text_parts: list[str] = []
        for i, tok in enumerate(tokens):
            if cancel_event and cancel_event.is_set():
                if result is not None:
                    result.finished = False
                    result.error = "cancelled"
                return
            piece = tok + (" " if i < len(tokens) - 1 else "")
            out_text_parts.append(piece)
            yield piece
            await asyncio.sleep(self.delay)

        if result is not None:
            result.text = "".join(out_text_parts)
            result.input_tokens = max(1, sum(len(m.content) for m in messages) // 4)
            result.output_tokens = max(1, len(response) // 4)
            result.model = model or self.default_model
            result.finished = True

    @staticmethod
    def _canned_response(system_hint: str, user_text: str) -> str:
        trimmed = user_text.strip().replace("\n", " ")
        if len(trimmed) > 240:
            trimmed = trimmed[:237] + "..."
        hint = system_hint.split(".")[0] if system_hint else "response"
        return (
            f"[mock {hint}] Here is a model-generated rewrite of the selected "
            f"passage so you can see token-by-token streaming: \"{trimmed}\". "
            f"Replace this provider with the real Anthropic adapter by setting "
            f"ANTHROPIC_API_KEY in .env."
        )


# ── Anthropic provider ───────────────────────────────────────────────────────


class AnthropicProvider:
    """
    Real Claude adapter using the Anthropic Python SDK.

    The SDK is only imported lazily so the backend doesn't hard-fail when the
    package isn't installed (e.g. grading environment without network access).
    """

    name = "anthropic"
    default_model = "claude-sonnet-4-6"

    def __init__(self, api_key: str, default_model: str | None = None) -> None:
        self.api_key = api_key
        if default_model:
            self.default_model = default_model
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "The `anthropic` package is not installed. "
                "Run `pip install anthropic` or use the mock provider."
            ) from e
        self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.4,
        cancel_event: asyncio.Event | None = None,
        result: StreamResult | None = None,
    ) -> AsyncIterator[str]:
        client = self._ensure_client()

        system_msgs = [m.content for m in messages if m.role == "system"]
        chat_msgs = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        chosen_model = model or self.default_model
        out_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0

        try:
            async with client.messages.stream(
                model=chosen_model,
                max_tokens=max_tokens,
                temperature=temperature,
                system="\n\n".join(system_msgs) if system_msgs else None,
                messages=chat_msgs,
            ) as stream:
                async for text in stream.text_stream:
                    if cancel_event and cancel_event.is_set():
                        if result is not None:
                            result.error = "cancelled"
                            result.finished = False
                        return
                    out_parts.append(text)
                    yield text
                final = await stream.get_final_message()
                input_tokens = getattr(final.usage, "input_tokens", 0) or 0
                output_tokens = getattr(final.usage, "output_tokens", 0) or 0
        except Exception as e:  # pragma: no cover — exercised at runtime
            if result is not None:
                result.error = str(e)
            raise

        if result is not None:
            result.text = "".join(out_parts)
            result.input_tokens = input_tokens
            result.output_tokens = output_tokens
            result.model = chosen_model
            result.finished = True


# ── Ollama provider ──────────────────────────────────────────────────────────


class OllamaProvider:
    """
    Local LLM via the Ollama REST API.

    Ollama speaks a simple HTTP protocol on ``http://localhost:11434`` by
    default. ``POST /api/chat`` with ``"stream": true`` returns newline-
    delimited JSON; each line is a partial completion with a ``message.content``
    delta. The final line has ``"done": true`` and token counts
    (``prompt_eval_count`` + ``eval_count``).

    No API key is required — just install Ollama (https://ollama.com) and
    ``ollama pull llama3.1`` (or any other model).

    Implemented with ``httpx`` (already a dev dep) so there's no extra runtime
    package to install.
    """

    name = "ollama"
    default_model = "llama3.1"

    def __init__(self, base_url: str = "http://localhost:11434", default_model: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        if default_model:
            self.default_model = default_model

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.4,
        cancel_event: asyncio.Event | None = None,
        result: StreamResult | None = None,
    ) -> AsyncIterator[str]:
        try:
            import httpx  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "The `httpx` package is not installed. "
                "Run `pip install httpx` or use a different provider."
            ) from e

        chosen_model = model or self.default_model
        payload = {
            "model": chosen_model,
            "stream": True,
            # Ollama accepts system/user/assistant role names directly.
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        out_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0

        url = f"{self.base_url}/api/chat"

        try:
            # No timeout on the outer request — streaming can take a while.
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise RuntimeError(
                            f"Ollama returned {resp.status_code}: "
                            f"{body.decode('utf-8', errors='replace')[:400]}"
                        )

                    async for line in resp.aiter_lines():
                        if cancel_event and cancel_event.is_set():
                            if result is not None:
                                result.error = "cancelled"
                                result.finished = False
                            return
                        if not line:
                            continue
                        try:
                            frame = json.loads(line)
                        except json.JSONDecodeError:
                            # Skip malformed frames defensively rather than bailing.
                            continue

                        # Token delta.
                        piece = (frame.get("message") or {}).get("content") or ""
                        if piece:
                            out_parts.append(piece)
                            yield piece

                        if frame.get("done"):
                            input_tokens = int(frame.get("prompt_eval_count") or 0)
                            output_tokens = int(frame.get("eval_count") or 0)
                            break
        except Exception as e:  # pragma: no cover — exercised at runtime
            if result is not None:
                result.error = str(e)
            raise

        if result is not None:
            result.text = "".join(out_parts)
            # Fallback token estimate if Ollama didn't populate counts.
            if input_tokens <= 0:
                input_tokens = max(1, sum(len(m.content) for m in messages) // 4)
            if output_tokens <= 0:
                output_tokens = max(1, len(result.text) // 4)
            result.input_tokens = input_tokens
            result.output_tokens = output_tokens
            result.model = chosen_model
            result.finished = True


# ── Gemini provider ──────────────────────────────────────────────────────────


class GeminiProvider:
    """
    Google Gemini via the ``google-genai`` Python SDK.

    The SDK is imported lazily so the backend doesn't hard-fail when the
    package isn't installed. Supports the free-tier model ``gemini-2.0-flash``
    out of the box; override via ``GOOGLE_MODEL`` env var.

    Gemini uses a slightly different message shape from OpenAI/Anthropic:
    ``contents=[{"role": "user"|"model", "parts": [{"text": "..."}]}]`` with
    the system prompt passed via ``GenerateContentConfig.system_instruction``.
    We translate the neutral ``ChatMessage`` list into that form here so the
    rest of the app can stay provider-agnostic.
    """

    name = "gemini"
    default_model = "gemini-2.0-flash"

    def __init__(self, api_key: str, default_model: str | None = None) -> None:
        self.api_key = api_key
        if default_model:
            self.default_model = default_model
        self._client = None
        self._types = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client, self._types
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "The `google-genai` package is not installed. "
                "Run `pip install google-genai` or use a different provider."
            ) from e
        self._client = genai.Client(api_key=self.api_key)
        self._types = types
        return self._client, self._types

    @staticmethod
    def _split(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
        """Gemini wants the system prompt separate and 'model' instead of
        'assistant' in the role field."""
        system_parts: list[str] = []
        contents: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})
        return "\n\n".join(system_parts), contents

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.4,
        cancel_event: asyncio.Event | None = None,
        result: StreamResult | None = None,
    ) -> AsyncIterator[str]:
        client, types = self._ensure_client()

        system_text, contents = self._split(messages)
        chosen_model = model or self.default_model

        config_kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system_text:
            config_kwargs["system_instruction"] = system_text
        config = types.GenerateContentConfig(**config_kwargs)

        out_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0

        try:
            # The aio streaming method in google-genai may return an async
            # iterator directly OR a coroutine that resolves to one, depending
            # on SDK version. Handle both.
            stream_obj = client.aio.models.generate_content_stream(
                model=chosen_model,
                contents=contents,
                config=config,
            )
            if asyncio.iscoroutine(stream_obj):
                stream_obj = await stream_obj

            async for chunk in stream_obj:
                if cancel_event and cancel_event.is_set():
                    if result is not None:
                        result.error = "cancelled"
                        result.finished = False
                    return

                piece = getattr(chunk, "text", None) or ""
                if piece:
                    out_parts.append(piece)
                    yield piece

                # Gemini populates usage on (at least) the final chunk.
                usage = getattr(chunk, "usage_metadata", None)
                if usage is not None:
                    input_tokens = int(getattr(usage, "prompt_token_count", 0) or input_tokens)
                    output_tokens = int(getattr(usage, "candidates_token_count", 0) or output_tokens)
        except Exception as e:  # pragma: no cover — exercised at runtime
            if result is not None:
                result.error = str(e)
            raise

        if result is not None:
            result.text = "".join(out_parts)
            if input_tokens <= 0:
                input_tokens = max(1, sum(len(m.content) for m in messages) // 4)
            if output_tokens <= 0:
                output_tokens = max(1, len(result.text) // 4)
            result.input_tokens = input_tokens
            result.output_tokens = output_tokens
            result.model = chosen_model
            result.finished = True


# ── factory ──────────────────────────────────────────────────────────────────


_PLACEHOLDER_PREFIXES = ("sk-ant-...", "sk-ant-placeholder", "placeholder", "your-", "xxx")


def _looks_like_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return (not v) or any(v.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def get_provider() -> LLMProvider:
    """
    Return the configured provider singleton.

    Selection rules (first match wins):
      * ``LLM_PROVIDER=mock``       — force the mock provider (tests/offline).
      * ``LLM_PROVIDER=ollama``     — use the Ollama REST API at ``OLLAMA_URL``
                                      (default ``http://localhost:11434``),
                                      model ``OLLAMA_MODEL`` (default ``llama3.1``).
      * ``LLM_PROVIDER=gemini``     — use Google Gemini via ``google-genai``,
                                      requires ``GOOGLE_API_KEY``, model
                                      ``GOOGLE_MODEL`` (default ``gemini-2.0-flash``).
      * ``LLM_PROVIDER=anthropic`` (or unset) — use the Anthropic adapter,
                                      requires ``ANTHROPIC_API_KEY``.

    If the configured provider's key/service is missing, we log and fall back
    to the mock so the app stays demo-able.
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    choice = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()

    if choice == "mock":
        _provider_instance = MockProvider()
        return _provider_instance

    if choice == "ollama":
        url = os.getenv("OLLAMA_URL", "http://localhost:11434").strip()
        model = os.getenv("OLLAMA_MODEL") or None
        _provider_instance = OllamaProvider(base_url=url, default_model=model)
        return _provider_instance

    if choice == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if api_key and not _looks_like_placeholder(api_key):
            model = os.getenv("GOOGLE_MODEL") or None
            _provider_instance = GeminiProvider(api_key=api_key, default_model=model)
            return _provider_instance
        print("[llm] LLM_PROVIDER=gemini but GOOGLE_API_KEY is missing — using MockProvider.")
        _provider_instance = MockProvider()
        return _provider_instance

    # Default: anthropic.
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if choice == "anthropic" and api_key and not _looks_like_placeholder(api_key):
        model = os.getenv("ANTHROPIC_MODEL") or None
        _provider_instance = AnthropicProvider(api_key=api_key, default_model=model)
        return _provider_instance

    # Fallback — unknown provider name or missing Anthropic key.
    if choice not in ("anthropic", ""):
        print(f"[llm] Unknown LLM_PROVIDER={choice!r} — using MockProvider.")
    else:
        print("[llm] No ANTHROPIC_API_KEY found — using MockProvider.")
    _provider_instance = MockProvider()
    return _provider_instance


def reset_provider() -> None:
    """Clear the cached provider (used by tests)."""
    global _provider_instance
    _provider_instance = None


_provider_instance: LLMProvider | None = None
