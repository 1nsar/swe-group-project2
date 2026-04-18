"""
WebSocket tests (Assignment 2 §2.3, §4.1).

We verify:
  - A connection with a valid token completes the handshake and receives an
    initial state frame with presence.
  - A connection without a token is rejected when ALLOW_DEV_WS_FALLBACK=0.
  - Two concurrent clients in the same room exchange update messages.
  - Typing messages propagate to peers.
"""
from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.tests.conftest import make_jwt


def test_unauthenticated_ws_is_rejected(client):
    """
    conftest sets ALLOW_DEV_WS_FALLBACK=0 so auth is required.

    The router accepts the socket and then closes it with code 1008 when the
    JWT is missing. Starlette's TestClient surfaces that server-initiated
    close on the next receive_* call, not on ``with`` entry — so we try to
    receive a frame inside the ``with`` block and expect WebSocketDisconnect.
    """
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/documents/doc-x") as ws:
            ws.receive_json()  # raises as soon as the server's close lands
    # 1008 = policy violation (WS_1008_POLICY_VIOLATION); 1000 = normal close.
    assert exc_info.value.code in (1008, 1000)


def test_valid_token_receives_initial_state(client):
    token = make_jwt(sub="alice-id", username="alice")
    with client.websocket_connect(f"/ws/documents/doc-1?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "state"
        assert msg["revision"] == 0
        assert msg["you"]["username"] == "alice"
        assert any(p["username"] == "alice" for p in msg["presence"])


def test_two_clients_exchange_updates(client):
    t1 = make_jwt(sub="u1", username="alice")
    t2 = make_jwt(sub="u2", username="bob")

    with client.websocket_connect(f"/ws/documents/doc-2?token={t1}") as a, \
         client.websocket_connect(f"/ws/documents/doc-2?token={t2}") as b:

        # Drain initial state frames on both.
        a.receive_json()
        # alice also sees bob join (presence update).
        _ = a.receive_json()
        b.receive_json()

        # alice sends an update.
        a.send_json({"type": "update", "content": {"type": "doc", "marker": "X"}})

        # alice gets an ack.
        ack = a.receive_json()
        assert ack["type"] == "ack"
        assert ack["revision"] == 1

        # bob gets the broadcast.
        upd = b.receive_json()
        assert upd["type"] == "update"
        assert upd["revision"] == 1
        assert upd["content"]["marker"] == "X"
        assert upd["from"]["username"] == "alice"


def test_typing_propagates(client):
    t1 = make_jwt(sub="u1", username="alice")
    t2 = make_jwt(sub="u2", username="bob")

    with client.websocket_connect(f"/ws/documents/doc-3?token={t1}") as a, \
         client.websocket_connect(f"/ws/documents/doc-3?token={t2}") as b:

        a.receive_json()   # initial state for alice
        a.receive_json()   # presence update when bob joins
        b.receive_json()   # initial state for bob

        a.send_json({"type": "typing"})
        msg = b.receive_json()
        assert msg["type"] == "typing"
        assert msg["from"]["username"] == "alice"


def test_hello_reconciles_stale_client(client):
    t1 = make_jwt(sub="u1", username="alice")
    t2 = make_jwt(sub="u2", username="bob")

    with client.websocket_connect(f"/ws/documents/doc-4?token={t1}") as a:
        a.receive_json()  # initial state rev=0
        a.send_json({"type": "update", "content": {"v": 1}})
        assert a.receive_json()["type"] == "ack"

        with client.websocket_connect(f"/ws/documents/doc-4?token={t2}") as b:
            state = b.receive_json()
            assert state["type"] == "state"
            # Room already had a snapshot when bob joined — content is replayed.
            assert state["revision"] == 1
            assert state["content"] == {"v": 1}

            # bob sends hello with a stale last_seen_revision to force a refresh.
            b.send_json({"type": "hello", "last_seen_revision": 0})
            refreshed = b.receive_json()
            assert refreshed["type"] == "state"
            assert refreshed["revision"] == 1
