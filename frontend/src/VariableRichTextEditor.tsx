import React from "react";
import { createPortal } from "react-dom";

type NotificationVariable = {
  name: string;
  token: string;
  label: string;
};

type NotificationVariableWithGroup = NotificationVariable & { group: string };
type SuggestionState = { query: string; from: number; to: number };
type MenuLayout = {
  maxHeight: number;
  placement: "top-start" | "bottom-start";
  ready: boolean;
  x: number;
  y: number;
};
type TextPosition = { node: Node; offset: number };

function VariableRichTextEditor({
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
  const [menuLayout, setMenuLayout] = React.useState<MenuLayout>({
    maxHeight: 256,
    placement: "top-start",
    ready: false,
    x: 0,
    y: 0
  });
  const activeIndexRef = React.useRef(0);
  const editorRef = React.useRef<HTMLDivElement | null>(null);
  const filteredRef = React.useRef<NotificationVariableWithGroup[]>([]);
  const itemRefs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const menuRef = React.useRef<HTMLDivElement | null>(null);
  const onChangeRef = React.useRef(onChange);
  const pendingSelectionRef = React.useRef<number | null>(null);
  const renderedSignatureRef = React.useRef("");
  const suggestionRef = React.useRef<SuggestionState | null>(null);
  const valueRef = React.useRef(value);

  onChangeRef.current = onChange;
  valueRef.current = value;

  const variableSignature = React.useMemo(() => variables.map((variable) => variable.name).join("\u0000"), [variables]);
  const variableNames = React.useMemo(() => new Set(variables.map((variable) => variable.name)), [variableSignature, variables]);

  const updateSuggestion = React.useCallback((nextSuggestion: SuggestionState | null) => {
    suggestionRef.current = nextSuggestion;
    setSuggestion(nextSuggestion);
  }, []);

  const updateActiveIndex = React.useCallback((nextIndex: number) => {
    activeIndexRef.current = nextIndex;
    setActiveIndex(nextIndex);
  }, []);

  const renderValue = React.useCallback((nextValue: string, caretOffset: number | null = null) => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.innerHTML = templateToEditorHtml(nextValue, variableNames);
    renderedSignatureRef.current = variableSignature;
    if (caretOffset !== null && document.activeElement === editor) {
      setSelectionOffset(editor, Math.min(caretOffset, nextValue.length));
    }
  }, [variableNames, variableSignature]);

  const commitValue = React.useCallback((nextValue: string, caretOffset: number) => {
    valueRef.current = nextValue;
    pendingSelectionRef.current = caretOffset;
    onChangeRef.current(nextValue);
    updateSuggestion(findMentionSuggestion(nextValue, caretOffset));
  }, [updateSuggestion]);

  const replaceRange = React.useCallback((from: number, to: number, replacement: string) => {
    const current = valueRef.current;
    const nextValue = current.slice(0, from) + replacement + current.slice(to);
    const caretOffset = from + replacement.length;
    commitValue(nextValue, caretOffset);
    renderValue(nextValue, caretOffset);
  }, [commitValue, renderValue]);

  const insertVariable = React.useCallback((variable: NotificationVariable) => {
    const activeSuggestion = suggestionRef.current;
    const editor = editorRef.current;
    const selection = editor ? getSelectionOffsets(editor) : null;
    const from = activeSuggestion?.from ?? selection?.start ?? valueRef.current.length;
    const to = activeSuggestion?.to ?? selection?.end ?? from;
    replaceRange(from, to, `@${variable.name} `);
    updateSuggestion(null);
  }, [replaceRange, updateSuggestion]);

  React.useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = value ?? "";
    const domValue = editor.childNodes.length ? editableText(editor) : "";
    const needsRender = domValue !== nextValue || renderedSignatureRef.current !== variableSignature;
    if (!needsRender) return;

    const currentSelection = getSelectionOffsets(editor)?.end ?? nextValue.length;
    const pendingSelection = pendingSelectionRef.current;
    pendingSelectionRef.current = null;
    renderValue(nextValue, pendingSelection ?? currentSelection);
  }, [renderValue, value, variableSignature]);

  React.useEffect(() => {
    const onSelectionChange = () => {
      const editor = editorRef.current;
      if (!editor || document.activeElement !== editor) return;
      const selection = getSelectionOffsets(editor);
      if (!selection || selection.start !== selection.end) {
        updateSuggestion(null);
        return;
      }
      updateSuggestion(findMentionSuggestion(valueRef.current, selection.end));
    };
    document.addEventListener("selectionchange", onSelectionChange);
    return () => document.removeEventListener("selectionchange", onSelectionChange);
  }, [updateSuggestion]);

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
    const editor = editorRef.current;
    const menu = menuRef.current;
    const isOpen = Boolean(editor && menu && suggestion && filtered.length);
    if (!isOpen || !editor || !menu || !suggestion) {
      setMenuLayout((current) => ({ ...current, ready: false }));
      return undefined;
    }

    const updatePosition = () => {
      const rect = caretRectForOffset(editor, suggestion.to);
      const menuWidth = menu.offsetWidth || 320;
      const menuHeight = Math.min(menu.offsetHeight || 256, 256);
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const gap = 8;
      const spaceAbove = Math.max(0, rect.top - gap);
      const spaceBelow = Math.max(0, viewportHeight - rect.bottom - gap);
      const placeAbove = spaceAbove >= Math.min(menuHeight, 180) || spaceAbove > spaceBelow;
      const maxHeight = Math.max(48, Math.min(256, Math.floor((placeAbove ? spaceAbove : spaceBelow) - gap)));
      const x = clamp(rect.left, gap, Math.max(gap, viewportWidth - menuWidth - gap));
      const rawY = placeAbove ? rect.top - menuHeight - gap : rect.bottom + gap;
      const y = clamp(rawY, gap, Math.max(gap, viewportHeight - Math.min(menuHeight, maxHeight) - gap));

      setMenuLayout({
        maxHeight,
        placement: placeAbove ? "top-start" : "bottom-start",
        ready: true,
        x,
        y
      });
    };

    const frame = window.requestAnimationFrame(updatePosition);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [filtered.length, filteredSignature, suggestion]);

  const handleInput = React.useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = editableText(editor);
    const caretOffset = getSelectionOffsets(editor)?.end ?? nextValue.length;
    commitValue(nextValue, caretOffset);
  }, [commitValue]);

  const handleKeyDown = React.useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const activeSuggestion = suggestionRef.current;
    const options = filteredRef.current;

    if (activeSuggestion) {
      if (event.key === "Escape") {
        event.preventDefault();
        updateSuggestion(null);
        return;
      }

      if (options.length) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          updateActiveIndex((activeIndexRef.current + 1) % options.length);
          return;
        }

        if (event.key === "ArrowUp") {
          event.preventDefault();
          updateActiveIndex((activeIndexRef.current - 1 + options.length) % options.length);
          return;
        }

        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault();
          const index = Math.min(activeIndexRef.current, options.length - 1);
          insertVariable(options[index]);
          return;
        }
      }
    }

    if (event.key === "Enter") {
      if (!multiline) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      const editor = editorRef.current;
      const selection = editor ? getSelectionOffsets(editor) : null;
      const from = selection?.start ?? valueRef.current.length;
      const to = selection?.end ?? from;
      replaceRange(from, to, "\n");
    }
  }, [insertVariable, multiline, replaceRange, updateActiveIndex, updateSuggestion]);

  const handlePaste = React.useCallback((event: React.ClipboardEvent<HTMLDivElement>) => {
    const text = event.clipboardData.getData("text/plain");
    if (!text) return;
    event.preventDefault();
    const editor = editorRef.current;
    const selection = editor ? getSelectionOffsets(editor) : null;
    const from = selection?.start ?? valueRef.current.length;
    const to = selection?.end ?? from;
    replaceRange(from, to, multiline ? text : text.replace(/\s+/g, " "));
  }, [multiline, replaceRange]);

  const handleClick = React.useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    const editor = editorRef.current;
    const pill = closestVariablePill(event.target);
    if (!editor || !pill) return;
    const from = offsetBeforeNode(editor, pill);
    const token = `@${pill.dataset.variable || ""}`;
    setSelectionOffset(editor, from + token.length);
    updateSuggestion({ query: "", from, to: from + token.length });
  }, [updateSuggestion]);

  const menu = suggestion && filtered.length && typeof document !== "undefined"
    ? createPortal(
      <div
        aria-label="Notification variables"
        className="variable-suggestion-menu"
        data-placement={menuLayout.placement}
        ref={menuRef}
        role="listbox"
        style={{
          "--variable-menu-max-height": `${menuLayout.maxHeight}px`,
          left: menuLayout.x,
          top: menuLayout.y,
          visibility: menuLayout.ready ? "visible" : "hidden"
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
                onMouseDown={(mouseEvent) => mouseEvent.preventDefault()}
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
          <div
            aria-label={label}
            aria-multiline={multiline}
            className={multiline ? "variable-editor-content multiline" : "variable-editor-content"}
            contentEditable
            onClick={handleClick}
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            ref={editorRef}
            role="textbox"
            suppressContentEditableWarning
          />
        </div>
      </label>
      {menu}
    </>
  );
}

export default VariableRichTextEditor;

function templateToEditorHtml(template: string, variables: Set<string>) {
  const parts: string[] = [];
  const pattern = /@([A-Za-z][A-Za-z0-9_]*)|\n/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(template || ""))) {
    if (match.index > lastIndex) parts.push(escapeHtml(template.slice(lastIndex, match.index)));
    if (match[0] === "\n") {
      parts.push("<br>");
    } else if (variables.has(match[1])) {
      const variableName = match[1];
      parts.push(
        `<span class="variable-pill" data-variable="${escapeAttribute(variableName)}" contenteditable="false">@${escapeHtml(variableName)}</span>`
      );
    } else {
      parts.push(escapeHtml(match[0]));
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < template.length) parts.push(escapeHtml(template.slice(lastIndex)));
  return parts.join("");
}

