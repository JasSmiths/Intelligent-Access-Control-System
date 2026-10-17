import React from "react";
import { useConfirm } from "./useConfirm";

/** true/false when decided synchronously; a promise while the discard confirmation is open. */
export type LeaveDecision = boolean | Promise<boolean>;

/** Run `proceed` once a leave decision allows it, synchronously when no confirmation was needed. */
export function whenLeaveAllowed(decision: LeaveDecision, proceed: () => void) {
  if (decision === true) proceed();
  else if (decision !== false) void decision.then((allowed) => { if (allowed) proceed(); });
}

/** Ask before discarding a dirty draft; returns the same pending answer to repeated requests. */
export function useDiscardConfirmation() {
  const askConfirm = useConfirm();
  const pending = React.useRef(false);
  return React.useCallback((label: string): Promise<boolean> | false => {
    if (pending.current) return false;
    pending.current = true;
    return askConfirm({ title: `Discard unsaved ${label}?`, message: "Your changes will be lost.", confirmLabel: "Discard", cancelLabel: "Keep editing", tone: "danger" })
      .finally(() => { pending.current = false; });
  }, [askConfirm]);
}

export function useEditorDismissGuard(dirty: boolean, busy: boolean, label = "changes") {
  const state = React.useRef({ dirty, busy, label }); state.current = { dirty, busy, label };
  const confirmDiscard = useDiscardConfirmation();
  return React.useCallback((): LeaveDecision => {
    const current = state.current;
    if (current.busy) return false;
    if (!current.dirty) return true;
    const decision = confirmDiscard(current.label);
    return decision && decision.then((allowed) => allowed && !state.current.busy);
  }, [confirmDiscard]);
}

/** One close path for Escape, backdrop, Close, and Cancel in a draft editor. */
export function useEditorDismiss(onClose: () => void, dirty: boolean, saving: boolean, label = "changes") {
  const close = React.useRef(onClose); close.current = onClose;
  const canLeave = useEditorDismissGuard(dirty, saving, label);
  return React.useCallback(() => { whenLeaveAllowed(canLeave(), () => close.current()); }, [canLeave]);
}
