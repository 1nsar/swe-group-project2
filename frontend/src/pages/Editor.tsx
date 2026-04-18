import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Underline from "@tiptap/extension-underline";
import Placeholder from "@tiptap/extension-placeholder";
import { documentsApi, type Document } from "../api/documents";
import EditorToolbar from "../components/EditorToolbar";
import VersionHistory from "../components/VersionHistory";

type SaveStatus = "idle" | "saving" | "saved" | "error";

const AUTOSAVE_DELAY = 1500; // ms after last keystroke

export default function Editor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [doc, setDoc] = useState<Document | null>(null);
  const [title, setTitle] = useState("");
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestContent = useRef<unknown>(null);
  const latestTitle = useRef<string>("");

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

  // ── auto-save ─────────────────────────────────────────────────────────────
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

  // ── editor ────────────────────────────────────────────────────────────────
  const editor = useEditor({
    extensions: [
      StarterKit,
      Underline,
      Placeholder.configure({ placeholder: "Start writing…" }),
    ],
    content: doc?.content ?? null,
    onUpdate({ editor }) {
      latestContent.current = editor.getJSON();
      scheduleSave();
    },
    editorProps: {
      attributes: { class: "tiptap-wrap" },
    },
  });

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

  // ── status label ──────────────────────────────────────────────────────────
  const statusLabel: Record<SaveStatus, string> = {
    idle: "",
    saving: "Saving…",
    saved: "Saved",
    error: "Save failed",
  };

  if (loading) return <p className="spinner" style={{ marginTop: "4rem" }}>Loading document…</p>;

  return (
    <div className="editor-shell">
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
        <button className="ghost" onClick={handleSaveSnapshot} title="Save a named version snapshot">
          Save Version
        </button>
      </div>

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
    </div>
  );
}
