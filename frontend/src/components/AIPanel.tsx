import { useEffect, useRef, useState } from "react";
import {
  aiApi,
  streamAI,
  type AIAction,
  type AIInteractionSummary,
  type QuotaSnapshot,
} from "../api/ai";
import { diffWords, type DiffPart } from "../utils/wordDiff";

/**
 * Build the accepted text from the diff parts given a set of rejected insert
 * indices. Used for partial AI suggestion acceptance (bonus feature).
 *
 * Rules:
 *  - equal  → always kept
 *  - delete + insert pair → use insert text if accepted, keep delete text if rejected
 *  - standalone insert → include if accepted, skip if rejected
 *  - standalone delete → always applied (text removed in suggestion)
 */
function buildPartialText(parts: DiffPart[], rejected: Set<number>): string {
  let result = "";
  let insertIdx = 0;
  let i = 0;
  while (i < parts.length) {
    const part = parts[i];
    if (part.op === "equal") {
      result += part.text;
      i++;
    } else if (part.op === "delete") {
      const next = parts[i + 1];
      if (next && next.op === "insert") {
        // Replacement pair: use insert if accepted, keep original if rejected
        result += rejected.has(insertIdx) ? part.text : next.text;
        insertIdx++;
        i += 2;
      } else {
        // Pure deletion — always apply
        i++;
      }
    } else {
      // Standalone insert
      if (!rejected.has(insertIdx)) result += part.text;
      insertIdx++;
      i++;
    }
  }
  return result;
}

interface Props {
  docId: string;
  /** The current selection text from the editor — empty if none. */
  selectionText: string;
  /** Text immediately before the selection in the document (capped upstream). */
  contextBefore?: string;
  /** Text immediately after the selection in the document (capped upstream). */
  contextAfter?: string;
  /** Called when the user accepts a suggestion; panel passes the chosen
   *  text so the editor can replace the selection and push an undoable change. */
  onAccept: (text: string) => void;
  /** Closes the panel (used by the editor's layout). */
  onClose: () => void;
  /** Optional: lets the panel open with a specific action from a toolbar button. */
  initialAction?: AIAction;
}

type PanelState =
  | { kind: "idle" }
  | { kind: "pending"; interactionId: string | null; controller: AbortController }
  | { kind: "streaming"; interactionId: string | null; output: string; controller: AbortController }
  | { kind: "ready"; interactionId: string; output: string }
  | { kind: "error"; message: string; code?: string }
  | { kind: "cancelled"; output: string };

const ACTION_LABELS: Record<AIAction, string> = {
  rewrite: "Rewrite / Rephrase",
  summarize: "Summarize",
  translate: "Translate",
  grammar: "Fix Grammar",
  custom: "Custom Prompt",
};

// Common languages first; the rest sorted alphabetically.
const LANGUAGES = [
  "English",
  "Spanish",
  "French",
  "German",
  "Italian",
  "Portuguese",
  "Chinese (Simplified)",
  "Japanese",
  "Korean",
  "Arabic",
  "Hindi",
  "Russian",
  "Turkish",
  "Vietnamese",
];

/**
 * AI side-panel.
 *
 * Assignment 1 alignment
 * ----------------------
 * * §2.5 ADR-004: "Suggestions appear in a side panel as a diff view
 *   (original vs. suggested). The document is never automatically modified."
 *   We render a two-column diff (original | suggestion) using a word-level
 *   diff. Accept flows through ``onAccept`` so the editor applies it as a
 *   single TipTap undoable step.
 * * §2.2 cost control: the panel shows a warning banner when the user's
 *   monthly AI token quota is at ≥80% and a blocking banner at 100%.
 * * §2.2 API Design: we accept structured error codes from the server
 *   (AI_QUOTA_EXCEEDED, AI_ACCESS_DENIED, AI_SERVICE_UNAVAILABLE) and
 *   surface each with a distinct message.
 * * §2.2 AI Integration Design: we pass ``context_before`` / ``context_after``
 *   alongside the selection so the LLM has immediate surrounding context.
 */
