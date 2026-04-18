"""
WebSocket router for real-time collaboration.

URL:    ws://HOST/ws/documents/{doc_id}?token=<JWT>
Auth:   JWT passed as a query param (see backend/dependencies/ws_auth.py).
        Browsers can't set custom headers on the WS handshake, so we rely on
        the query param. The handshake is rejected (close 1008) if the token
        is missing or invalid — except when ALLOW_DEV_WS_FALLBACK=1 (dev).

Protocol
--------
All messages are JSON objects with a required ``type`` field.

Client → Server
  {"type": "hello", "last_seen_revision": 0}
  {"type": "update", "content": <tiptap JSON>, "base_revision": n}
  {"type": "typing"}
  {"type": "cursor", "anchor": int, "head": int}   # optional, bonus-tier

Server → Client
  {"type": "state",    "revision": n, "content": <tiptap JSON>|null, "presence": [...]}
  {"type": "update",   "revision": n, "content": ..., "from": {user_id, username}}
  {"type": "presence", "presence": [...]}
  {"type": "typing",   "from": {user_id, username}}
  {"type": "cursor",   "from": {user_id, username}, "anchor": int, "head": int}
  {"type": "error",    "message": str}

Concurrency model (baseline)
----------------------------
Last-write-wins at the snapshot level. Each accepted update bumps a monotonic
``revision`` counter per document. Clients echo the latest revision they know
about; stale clients get a fresh ``state`` message so they can reconcile.

On reconnect, the client sends ``hello`` with its last-seen revision; if the
server is ahead, it replies with a ``state`` carrying the current snapshot.
If the server has no in-memory state (first connection after restart), the
client's own local content is the source of truth.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.dependencies.ws_auth import authenticate_ws
from backend.services.collaboration_manager import manager

router = APIRouter(tags=["collaboration"])


@router.websocket("/ws/documents/{doc_id}")
async def collaborate(websocket: WebSocket, doc_id: str) -> None:
    await websocket.accept()

    user = await authenticate_ws(websocket)
    if user is None:
        # authenticate_ws already closed the socket
        return

    room = await manager.connect(doc_id, websocket, user)

    # Send initial state to the new client so it can reconcile.
    revision, snapshot = manager.latest_state(doc_id)
    await websocket.send_json(
        {
            "type": "state",
            "revision": revision,
            "content": snapshot,
            "presence": manager.presence(doc_id),
            "you": {"user_id": user.id, "username": user.username},
        }
    )

    # Broadcast presence update to everyone else.
    await manager.broadcast(
        doc_id,
        {"type": "presence", "presence": manager.presence(doc_id)},
        exclude=websocket,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid json"})
                continue

            mtype = msg.get("type")

            if mtype == "hello":
                # Client asking for fresh state after reconnect.
                last_seen = int(msg.get("last_seen_revision") or 0)
                current_rev, current_snap = manager.latest_state(doc_id)
                if current_rev > last_seen:
                    await websocket.send_json(
                        {
                            "type": "state",
                            "revision": current_rev,
                            "content": current_snap,
                            "presence": manager.presence(doc_id),
                        }
                    )

            elif mtype == "update":
                content: Any = msg.get("content")
                new_rev = await manager.accept_update(doc_id, content)
                await manager.broadcast(
                    doc_id,
                    {
                        "type": "update",
                        "revision": new_rev,
                        "content": content,
                        "from": {"user_id": user.id, "username": user.username},
                    },
                    exclude=websocket,
                )
                # Ack to sender with authoritative revision.
                await websocket.send_json({"type": "ack", "revision": new_rev})

            elif mtype == "typing":
                manager.mark_typing(doc_id, websocket)
                await manager.broadcast(
                    doc_id,
                    {
                        "type": "typing",
                        "from": {"user_id": user.id, "username": user.username},
                    },
                    exclude=websocket,
                )

            elif mtype == "cursor":
                # Bonus-tier remote cursors. We just forward the position.
                await manager.broadcast(
                    doc_id,
                    {
                        "type": "cursor",
                        "from": {"user_id": user.id, "username": user.username},
                        "anchor": int(msg.get("anchor") or 0),
                        "head": int(msg.get("head") or 0),
                    },
                    exclude=websocket,
                )

            elif mtype == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json(
                    {"type": "error", "message": f"unknown message type: {mtype!r}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(doc_id, websocket)
        # Notify remaining peers that presence changed.
        try:
            await manager.broadcast(
                doc_id,
                {"type": "presence", "presence": manager.presence(doc_id)},
            )
        except Exception:
            # Room may already be empty; no harm done.
            pass
