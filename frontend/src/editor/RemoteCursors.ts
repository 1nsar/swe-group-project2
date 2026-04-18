/**
 * RemoteCursors — a TipTap/ProseMirror extension that draws other collaborators'
 * carets and selections inside the editor view.
 *
 * Assignment 1 alignment
 * ----------------------
 * §1.2 FR-RT-03 Cursor/Editing Position visibility: "Other collaborators can
 * see which part of the document the user is currently editing."
 *
 * Implementation
 * --------------
 * Each remote user gets a ``Decoration.widget`` for their caret (a coloured
 * vertical bar with a username tag) plus a ``Decoration.inline`` for any
 * selection span when anchor != head. Colours are hashed from the user id so
 * they stay stable across reconnects without having to coordinate with the
 * server.
 *
 * The extension stores remote cursors in its own plugin state; the Editor
 * page calls ``editor.commands.setRemoteCursors([...])`` whenever the
 * ``useCollaboration`` hook updates. We clamp positions to the current
 * document size so a stale cursor can't throw inside ProseMirror.
 */
import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

export interface RemoteCursor {
  user_id: string;
  username: string;
  anchor: number;
  head: number;
}

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    remoteCursors: {
      setRemoteCursors: (cursors: RemoteCursor[]) => ReturnType;
    };
  }
}

const pluginKey = new PluginKey<DecorationSet>("remote-cursors");

// Deterministic HSL colour from the user id — matches the avatar palette in
// ``PresenceBar.tsx`` visually (same hash idea).
function userColor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++) {
    h = (h * 31 + userId.charCodeAt(i)) >>> 0;
  }
  return `hsl(${h % 360}, 70%, 45%)`;
}

function clamp(pos: number, max: number): number {
  if (!Number.isFinite(pos)) return 1;
  return Math.max(1, Math.min(pos, max));
}

function buildDecorations(doc: { content: { size: number } }, cursors: RemoteCursor[]): DecorationSet {
  const max = doc.content.size;
  const decos: Decoration[] = [];

  for (const c of cursors) {
    const anchor = clamp(c.anchor, max);
    const head = clamp(c.head, max);
    const color = userColor(c.user_id);

    // Selection highlight if it's a range.
    if (anchor !== head) {
      const from = Math.min(anchor, head);
      const to = Math.max(anchor, head);
      decos.push(
        Decoration.inline(from, to, {
          class: "remote-selection",
          style: `background-color: ${color}33;`, // 20% opacity
        }),
      );
    }

    // Caret widget at head position.
    decos.push(
      Decoration.widget(head, () => {
        const wrap = document.createElement("span");
        wrap.className = "remote-cursor";
        wrap.style.borderColor = color;
        const bar = document.createElement("span");
        bar.className = "remote-cursor-bar";
        bar.style.backgroundColor = color;
        const label = document.createElement("span");
        label.className = "remote-cursor-label";
        label.style.backgroundColor = color;
        label.textContent = c.username;
        wrap.appendChild(bar);
        wrap.appendChild(label);
        return wrap;
      }, { side: 1 }),
    );
  }

  return DecorationSet.create(doc as never, decos);
}

export const RemoteCursors = Extension.create({
  name: "remoteCursors",

  addCommands() {
    return {
      setRemoteCursors:
        (cursors: RemoteCursor[]) =>
        ({ tr, dispatch }) => {
          if (dispatch) {
            tr.setMeta(pluginKey, cursors);
            dispatch(tr);
          }
          return true;
        },
    };
  },

  addProseMirrorPlugins() {
    return [
      new Plugin<DecorationSet>({
        key: pluginKey,
        state: {
          init(_cfg, { doc }) {
            return buildDecorations(doc, []);
          },
          apply(tr, old) {
            const incoming = tr.getMeta(pluginKey) as RemoteCursor[] | undefined;
            if (incoming) {
              return buildDecorations(tr.doc, incoming);
            }
            if (tr.docChanged) {
              return old.map(tr.mapping, tr.doc);
            }
            return old;
          },
        },
        props: {
          decorations(state) {
            return pluginKey.getState(state);
          },
        },
      }),
    ];
  },
});
