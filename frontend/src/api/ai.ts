import { api } from "./client";

export type AIAction = "rewrite" | "summarize" | "translate" | "grammar" | "custom";
export type AIStatus =
  | "pending"
  | "streaming"
  | "completed"
  | "cancelled"
  | "error"
  | "accepted"
  | "rejected";

export interface AIInteractionSummary {
  id: string;
  document_id: string;
  user_id: string;
  action: AIAction;
  tone: string | null;
  length: string | null;
  target_language: string | null;
  // §2.2 Privacy: no raw text previews — only metadata + hash.
  prompt_hash: string;
  selection_length: number;
  output_length: number;
  status: AIStatus;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
  completed_at: string | null;
  decided_at: string | null;
  retention_expires_at: string;
}

export interface AIStreamRequest {
  document_id: string;
  action: AIAction;
  selection: string;
  context_before?: string;
  context_after?: string;
  tone?: string;
  length?: string;
  instruction?: string;
  target_language?: string;
}

export interface QuotaSnapshot {
  used: number;
  limit: number;
  remaining: number;
  fraction: number;
  warning: boolean;
  reset_at: string;
}

export interface StreamMeta {
  interaction_id: string;
  model: string;
  provider: string;
  role?: string;
  quota?: QuotaSnapshot;
}

export interface StreamDone {
  interaction_id: string;
  output: string;
  input_tokens?: number;
  output_tokens?: number;
  quota?: QuotaSnapshot;
}

export interface StreamError {
  code?: string;
  message: string;
}

export interface StreamHandlers {
  onMeta?: (info: StreamMeta) => void;
  onPending?: (info: { interaction_id: string }) => void;
  onToken?: (delta: string) => void;
  onDone?: (payload: StreamDone) => void;
  onError?: (err: StreamError) => void;
  onCancel?: () => void;
}

/**
 * Streams an AI completion from ``/api/ai/stream`` using fetch + ReadableStream
 * (not EventSource) because we need to POST a JSON body and add the
 * Authorization header — EventSource doesn't support either.
 *
 * Returns an ``AbortController`` so the caller can cancel the request.
 */
export function streamAI(req: AIStreamRequest, handlers: StreamHandlers): AbortController {
  const BASE =
    (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8080";
  const controller = new AbortController();
  const token = localStorage.getItem("access_token") ?? "";

  (async () => {
    let response: Response;
    try {
      response = await fetch(`${BASE}/api/ai/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
        },
        body: JSON.stringify(req),
        signal: controller.signal,
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        handlers.onCancel?.();
      } else {
        handlers.onError?.({ message: (e as Error).message ?? "network error" });
      }
      return;
    }

    if (!response.ok) {
      // Surface structured HTTPException detail when possible (quota/role errors).
      let code: string | undefined;
      let message = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        const detail = (body && (body.detail ?? body)) ?? {};
        if (typeof detail === "string") {
          message = detail;
        } else {
          code = detail.code ?? detail.error ?? undefined;
          message = detail.reason ?? detail.message ?? message;
        }
      } catch {
        /* ignore parse failures */
      }
      handlers.onError?.({ code, message });
      return;
    }

    if (!response.body) {
      handlers.onError?.({ message: "empty response body" });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      // SSE parsing loop: events are separated by blank lines; each event's
      // data line starts with "data: ". We strip that and JSON-parse.
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const lines = rawEvent.split("\n");
          const dataLines = lines
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trimStart());
          if (dataLines.length === 0) continue;
          const payloadStr = dataLines.join("\n");
          let payload: { type: string; [k: string]: unknown };
          try {
            payload = JSON.parse(payloadStr);
          } catch {
            continue;
          }
          switch (payload.type) {
            case "meta":
              handlers.onMeta?.({
                interaction_id: payload.interaction_id as string,
                model: payload.model as string,
                provider: payload.provider as string,
                role: payload.role as string | undefined,
                quota: payload.quota as QuotaSnapshot | undefined,
              });
              break;
            case "pending":
              handlers.onPending?.({ interaction_id: payload.interaction_id as string });
              break;
            case "token":
              handlers.onToken?.(payload.delta as string);
              break;
            case "done":
              handlers.onDone?.({
                interaction_id: payload.interaction_id as string,
                output: payload.output as string,
                input_tokens: payload.input_tokens as number | undefined,
                output_tokens: payload.output_tokens as number | undefined,
                quota: payload.quota as QuotaSnapshot | undefined,
              });
              return;
            case "cancel":
              handlers.onCancel?.();
              return;
            case "error":
              handlers.onError?.({
                code: payload.code as string | undefined,
                message: (payload.message as string) ?? "stream error",
              });
              return;
            default:
              break;
          }
        }
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        handlers.onCancel?.();
      } else {
        handlers.onError?.({ message: (e as Error).message ?? "stream failed" });
      }
    }
  })();

  return controller;
}

// ── decision + history + quota endpoints ────────────────────────────────────

export const aiApi = {
  listActions: (documentId?: string) =>
    api.get<{ actions: string[] }>(
      documentId ? `/api/ai/actions?document_id=${encodeURIComponent(documentId)}` : "/api/ai/actions",
    ),

  quota: () => api.get<QuotaSnapshot>("/api/ai/quota"),

  accept: (id: string, appliedLength?: number) =>
    api.post<AIInteractionSummary>(`/api/ai/interactions/${id}/accept`, {
      applied_length: appliedLength,
    }),

  reject: (id: string) =>
    api.post<AIInteractionSummary>(`/api/ai/interactions/${id}/reject`, {}),

  history: (documentId: string) =>
    api.get<AIInteractionSummary[]>(`/api/ai/history/${documentId}`),

  get: (id: string) => api.get<AIInteractionSummary>(`/api/ai/interactions/${id}`),
};
