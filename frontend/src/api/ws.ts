/**
 * Small helper that builds the WebSocket URL for a document's collaboration
 * room. The token is passed as a query parameter because browsers can't set
 * custom headers on the WebSocket handshake.
 */

const DEFAULT_WS_BASE = "ws://localhost:8080";

export function buildCollabUrl(docId: string): string {
  const base = (import.meta.env.VITE_WS_URL as string | undefined) ?? DEFAULT_WS_BASE;
  const token = localStorage.getItem("access_token") ?? "";
  const params = new URLSearchParams();
  if (token) params.set("token", token);
  const qs = params.toString();
  return `${base}/ws/documents/${encodeURIComponent(docId)}${qs ? `?${qs}` : ""}`;
}
