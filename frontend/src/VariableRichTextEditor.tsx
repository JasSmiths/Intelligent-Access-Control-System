import React from "react";
import { createPortal } from "react-dom";
import { getUsableViewportBounds, observeOverlayPlacement, placeOverlay } from "./lib/viewportPlacement";
import type { NotificationEndpoint, VariableRecipientRestriction } from "./api/workflows";
import { restrictionsAfterReplacement, templateOccurrences } from "./lib/templateRecipients";

type NotificationVariable = {
  name: string;
  token: string;
  label: string;
};

type NotificationVariableWithGroup = NotificationVariable & { group: string };
type SuggestionState = { query: string; from: number; to: number };
type MenuLayout = {
  maxHeight: number;
  maxWidth: number;
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
  onChange,
  recipients,
  variableRecipients = []
}: {
  label: string;
  multiline?: boolean;
  value: string;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (value: string, restrictions?: VariableRecipientRestriction[]) => void;
  recipients?: NotificationEndpoint[];
  variableRecipients?: VariableRecipientRestriction[];
}) {
  const [audience, setAudience] = React.useState<{ occurrence: number; name: string; anchor: HTMLElement; focus?: boolean } | null>(null);
  const [audienceSearch, setAudienceSearch] = React.useState("");
  const [audienceLayout, setAudienceLayout] = React.useState<MenuLayout>({maxHeight: 320, maxWidth: 320, placement: "bottom-start", ready: false, x: 0, y: 0});
  const audienceMenuRef = React.useRef<HTMLDivElement | null>(null);
  const audienceCloseRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const restrictionsRef = React.useRef(variableRecipients);
  const [suggestion, setSuggestion] = React.useState<SuggestionState | null>(null);
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [menuLayout, setMenuLayout] = React.useState<MenuLayout>({
    maxHeight: 256,
    maxWidth: 360,
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
  restrictionsRef.current = variableRecipients;

  const variableSignature = React.useMemo(() => variables.map((variable) => variable.name).join("\u0000"), [variables]);
  const variableNames = React.useMemo(() => new Set(variables.map((variable) => variable.name)), [variableSignature, variables]);
  const renderSignature = `${variableSignature}\u0000${JSON.stringify(variableRecipients)}\u0000${recipients?.map((item) => item.label).join("\u0000") ?? ""}`;

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
    editor.innerHTML = templateToEditorHtml(nextValue, variableNames, restrictionsRef.current, recipients);
    renderedSignatureRef.current = renderSignature;
    if (caretOffset !== null && document.activeElement === editor) {
      setSelectionOffset(editor, Math.min(caretOffset, nextValue.length));
    }
  }, [variableNames, renderSignature, recipients]);

  const commitValue = React.useCallback((nextValue: string, caretOffset: number, restrictions = restrictionsRef.current) => {
    valueRef.current = nextValue;
    pendingSelectionRef.current = caretOffset;
    restrictionsRef.current = restrictions;
    onChangeRef.current(nextValue, restrictions);
    updateSuggestion(findMentionSuggestion(nextValue, caretOffset));
  }, [updateSuggestion]);

  const replaceRange = React.useCallback((from: number, to: number, replacement: string) => {
    const current = valueRef.current;
    const nextValue = current.slice(0, from) + replacement + current.slice(to);
    const caretOffset = from + replacement.length;
    const restrictions = restrictionsAfterReplacement(current, restrictionsRef.current, from, to, replacement);
    commitValue(nextValue, caretOffset, restrictions);
    renderValue(nextValue, caretOffset);
  }, [commitValue, renderValue]);

  const insertVariable = React.useCallback((variable: NotificationVariable) => {
    const activeSuggestion = suggestionRef.current;
    const editor = editorRef.current;
    const selection = editor ? getSelectionOffsets(editor) : null;
    const from = activeSuggestion?.from ?? selection?.start ?? valueRef.current.length;
    const to = activeSuggestion?.to ?? selection?.end ?? from;
    replaceRange(from, to, `@${variable.name}`);
    updateSuggestion(null);
  }, [replaceRange, updateSuggestion]);

  React.useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = value ?? "";
    const domValue = editor.childNodes.length ? editableText(editor) : "";
    const needsRender = domValue !== nextValue || renderedSignatureRef.current !== renderSignature;
    if (!needsRender) return;

    const currentSelection = getSelectionOffsets(editor)?.end ?? nextValue.length;
    const pendingSelection = pendingSelectionRef.current;
    pendingSelectionRef.current = null;
    renderValue(nextValue, pendingSelection ?? currentSelection);
  }, [renderValue, value, renderSignature]);

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
      const placement = placeOverlay(rect, { width: menu.offsetWidth, height: Math.min(menu.scrollHeight, 256) }, getUsableViewportBounds());

      setMenuLayout({
        maxHeight: Math.min(256, placement.maxHeight),
        maxWidth: placement.maxWidth,
        placement: placement.side === "top" ? "top-start" : "bottom-start",
        ready: true,
        x: placement.left,
        y: placement.top
      });
    };

    return observeOverlayPlacement(editor, menu, updatePosition);
  }, [filtered.length, filteredSignature, suggestion]);

  const handleInput = React.useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = editableText(editor);
    const caretOffset = getSelectionOffsets(editor)?.end ?? nextValue.length;
    commitValue(nextValue, caretOffset, restrictionsFromEditor(editor, nextValue));
  }, [commitValue]);

  const handleKeyDown = React.useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const pill = closestVariablePill(event.target);
    if (recipients !== undefined && pill && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      updateSuggestion(null);
      setAudience({ occurrence: Number(pill.dataset.occurrence), name: pill.dataset.variable!, anchor: pill, focus: true });
      return;
    }
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
  }, [insertVariable, multiline, replaceRange, updateActiveIndex, updateSuggestion, recipients]);

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
    if (recipients !== undefined) {
      updateSuggestion(null);
      setAudienceSearch("");
      setAudience({ occurrence: Number(pill.dataset.occurrence), name: pill.dataset.variable!, anchor: pill });
      return;
    }
    const from = offsetBeforeNode(editor, pill);
    const token = `@${pill.dataset.variable || ""}`;
    setSelectionOffset(editor, from + token.length);
    updateSuggestion({ query: "", from, to: from + token.length });
  }, [updateSuggestion, recipients]);

  const keepAudienceOpen = () => {
    if (audienceCloseRef.current !== null) clearTimeout(audienceCloseRef.current);
    audienceCloseRef.current = null;
  };
  const closeAudienceSoon = () => {
    keepAudienceOpen();
    audienceCloseRef.current = setTimeout(() => {
      if (!audienceMenuRef.current?.contains(document.activeElement)) setAudience(null);
    }, 220);
  };
  React.useEffect(() => () => { if (audienceCloseRef.current !== null) clearTimeout(audienceCloseRef.current); }, []);
  React.useLayoutEffect(() => {
    if (!audience || !audienceMenuRef.current || !editorRef.current) return;
    const match = templateOccurrences(value)[audience.occurrence];
    if (!match || match[1] !== audience.name) { setAudience(null); return; }
    const anchor = editorRef.current.querySelector<HTMLElement>(`[data-occurrence="${audience.occurrence}"]`) ?? audience.anchor;
    const menu = audienceMenuRef.current;
    const update = () => {
      const placed = placeOverlay(anchor.getBoundingClientRect(), {width: menu.offsetWidth, height: Math.min(menu.scrollHeight, 320)}, getUsableViewportBounds());
      setAudienceLayout({maxHeight: Math.min(320, placed.maxHeight), maxWidth: placed.maxWidth, placement: placed.side === "top" ? "top-start" : "bottom-start", ready: true, x: placed.left, y: placed.top});
    };
    return observeOverlayPlacement(anchor, menu, update);
  }, [audience, value, renderSignature, audienceSearch]);
  React.useEffect(() => {
    if (!audience) return;
    if (audience.focus) audienceMenuRef.current?.querySelector<HTMLInputElement>("input")?.focus();
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !audienceMenuRef.current?.contains(event.target) && !editorRef.current?.contains(event.target)) setAudience(null);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [audience]);
  const audienceRule = audience ? variableRecipients.find((rule) => rule.occurrence === audience.occurrence) : undefined;
  const audienceChoices = [...(recipients ?? [])];
  for (const id of audienceRule?.target_ids ?? []) {
    if (!audienceChoices.some((item) => item.id === id)) audienceChoices.push({id, label: "Unavailable recipient", provider: "", detail: ""});
  }
  const setVariableAudience = (targetIds: string[] | null) => {
    if (!audience) return;
    const rules = restrictionsRef.current.filter((rule) => rule.occurrence !== audience.occurrence);
    if (targetIds !== null) rules.push({occurrence: audience.occurrence, name: audience.name, target_ids: targetIds});
    rules.sort((a, b) => a.occurrence - b.occurrence);
    restrictionsRef.current = rules;
    onChangeRef.current(valueRef.current, rules);
  };
  const audienceMenu = audience && recipients !== undefined ? createPortal(
    <div ref={audienceMenuRef} className="variable-recipient-menu" role="dialog" aria-label={`Recipients for @${audience.name}`}
      onMouseEnter={keepAudienceOpen} onMouseLeave={closeAudienceSoon}
      onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setAudience(null); editorRef.current?.querySelector<HTMLElement>(`[data-occurrence="${audience.occurrence}"]`)?.focus(); } }}
      style={{left: audienceLayout.x, top: audienceLayout.y, maxHeight: audienceLayout.maxHeight, maxWidth: audienceLayout.maxWidth, visibility: audienceLayout.ready ? "visible" : "hidden"}}>
      <strong>@{audience.name}</strong><span>Show this occurrence to</span>
      <label><input type="checkbox" checked={!audienceRule} onChange={(event) => setVariableAudience(event.target.checked ? null : [])} />Everyone</label>
      {audienceChoices.length > 5 ? <input aria-label="Search variable recipients" value={audienceSearch} onChange={(event) => setAudienceSearch(event.target.value)} placeholder="Search recipients" /> : null}
      <div className="variable-recipient-options">
        {audienceChoices.filter((item) => `${item.label} ${item.provider}`.toLowerCase().includes(audienceSearch.toLowerCase())).map((item) => (
          <label key={item.id}><input type="checkbox" checked={audienceRule?.target_ids.includes(item.id) ?? false}
            onChange={(event) => setVariableAudience(event.target.checked ? [...(audienceRule?.target_ids ?? []), item.id] : (audienceRule?.target_ids ?? []).filter((id) => id !== item.id))} />{item.label}</label>
        ))}
      </div>
      <small>Other recipients get the message without this variable.</small>
      <button type="button" onClick={() => setAudience(null)}>Done</button>
    </div>, document.body) : null;

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
          maxWidth: menuLayout.maxWidth,
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
            onMouseOver={(event) => {
              const pill = closestVariablePill(event.target);
              if (recipients === undefined || !pill) return;
              keepAudienceOpen(); updateSuggestion(null); setAudienceSearch("");
              setAudience({occurrence: Number(pill.dataset.occurrence), name: pill.dataset.variable!, anchor: pill});
            }}
            onMouseOut={(event) => {
              const pill = closestVariablePill(event.target);
              if (pill && !(event.relatedTarget instanceof Node && pill.contains(event.relatedTarget))) closeAudienceSoon();
            }}
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
      {audienceMenu}
    </>
  );
}

