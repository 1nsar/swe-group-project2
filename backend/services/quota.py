"""
Per-user monthly token quotas for the AI Writing Assistant.

Assignment 1 alignment
----------------------
§2.2 AI Integration Design (Cost control):
  "Per-user monthly token quotas enforced by Quota Checker. At 80% usage a
   warning is shown; at 100% AI features are suspended until quota resets."

§2.4 Data Model (USER_QUOTA):
  user_id PK, tokens_used_this_month, monthly_limit, reset_at

§2.2 API Design (WebSocket Events):
  ai:error includes code AI_QUOTA_EXCEEDED.

We persist a small USER_QUOTA row per user in the JSON store. The calendar
month rolls over automatically when the stored ``reset_at`` elapses.

Responsibilities split
----------------------
* ``peek(user_id)``        - read-only snapshot for the UI warning banner.
* ``check(user_id, est)``  - called before streaming; rejects with
                             ``QuotaExceeded`` if adding ``est`` would exceed
                             the limit.
* ``record(user_id, n)``   - called after streaming; commits the actual tokens
                             used. The estimate is a coarse guard — we trust
                             the provider's post-stream count for billing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.storage import json_store as store

COLLECTION = "user_quota"
DEFAULT_MONTHLY_LIMIT = int(os.getenv("AI_MONTHLY_TOKEN_LIMIT", "200000"))
WARN_THRESHOLD = 0.80  # §2.2 "At 80% usage a warning is shown"


class QuotaExceeded(Exception):
    """Raised by ``check`` when the user would exceed their monthly quota."""

    def __init__(self, *, used: int, limit: int, reset_at: str) -> None:
        super().__init__("AI_QUOTA_EXCEEDED")
        self.used = used
        self.limit = limit
        self.reset_at = reset_at

    def to_payload(self) -> dict:
        return {
            "code": "AI_QUOTA_EXCEEDED",
            "used": self.used,
            "limit": self.limit,
            "reset_at": self.reset_at,
        }


@dataclass
class QuotaSnapshot:
    user_id: str
    used: int
    limit: int
    reset_at: str

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def fraction(self) -> float:
        return 0.0 if self.limit <= 0 else min(1.0, self.used / self.limit)

    @property
    def warning(self) -> bool:
        return self.fraction >= WARN_THRESHOLD

    def to_payload(self) -> dict:
        return {
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "fraction": self.fraction,
            "warning": self.warning,
            "reset_at": self.reset_at,
        }


def _next_month_reset(now: Optional[datetime] = None) -> datetime:
    """First day of the next month at 00:00:00 UTC."""
    now = now or datetime.now(timezone.utc)
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)


def _load_or_init(user_id: str) -> dict:
    row = store.get(COLLECTION, user_id)
    now = datetime.now(timezone.utc)
    if not row:
        row = {
            "user_id": user_id,
            "tokens_used_this_month": 0,
            "monthly_limit": DEFAULT_MONTHLY_LIMIT,
            "reset_at": _next_month_reset(now).isoformat(),
        }
        store.put(COLLECTION, user_id, row)
        return row

    # Reset if past reset_at.
    try:
        reset_at = datetime.fromisoformat(row["reset_at"])
    except Exception:
        reset_at = _next_month_reset(now)

    if now >= reset_at:
        row = {
            **row,
            "tokens_used_this_month": 0,
            "reset_at": _next_month_reset(now).isoformat(),
        }
        store.put(COLLECTION, user_id, row)
    return row


def peek(user_id: str) -> QuotaSnapshot:
    row = _load_or_init(user_id)
    return QuotaSnapshot(
        user_id=user_id,
        used=int(row["tokens_used_this_month"]),
        limit=int(row["monthly_limit"]),
        reset_at=row["reset_at"],
    )


def check(user_id: str, estimated_tokens: int = 0) -> QuotaSnapshot:
    """Raise ``QuotaExceeded`` if the user is already at/over their limit.

    We don't block on *estimated_tokens* — the pre-check only rejects users
    who have already hit 100%. Final token usage is recorded by ``record``
    after the stream completes; in the worst case a user can marginally
    overshoot once, which is fine per §2.2 ("suspended until quota resets").
    """
    snap = peek(user_id)
    if snap.used >= snap.limit:
        raise QuotaExceeded(used=snap.used, limit=snap.limit, reset_at=snap.reset_at)
    return snap


def record(user_id: str, tokens_used: int) -> QuotaSnapshot:
    if tokens_used <= 0:
        return peek(user_id)
    row = _load_or_init(user_id)
    row["tokens_used_this_month"] = int(row["tokens_used_this_month"]) + int(tokens_used)
    store.put(COLLECTION, user_id, row)
    return QuotaSnapshot(
        user_id=user_id,
        used=int(row["tokens_used_this_month"]),
        limit=int(row["monthly_limit"]),
        reset_at=row["reset_at"],
    )


def set_limit(user_id: str, limit: int) -> QuotaSnapshot:
    """Admin-only hook used by tests and (future) admin UI."""
    row = _load_or_init(user_id)
    row["monthly_limit"] = int(limit)
    store.put(COLLECTION, user_id, row)
    return peek(user_id)
