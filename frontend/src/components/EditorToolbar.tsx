import type { Editor } from "@tiptap/react";

interface Props {
  editor: Editor;
  onToggleHistory: () => void;
  historyOpen: boolean;
}

export default function EditorToolbar({ editor, onToggleHistory, historyOpen }: Props) {
  const btn = (label: string, action: () => void, active: boolean, title?: string) => (
    <button
      key={label}
      className={active ? "active" : ""}
      onMouseDown={(e) => { e.preventDefault(); action(); }}
      title={title ?? label}
    >
      {label}
    </button>
  );

  return (
    <div className="toolbar">
      {btn("B", () => editor.chain().focus().toggleBold().run(), editor.isActive("bold"), "Bold")}
      {btn("I", () => editor.chain().focus().toggleItalic().run(), editor.isActive("italic"), "Italic")}
      {btn("U", () => editor.chain().focus().toggleUnderline().run(), editor.isActive("underline"), "Underline")}
      {btn("S", () => editor.chain().focus().toggleStrike().run(), editor.isActive("strike"), "Strikethrough")}
      {btn("Code", () => editor.chain().focus().toggleCode().run(), editor.isActive("code"))}

      <div className="toolbar-sep" />

      {btn("H1", () => editor.chain().focus().toggleHeading({ level: 1 }).run(), editor.isActive("heading", { level: 1 }))}
      {btn("H2", () => editor.chain().focus().toggleHeading({ level: 2 }).run(), editor.isActive("heading", { level: 2 }))}
      {btn("H3", () => editor.chain().focus().toggleHeading({ level: 3 }).run(), editor.isActive("heading", { level: 3 }))}

      <div className="toolbar-sep" />

      {btn("• List", () => editor.chain().focus().toggleBulletList().run(), editor.isActive("bulletList"), "Bullet list")}
      {btn("1. List", () => editor.chain().focus().toggleOrderedList().run(), editor.isActive("orderedList"), "Ordered list")}
      {btn("❝", () => editor.chain().focus().toggleBlockquote().run(), editor.isActive("blockquote"), "Blockquote")}
      {btn("</>", () => editor.chain().focus().toggleCodeBlock().run(), editor.isActive("codeBlock"), "Code block")}

      <div className="toolbar-sep" />

      {btn("↩ Undo", () => editor.chain().focus().undo().run(), false)}
      {btn("↪ Redo", () => editor.chain().focus().redo().run(), false)}

      <button
        className={`history-btn${historyOpen ? " active" : ""}`}
        onClick={onToggleHistory}
        title="Version history"
      >
        🕐 History
      </button>
    </div>
  );
}
