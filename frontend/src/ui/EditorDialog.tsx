import React from "react";
import { X } from "lucide-react";
import { useEditorDismissGuard } from "./useEditorDismiss";
import { useModalClose } from "./useModalClose";
import { useModalFocus } from "./useModalFocus";

export type EditorCloseGuard = () => boolean;
type ClosePorts = { requestClose: () => void; closeAccepted: () => void | Promise<void> };

export function EditorDialog({ title, description, dirty, busy, onClose, onGuardChange, tabs, children }: {
  title: string;
  description: string;
  dirty: boolean;
  busy: boolean;
  onClose: () => void;
  onGuardChange?: (guard: EditorCloseGuard | null) => void;
  tabs?: React.ReactNode;
  children: (ports: ClosePorts) => React.ReactNode;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const closeAccepted = useModalClose(ref, onClose);
  const canLeave = useEditorDismissGuard(dirty, busy, "integration changes");
  const requestClose = React.useCallback(() => { if (canLeave()) void closeAccepted(); }, [canLeave, closeAccepted]);
  useModalFocus(ref, true, requestClose);
  React.useLayoutEffect(() => { onGuardChange?.(canLeave); return () => onGuardChange?.(null); }, [onGuardChange, canLeave]);
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
    <div ref={ref} className="modal-card integration-modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-header"><div><h2>{title}</h2><p>{description}</p></div><button className="icon-button" onClick={requestClose} type="button" aria-label="Close"><X size={16} /></button></div>
      {tabs}{children({ requestClose, closeAccepted })}
    </div>
  </div>;
}
