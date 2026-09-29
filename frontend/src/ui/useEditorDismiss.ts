import React from "react";

/** One close path for Escape, backdrop, Close, and Cancel in a draft editor. */
export function useEditorDismiss(onClose: () => void, dirty: boolean, saving: boolean, label = "changes") {
  const state = React.useRef({ onClose, dirty, saving, label });
  state.current = { onClose, dirty, saving, label };
  return React.useCallback(() => {
    const current = state.current;
    if (current.saving) return;
    if (current.dirty && !window.confirm(`Discard unsaved ${current.label}?`)) return;
    current.onClose();
  }, []);
}
