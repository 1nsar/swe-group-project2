import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Underline from "@tiptap/extension-underline";
import Placeholder from "@tiptap/extension-placeholder";
import { documentsApi, type Document, type ShareLinkResponse } from "../api/documents";
import EditorToolbar from "../components/EditorToolbar";
import VersionHistory from "../components/VersionHistory";
import PresenceBar from "../components/PresenceBar";
import AIPanel from "../components/AIPanel";
import { useCollaboration, type RemoteUpdate } from "../hooks/useCollaboration";
import { RemoteCursors } from "../editor/RemoteCursors";

type SaveStatus = "idle" | "saving" | "saved" | "error";

const AUTOSAVE_DELAY = 1500; // ms after last keystroke
const COLLAB_BROADCAST_DELAY = 300; // ms debounce for WS broadcast
// Budgets for AI context around the selection (§2.2 AI Integration Design).
const CONTEXT_BEFORE_CHARS = 2000;
const CONTEXT_AFTER_CHARS = 800;

export default function Editor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [doc, setDoc] = useState<Document | null>(null);
  const [title, setTitle] = useState("");
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [selectionText, setSelectionText] = useState("");
  const [contextBefore, setContextBefore] = useState("");
  const [contextAfter, setContextAfter] = useState("");
  const [loading, setLoading] = useState(true);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareLink, setShareLink] = useState<ShareLinkResponse | null>(null);
  const [shareRole, setShareRole] = useState<"viewer" | "editor">("editor");
  const [shareCopied, setShareCopied] = useState(false);

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const broadcastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestContent = useRef<unknown>(null);
  const latestTitle = useRef<string>("");
  // Guards against re-broadcasting content we just received from the server.
  const applyingRemote = useRef<boolean>(false);

  // ── load document ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (!id) return;
    documentsApi
      .get(id)
      .then((d) => {
        setDoc(d);
        setTitle(d.title);
        latestTitle.current = d.title;
        latestContent.current = d.content;
      })
      .catch(() => navigate("/"))
      .finally(() => setLoading(false));
  }, [id, navigate]);

  // ── auto-save (HTTP PUT) ──────────────────────────────────────────────────
  const scheduleSave = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    setSaveStatus("saving");
    saveTimer.current = setTimeout(async () => {
      if (!id) return;
      try {
        await documentsApi.update(id, {
          title: latestTitle.current,
          content: latestContent.current,
        });
        setSaveStatus("saved");
        setTimeout(() => setSaveStatus("idle"), 2000);
      } catch {
        setSaveStatus("error");
      }
    }, AUTOSAVE_DELAY);
  }, [id]);

  // ── remote update handler (from WebSocket) ────────────────────────────────
  const onRemoteUpdate = useCallback((u: RemoteUpdate) => {
    applyingRemote.current = true;
    latestContent.current = u.content;
    editorRef.current?.commands.setContent(u.content as never, false);
    setTimeout(() => {
      applyingRemote.current = false;
    }, 0);
  }, []);

  // Collaboration hook (WebSocket-based). `enabled` kicks in once the doc has loaded.
  const {
    status,
    presence,
    remoteCursors,
    sendUpdate,
    sendTyping,
    sendCursor,
    pendingOfflineUpdates,
    you,
  } = useCollaboration({
    docId: id,
    enabled: !!doc,
    onRemoteUpdate,
  });

  // ── editor ────────────────────────────────────────────────────────────────
  const editor = useEditor({
    extensions: [
      StarterKit,
      Underline,
      Placeholder.configure({ placeholder: "Start writing…" }),
      RemoteCursors,
    ],
    content: doc?.content ?? null,
    onUpdate({ editor }) {
      const json = editor.getJSON();
      latestContent.current = json;

      // Don't re-broadcast content that just came in over the socket.
      if (applyingRemote.current) {
        return;
      }

      sendTyping();
      if (broadcastTimer.current) clearTimeout(broadcastTimer.current);
      broadcastTimer.current = setTimeout(() => {
        sendUpdate(json);
      }, COLLAB_BROADCAST_DELAY);

      scheduleSave();
    },
    onSelectionUpdate({ editor }) {
      const { from, to } = editor.state.selection;
      const docSize = editor.state.doc.content.size;

      // Selection text + surrounding context buckets for the AI panel.
      if (from === to) {
        setSelectionText("");
      } else {
        setSelectionText(editor.state.doc.textBetween(from, to, "\n"));
      }
      const beforeFrom = Math.max(1, from - CONTEXT_BEFORE_CHARS);
      const afterTo = Math.min(docSize, to + CONTEXT_AFTER_CHARS);
      setContextBefore(editor.state.doc.textBetween(beforeFrom, from, "\n"));
      setContextAfter(editor.state.doc.textBetween(to, afterTo, "\n"));

      // Broadcast cursor to peers (§1.2 FR-RT-03).
      sendCursor(from, to);
    },
    editorProps: {
      attributes: { class: "tiptap-wrap" },
    },
  });

  // Keep a ref to the editor so our callbacks can reach it without stale closures.
  const editorRef = useRef(editor);
  useEffect(() => {
    editorRef.current = editor;
  }, [editor]);

  // Push remote cursors into the decoration plugin whenever they change.
  useEffect(() => {
    if (!editor) return;
    // Don't render our own caret as a remote decoration.
    const mine = you?.user_id;
    const visible = remoteCursors.filter((c) => c.user_id !== mine);
    editor.commands.setRemoteCursors(visible);
  }, [editor, remoteCursors, you]);

  // Populate editor once doc loads (editor may init before fetch resolves)
  useEffect(() => {
    if (editor && doc && !editor.getText()) {
      editor.commands.setContent(doc.content as never);
    }
  }, [editor, doc]);

  // ── title change ──────────────────────────────────────────────────────────
  function handleTitleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value;
    setTitle(val);
    latestTitle.current = val;
    scheduleSave();
  }

  // ── manual snapshot ───────────────────────────────────────────────────────
  async function handleSaveSnapshot() {
    if (!id) return;
    await documentsApi.update(id, {
      title: latestTitle.current,
      content: latestContent.current,
    });
    await documentsApi.saveVersion(id);
    setSaveStatus("saved");
    setTimeout(() => setSaveStatus("idle"), 2000);
  }

  // ── apply AI suggestion to the editor ─────────────────────────────────────
  function handleAcceptAI(text: string) {
    if (!editor) return;
    const { from, to } = editor.state.selection;
    const chain = editor.chain().focus();
    if (from === to) {
      chain.insertContent(text).run();
    } else {
      chain.insertContentAt({ from, to }, text).run();
    }
  }

  // ── share link ────────────────────────────────────────────────────────────
  async function handleGenerateShareLink() {
    if (!id) return;
    const link = await documentsApi.createShareLink(id, shareRole);
    setShareLink(link);
  }

  function handleCopy() {
    if (!shareLink) return;
    const url = `${window.location.origin}/join/${shareLink.token}`;
    navigator.clipboard.writeText(url);
    setShareCopied(true);
    setTimeout(() => setShareCopied(false), 2000);
  }

  // ── status label ──────────────────────────────────────────────────────────
  const statusLabel: Record<SaveStatus, string> = {
    idle: "",
    saving: "Saving…",
    saved: "Saved",
    error: "Save failed",
  };

  if (loading) return <p className="spinner" style={{ marginTop: "4rem" }}>Loading document…</p>;

  return (
    <div className={`editor-shell${aiOpen ? " ai-open" : ""}`}>
      {/* Top bar */}
      <div className="editor-topbar">
        <button className="ghost back-btn" onClick={() => navigate("/")}>← Back</button>
        <input
          className="title-input"
          type="text"
          value={title}
          onChange={handleTitleChange}
          placeholder="Untitled Document"
        />
        <span className={`autosave-status ${saveStatus}`}>
          {statusLabel[saveStatus]}
        </span>
        <PresenceBar
          presence={presence}
          status={status}
          you={you}
          pendingOfflineUpdates={pendingOfflineUpdates}
        />
        <button
          className={`ghost${aiOpen ? " active" : ""}`}
          onClick={() => setAiOpen((o) => !o)}
          title="Open AI assistant"
        >
          ✨ AI
        </button>
        <button className="ghost" onClick={handleSaveSnapshot} title="Save a named version snapshot">
          Save Version
        </button>
        {doc?.owner_id === doc?.owner_id && (
          <button className="ghost" onClick={() => { setShareOpen(true); setShareLink(null); }} title="Share document">
            🔗 Share
          </button>
        )}
      </div>

      {/* Share modal */}
      {shareOpen && (
        <div className="modal-backdrop" onClick={() => setShareOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <span>Share document</span>
              <button className="ghost" onClick={() => setShareOpen(false)}>✕</button>
            </div>
            <div className="modal-body">
              <label className="ai-label">
                Access level
                <select value={shareRole} onChange={(e) => { setShareRole(e.target.value as "viewer" | "editor"); setShareLink(null); }}>
                  <option value="editor">Editor — can read and write</option>
                  <option value="viewer">Viewer — read only</option>
                </select>
              </label>
              <button className="primary" onClick={handleGenerateShareLink} style={{ marginTop: "0.5rem" }}>
                Generate link
              </button>
              {shareLink && (
                <div className="share-link-box">
                  <code className="share-link-url">{window.location.origin}/join/{shareLink.token}</code>
                  <button className="primary" onClick={handleCopy}>
                    {shareCopied ? "✓ Copied!" : "Copy"}
                  </button>
                  <p className="ai-note">Expires in 7 days. Anyone with this link can join as {shareLink.role}.</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Formatting toolbar */}
      {editor && (
        <EditorToolbar
          editor={editor}
          onToggleHistory={() => setHistoryOpen((o) => !o)}
          historyOpen={historyOpen}
        />
      )}

      {/* Editor body */}
      <div className="editor-body">
        <EditorContent editor={editor} />
      </div>

      {/* Version history slide-in panel */}
      {historyOpen && id && (
        <VersionHistory
          docId={id}
          onClose={() => setHistoryOpen(false)}
          onRestored={() => {
            documentsApi.get(id).then((d) => {
              setDoc(d);
              setTitle(d.title);
              latestTitle.current = d.title;
              latestContent.current = d.content;
              editor?.commands.setContent(d.content as never);
            });
          }}
        />
      )}

      {/* AI side panel */}
      {aiOpen && id && (
        <AIPanel
          docId={id}
          selectionText={selectionText}
          contextBefore={contextBefore}
          contextAfter={contextAfter}
          onAccept={handleAcceptAI}
          onClose={() => setAiOpen(false)}
        />
      )}
    </div>
  );
}
