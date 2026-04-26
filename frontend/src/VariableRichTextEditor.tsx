import React from "react";
import { Mention } from "@tiptap/extension-mention";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

type NotificationVariable = {
  name: string;
  token: string;
  label: string;
};

export function VariableRichTextEditor({
  label,
  multiline = false,
  value,
  variables,
  onChange
}: {
  label: string;
  multiline?: boolean;
  value: string;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (value: string) => void;
}) {
  const [suggestion, setSuggestion] = React.useState<{ query: string; from: number; to: number } | null>(null);
  const valueRef = React.useRef(value);
  const variablesRef = React.useRef(variables);
  variablesRef.current = variables;

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        blockquote: false,
        bulletList: false,
        codeBlock: false,
        heading: false,
        horizontalRule: false,
        orderedList: false
      }),
      Mention.configure({
        HTMLAttributes: { class: "variable-pill" },
        renderText({ node }) {
          return `@${node.attrs.label ?? node.attrs.id}`;
        },
        renderHTML({ node }) {
          return ["span", { class: "variable-pill", "data-variable": node.attrs.label ?? node.attrs.id }, `@${node.attrs.label ?? node.attrs.id}`];
        }
      })
    ],
    content: templateToTiptapDoc(value, variables),
    editorProps: {
      attributes: {
        class: multiline ? "variable-editor-content multiline" : "variable-editor-content"
      }
    },
    onUpdate({ editor: activeEditor }) {
      const next = tiptapDocToTemplate(activeEditor.getJSON());
      valueRef.current = next;
      onChange(next);
      setSuggestion(findMentionSuggestion(activeEditor));
    },
    onSelectionUpdate({ editor: activeEditor }) {
      setSuggestion(findMentionSuggestion(activeEditor));
    }
  }, []);

  React.useEffect(() => {
    if (!editor || value === valueRef.current) return;
    valueRef.current = value;
    editor.commands.setContent(templateToTiptapDoc(value, variablesRef.current), { emitUpdate: false });
  }, [editor, value]);

  React.useEffect(() => {
    if (!editor) return undefined;
    const element = editor.view.dom;
    const onClick = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest(".variable-pill") : null;
      if (!target) return;
      const pos = editor.view.posAtDOM(target, 0);
      editor.commands.setTextSelection({ from: pos, to: pos + 1 });
      setSuggestion({ query: "", from: pos, to: pos + 1 });
    };
    element.addEventListener("click", onClick);
    return () => element.removeEventListener("click", onClick);
  }, [editor]);

  const filtered = React.useMemo(() => {
    const query = suggestion?.query.toLowerCase() ?? "";
    return variables.filter((variable) => `${variable.name} ${variable.label} ${variable.group}`.toLowerCase().includes(query)).slice(0, 10);
  }, [suggestion?.query, variables]);

  const insertVariable = (variable: NotificationVariable) => {
    if (!editor || !suggestion) return;
    editor.chain().focus().deleteRange({ from: suggestion.from, to: suggestion.to }).insertContent({ type: "mention", attrs: { id: variable.name, label: variable.name } }).insertContent(" ").run();
    setSuggestion(null);
  };

  return (
    <label className="field variable-editor-field">
      <span>{label}</span>
      <div className="variable-editor-wrap">
        <EditorContent editor={editor} />
        {suggestion && filtered.length ? (
          <div className="variable-suggestion-menu">
            {groupVariables(filtered).map((group) => (
              <div className="variable-suggestion-group" key={group.group}>
                <strong>{group.group}</strong>
                {group.items.map((variable) => (
                  <button key={variable.name} onMouseDown={(event) => event.preventDefault()} onClick={() => insertVariable(variable)} type="button">
                    <code>{variable.token}</code>
                    <span>{variable.label}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </label>
  );
}

export default VariableRichTextEditor;

function templateToTiptapDoc(template: string, variables: Array<NotificationVariable & { group?: string }>) {
  const names = new Set(variables.map((variable) => variable.name));
  const paragraphs = (template || "").split(/\n/);
  return {
    type: "doc",
    content: paragraphs.map((paragraph) => ({
      type: "paragraph",
      content: templateLineToTiptap(paragraph, names)
    }))
  };
}

function templateLineToTiptap(line: string, names: Set<string>) {
  const content: Array<Record<string, unknown>> = [];
  const pattern = /@([A-Za-z][A-Za-z0-9_]*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line))) {
    if (match.index > lastIndex) content.push({ type: "text", text: line.slice(lastIndex, match.index) });
    if (names.has(match[1])) {
      content.push({ type: "mention", attrs: { id: match[1], label: match[1] } });
    } else {
      content.push({ type: "text", text: match[0] });
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < line.length) content.push({ type: "text", text: line.slice(lastIndex) });
  return content.length ? content : undefined;
}

function tiptapDocToTemplate(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const raw = node as { type?: string; text?: string; attrs?: Record<string, unknown>; content?: unknown[] };
  if (raw.type === "text") return raw.text ?? "";
  if (raw.type === "mention") return `@${String(raw.attrs?.label ?? raw.attrs?.id ?? "")}`;
  const children = raw.content?.map(tiptapDocToTemplate) ?? [];
  if (raw.type === "doc") return children.join("\n");
  if (raw.type === "paragraph") return children.join("");
  return children.join("");
}

function findMentionSuggestion(editor: NonNullable<ReturnType<typeof useEditor>>) {
  const { from } = editor.state.selection;
  const start = Math.max(1, from - 48);
  const text = editor.state.doc.textBetween(start, from, "\n", " ");
  const match = text.match(/(?:^|\s)@([A-Za-z0-9_]*)$/);
  if (!match) return null;
  const query = match[1];
  return { query, from: from - query.length - 1, to: from };
}

function groupVariables(variables: Array<NotificationVariable & { group: string }>) {
  const grouped = new Map<string, Array<NotificationVariable & { group: string }>>();
  for (const variable of variables) {
    const rows = grouped.get(variable.group) ?? [];
    rows.push(variable);
    grouped.set(variable.group, rows);
  }
  return Array.from(grouped.entries()).map(([group, items]) => ({ group, items }));
}
