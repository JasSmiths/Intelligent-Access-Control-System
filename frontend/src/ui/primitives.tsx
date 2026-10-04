import { AlertCircle, ChevronDown, LoaderCircle, RefreshCw } from "lucide-react";
import React from "react";
export type BadgeTone = "green" | "gray" | "amber" | "red" | "blue" | "purple";
export function Badge({ children, tone }: { children: React.ReactNode; tone: BadgeTone }) {
  return <span className={`badge ${tone}`}><span className="badge-label">{children}</span></span>;
}
export function PanelHeader({ title, action, actionKind, onAction }: { title: string; action?: string; actionKind?: "link" | "select"; onAction?: () => void }) {
  return (
    <div className="panel-header">
      <h2>{title}</h2>
      {action && onAction ? (
        actionKind === "select" ? (
          <button className="panel-select" onClick={onAction} type="button">{action}<ChevronDown size={14} /></button>
        ) : <button className="panel-link" onClick={onAction} type="button">{action}</button>
      ) : null}
    </div>
  );
}
export function CardHeader({ icon: Icon, title, action }: { icon: React.ElementType; title: string; action?: React.ReactNode }) {
  return <div className="card-header"><div className="card-title"><Icon size={17} /><h2>{title}</h2></div>{action}</div>;
}
export function Toolbar({ title, count, badge, icon: Icon, children }: { title: string; count?: number; badge?: React.ReactNode; icon: React.ElementType; children?: React.ReactNode }) {
  const badgeContent = badge ?? (typeof count === "number" ? count : null);
  return <div className="toolbar"><div className="card-title"><Icon size={18} /><h2>{title}</h2>{badgeContent !== null ? <Badge tone="gray">{badgeContent}</Badge> : null}</div>{children}</div>;
}
export function EmptyState({ icon: Icon, label, description, action }: {
  icon: React.ElementType;
  label: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon"><Icon size={22} aria-hidden="true" /></div>
      <span>{label}</span>
      {description ? <p className="empty-state-description">{description}</p> : null}
      {action ? <div className="empty-state-action">{action}</div> : null}
    </div>
  );
}

export function LoadingState({ label, compact = false }: { label: string; compact?: boolean }) {
  return (
    <div className={`loading-state${compact ? " compact" : ""}`} role="status" aria-label={label}>
      <div className="loading-state-label"><LoaderCircle size={18} aria-hidden="true" /><span>{label}</span></div>
      {!compact ? <div className="loading-state-lines" aria-hidden="true"><i /><i /><i /></div> : null}
    </div>
  );
}

export function ErrorState({ title, description, onRetry, retrying = false }: {
  title: string;
  description: string;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={21} aria-hidden="true" />
      <div className="error-state-copy"><strong>{title}</strong><p>{description}</p></div>
      {onRetry ? <button className="secondary-button" type="button" onClick={onRetry} disabled={retrying}><RefreshCw size={14} aria-hidden="true" />{retrying ? "Retrying…" : "Try again"}</button> : null}
    </div>
  );
}
