import { useModalFocus } from "../../ui/useModalFocus";
import { useModalClose } from "../../ui/useModalClose";
import { Activity, ArrowRight, CalendarDays, Car, Clock3, Loader2, Pencil, Trash2, UserRound, X } from "lucide-react";
import React from "react";
import { createPortal } from "react-dom";

import { api, isAbortError } from "../../api/client";
import { formatDate } from "../../lib/format";
import type { RealtimeMessage } from "../../api/types";



import type { VisitorPass, VisitorPassLogEntry } from "./types";
import { VisitorPassStatusPill, VisitorPassAvatar, VisitorPassDetailTile } from "./components";
import { isVisitorPassAuditLogEvent, visitorPassLogsEqual, visitorPassWindowLabel, visitorPassSourceLabel, visitorPassPassDurationLabel, visitorPassVisitDurationLabel, visitorPassLogDetails, visitorPassLogIcon } from "./model";

export function VisitorPassDetailsModal({
  visitorPass,
  latestRealtime,
  onClose: finishClose,
  onEdit: finishEdit,
  onCancel,
  onDelete,
}: {
  visitorPass: VisitorPass;
  latestRealtime: RealtimeMessage | null;
  onClose: () => void;
  onEdit: (visitorPass: VisitorPass) => void;
  onCancel: (visitorPass: VisitorPass) => Promise<VisitorPass | null>;
  onDelete: (visitorPass: VisitorPass) => Promise<boolean>;
}) {
  const modalRef = React.useRef<HTMLDivElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const onEdit = useModalClose(modalRef, finishEdit);
  const [activeTab, setActiveTab] = React.useState<"details" | "log">("details");
  const [logs, setLogs] = React.useState<VisitorPassLogEntry[]>([]);
  const [logsLoading, setLogsLoading] = React.useState(false);
  const [logsLoaded, setLogsLoaded] = React.useState(false);
  const [logsError, setLogsError] = React.useState("");
  const [action, setAction] = React.useState<"cancel" | "delete" | null>(null);
  const [confirmAction, setConfirmAction] = React.useState<"cancel" | "delete" | null>(null);
  const confirmationModalRef = React.useRef<HTMLDivElement>(null);
  const closeConfirmation = useModalClose(confirmationModalRef, () => setConfirmAction(null));
  useModalFocus(modalRef, true, () => { if (action === null) onClose(); });
  const logsLoadSequenceRef = React.useRef(0);
  const logsLoadAbortRef = React.useRef<AbortController | null>(null);
  const isDuration = visitorPass.pass_type === "duration";
  const canModify = visitorPass.status === "active" || visitorPass.status === "scheduled";
  const vehicleValue = [visitorPass.vehicle_colour, visitorPass.vehicle_make].filter(Boolean).join(" ") || visitorPass.number_plate || "Vehicle details pending";
  const vehicleDetail = visitorPass.number_plate ? `Registration ${visitorPass.number_plate}` : "Plate pending";
  const sourceLabel = visitorPassSourceLabel(visitorPass.creation_source);
  const visitDuration = visitorPassVisitDurationLabel(visitorPass);
  const passDuration = visitorPassPassDurationLabel(visitorPass);
  const visitDetail = visitorPass.departure_time
    ? `Left ${formatDate(visitorPass.departure_time)}`
    : visitorPass.arrival_time
      ? `Arrived ${formatDate(visitorPass.arrival_time)}`
      : passDuration
        ? "Pass window"
        : "No visit telemetry";
  const telemetryLinked = Boolean(visitorPass.arrival_event_id || visitorPass.departure_event_id);

  const loadLogs = React.useCallback(async (showLoading = false) => {
    const sequence = logsLoadSequenceRef.current + 1;
    logsLoadSequenceRef.current = sequence;
    logsLoadAbortRef.current?.abort();
    const controller = new AbortController();
    logsLoadAbortRef.current = controller;
    if (showLoading) setLogsLoading(true);
    setLogsError("");
    try {
      const rows = await api.get<VisitorPassLogEntry[]>(`/api/v1/visitor-passes/${visitorPass.id}/logs`, {
        signal: controller.signal
      });
      if (logsLoadSequenceRef.current !== sequence) return;
      setLogs((current) => visitorPassLogsEqual(current, rows) ? current : rows);
      setLogsLoaded(true);
    } catch (logError) {
      if (isAbortError(logError)) return;
      if (logsLoadSequenceRef.current !== sequence) return;
      setLogsError(logError instanceof Error ? logError.message : "Unable to load Visitor Pass log");
    } finally {
      if (logsLoadSequenceRef.current === sequence) {
        if (showLoading) setLogsLoading(false);
        if (logsLoadAbortRef.current === controller) {
          logsLoadAbortRef.current = null;
        }
      }
    }
  }, [visitorPass.id]);

  React.useEffect(() => {
    logsLoadSequenceRef.current += 1;
    logsLoadAbortRef.current?.abort();
    setActiveTab("details");
    setLogs([]);
    setLogsError("");
    setLogsLoaded(false);
  }, [visitorPass.id]);

  React.useEffect(() => () => {
    logsLoadSequenceRef.current += 1;
    logsLoadAbortRef.current?.abort();
  }, []);



  React.useEffect(() => {
    if (activeTab !== "log") return undefined;
    loadLogs(!logsLoaded).catch(() => undefined);
  }, [activeTab, loadLogs, logsLoaded, visitorPass.updated_at]);



  React.useEffect(() => {
    const latest = latestRealtime;
    if (!latest || activeTab !== "log") return;
    if (!isVisitorPassAuditLogEvent(latest, visitorPass.id)) return;
    loadLogs(false).catch(() => undefined);
  }, [activeTab, latestRealtime, loadLogs, visitorPass.id]);





  const confirmVisitorPassAction = async () => {
    if (!confirmAction || action !== null) return;
    setAction(confirmAction);
    try {
      if (confirmAction === "cancel") {
        await onCancel(visitorPass);
        await closeConfirmation();
        return;
      }
      const deleted = await onDelete(visitorPass);
      if (!deleted) {
        setAction(null);
        return;
      }
      await closeConfirmation();
      await onClose();
    } finally {
      if (confirmAction === "cancel") setAction(null);
    }
  };

  return (
    <>
      <div className="modal-backdrop" role="presentation">
        <div ref={modalRef} className="modal-card visitor-pass-detail-modal" role="dialog" aria-modal="true" aria-labelledby="visitor-pass-detail-title">
          <div className="modal-header visitor-pass-detail-header">
            <VisitorPassAvatar visitorPass={visitorPass} />
            <div className="visitor-pass-detail-title">
              <span>{visitorPass.pass_type === "duration" ? "Duration Pass" : "Visitor Pass"}</span>
              <h2 id="visitor-pass-detail-title">{visitorPass.visitor_name}</h2>
              <p><CalendarDays size={17} /> {formatDate(visitorPass.window_start)} to {formatDate(visitorPass.window_end)}</p>
            </div>
            <div className="visitor-pass-detail-header-actions">
              <VisitorPassStatusPill status={visitorPass.status} />

              <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="visitor-pass-detail-tabs" role="tablist" aria-label="Visitor Pass details">
            <button className={activeTab === "details" ? "active" : ""} onClick={() => setActiveTab("details")} type="button" role="tab" aria-selected={activeTab === "details"}>
              Details
            </button>

            <button className={activeTab === "log" ? "active" : ""} onClick={() => setActiveTab("log")} type="button" role="tab" aria-selected={activeTab === "log"}>
              Log
            </button>
          </div>


          {activeTab === "log" ? (
            <section className="visitor-pass-log-panel">
              {logsError ? <div className="auth-error">{logsError}</div> : null}
              {logsLoading && !logs.length ? (
                <div className="visitor-pass-thread-empty">
                  <Loader2 className="spin" size={17} /> Loading Visitor Pass log
                </div>
              ) : logs.length ? (
                <VisitorPassLogTimeline logs={logs} visitorPass={visitorPass} />
              ) : (
                <div className="visitor-pass-thread-empty">No changes have been logged for this pass yet</div>
              )}
            </section>
          ) : (
            <section className="visitor-pass-detail-body">
              <div className="visitor-pass-detail-window-card">
                <span className="visitor-pass-window-orb">
                  <Clock3 size={34} />
                </span>
                <div className="visitor-pass-window-times">
                  <span>Window</span>
                  <strong>{formatDate(visitorPass.window_start)}</strong>
                  <small>{visitorPassWindowLabel(visitorPass)}</small>
                </div>
                <ArrowRight className="visitor-pass-window-arrow" size={30} />
                <div className="visitor-pass-window-times">
                  <span>Until</span>
                  <strong>{formatDate(visitorPass.window_end)}</strong>
                  <small>{passDuration || "Window duration pending"}</small>
                </div>
                <div className="visitor-pass-window-source">
                  <span className="visitor-pass-window-source-icon"><UserRound size={20} /></span>
                  <div>
                    <span>Source</span>
                    <strong>{sourceLabel}</strong>
                    <small>{visitorPass.created_by ? `Created by ${visitorPass.created_by}` : formatDate(visitorPass.created_at)}</small>
                  </div>
                </div>
              </div>
              <div className="visitor-pass-detail-grid">
                <VisitorPassDetailTile
                  detail={vehicleDetail}
                  icon={Car}
                  label="Vehicle"
                  tone="green"
                  value={vehicleValue}
                />
                <VisitorPassDetailTile
                  detail={visitDetail}
                  icon={Clock3}
                  label="Duration"
                  tone="amber"
                  value={visitDuration || passDuration || "Not available"}
                />

                <VisitorPassDetailTile
                  detail={visitorPass.telemetry_trace_id || (telemetryLinked ? "Access events linked" : "No access events linked")}
                  icon={Activity}
                  label="Telemetry"
                  tone="purple"
                  value={visitorPass.telemetry_trace_id ? "Trace linked" : "No trace linked"}
                />
              </div>
            </section>
          )}

          <div className="modal-actions visitor-pass-detail-actions">
            <button className="secondary-button" onClick={onClose} disabled={action !== null} type="button">
              Close
            </button>
            <button className="secondary-button" onClick={() => onEdit(visitorPass)} disabled={!canModify || action !== null} type="button">
              <Pencil size={15} /> Edit
            </button>
            <button className="secondary-button danger" onClick={() => setConfirmAction("cancel")} disabled={!canModify || action !== null} type="button">
              <X size={15} /> {action === "cancel" ? "Cancelling..." : "Cancel pass"}
            </button>
            <button className="danger-button" onClick={() => setConfirmAction("delete")} disabled={action !== null} type="button">
              <Trash2 size={15} /> {action === "delete" ? "Deleting..." : "Delete pass"}
            </button>
          </div>
        </div>
      </div>
      {confirmAction ? (
        <VisitorPassActionConfirmModal
          action={confirmAction}
          loading={action === confirmAction}
          modalRef={confirmationModalRef}
          onCancel={() => setConfirmAction(null)}
          onConfirm={confirmVisitorPassAction}
          visitorPass={visitorPass}
        />
      ) : null}
    </>
  );
}

