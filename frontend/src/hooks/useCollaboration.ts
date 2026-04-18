/**
 * useCollaboration — WebSocket hook for real-time collaboration.
 *
 * Assignment 1 alignment
 * ----------------------
 * * §1.2 FR-RT-01 Real-Time Text Updates: full document snapshot broadcast
 *   on edit (LWW baseline, see DEVIATIONS — Yjs CRDT is a follow-up).
 * * §1.2 FR-RT-02 Presence Awareness: ``presence`` list exposes active users.
 * * §1.2 FR-RT-03 Cursor/Editing Position visibility: ``remoteCursors``
 *   exposes per-user cursor positions so the Editor page can render an
 *   overlay. The hook itself is rendering-agnostic.
 * * §1.2 FR-RT-05 Reconnection: exponential-backoff reconnect + offline
 *   queue that drains on reopen. Client sends ``hello`` with its last-seen
 *   revision so the server can send a fresh ``state`` if it is ahead.
 * * §1.2 FR-RT-06 Push-based communication: WebSocket, not polling.
 *
 * Concurrency policy: baseline last-write-wins. Every accepted update bumps
 * a server-side revision; if we see a higher revision than our last-seen,
 * we accept the remote snapshot.
 *
 * The hook is deliberately UI-agnostic — the Editor page decides what to do
 * with ``remoteContent``, ``presence``, and ``remoteCursors``.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { buildCollabUrl } from "../api/ws";

export interface PresenceUser {
  user_id: string;
  username: string;
  connections: number;
  typing?: boolean;
}

export interface RemoteUpdate {
  revision: number;
  content: unknown;
  from?: { user_id: string; username: string };
}

export interface RemoteCursor {
  user_id: string;
  username: string;
  anchor: number;
  head: number;
  /** Wall-clock timestamp the last cursor update was received (ms). */
  updated_at: number;
}

type Status = "connecting" | "open" | "offline" | "closed";

interface Options {
  docId: string | undefined;
  enabled: boolean;
  onRemoteUpdate?: (update: RemoteUpdate) => void;
}

interface Api {
  status: Status;
  presence: PresenceUser[];
  remoteCursors: RemoteCursor[];
  sendUpdate: (content: unknown) => void;
  sendTyping: () => void;
  sendCursor: (anchor: number, head: number) => void;
  pendingOfflineUpdates: number;
  you: { user_id: string; username: string } | null;
}

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8000;
// Drop remote cursors we haven't heard from in this many ms — avoids stale
// ghost carets if a user disconnects without the server noticing.
const CURSOR_STALE_MS = 30_000;
// Throttle outgoing cursor messages so we don't spam the socket.
const CURSOR_SEND_THROTTLE_MS = 150;