export default function AIPanel({
  docId,
  selectionText,
  contextBefore = "",
  contextAfter = "",
  onAccept,
  onClose,
  initialAction,
}: Props) {
  const [action, setAction] = useState<AIAction>(initialAction ?? "rewrite");
  const [tone, setTone] = useState("neutral");
  const [length, setLength] = useState("medium");
  const [instruction, setInstruction] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("Spanish");
  const [state, setState] = useState<PanelState>({ kind: "idle" });
  const [history, setHistory] = useState<AIInteractionSummary[]>([]);
  const [tab, setTab] = useState<"compose" | "history">("compose");
  const [quota, setQuota] = useState<QuotaSnapshot | null>(null);
  const [allowedActions, setAllowedActions] = useState<AIAction[] | null>(null);
  const outputRef = useRef<HTMLDivElement | null>(null);

  // Load quota on mount and after every successful completion.
  useEffect(() => {
    aiApi.quota().then(setQuota).catch(() => setQuota(null));
  }, []);

  // Ask the server which actions this user may invoke on this doc (role gating).
  useEffect(() => {
    aiApi
      .listActions(docId)
      .then((r) => setAllowedActions((r.actions as AIAction[]) ?? null))
      .catch(() => setAllowedActions(null));
  }, [docId]);

  // Auto-scroll the streaming output as tokens arrive.
  useEffect(() => {
    if (state.kind === "streaming" && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [state]);

  // Load history lazily when the tab is opened or after a new interaction.
  useEffect(() => {
    if (tab !== "history") return;
    aiApi.history(docId).then(setHistory).catch(() => setHistory([]));
  }, [tab, docId, state.kind]);

  async function start() {
    if (!selectionText.trim() && action !== "custom") {
      setState({ kind: "error", message: "Select some text in the document first." });
      return;
    }
    if (action === "translate" && !targetLanguage.trim()) {
      setState({ kind: "error", message: "Pick a target language." });
      return;
    }

    const controller = streamAI(
      {
        document_id: docId,
        action,
        selection: selectionText,
        context_before: contextBefore,
        context_after: contextAfter,
        tone,
        length,
        instruction,
        target_language: action === "translate" ? targetLanguage : undefined,
      },
      {
        onMeta: (meta) => {
          if (meta.quota) setQuota(meta.quota);
          setState({
            kind: "pending",
            interactionId: meta.interaction_id,
            controller,
          });
        },
        onPending: () => {
          setState((prev) => {
            if (prev.kind !== "pending") return prev;
            return {
              kind: "streaming",
              interactionId: prev.interactionId,
              output: "",
              controller: prev.controller,
            };
          });
        },
        onToken: (delta) => {
          setState((prev) => {
            if (prev.kind === "pending") {
              return {
                kind: "streaming",
                interactionId: prev.interactionId,
                output: delta,
                controller: prev.controller,
              };
            }
            if (prev.kind !== "streaming") return prev;
            return { ...prev, output: prev.output + delta };
          });
        },
        onDone: ({ interaction_id, output, quota: q }) => {
          if (q) setQuota(q);
          setState({ kind: "ready", interactionId: interaction_id, output });
        },
        onError: ({ code, message }) => {
          setState({ kind: "error", code, message });
        },
        onCancel: () => {
          setState((prev) => {
            const output =
              prev.kind === "streaming"
                ? prev.output
                : prev.kind === "cancelled"
                  ? prev.output
                  : "";
            return { kind: "cancelled", output };
          });
        },
      },
    );

    // Enter pending immediately; the onMeta callback will set the real id.
    setState({ kind: "pending", interactionId: null, controller });
  }

  function cancel() {
    if (state.kind === "streaming" || state.kind === "pending") {
      state.controller.abort();
    }
  }

  async function accept() {
    if (state.kind !== "ready") return;
    try {
      await aiApi.accept(state.interactionId, state.output.length);
    } catch {
      // Non-fatal — local UX wins; history will just miss the decided_at stamp.
    }
    onAccept(state.output);
    setState({ kind: "idle" });
  }

  async function reject() {
    if (state.kind !== "ready") return;
    try {
      await aiApi.reject(state.interactionId);
    } catch {
      /* ignore */
    }
    setState({ kind: "idle" });
  }

  // ── partial acceptance ────────────────────────────────────────────────────
  const [rejectedInserts, setRejectedInserts] = useState<Set<number>>(new Set());

  // Reset rejected set whenever a new suggestion arrives.
  useEffect(() => {
    if (state.kind === "ready") setRejectedInserts(new Set());
  }, [state.kind]);

  function toggleInsert(idx: number) {
    setRejectedInserts((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  }

  async function acceptPartial() {
    if (state.kind !== "ready") return;
    const parts = diffWords(selectionText, state.output);
    const text = buildPartialText(parts, rejectedInserts);
    try {
      await aiApi.accept(state.interactionId, text.length);
    } catch { /* non-fatal */ }
    onAccept(text);
    setState({ kind: "idle" });
  }

  const streaming = state.kind === "streaming";
  const pending = state.kind === "pending";
  const ready = state.kind === "ready";
  const output =
    state.kind === "streaming" || state.kind === "ready" || state.kind === "cancelled"
      ? state.output
      : "";

  // If server filtered the allowed list, narrow the dropdown.
  const renderableActions: AIAction[] =
    allowedActions !== null
      ? (Object.keys(ACTION_LABELS) as AIAction[]).filter((a) => allowedActions.includes(a))
      : (Object.keys(ACTION_LABELS) as AIAction[]);

  return (
    <aside className="ai-panel" aria-label="AI writing assistant">
      <header className="ai-panel-header">
        <span>AI Assistant</span>
        <button className="ghost" onClick={onClose} aria-label="Close AI panel">✕</button>
      </header>

      {/* Quota banner — §2.2 cost control. */}
      {quota && (quota.warning || quota.remaining === 0) && (
        <div
          className={`ai-quota-banner ${quota.remaining === 0 ? "exceeded" : "warning"}`}
          role="status"
        >
          {quota.remaining === 0 ? (
            <>
              <strong>AI quota reached.</strong> Resets{" "}
              {new Date(quota.reset_at).toLocaleDateString()}.
            </>
          ) : (
            <>
              Using <strong>{Math.round(quota.fraction * 100)}%</strong> of your monthly
              AI quota ({quota.used}/{quota.limit} tokens).
            </>
          )}
        </div>
      )}

      <nav className="ai-panel-tabs">
        <button
          className={tab === "compose" ? "tab active" : "tab"}
          onClick={() => setTab("compose")}
        >
          Compose
        </button>
        <button
          className={tab === "history" ? "tab active" : "tab"}
          onClick={() => setTab("history")}
        >
          History
        </button>
      </nav>

      {tab === "compose" ? (
        <div className="ai-panel-body">
          {renderableActions.length === 0 && (
            <div className="ai-error">
              You don't have permission to use AI features on this document.
            </div>
          )}

          <label className="ai-label">
            Action
            <select
              value={action}
              onChange={(e) => setAction(e.target.value as AIAction)}
              disabled={streaming || pending}
            >
              {renderableActions.map((a) => (
                <option key={a} value={a}>
                  {ACTION_LABELS[a]}
                </option>
              ))}
            </select>
          </label>

          {action === "rewrite" && (
            <label className="ai-label">
              Tone
              <select
                value={tone}
                onChange={(e) => setTone(e.target.value)}
                disabled={streaming || pending}
              >
                <option value="neutral">Neutral</option>
                <option value="formal">Formal</option>
                <option value="friendly">Friendly</option>
                <option value="concise">Concise</option>
                <option value="persuasive">Persuasive</option>
              </select>
            </label>
          )}

          {action === "summarize" && (
            <label className="ai-label">
              Length
              <select
                value={length}
                onChange={(e) => setLength(e.target.value)}
                disabled={streaming || pending}
              >
                <option value="short">Short (1–2 sentences)</option>
                <option value="medium">Medium (paragraph)</option>
                <option value="long">Long (several paragraphs)</option>
                <option value="bullets">Bullet points</option>
              </select>
            </label>
          )}

          {action === "translate" && (
            <label className="ai-label">
              Target language
              <select
                value={targetLanguage}
                onChange={(e) => setTargetLanguage(e.target.value)}
                disabled={streaming || pending}
              >
                {LANGUAGES.map((l) => (
                  <option key={l} value={l}>{l}</option>
                ))}
              </select>
            </label>
          )}

          {action === "custom" && (
            <label className="ai-label">
              Instruction
              <textarea
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                rows={3}
                placeholder="e.g. Make this into a bulleted list of key points."
                disabled={streaming || pending}
              />
            </label>
          )}

          <div className="ai-selection">
            <div className="ai-section-title">Selected text</div>
            <div className="ai-selection-box">
              {selectionText.trim() ? selectionText : <em>— nothing selected —</em>}
            </div>
            {(contextBefore.length > 0 || contextAfter.length > 0) && (
              <div className="ai-context-note">
                Sending {contextBefore.length} chars before / {contextAfter.length} chars after as context.
              </div>
            )}
          </div>

          <div className="ai-actions-row">
            {!streaming && !pending ? (
              <button
                className="primary"
                onClick={start}
                disabled={renderableActions.length === 0 || (quota?.remaining === 0)}
              >
                ▶ Generate
              </button>
            ) : (
              <button className="danger" onClick={cancel}>
                ■ Stop
              </button>
            )}
          </div>

          {(streaming || pending || ready || state.kind === "cancelled" || state.kind === "error") && (
            <div className="ai-output" ref={outputRef}>
              <div className="ai-section-title">Suggestion</div>

              {pending && (
                <div className="ai-output-box ai-pending">
                  <em>AI pending…</em>
                </div>
              )}

              {streaming && (
                <div className="ai-output-box">
                  {output || <em>(waiting for tokens…)</em>}
                  <span className="ai-cursor">▋</span>
                </div>
              )}

              {ready && (
                <InteractiveDiffView
                  original={selectionText}
                  suggestion={output}
                  rejectedInserts={rejectedInserts}
                  onToggle={toggleInsert}
                />
              )}

              {state.kind === "cancelled" && output && (
                <>
                  <div className="ai-output-box">{output}</div>
                  <div className="ai-note">
                    Generation cancelled — partial output shown above.
                  </div>
                </>
              )}

              {state.kind === "error" && (
                <div className="ai-error">
                  {state.code === "AI_QUOTA_EXCEEDED" && (
                    <strong>AI_QUOTA_EXCEEDED. </strong>
                  )}
                  {state.code === "AI_ACCESS_DENIED" && (
                    <strong>AI_ACCESS_DENIED. </strong>
                  )}
                  {state.code === "AI_SERVICE_UNAVAILABLE" && (
                    <strong>AI_SERVICE_UNAVAILABLE. </strong>
                  )}
                  {state.message}
                </div>
              )}

              {ready && (
                <div className="ai-actions-row">
                  <button className="primary" onClick={accept}>✓ Accept All</button>
                  {rejectedInserts.size > 0 && (
                    <button className="primary" onClick={acceptPartial}>
                      ✓ Accept Selected
                    </button>
                  )}
                  <button className="ghost" onClick={reject}>✕ Reject</button>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="ai-panel-body">
          {history.length === 0 && <p className="empty-state">No AI interactions yet.</p>}
          {history.map((h) => (
            <div key={h.id} className="ai-history-item">
              <div className="ai-history-head">
                <strong>{ACTION_LABELS[h.action] ?? h.action}</strong>
                <span className={`ai-chip status-${h.status}`}>{h.status}</span>
              </div>
              <div className="ai-history-preview">
                <span className="muted">sel:</span> {h.selection_length} chars
                {" • "}
                <span className="muted">out:</span> {h.output_length} chars
                {h.target_language && (
                  <>
                    {" • "}
                    <span className="muted">to:</span> {h.target_language}
                  </>
                )}
              </div>
              <div className="ai-history-meta">
                <span>{h.provider}/{h.model}</span>
                <span title={`prompt hash ${h.prompt_hash.slice(0, 8)}`}>
                  #{h.prompt_hash.slice(0, 8)}
                </span>
                <span>{new Date(h.created_at).toLocaleString()}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}

/**
 * Interactive two-column diff view (§2.5 ADR-004 + bonus partial acceptance).
 * Insert spans in the right column are clickable — clicking toggles the insert
 * into the rejected set so the user can accept only the parts they want.
 */
function InteractiveDiffView({
  original,
  suggestion,
  rejectedInserts,
  onToggle,
}: {
  original: string;
  suggestion: string;
  rejectedInserts: Set<number>;
  onToggle: (idx: number) => void;
}) {
  const parts = diffWords(original, suggestion);

  // Tag each insert op with a stable index matching buildPartialText's counter.
  let tagIdx = 0;
  const tagged = parts.map((p) => ({ ...p, insertIdx: p.op === "insert" ? tagIdx++ : -1 }));

  return (
    <div className="ai-diff">
      <div className="ai-diff-col">
        <div className="ai-diff-label">Original</div>
        <div className="ai-diff-body">
          {original.trim() === "" && <em>(no selection)</em>}
          {tagged.map((p, i) => {
            if (p.op === "insert") return null;
            const cls = p.op === "delete" ? "diff-del" : "diff-eq";
            return <span key={`o-${i}`} className={cls}>{p.text}</span>;
          })}
        </div>
      </div>
      <div className="ai-diff-col">
        <div className="ai-diff-label">
          Suggestion
          {rejectedInserts.size > 0 && (
            <span className="diff-partial-hint"> (click inserts to toggle)</span>
          )}
        </div>
        <div className="ai-diff-body">
          {suggestion.trim() === "" && <em>(empty)</em>}
          {tagged.map((p, i) => {
            if (p.op === "delete") return null;
            if (p.op === "insert") {
              const rejected = rejectedInserts.has(p.insertIdx);
              return (
                <span
                  key={`s-${i}`}
                  className={`diff-ins${rejected ? " rejected" : ""}`}
                  onClick={() => onToggle(p.insertIdx)}
                  title={rejected ? "Click to include this change" : "Click to exclude this change"}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && onToggle(p.insertIdx)}
                >
                  {p.text}
                </span>
              );
            }
            return <span key={`s-${i}`} className="diff-eq">{p.text}</span>;
          })}
        </div>
      </div>
    </div>
  );
}