function VisitorPassActionConfirmModal({
  action,
  visitorPass,
  loading,
  modalRef: providedRef,
  onCancel: finishCancel,
  onConfirm
}: {
  action: "cancel" | "delete";
  visitorPass: VisitorPass;
  loading: boolean;
  modalRef?: React.RefObject<HTMLDivElement | null>;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const localRef = React.useRef<HTMLDivElement>(null);
  const modalRef = providedRef ?? localRef;
  const onCancel = useModalClose(modalRef, finishCancel);
  useModalFocus(modalRef, true, () => { if (!loading) onCancel(); });
  const isDelete = action === "delete";
  return createPortal(
    <div className="modal-backdrop stacked-modal" role="presentation">
      <div ref={modalRef} className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="visitor-pass-action-confirm-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className={`gate-confirm-icon ${isDelete ? "danger" : ""}`}>
              {isDelete ? <Trash2 size={20} /> : <X size={20} />}
            </span>
            <div>
              <h2 id="visitor-pass-action-confirm-title">
                {isDelete ? "Delete" : "Cancel"} Visitor Pass?
              </h2>
              <p>
                {isDelete
                  ? `This will permanently delete the pass for ${visitorPass.visitor_name}.`
                  : `This will cancel the active window for ${visitorPass.visitor_name}.`}
              </p>
            </div>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Keep pass
          </button>
          <button className={isDelete ? "danger-button" : "secondary-button danger"} disabled={loading} onClick={onConfirm} type="button">
            {isDelete ? <Trash2 size={15} /> : <X size={15} />}
            {loading ? (isDelete ? "Deleting..." : "Cancelling...") : isDelete ? "Delete pass" : "Cancel pass"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

function VisitorPassLogTimeline({ logs, visitorPass }: { logs: VisitorPassLogEntry[]; visitorPass: VisitorPass }) {
  return (
    <div className="visitor-pass-log-list">
      {logs.map((log) => {
        const details = visitorPassLogDetails(log, visitorPass);
        return (
          <article className="visitor-pass-log-entry" key={log.id}>
            <span className={`visitor-pass-log-dot ${details.tone}`} />
            <div>
              <div className="visitor-pass-log-head">
                <span className={`visitor-pass-log-icon ${details.tone}`}>
                  {React.createElement(visitorPassLogIcon(log.action), { size: 24 })}
                </span>
                <div>
                  <strong>{details.title}</strong>
                  <p>{details.description}</p>
                </div>
                <time><Clock3 size={15} /> {formatDate(log.timestamp)}</time>
              </div>
              {details.fields.length ? (
                <div className="visitor-pass-log-fields">
                  {details.fields.map((field) => (
                    <span key={`${log.id}-${field.label}`}>
                      <small>{field.label}</small>
                      <strong>{field.value}</strong>
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}
