"""
In-memory collaboration manager: one "room" per document, tracking the
connected users and fanning messages out to the rest of the room.

Design notes
------------
- A room is identified by a document id (string).
- Each connection is a (websocket, user) pair. Multiple connections from the
  same user are allowed (two tabs, two devices) — presence de-dupes by user id.
- Concurrency model for the baseline is last-write-wins: when a client sends
  a full document snapshot, the server accepts it, bumps a monotonic revision
  counter, and broadcasts the snapshot + revision to other clients. This is
  the non-bonus approach documented in Assignment 2.
- No persistence here — the HTTP auto-save path (routers/documents.py) is
  still the source of truth. The WebSocket layer only propagates updates
  between tabs in real time.

The manager is a simple module-level singleton so that all WebSocket
handlers share the same state within a single uvicorn worker.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from backend.models.user import CurrentUser


@dataclass
class Connection:
    """A single WebSocket connection bound to an authenticated user."""

    websocket: WebSocket
    user: CurrentUser
    joined_at: float = field(default_factory=time.time)
    last_typing_at: float = 0.0


@dataclass
class Room:
    """
    A live collaboration room for one document.

    ``revision`` is a monotonic counter bumped on every accepted update.
    Clients use it to reconcile ordering and to detect stale state on
    reconnect (if the client's last-seen revision is older than the server's,
    the server replies with a ``state`` message containing the latest
    snapshot).
    """

    doc_id: str
    connections: list[Connection] = field(default_factory=list)
    revision: int = 0
    latest_snapshot: Any = None  # last full doc content broadcast through this room
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CollaborationManager:
    """
    Tracks all active rooms and routes messages between connections.

    Public surface is intentionally small:

    - ``connect(doc_id, websocket, user)`` — register a new connection
    - ``disconnect(doc_id, websocket)`` — clean up on close
    - ``broadcast(doc_id, message, exclude=ws)`` — fan-out to the room
    - ``presence(doc_id)`` — current unique users in the room
    - ``get_room(doc_id)`` — read-only access for tests
    """

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._global_lock = asyncio.Lock()

    # ── room lifecycle ────────────────────────────────────────────────────────

    async def _get_or_create_room(self, doc_id: str) -> Room:
        async with self._global_lock:
            room = self._rooms.get(doc_id)
            if room is None:
                room = Room(doc_id=doc_id)
                self._rooms[doc_id] = room
            return room

    async def connect(
        self,
        doc_id: str,
        websocket: WebSocket,
        user: CurrentUser,
    ) -> Room:
        room = await self._get_or_create_room(doc_id)
        async with room.lock:
            room.connections.append(Connection(websocket=websocket, user=user))
        return room

    async def disconnect(self, doc_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(doc_id)
        if room is None:
            return
        async with room.lock:
            room.connections = [c for c in room.connections if c.websocket is not websocket]
            # Leave empty rooms in the dict; they're cheap and help preserve
            # revision counters if a user reconnects within the same session.

    # ── presence ──────────────────────────────────────────────────────────────

    def presence(self, doc_id: str) -> list[dict]:
        room = self._rooms.get(doc_id)
        if room is None:
            return []
        # Deduplicate by user id — a user with two tabs is still one presence entry.
        seen: dict[str, dict] = {}
        for conn in room.connections:
            if conn.user.id not in seen:
                seen[conn.user.id] = {
                    "user_id": conn.user.id,
                    "username": conn.user.username,
                    "connections": 1,
                    "typing": (time.time() - conn.last_typing_at) < 3.0,
                }
            else:
                seen[conn.user.id]["connections"] += 1
                if (time.time() - conn.last_typing_at) < 3.0:
                    seen[conn.user.id]["typing"] = True
        return list(seen.values())

    def mark_typing(self, doc_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(doc_id)
        if room is None:
            return
        for conn in room.connections:
            if conn.websocket is websocket:
                conn.last_typing_at = time.time()
                return

    # ── broadcasting ──────────────────────────────────────────────────────────

    async def broadcast(
        self,
        doc_id: str,
        message: dict,
        exclude: WebSocket | None = None,
    ) -> None:
        room = self._rooms.get(doc_id)
        if room is None:
            return

        # Copy the connection list so we can safely remove dead sockets mid-iteration.
        targets = list(room.connections)
        dead: list[WebSocket] = []
        for conn in targets:
            if conn.websocket is exclude:
                continue
            try:
                await conn.websocket.send_json(message)
            except Exception:
                dead.append(conn.websocket)

        for ws in dead:
            await self.disconnect(doc_id, ws)

    # ── document state ────────────────────────────────────────────────────────

    async def accept_update(
        self,
        doc_id: str,
        snapshot: Any,
    ) -> int:
        """
        Record a full-document snapshot and return the new revision number.

        Baseline policy: last-write-wins. We accept every update and bump the
        revision. Clients that were behind will see a higher revision than
        their last-seen value and can reconcile by overwriting their local
        state (the snapshot is always the full document content).
        """
        room = await self._get_or_create_room(doc_id)
        async with room.lock:
            room.revision += 1
            room.latest_snapshot = snapshot
            return room.revision

    def latest_state(self, doc_id: str) -> tuple[int, Any]:
        room = self._rooms.get(doc_id)
        if room is None:
            return 0, None
        return room.revision, room.latest_snapshot

    def get_room(self, doc_id: str) -> Room | None:
        return self._rooms.get(doc_id)


# module-level singleton
manager = CollaborationManager()
