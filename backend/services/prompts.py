"""
Prompt templates for AI actions.

Assignment 1 alignment
----------------------
* §2.2 Capability Area B lists four features: rewrite, summarise, translate,
  restructure. We implement rewrite, summarize, and translate directly. The
  restructure action isn't a baseline feature for this milestone — it's tracked
  as a follow-up in DEVIATIONS.md.
* §2.2 AI Integration Design: "Selected text + up to 500 tokens before + 200
  tokens after. Full document is never sent by default." We take this literally
  and expose separate ``context_before`` / ``selected_text`` / ``context_after``
  fields with separate char budgets that approximate the token targets.
* §2.2 AI Integration Design: prompts live in "/prompts" and can be loaded
  without redeployment. We keep a default dict here and allow a JSON override
  at runtime via ``PROMPTS_OVERRIDE_PATH``.

Each template has:
- ``system``: the system instruction given to the model
- ``user``: a format string that receives context variables

Context sizing
--------------
We don't run a tokenizer at this layer (and providers differ). A safe rule of
thumb is ~4 characters per token for English prose, so:

    500 tokens before  ≈ 2000 chars
    200 tokens after   ≈  800 chars
    selection budget   = 6000 chars   (unchanged from the previous baseline)

``build_messages`` truncates each bucket independently and notes the
truncation in the user prompt so the model handles partial context gracefully.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.services.llm_provider import ChatMessage

# Per-bucket char budgets. These approximate the §2.2 token targets and can
# be overridden via env for load/perf tuning.
MAX_SELECTION_CHARS = int(os.getenv("AI_MAX_SELECTION_CHARS", "6000"))
MAX_CONTEXT_BEFORE_CHARS = int(os.getenv("AI_MAX_CONTEXT_BEFORE_CHARS", "2000"))
MAX_CONTEXT_AFTER_CHARS = int(os.getenv("AI_MAX_CONTEXT_AFTER_CHARS", "800"))


# Default templates. Override by pointing PROMPTS_OVERRIDE_PATH at a JSON file.
_DEFAULTS: dict[str, dict[str, str]] = {
    "rewrite": {
        "system": (
            "You are a careful writing assistant inside a collaborative "
            "document editor. Rewrite the user's selected passage to improve "
            "its clarity and flow while preserving the author's meaning and "
            "any inline formatting conventions (Markdown-style emphasis, "
            "lists). Return only the rewritten passage — no preamble, no "
            "closing commentary, no code fences."
        ),
        "user": (
            "Tone: {tone}.\n"
            "{context_block}"
            "Rewrite the following selection accordingly.\n"
            "---\n"
            "{selection}\n"
            "---\n"
            "{truncation_note}"
        ),
    },
    "summarize": {
        "system": (
            "You are a careful writing assistant. Summarize the user's "
            "selected passage at the requested length. Preserve key facts, "
            "names, and numbers. Return only the summary — no preamble."
        ),
        "user": (
            "Length: {length}.\n"
            "{context_block}"
            "Summarize the following selection accordingly.\n"
            "---\n"
            "{selection}\n"
            "---\n"
            "{truncation_note}"
        ),
    },
    "translate": {
        "system": (
            "You are a translator working inside a collaborative document "
            "editor. Translate the user's selected passage into the requested "
            "target language. Preserve structure (paragraphs, lists, emphasis) "
            "and any proper nouns. Return only the translated passage — no "
            "preamble, no source-language quote, no code fences."
        ),
        "user": (
            "Target language: {target_language}.\n"
            "{context_block}"
            "Translate the following selection into {target_language}.\n"
            "---\n"
            "{selection}\n"
            "---\n"
            "{truncation_note}"
        ),
    },
    "grammar": {
        "system": (
            "You are a careful proofreader. Fix grammar, spelling, and "
            "punctuation in the user's selected passage. Keep the author's "
            "voice and meaning intact. Return only the corrected passage."
        ),
        "user": (
            "{context_block}"
            "Fix grammar and spelling in the following selection.\n"
            "---\n"
            "{selection}\n"
            "---\n"
            "{truncation_note}"
        ),
    },
    "custom": {
        "system": (
            "You are a writing assistant. Apply the user's custom instruction "
            "to the selected passage. Return only the resulting passage — no "
            "meta commentary, no code fences."
        ),
        "user": (
            "Instruction: {instruction}\n"
            "{context_block}"
            "Apply the instruction to the selection below.\n"
            "---\n"
            "{selection}\n"
            "---\n"
            "{truncation_note}"
        ),
    },
}


@dataclass
class PromptContext:
    """Variables passed to the user-template format string.

    ``selection`` is the user-highlighted text. ``context_before`` and
    ``context_after`` are the immediate surrounding document text, capped per
    §2.2. These are sent to the model as **context only** — the model is
    instructed to operate on the selection.
    """

    selection: str = ""
    context_before: str = ""
    context_after: str = ""
    tone: str = "neutral"
    length: str = "medium"
    instruction: str = ""
    target_language: str = "English"


def _load_templates() -> dict[str, dict[str, str]]:
    override = os.getenv("PROMPTS_OVERRIDE_PATH")
    if not override:
        return _DEFAULTS
    p = Path(override)
    if not p.exists():
        return _DEFAULTS
    try:
        data = json.loads(p.read_text())
        # Merge over defaults so missing keys still work.
        merged = {k: dict(v) for k, v in _DEFAULTS.items()}
        for name, tpl in data.items():
            merged.setdefault(name, {}).update(tpl)
        return merged
    except Exception as e:
        print(f"[prompts] failed to load override {override}: {e}")
        return _DEFAULTS


def available_actions() -> list[str]:
    return sorted(_load_templates().keys())


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Head+tail truncate so summaries stay faithful to both ends."""
    if len(text) <= limit:
        return text, False
    half = max(1, limit // 2)
    head = text[:half]
    tail = text[-half:]
    return f"{head}\n...[truncated for length]...\n{tail}", True


def _build_context_block(ctx: PromptContext) -> tuple[str, list[str]]:
    """
    Render the context-before/context-after surrounding the selection as an
    optional block in the user prompt. Returns the block text (may be empty)
    and a list of buckets that were truncated.
    """
    parts: list[str] = []
    truncated: list[str] = []

    if ctx.context_before:
        before, was = _truncate(ctx.context_before, MAX_CONTEXT_BEFORE_CHARS)
        if was:
            truncated.append("context_before")
        parts.append(f"Context before selection:\n{before}\n")

    if ctx.context_after:
        after, was = _truncate(ctx.context_after, MAX_CONTEXT_AFTER_CHARS)
        if was:
            truncated.append("context_after")
        parts.append(f"Context after selection:\n{after}\n")

    if not parts:
        return "", truncated

    # Blank line between context block and the selection for readability.
    return "\n".join(parts) + "\n", truncated


def build_messages(action: str, ctx: PromptContext) -> list[ChatMessage]:
    """
    Build the ``[ChatMessage]`` list passed to the LLM provider.

    Raises KeyError if the action isn't a known template.
    """
    templates = _load_templates()
    if action not in templates:
        raise KeyError(f"Unknown AI action: {action!r}")

    tpl = templates[action]

    trimmed_sel, sel_truncated = _truncate(ctx.selection or "", MAX_SELECTION_CHARS)
    context_block, ctx_truncated = _build_context_block(ctx)

    truncated_buckets = ctx_truncated + (["selection"] if sel_truncated else [])
    if truncated_buckets:
        note = (
            "(The following buckets were truncated because they exceeded "
            f"their budget: {', '.join(truncated_buckets)}. Keep the "
            "response consistent with the visible portions.)"
        )
    else:
        note = ""

    user_prompt = tpl["user"].format(
        selection=trimmed_sel,
        context_block=context_block,
        tone=ctx.tone or "neutral",
        length=ctx.length or "medium",
        instruction=ctx.instruction or "(no extra instruction)",
        target_language=ctx.target_language or "English",
        truncation_note=note,
    )

    return [
        ChatMessage(role="system", content=tpl["system"]),
        ChatMessage(role="user", content=user_prompt),
    ]
