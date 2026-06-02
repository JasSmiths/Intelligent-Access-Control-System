import { AlertTriangle, CheckCircle2, ChevronRight, RefreshCcw } from "lucide-react";
import React from "react";
import type { Anomaly } from "../api/types";
import { alertSeverityLabel, alertSeverityTone, formatDate, isActionableAlert, titleCase } from "../lib/format";
import { Badge, EmptyState } from "../ui/primitives";
export const AlertTray = React.forwardRef<HTMLDivElement, {
  anomalies: Anomaly[];
  onRefresh: () => Promise<void>;
  onViewAll: () => void;
}>(function AlertTray({ anomalies, onRefresh, onViewAll }, ref) {
  const alertCount = anomalies.length;
  const recentAnomalies = anomalies.slice(0, 8);
  return (
    <div className="alert-tray" id="alert-tray" ref={ref} role="dialog" aria-label="Alerts">
      <div className="alert-tray-header">
        <div>
          <strong>Alerts</strong>
          <span>{alertCount ? `${alertCount} actionable alert${alertCount === 1 ? "" : "s"}` : "No actionable alerts"}</span>
        </div>
        <button className="icon-button" onClick={() => onRefresh().catch(() => undefined)} type="button" aria-label="Refresh alerts">
          <RefreshCcw size={15} />
        </button>
      </div>
      <div className="alert-tray-list">
        {recentAnomalies.length ? recentAnomalies.map((anomaly) => (
          <article className="alert-tray-row" key={anomaly.id}>
            <span className={`alert-tray-icon ${anomaly.severity}`}>
              <AlertTriangle size={17} />
            </span>
            <div>
              <div className="alert-tray-row-head">
                <strong>{titleCase(anomaly.type)}</strong>
                <Badge tone={alertSeverityTone(anomaly.severity)}>{alertSeverityLabel(anomaly.severity)}</Badge>
              </div>
              <p>{anomaly.message}</p>
              <time>{formatDate(anomaly.last_seen_at || anomaly.created_at)}</time>
            </div>
          </article>
        )) : (
          <EmptyState icon={CheckCircle2} label="No actionable alerts" />
        )}
      </div>
      <button className="alert-tray-view-all" onClick={onViewAll} type="button">
        View all alerts
        <ChevronRight size={15} />
      </button>
    </div>
  );
});
export function isBellAlert(alert: Anomaly) {
  return isActionableAlert(alert) && alert.type !== "unauthorized_plate";
}