export default VariableRichTextEditor;

function templateToEditorHtml(template: string, variables: Set<string>, restrictions: VariableRecipientRestriction[], recipients?: NotificationEndpoint[]) {
  const parts: string[] = [];
  const pattern = /@([A-Za-z][A-Za-z0-9_]*)|\n/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let occurrence = -1;

  while ((match = pattern.exec(template || ""))) {
    if (match.index > lastIndex) parts.push(escapeHtml(template.slice(lastIndex, match.index)));
    if (match[0] === "\n") {
      parts.push("<br>");
    } else if (variables.has(match[1])) {
      occurrence += 1;
      const variableName = match[1];
      const rule = restrictions.find((item) => item.occurrence === occurrence && item.name === variableName);
      const audienceLabel = rule ? (rule.target_ids.map((id) => recipients?.find((item) => item.id === id)?.label ?? "Unavailable recipient").join(", ") || "Nobody") : "Everyone";
      parts.push(
        `<span class="variable-pill${rule ? " is-restricted" : ""}" data-variable="${escapeAttribute(variableName)}" data-occurrence="${occurrence}"${rule ? ` data-recipient-ids="${escapeAttribute(JSON.stringify(rule.target_ids))}"` : ""}${recipients !== undefined ? ` role="button" tabindex="0" aria-label="Recipients for @${escapeAttribute(variableName)}: ${escapeAttribute(audienceLabel)}"` : ""} contenteditable="false">@${escapeHtml(variableName)}${rule ? `<small class="variable-recipient-badge">${escapeHtml(audienceLabel)}</small>` : ""}</span>`
      );
    } else {
      occurrence += 1;
      parts.push(escapeHtml(match[0]));
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < template.length) parts.push(escapeHtml(template.slice(lastIndex)));
  return parts.join("");
}

function restrictionsFromEditor(root: HTMLElement, value: string): VariableRecipientRestriction[] {
  const occurrences = templateOccurrences(value);
  const result: VariableRecipientRestriction[] = [];
  let offset = 0;
  const visit = (node: Node) => {
    if (node instanceof HTMLElement && node.classList.contains("variable-pill")) {
      const occurrence = occurrences.findIndex((match) => match.index === offset && match[1] === node.dataset.variable);
      if (node.dataset.recipientIds !== undefined && occurrence >= 0) {
        result.push({occurrence, name: node.dataset.variable!, target_ids: JSON.parse(node.dataset.recipientIds)});
      }
      offset += textFromNode(node).length;
    } else if (node.nodeType === Node.TEXT_NODE || node instanceof HTMLElement && node.tagName === "BR") {
      offset += textFromNode(node).length;
    } else node.childNodes.forEach(visit);
  };
  root.childNodes.forEach(visit);
  return result;
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
