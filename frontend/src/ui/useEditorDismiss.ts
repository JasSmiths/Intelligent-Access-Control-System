import React from "react";

export function useEditorDismissGuard(dirty: boolean, busy: boolean, label = "changes") {
  const state = React.useRef({ dirty, busy, label }); state.current = { dirty, busy, label };
  return React.useCallback(() => {
    const current = state.current;
    return !current.busy && (!current.dirty || window.confirm(`Discard unsaved ${current.label}?`));
  }, []);
}

/** One close path for Escape, backdrop, Close, and Cancel in a draft editor. */
export function useEditorDismiss(onClose: () => void, dirty: boolean, saving: boolean, label = "changes") {
  const close = React.useRef(onClose); close.current = onClose;
  const canLeave = useEditorDismissGuard(dirty, saving, label);
  return React.useCallback(() => { if (canLeave()) close.current(); }, [canLeave]);
}
