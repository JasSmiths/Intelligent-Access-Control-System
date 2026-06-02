import { ChevronDown } from "lucide-react";
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
export function MetricCard({ icon: Icon, label, value, detail, tone }: { icon: React.ElementType; label: string; value: string; detail: string; tone: BadgeTone }) {
  return <div className="card metric-card"><div className={`metric-icon ${tone}`}><Icon size={20} /></div><span className="metric-label">{label}</span><strong>{value}</strong><span className="metric-detail">{detail}</span></div>;
}
export function CardHeader({ icon: Icon, title, action }: { icon: React.ElementType; title: string; action?: React.ReactNode }) {
  return <div className="card-header"><div className="card-title"><Icon size={17} /><h2>{title}</h2></div>{action}</div>;
}
export function Toolbar({ title, count, badge, icon: Icon, children }: { title: string; count?: number; badge?: React.ReactNode; icon: React.ElementType; children?: React.ReactNode }) {
  const badgeContent = badge ?? (typeof count === "number" ? count : null);
  return <div className="toolbar"><div className="card-title"><Icon size={18} /><h2>{title}</h2>{badgeContent !== null ? <Badge tone="gray">{badgeContent}</Badge> : null}</div>{children}</div>;
}
export function EmptyState({ icon: Icon, label }: { icon: React.ElementType; label: string }) {
  return <div className="empty-state"><Icon size={22} /><span>{label}</span></div>;
}
