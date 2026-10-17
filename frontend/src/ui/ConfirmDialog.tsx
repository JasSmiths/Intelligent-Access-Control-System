import React from "react";
import { AlertTriangle, HelpCircle, Loader2 } from "lucide-react";
import { useModalClose } from "./useModalClose";
import { useModalFocus } from "./useModalFocus";

export type ConfirmTone = "default" | "danger";

export type ConfirmDialogProps = {
  title: string;
  message?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Danger styles the confirm action for deletes, removals and discards. */
  tone?: ConfirmTone;
  /** Disables both actions and Escape while the owner finishes work. */
  busy?: boolean;
  busyLabel?: string;
  /** Optional work that runs with the dialog open and busy; the dialog confirms after it resolves. */
  action?: () => Promise<void>;
  /** Called once after the dialog has closed (and its exit animation finished). */
  onResult: (confirmed: boolean) => void;
  /** Called instead of onResult when the action rejects. */
  onActionError?: (error: unknown) => void;
};

/** In-app replacement for the browser's native confirm dialog. Prefer useConfirm() over rendering this directly. */
export function ConfirmDialog({
  title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", tone = "default",
  busy = false, busyLabel, action, onResult, onActionError,
}: ConfirmDialogProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const titleId = React.useId();
  const messageId = React.useId();
  const [running, setRunning] = React.useState(false);
  const settled = React.useRef(false);
  const working = busy || running;
  const workingRef = React.useRef(working); workingRef.current = working;
  const finish = useModalClose(ref, (confirmed: boolean) => {
    if (settled.current) return;
    settled.current = true;
    onResult(confirmed);
  });
  const cancel = React.useCallback(() => { if (!workingRef.current && !settled.current) void finish(false); }, [finish]);
  useModalFocus(ref, true, cancel);
  const accept = async () => {
    if (workingRef.current || settled.current) return;
    if (!action) { void finish(true); return; }
    workingRef.current = true; setRunning(true);
    try {
      await action();
    } catch (error) {
      if (settled.current) return;
      settled.current = true;
      if (onActionError) onActionError(error); else onResult(false);
      return;
    }
    workingRef.current = false; setRunning(false);
    void finish(true);
  };
  const danger = tone === "danger";
  const Icon = danger ? AlertTriangle : HelpCircle;
  return <div className="modal-backdrop stacked-modal confirm-dialog-backdrop" role="presentation">
    <div ref={ref} className="modal-card gate-confirm-modal confirm-dialog" role="alertdialog" aria-modal="true"
      aria-labelledby={titleId} aria-describedby={message ? messageId : undefined} aria-busy={working || undefined}>
      <div className="modal-header">
        <div className="gate-confirm-title">
          <span className={`gate-confirm-icon${danger ? " danger" : ""}`} aria-hidden="true"><Icon size={20} /></span>
          <div>
            <h2 id={titleId}>{title}</h2>
            {message ? <p id={messageId}>{message}</p> : null}
          </div>
        </div>
      </div>
      <div className="modal-actions">
        <button className="secondary-button" disabled={working} onClick={cancel} type="button">{cancelLabel}</button>
        <button className={danger ? "danger-button" : "primary-button"} disabled={working} onClick={() => void accept()} type="button">
          {working ? <Loader2 className="spin" size={15} aria-hidden="true" /> : null}
          {working && busyLabel ? busyLabel : confirmLabel}
        </button>
      </div>
    </div>
  </div>;
}
