"""Unit tests for the prompt template module (Assignment 2 §3.4)."""
from __future__ import annotations

import json

from backend.services.prompts import (
    MAX_CONTEXT_AFTER_CHARS,
    MAX_CONTEXT_BEFORE_CHARS,
    MAX_SELECTION_CHARS,
    PromptContext,
    available_actions,
    build_messages,
)


def test_available_actions_includes_baseline_features():
    actions = set(available_actions())
    # Path B: Assignment 1 §1.2 names rewrite/summarize/translate; we also
    # ship grammar + custom as additive improvements (see DEVIATIONS #7).
    assert {"rewrite", "summarize", "translate", "grammar", "custom"}.issubset(actions)


def test_build_messages_shape():
    ctx = PromptContext(selection="Hello world.", tone="friendly")
    msgs = build_messages("rewrite", ctx)
    assert len(msgs) == 2
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
    assert "Hello world." in msgs[1].content
    assert "friendly" in msgs[1].content


def test_unknown_action_raises_keyerror():
    import pytest

    with pytest.raises(KeyError):
        build_messages("does-not-exist", PromptContext(selection="x"))


def test_selection_longer_than_budget_is_truncated():
    long = "a" * (MAX_SELECTION_CHARS + 500)
    msgs = build_messages("summarize", PromptContext(selection=long, length="short"))
    user = msgs[1].content
    # We should see the truncation marker and a truncation-aware note.
    assert "[truncated for length]" in user
    assert "truncated" in user.lower()


def test_override_from_file(tmp_path, monkeypatch):
    """PROMPTS_OVERRIDE_PATH lets ops swap wording without a code change."""
    override = tmp_path / "prompts.json"
    override.write_text(
        json.dumps(
            {
                "rewrite": {
                    "system": "SENTINEL-SYSTEM",
                    "user": "SENTINEL-USER {selection}",
                }
            }
        )
    )
    monkeypatch.setenv("PROMPTS_OVERRIDE_PATH", str(override))

    msgs = build_messages("rewrite", PromptContext(selection="abc"))
    assert msgs[0].content == "SENTINEL-SYSTEM"
    assert msgs[1].content.startswith("SENTINEL-USER abc")


def test_custom_action_carries_instruction():
    ctx = PromptContext(selection="hi", instruction="Translate to French")
    msgs = build_messages("custom", ctx)
    assert "Translate to French" in msgs[1].content


# ── Path B additions ──────────────────────────────────────────────────────


def test_translate_action_renders_target_language():
    """Assignment 1 §1.2 FR-AI-03 Translate requires a target language."""
    ctx = PromptContext(selection="Hola mundo.", target_language="French")
    msgs = build_messages("translate", ctx)
    user = msgs[1].content
    assert "French" in user
    assert "Hola mundo." in user
    # System prompt must tell the model it's translating, not rewriting.
    assert "translat" in msgs[0].content.lower()


def test_context_before_and_after_are_included_independently():
    """§2.2 AI Integration Design — three context buckets, each budgeted.

    The template renders both context buckets as a labelled preamble before
    the selection fences ("Context before selection:" / "Context after
    selection:"), so the model knows their logical position relative to the
    selection without us splitting the selection fences in two.
    """
    ctx = PromptContext(
        selection="MID",
        context_before="BEFORE-TEXT",
        context_after="AFTER-TEXT",
    )
    user = build_messages("rewrite", ctx)[1].content
    assert "BEFORE-TEXT" in user
    assert "AFTER-TEXT" in user
    assert "MID" in user
    # Labels tell the model what each bucket represents.
    assert "Context before selection" in user
    assert "Context after selection" in user
    # The labelled buckets precede the selection; before comes before after.
    assert user.index("BEFORE-TEXT") < user.index("AFTER-TEXT") < user.index("MID")


def test_context_before_truncation_is_independent_of_selection():
    """Oversized context_before must not eat into the selection budget."""
    big_before = "b" * (MAX_CONTEXT_BEFORE_CHARS + 500)
    ctx = PromptContext(
        selection="short-selection",
        context_before=big_before,
    )
    user = build_messages("rewrite", ctx)[1].content
    # The selection is preserved verbatim.
    assert "short-selection" in user
    # The context_before bucket was truncated.
    assert "[truncated for length]" in user
    # Truncation note names the specific bucket.
    assert "context_before" in user


def test_context_after_truncation_is_independent_of_selection():
    big_after = "a" * (MAX_CONTEXT_AFTER_CHARS + 500)
    ctx = PromptContext(
        selection="short-selection",
        context_after=big_after,
    )
    user = build_messages("rewrite", ctx)[1].content
    assert "short-selection" in user
    assert "[truncated for length]" in user
    assert "context_after" in user


def test_no_context_block_when_both_buckets_empty():
    """If context_before/after are empty, the user prompt should not mention them."""
    ctx = PromptContext(selection="standalone", context_before="", context_after="")
    user = build_messages("rewrite", ctx)[1].content
    assert "Context before" not in user
    assert "Context after" not in user