function editableText(root: HTMLElement) {
  return Array.from(root.childNodes).map(textFromNode).join("").replace(/\u00a0/g, " ");
}

function textFromNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
  if (!(node instanceof HTMLElement)) return "";
  if (node.classList.contains("variable-pill")) return `@${node.dataset.variable || ""}`;
  if (node.tagName === "BR") return "\n";
  return Array.from(node.childNodes).map(textFromNode).join("");
}

function findMentionSuggestion(template: string, caretOffset: number) {
  const start = Math.max(0, caretOffset - 48);
  const text = template.slice(start, caretOffset);
  const match = text.match(/(?:^|\s)@([A-Za-z0-9_]*)$/);
  if (!match) return null;
  const query = match[1];
  return { query, from: caretOffset - query.length - 1, to: caretOffset };
}

function getSelectionOffsets(root: HTMLElement) {
  const selection = window.getSelection();
  if (!selection || !selection.anchorNode || !selection.focusNode) return null;
  if (!root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) return null;
  const anchor = offsetForPoint(root, selection.anchorNode, selection.anchorOffset);
  const focus = offsetForPoint(root, selection.focusNode, selection.focusOffset);
  if (anchor === null || focus === null) return null;
  return { start: Math.min(anchor, focus), end: Math.max(anchor, focus) };
}