export function useCollaboration({ docId, enabled, onRemoteUpdate }: Options): Api {
  const [status, setStatus] = useState<Status>("closed");
  const [presence, setPresence] = useState<PresenceUser[]>([]);
  const [remoteCursors, setRemoteCursors] = useState<RemoteCursor[]>([]);
  const [you, setYou] = useState<{ user_id: string; username: string } | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const lastSeenRevRef = useRef<number>(0);
  const reconnectAttemptRef = useRef<number>(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const offlineQueueRef = useRef<unknown[]>([]);
  const typingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cursorThrottleRef = useRef<number>(0);
  const cursorPendingRef = useRef<{ anchor: number; head: number } | null>(null);
  const onRemoteUpdateRef = useRef(onRemoteUpdate);
  useEffect(() => {
    onRemoteUpdateRef.current = onRemoteUpdate;
  }, [onRemoteUpdate]);

  // Periodically sweep stale cursors.
  useEffect(() => {
    const id = setInterval(() => {
      const cutoff = Date.now() - CURSOR_STALE_MS;
      setRemoteCursors((prev) => prev.filter((c) => c.updated_at >= cutoff));
    }, 5_000);
    return () => clearInterval(id);
  }, []);

  // Drop cursors for users who leave presence.
  useEffect(() => {
    const alive = new Set(presence.map((p) => p.user_id));
    setRemoteCursors((prev) => prev.filter((c) => alive.has(c.user_id)));
  }, [presence]);

  // ── connection loop ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!enabled || !docId) return;

    let cancelled = false;

    function connect() {
      if (cancelled) return;
      setStatus("connecting");
      const ws = new WebSocket(buildCollabUrl(docId!));
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) {
          ws.close();
          return;
        }
        reconnectAttemptRef.current = 0;
        setStatus("open");
        ws.send(
          JSON.stringify({ type: "hello", last_seen_revision: lastSeenRevRef.current }),
        );
        // Drain any updates buffered while offline.
        for (const content of offlineQueueRef.current) {
          ws.send(JSON.stringify({ type: "update", content }));
        }
        offlineQueueRef.current = [];
      };

      ws.onmessage = (event) => {
        let msg: { type: string; [k: string]: unknown };
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        switch (msg.type) {
          case "state": {
            const rev = (msg.revision as number) ?? 0;
            lastSeenRevRef.current = Math.max(lastSeenRevRef.current, rev);
            if (msg.presence) setPresence(msg.presence as PresenceUser[]);
            if (msg.you) setYou(msg.you as { user_id: string; username: string });
            if (msg.content !== null && msg.content !== undefined) {
              onRemoteUpdateRef.current?.({
                revision: rev,
                content: msg.content,
              });
            }
            break;
          }
          case "update": {
            const rev = (msg.revision as number) ?? 0;
            if (rev > lastSeenRevRef.current) {
              lastSeenRevRef.current = rev;
              onRemoteUpdateRef.current?.({
                revision: rev,
                content: msg.content,
                from: msg.from as RemoteUpdate["from"],
              });
            }
            break;
          }
          case "ack": {
            const rev = (msg.revision as number) ?? 0;
            lastSeenRevRef.current = Math.max(lastSeenRevRef.current, rev);
            break;
          }
          case "presence": {
            setPresence((msg.presence as PresenceUser[]) ?? []);
            break;
          }
          case "typing": {
            // Merge a transient typing flag into presence.
            const from = msg.from as { user_id: string } | undefined;
            if (!from) break;
            setPresence((prev) =>
              prev.map((p) => (p.user_id === from.user_id ? { ...p, typing: true } : p)),
            );
            setTimeout(() => {
              setPresence((prev) =>
                prev.map((p) =>
                  p.user_id === from.user_id ? { ...p, typing: false } : p,
                ),
              );
            }, 2500);
            break;
          }
          case "cursor": {
            const from = msg.from as { user_id: string; username: string } | undefined;
            if (!from) break;
            const anchor = Number(msg.anchor ?? 0);
            const head = Number(msg.head ?? 0);
            setRemoteCursors((prev) => {
              const rest = prev.filter((c) => c.user_id !== from.user_id);
              rest.push({
                user_id: from.user_id,
                username: from.username,
                anchor,
                head,
                updated_at: Date.now(),
              });
              return rest;
            });
            break;
          }
          case "error":
            console.warn("[collab] server error:", msg.message);
            break;
          default:
            break;
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        setStatus("offline");
        setRemoteCursors([]); // remote carets are meaningless while disconnected
        const attempt = reconnectAttemptRef.current + 1;
        reconnectAttemptRef.current = attempt;
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS);
        reconnectTimerRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // onclose will follow and trigger the reconnect.
      };
    }

    connect();

    return () => {
      cancelled = true;
      setStatus("closed");
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
        wsRef.current.close();
      }
      wsRef.current = null;
    };
  }, [docId, enabled]);

  // ── outgoing ──────────────────────────────────────────────────────────────
  const sendUpdate = useCallback((content: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "update", content }));
    } else {
      // Offline queue — the latest snapshot replaces any older one since we
      // always ship the full doc. Keeping only the latest avoids a burst on
      // reconnect.
      offlineQueueRef.current = [content];
    }
  }, []);

  const sendTyping = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (typingTimerRef.current) return;
    typingTimerRef.current = setTimeout(() => {
      typingTimerRef.current = null;
    }, 1000);
    ws.send(JSON.stringify({ type: "typing" }));
  }, []);

  const sendCursor = useCallback((anchor: number, head: number) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const now = Date.now();
    cursorPendingRef.current = { anchor, head };
    const wait = Math.max(0, cursorThrottleRef.current + CURSOR_SEND_THROTTLE_MS - now);
    if (wait === 0) {
      cursorThrottleRef.current = now;
      const p = cursorPendingRef.current;
      cursorPendingRef.current = null;
      ws.send(JSON.stringify({ type: "cursor", anchor: p.anchor, head: p.head }));
    } else {
      // Coalesce: keep latest pending; fire once throttle elapses.
      setTimeout(() => {
        const pending = cursorPendingRef.current;
        if (!pending) return;
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        cursorThrottleRef.current = Date.now();
        cursorPendingRef.current = null;
        wsRef.current.send(
          JSON.stringify({ type: "cursor", anchor: pending.anchor, head: pending.head }),
        );
      }, wait);
    }
  }, []);

  return {
    status,
    presence,
    remoteCursors,
    sendUpdate,
    sendTyping,
    sendCursor,
    pendingOfflineUpdates: offlineQueueRef.current.length,
    you,
  };
}
