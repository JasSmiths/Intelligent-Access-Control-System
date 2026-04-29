import React from "react";
import { computePosition, flip, offset, shift, size, type Placement, type VirtualElement } from "@floating-ui/dom";
import { Mention } from "@tiptap/extension-mention";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { createPortal } from "react-dom";

type NotificationVariable = {
  name: string;
  token: string;
  label: string;
};

type NotificationVariableWithGroup = NotificationVariable & { group: string };
type SuggestionState = { query: string; from: number; to: number };
type TiptapEditor = NonNullable<ReturnType<typeof useEditor>>;
type FloatingLayout = {
  maxHeight: number;
  placement: Placement;
  ready: boolean;
  x: number;
  y: number;
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
  const [suggestion, setSuggestion] = React.useState<SuggestionState | null>(null);
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [floatingLayout, setFloatingLayout] = React.useState<FloatingLayout>({
    maxHeight: 256,
    placement: "top-start",
    ready: false,
    x: 0,
    y: 0
  });
  const activeIndexRef = React.useRef(0);
  const editorRef = React.useRef<TiptapEditor | null>(null);
  const filteredRef = React.useRef<NotificationVariableWithGroup[]>([]);
  const itemRefs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const menuRef = React.useRef<HTMLDivElement | null>(null);
  const onChangeRef = React.useRef(onChange);
  const suggestionRef = React.useRef<SuggestionState | null>(null);
  const valueRef = React.useRef(value);
  const variablesRef = React.useRef(variables);

  onChangeRef.current = onChange;
  variablesRef.current = variables;

  const updateSuggestion = React.useCallback((nextSuggestion: SuggestionState | null) => {
    suggestionRef.current = nextSuggestion;
    setSuggestion(nextSuggestion);
  }, []);

  const updateActiveIndex = React.useCallback((nextIndex: number) => {
    activeIndexRef.current = nextIndex;
    setActiveIndex(nextIndex);
  }, []);

  const insertVariable = React.useCallback((variable: NotificationVariable) => {
    const activeEditor = editorRef.current;
    const activeSuggestion = suggestionRef.current;
    if (!activeEditor || !activeSuggestion) return;
    activeEditor
      .chain()
      .focus()
      .deleteRange({ from: activeSuggestion.from, to: activeSuggestion.to })
      .insertContent({ type: "mention", attrs: { id: variable.name, label: variable.name } })
      .insertContent(" ")
      .run();
    updateSuggestion(null);
  }, [updateSuggestion]);

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
      },
      handleKeyDown(_view, event) {
        const activeSuggestion = suggestionRef.current;
        const options = filteredRef.current;
        if (!activeSuggestion) return false;

        if (event.key === "Escape") {
          event.preventDefault();
          updateSuggestion(null);
          return true;
        }

        if (!options.length) return false;

        if (event.key === "ArrowDown") {
          event.preventDefault();
          updateActiveIndex((activeIndexRef.current + 1) % options.length);
          return true;
        }

        if (event.key === "ArrowUp") {
          event.preventDefault();
          updateActiveIndex((activeIndexRef.current - 1 + options.length) % options.length);
          return true;
        }

        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault();
          const index = Math.min(activeIndexRef.current, options.length - 1);
          insertVariable(options[index]);
          return true;
        }

        return false;
      }
    },
    onUpdate({ editor: activeEditor }) {
      const next = tiptapDocToTemplate(activeEditor.getJSON());
      valueRef.current = next;
      onChangeRef.current(next);
      updateSuggestion(findMentionSuggestion(activeEditor));
    },
    onSelectionUpdate({ editor: activeEditor }) {
      updateSuggestion(findMentionSuggestion(activeEditor));
    }
  }, []);
  editorRef.current = editor;

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
      updateSuggestion({ query: "", from: pos, to: pos + 1 });
    };
    element.addEventListener("click", onClick);
    return () => element.removeEventListener("click", onClick);
  }, [editor, updateSuggestion]);

  const filtered = React.useMemo(() => {
    const query = suggestion?.query.toLowerCase() ?? "";
    return variables.filter((variable) => `${variable.name} ${variable.label} ${variable.group}`.toLowerCase().includes(query));
  }, [suggestion?.query, variables]);
  const grouped = React.useMemo(() => groupVariables(filtered), [filtered]);
  const filteredSignature = React.useMemo(() => filtered.map((variable) => variable.name).join("\u0000"), [filtered]);

  filteredRef.current = filtered;
  suggestionRef.current = suggestion;

  React.useEffect(() => {
    itemRefs.current.length = filtered.length;
    updateActiveIndex(0);
  }, [filtered.length, filteredSignature, suggestion?.query, updateActiveIndex]);

  React.useEffect(() => {
    itemRefs.current[activeIndex]?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, filteredSignature]);

  React.useLayoutEffect(() => {
    const isOpen = Boolean(editor && suggestion && filtered.length);
    if (!isOpen) {
      setFloatingLayout((current) => ({ ...current, ready: false }));
      return undefined;
    }

    let cancelled = false;
    const menu = menuRef.current;
    if (!menu || !editor || !suggestion) return undefined;

    const updatePosition = async () => {
      const cursor = getCursorVirtualElement(editor, suggestion.to);
      if (!cursor || !menuRef.current) return;

      let maxHeight = 256;
      const position = await computePosition(cursor, menuRef.current, {
        middleware: [
          offset(8),
          flip({ padding: 8 }),
          shift({ padding: 8 }),
          size({
            padding: 8,
            apply({ availableHeight }) {
              maxHeight = Math.max(48, Math.min(256, Math.floor(availableHeight)));
            }
          })
        ],
        placement: "top-start",
        strategy: "fixed"
      });

      if (cancelled) return;
      setFloatingLayout({
        maxHeight,
        placement: position.placement,
        ready: true,
        x: position.x,
        y: position.y
      });
    };

    const frame = window.requestAnimationFrame(updatePosition);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);

    return () => {
      cancelled = true;
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [editor, filtered.length, filteredSignature, suggestion]);

  const menu = suggestion && filtered.length && typeof document !== "undefined"
    ? createPortal(
      <div
        aria-label="Notification variables"
        className="variable-suggestion-menu"
        data-placement={floatingLayout.placement}
        ref={menuRef}
        role="listbox"
        style={{
          "--variable-menu-max-height": `${floatingLayout.maxHeight}px`,
          left: floatingLayout.x,
          top: floatingLayout.y,
          visibility: floatingLayout.ready ? "visible" : "hidden"
        } as React.CSSProperties}
      >
        {grouped.map((group) => (
          <div className="variable-suggestion-group" key={group.group}>
            <strong>{group.group}</strong>
            {group.items.map(({ index, variable }) => (
              <button
                aria-selected={index === activeIndex}
                className={index === activeIndex ? "active" : undefined}
                key={variable.name}
                onClick={() => insertVariable(variable)}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => updateActiveIndex(index)}
                ref={(node) => {
                  itemRefs.current[index] = node;
                }}
                role="option"
                type="button"
              >
                <code>{variable.token}</code>
                <span>{variable.label}</span>
              </button>
            ))}
          </div>
        ))}
      </div>,
      document.body
    )
    : null;

  return (
    <>
      <label className="field variable-editor-field">
        <span>{label}</span>
        <div className="variable-editor-wrap">
          <EditorContent editor={editor} />
        </div>
      </label>
      {menu}
    </>
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

function findMentionSuggestion(editor: TiptapEditor) {
  const { from } = editor.state.selection;
  const start = Math.max(1, from - 48);
  const text = editor.state.doc.textBetween(start, from, "\n", " ");
  const match = text.match(/(?:^|\s)@([A-Za-z0-9_]*)$/);
  if (!match) return null;
  const query = match[1];
  return { query, from: from - query.length - 1, to: from };
}

function getCursorVirtualElement(editor: TiptapEditor, position: number): VirtualElement | null {
  try {
    const coords = editor.view.coordsAtPos(position);
    const width = Math.max(1, coords.right - coords.left);
    const height = Math.max(1, coords.bottom - coords.top);
    return {
      getBoundingClientRect() {
        return {
          bottom: coords.top + height,
          height,
          left: coords.left,
          right: coords.left + width,
          top: coords.top,
          width,
          x: coords.left,
          y: coords.top,
          toJSON() {
            return this;
          }
        };
      }
    };
  } catch {
    return null;
  }
}

function groupVariables(variables: NotificationVariableWithGroup[]) {
  const grouped = new Map<string, Array<{ index: number; variable: NotificationVariableWithGroup }>>();
  variables.forEach((variable, index) => {
    const rows = grouped.get(variable.group) ?? [];
    rows.push({ index, variable });
    grouped.set(variable.group, rows);
  });
  return Array.from(grouped.entries()).map(([group, items]) => ({ group, items }));
}