function offsetForPoint(root: HTMLElement, target: Node, targetOffset: number) {
  let total = 0;
  let found = false;

  const walk = (node: Node): void => {
    if (found) return;
    if (node === target) {
      if (node.nodeType === Node.TEXT_NODE) {
        total += Math.min(targetOffset, node.textContent?.length ?? 0);
      } else {
        const children = Array.from(node.childNodes).slice(0, targetOffset);
        total += children.reduce((sum, child) => sum + textLength(child), 0);
      }
      found = true;
      return;
    }

    if (node.nodeType === Node.TEXT_NODE || isAtomicTextNode(node)) {
      total += textLength(node);
      return;
    }

    node.childNodes.forEach(walk);
  };

  walk(root);
  return found ? total : null;
}

function offsetBeforeNode(root: HTMLElement, target: Node) {
  const parent = target.parentNode;
  if (!parent) return 0;
  const index = Array.from(parent.childNodes).indexOf(target as ChildNode);
  return offsetForPoint(root, parent, Math.max(0, index)) ?? 0;
}

function setSelectionOffset(root: HTMLElement, offset: number) {
  const selection = window.getSelection();
  if (!selection) return;
  const position = positionAtOffset(root, offset);
  const range = document.createRange();
  range.setStart(position.node, position.offset);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}

function positionAtOffset(root: HTMLElement, offset: number): TextPosition {
  let remaining = Math.max(0, offset);

  const walkChildren = (parent: Node): TextPosition | null => {
    const children = Array.from(parent.childNodes);
    for (const [index, child] of children.entries()) {
      const length = textLength(child);
      if (child.nodeType === Node.TEXT_NODE) {
        if (remaining <= length) return { node: child, offset: remaining };
        remaining -= length;
        continue;
      }

      if (isAtomicTextNode(child)) {
        if (remaining <= 0) return { node: parent, offset: index };
        if (remaining <= length) return { node: parent, offset: index + 1 };
        remaining -= length;
        continue;
      }

      const position = walkChildren(child);
      if (position) return position;
    }
    return null;
  };

  return walkChildren(root) ?? { node: root, offset: root.childNodes.length };
}

function caretRectForOffset(root: HTMLElement, offset: number) {
  const position = positionAtOffset(root, offset);
  const range = document.createRange();
  range.setStart(position.node, position.offset);
  range.collapse(true);
  const rect = range.getClientRects()[0] ?? range.getBoundingClientRect();
  if (rect && (rect.width || rect.height)) return rect;
  return root.getBoundingClientRect();
}

function textLength(node: Node): number {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent?.length ?? 0;
  if (!(node instanceof HTMLElement)) return 0;
  if (node.classList.contains("variable-pill")) return `@${node.dataset.variable || ""}`.length;
  if (node.tagName === "BR") return 1;
  return Array.from(node.childNodes).reduce((sum, child) => sum + textLength(child), 0);
}

function isAtomicTextNode(node: Node) {
  return node instanceof HTMLElement && (node.classList.contains("variable-pill") || node.tagName === "BR");
}

function closestVariablePill(target: EventTarget | null) {
  if (!(target instanceof Node)) return null;
  const element = target instanceof Element ? target : target.parentElement;
  return element?.closest<HTMLElement>(".variable-pill") ?? null;
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

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttribute(value: string) {
  return escapeHtml(value).replace(/"/g, "&quot;");
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}
