import type { PresenceUser } from "../hooks/useCollaboration";

interface Props {
  presence: PresenceUser[];
  status: "connecting" | "open" | "offline" | "closed";
  you: { user_id: string; username: string } | null;
  pendingOfflineUpdates: number;
}

/**
 * Tiny presence indicator for the editor toolbar.
 *
 * Shows one colored avatar-chip per unique user connected to the document's
 * collaboration room. The current user (you) is rendered first; remote users
 * show a typing dot when the server reports typing activity.
 */
export default function PresenceBar({ presence, status, you, pendingOfflineUpdates }: Props) {
  const statusLabel: Record<string, string> = {
    connecting: "Connecting…",
    open: "Live",
    offline: "Offline",
    closed: "Disconnected",
  };

  const ordered = [...presence].sort((a, b) => {
    if (you && a.user_id === you.user_id) return -1;
    if (you && b.user_id === you.user_id) return 1;
    return a.username.localeCompare(b.username);
  });

  return (
    <div className="presence-bar" role="status" aria-label="Collaboration status">
      <span className={`presence-dot presence-${status}`} />
      <span className="presence-status">{statusLabel[status]}</span>
      {pendingOfflineUpdates > 0 && (
        <span className="presence-queue" title="Pending updates — will sync on reconnect">
          ↻ {pendingOfflineUpdates}
        </span>
      )}
      <div className="presence-avatars">
        {ordered.map((u) => (
          <span
            key={u.user_id}
            className={`avatar-chip${u.typing ? " typing" : ""}`}
            style={{ background: colorFor(u.user_id) }}
            title={`${u.username}${u.typing ? " (typing…)" : ""}${
              you && u.user_id === you.user_id ? " (you)" : ""
            }`}
          >
            {u.username.slice(0, 2).toUpperCase()}
          </span>
        ))}
      </div>
    </div>
  );
}

function colorFor(userId: string): string {
  // Deterministic HSL hash — gives every user a distinct, stable color.
  let h = 0;
  for (let i = 0; i < userId.length; i++) h = (h * 31 + userId.charCodeAt(i)) & 0xffff;
  return `hsl(${h % 360}, 62%, 48%)`;
}
