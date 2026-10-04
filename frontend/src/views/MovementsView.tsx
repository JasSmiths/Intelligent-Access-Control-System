import {
AlertTriangle,
ArrowLeft,
ArrowRight,
CheckCircle2,
Clock3,
MoveHorizontal,
RefreshCw,
ShieldAlert
} from "lucide-react";
import React from "react";

import { api, isAbortError } from "../api/client";
import { formatDate, movementSagaDisplay, titleCase } from "../lib/format";
import { Badge, EmptyState, ErrorState, LoadingState, Toolbar } from "../ui/primitives";
import { useModalFocus } from "../ui/useModalFocus";
import type { BadgeTone } from "../ui/primitives";
import { useHistoryPage } from "./useHistoryPage";

type GateCommand = {
  id: string;
  state: string;
  source: string;
  gate_key: string;
  controller: string;
  reason: string;
  actor: string | null;
  registration_number: string | null;
  lease_expires_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  accepted: boolean | null;
  gate_state: string | null;
  detail: string | null;
  mechanically_confirmed: boolean;
  requires_reconciliation: boolean;
};

type MovementRecord = {
  id: string;
  source: string;
  state: string;
  access_event_id: string | null;
  registration_number: string | null;
  direction: string | null;
  decision: string | null;
  occurred_at: string;
  gate_command_required: boolean;
  presence_committed: boolean;
  reconciliation_required: boolean;
  failure_detail: string | null;
  updated_at: string;
  gate_commands: GateCommand[];
  intent_payload?: Record<string, unknown>;
  decision_payload?: Record<string, unknown>;
  state_history?: Array<Record<string, unknown>>;
};

type MovementFilter = "all" | "pending" | "confirmed" | "needs_reconciliation" | "failed" | "suppressed" | "unknown";

type MovementExplanation = {
  label: string;
  value: string;
};

const FILTERS: Array<{ key: MovementFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "confirmed", label: "Confirmed" },
  { key: "needs_reconciliation", label: "Needs Reconciliation" },
  { key: "failed", label: "Failed" },
  { key: "suppressed", label: "Suppressed" }
  ,{ key: "unknown", label: "Unknown" }
];

export function MovementsView({ refreshToken, resetToken, targetId }: { refreshToken: number; resetToken: number; targetId: string | null }) {
  const [query, setQuery] = React.useState("");
  const [from, setFrom] = React.useState("");
  const [to, setTo] = React.useState("");
  const [selected, setSelected] = React.useState<MovementRecord | null>(null);
  const [filter, setFilter] = React.useState<MovementFilter>("all");
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [actionLoading, setActionLoading] = React.useState(false);
  const detailLoadSequenceRef = React.useRef(0);
  const detailLoadAbortRef = React.useRef<AbortController | null>(null);
  const focusBeforeDetailRef = React.useRef<HTMLElement | null>(null);
  const [narrow, setNarrow] = React.useState(() => typeof window.matchMedia === "function" && window.matchMedia("(max-width: 980px)").matches);
  React.useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(max-width: 980px)");
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const deferredQuery = React.useDeferredValue(query);
  const filters = new URLSearchParams();
  if (deferredQuery.trim()) filters.set("q", deferredQuery.trim());
  if (from) filters.set("from", from);
  if (to) filters.set("to", to);
  if (filter !== "all") filters.set("category", filter);
  const history = useHistoryPage<MovementRecord>("/api/v1/access/movements/history", filters, refreshToken, resetToken);
  React.useEffect(() => {
    if (!targetId) setSelected(null);
  }, [targetId]);
  React.useEffect(() => {
    if (!targetId) return;
    if (history.loading) return;
    if (history.items.some((item) => item.id === targetId)) {
      const match = history.items.find((item) => item.id === targetId);
      if (match) setSelected(match);
      return;
    }
    let active = true;
    void api.get<MovementRecord>(`/api/v1/access/movements/${encodeURIComponent(targetId)}`)
      .then((item) => { if (active) setSelected(item); })
      .catch((error: unknown) => { if (active) setActionError(errorMessage(error)); });
    return () => { active = false; };
  }, [history.items, history.loading, targetId]);

  const loadMovementDetail = React.useCallback(
    async (movement: MovementRecord, options: { optimistic?: boolean; quiet?: boolean } = {}) => {
      const sequence = detailLoadSequenceRef.current + 1;
      detailLoadSequenceRef.current = sequence;
      detailLoadAbortRef.current?.abort();
      const controller = new AbortController();
      detailLoadAbortRef.current = controller;
      if (options.optimistic !== false) {
        setSelected(movement);
      }
      if (!options.quiet) {
        setActionError(null);
      }
      setDetailLoading(true);
      try {
        const detail = await api.get<MovementRecord>(`/api/v1/access/movements/${movement.id}`, {
          signal: controller.signal
        });
        if (detailLoadSequenceRef.current !== sequence) return;
        setSelected((current) => (current?.id === movement.id ? detail : current));
      } catch (detailError) {
        if (isAbortError(detailError)) return;
        if (detailLoadSequenceRef.current !== sequence) return;
        if (!options.quiet) {
          setActionError(errorMessage(detailError));
        }
      } finally {
        if (detailLoadSequenceRef.current === sequence) {
          setDetailLoading(false);
          if (detailLoadAbortRef.current === controller) {
            detailLoadAbortRef.current = null;
          }
        }
      }
    },
    []
  );

  React.useEffect(() => () => {
    detailLoadSequenceRef.current += 1;
    detailLoadAbortRef.current?.abort();
  }, []);

  const selectedNeedsDetail = Boolean(selected && !hasMovementDetail(selected));

  React.useEffect(() => {
    if (!selected || !selectedNeedsDetail) return;
    void loadMovementDetail(selected, { optimistic: false, quiet: true });
  }, [loadMovementDetail, selected, selectedNeedsDetail]);

  const visibleMovements = history.items;
  const firstItem = history.index * 50 + 1;
  const lastItem = history.index * 50 + visibleMovements.length;

  const selectMovement = async (movement: MovementRecord) => {
    focusBeforeDetailRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    await loadMovementDetail(movement);
  };

  const closeDetail = () => {
    setSelected(null);
    focusBeforeDetailRef.current?.focus();
  };

  const requestReconciliation = async () => {
    if (!selected || actionLoading) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await api.post<MovementRecord>(`/api/v1/access/movements/${selected.id}/reconciliation-required`, {
        reason: "Operator flagged movement for review from Movement detail."
      });
      setSelected(updated);
      history.refresh();
    } catch (requestError) {
      setActionError(errorMessage(requestError));
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <section className="view-stack movements-view">
      <Toolbar title="Movements" icon={MoveHorizontal}>
        <button className="icon-button" type="button" onClick={history.refresh} disabled={history.loading} aria-label="Refresh movements" title="Refresh movements">
          <RefreshCw size={16} />
        </button>
      </Toolbar>

      <div className="history-filters" aria-label="Movement filters">
        <label>Search movements<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Plate, source, or failure" /></label>
        <label>From<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
        <label>Before<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
        {(query || from || to || filter !== "all") ? <button className="secondary-button" type="button" onClick={() => { setQuery(""); setFrom(""); setTo(""); setFilter("all"); }}>Clear filters</button> : null}
        <p className="history-date-hint">Dates use the site timezone. Before excludes the selected date.</p>
      </div>
      <div className="movement-filters" aria-label="Movement status filters">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            className={filter === item.key ? "active" : ""}
            aria-pressed={filter === item.key}
            type="button"
            onClick={() => setFilter(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {history.newActivity ? <button className="history-new-activity" type="button" onClick={history.refresh}>New activity available. Return to latest.</button> : null}
      {history.error ? <ErrorState title="Movements unavailable" description={history.error} onRetry={history.refresh} retrying={history.loading} /> : null}
      {selected && !visibleMovements.some((item) => item.id === selected.id) ? <div className="callout">Showing the selected movement from outside this page.</div> : null}

      <div className="movement-layout">
        <div className="table-card movement-table-card">
          <table>
            <thead>
              <tr>
                <th>Plate</th>
                <th>Status</th>
                <th>Direction</th>
                <th>Decision</th>
                <th>When</th>
                <th>Commands</th>
              </tr>
            </thead>
            <tbody>
              {visibleMovements.map((movement) => {
                const display = movementSagaDisplay(movement);
                return (
                  <tr
                    key={movement.id}
                    className={selected?.id === movement.id ? "selected" : ""}
                  >
                    <td>
                      <button className="movement-row-button" type="button" onClick={() => void selectMovement(movement)} aria-label={`View movement ${movement.registration_number || "unknown plate"} at ${formatDate(movement.occurred_at)}`}><strong>{movement.registration_number || "Unknown"}</strong></button>
                      <span className="table-muted-line">{movement.source}</span>
                    </td>
                    <td>{display ? <Badge tone={display.tone}>{display.label}</Badge> : <Badge tone="gray">{titleCase(movement.state)}</Badge>}</td>
                    <td>{movement.direction || "--"}</td>
                    <td>{movement.decision ? <Badge tone={movement.decision === "granted" ? "green" : "red"}>{movement.decision}</Badge> : "--"}</td>
                    <td>{formatDate(movement.occurred_at)}</td>
                    <td>{movement.gate_commands.length}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {visibleMovements.length || history.index ? (
            <div
              className="top-charts-pagination"
              style={{ padding: "8px 12px", borderTop: "1px solid var(--line)" }}
              aria-label="Movements pagination"
            >
              <span>{visibleMovements.length ? `${firstItem}-${lastItem}` : "0"} records on this page{history.nextCursor ? "; more available" : ""}</span>
              <div className="top-charts-pagination-controls">
                <button
                  aria-label="Previous page"
                  className="icon-button top-charts-page-button"
                  disabled={history.index === 0 || history.loading}
                  onClick={history.previous}
                  type="button"
                >
                  <ArrowLeft size={15} />
                </button>
                <span>Page {history.index + 1}</span>
                <button
                  aria-label="Next page"
                  className="icon-button top-charts-page-button"
                  disabled={!history.nextCursor || history.loading}
                  onClick={history.next}
                  type="button"
                >
                  <ArrowRight size={15} />
                </button>
              </div>
            </div>
          ) : null}
          {history.loading ? <LoadingState label="Loading movements…" compact={visibleMovements.length > 0} /> : null}
          {!history.loading && !history.error && !visibleMovements.length ? <EmptyState icon={Clock3} label={query || from || to || filter !== "all" ? "No movements match these filters." : "No movements recorded yet."} description={query || from || to || filter !== "all" ? "Try a wider date range or clear the filters to see more activity." : "Movement decisions and command outcomes will appear here as vehicles arrive and leave."} /> : null}
        </div>

        <MovementDetail
          movement={selected}
          actionError={actionError}
          detailLoading={detailLoading}
          actionLoading={actionLoading}
          onClose={closeDetail}
          narrow={narrow}
          onRequestReconciliation={requestReconciliation}
        />
      </div>
    </section>
  );
}

function MovementDetail({
  movement,
  actionError,
  detailLoading,
  actionLoading,
  onClose,
  narrow,
  onRequestReconciliation
}: {
  movement: MovementRecord | null;
  actionError: string | null;
  detailLoading: boolean;
  actionLoading: boolean;
  onClose: () => void;
  narrow: boolean;
  onRequestReconciliation: () => Promise<void>;
}) {
  const detailRef = React.useRef<HTMLElement>(null);
  useModalFocus(detailRef, narrow && Boolean(movement), onClose);
  if (!movement) {
    return (
      <aside className="movement-detail-panel movement-detail-empty">
        <EmptyState icon={MoveHorizontal} label="Select a movement." description="Choose a plate to inspect its access decision, evidence, and command history." />
      </aside>
    );
  }
  const display = movementSagaDisplay(movement);
  const explanations = movementExplanations(movement);
  const loadingDetailPayload = detailLoading && !hasMovementDetail(movement);
  return (
    <aside ref={detailRef} className="movement-detail-panel" role={narrow ? "dialog" : undefined} aria-modal={narrow ? true : undefined} aria-label="Movement details">
      <div className="movement-detail-head">
        <div>
          <span className="eyebrow">{movement.source}</span>
          <h2>{movement.registration_number || "Unknown plate"}</h2>
          <p>{formatDate(movement.occurred_at)}</p>
        </div>
        {display ? <Badge tone={display.tone}>{display.label}</Badge> : null}
        <button className="movement-detail-close" type="button" onClick={onClose} aria-label="Close movement details">Close</button>
      </div>

      <div className="movement-detail-actions">
        <button
          aria-label="Flag this movement for operator review. This does not change the gate, presence, or hardware state."
          className="secondary-button"
          title="Flags this movement for operator review. It does not change the gate, presence, or hardware state."
          type="button"
          disabled={actionLoading}
          onClick={() => void onRequestReconciliation()}
        >
          <ShieldAlert size={15} /> {actionLoading ? "Flagging…" : "Flag for Review"}
        </button>
      </div>
      {actionError ? <div className="callout danger"><AlertTriangle size={16} /> {actionError}</div> : null}

      <div className="movement-detail-grid">
        <DetailTile label="Saga State" value={titleCase(movement.state)} tone={statusTone(movement)} />
        <DetailTile label="Direction" value={movement.direction || "--"} />
        <DetailTile label="Presence" value={movement.presence_committed ? "Committed" : "Pending"} />
        <DetailTile label="Gate" value={movement.gate_command_required === true ? "Required" : movement.gate_command_required === false ? "Not Required" : "Unknown"} />
      </div>

      {movement.failure_detail ? (
        <div className="movement-failure">
          <AlertTriangle size={16} />
          <span>{movement.failure_detail}</span>
        </div>
      ) : null}

      <section className="movement-detail-section movement-why-section">
        <h3>Why</h3>
        {loadingDetailPayload ? <div className="movement-detail-loading">Loading recorded decision details...</div> : null}
        <div className="movement-explanation-list">
          {explanations.map((item) => (
            <div className="movement-explanation-row" key={item.label}>
              <span>{item.label}</span>
              <p>{item.value}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="movement-detail-section">
        <h3>Gate Commands</h3>
        {movement.gate_commands.length ? movement.gate_commands.map((command) => (
          <div className="movement-command-row" key={command.id}>
            <div>
              <strong>{titleCase(command.state)}</strong>
              <span>{command.reason}</span>
            </div>
            <Badge tone={commandTone(command)}>{command.gate_state || "unknown"}</Badge>
          </div>
        )) : <EmptyState icon={movement.gate_command_required ? AlertTriangle : CheckCircle2} label={movement.gate_command_required ? "A gate command was required, but no command record is available." : movement.gate_command_required === false ? "No gate command was required." : "Gate command requirement is unknown."} />}
      </section>

      <section className="movement-detail-section">
        <h3>State History</h3>
        <div className="movement-history">
          {(movement.state_history || []).map((item, index) => (
            <div key={`${item.state}-${index}`}>
              <strong>{titleCase(String(item.state || ""))}</strong>
              <span>{String(item.detail || "")}</span>
            </div>
          ))}
          {!movement.state_history?.length ? <span className="table-muted-line">No detailed history loaded.</span> : null}
        </div>
      </section>
    </aside>
  );
}

function DetailTile({ label, value, tone = "gray" }: { label: string; value: string; tone?: BadgeTone }) {
  return (
    <div className={`movement-detail-tile ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function hasMovementDetail(movement: MovementRecord): boolean {
  return Boolean(movement.intent_payload || movement.decision_payload || movement.state_history);
}

function movementExplanations(movement: MovementRecord): MovementExplanation[] {
  const decisionPayload = movement.decision_payload || {};
  const intentPayload = movement.intent_payload || {};
  const suppressionReason = payloadString(decisionPayload, "suppression_reason") || payloadString(intentPayload, "suppression_reason");
  const decisionSource = payloadString(decisionPayload, "source");
  const physicalAction = payloadString(decisionPayload, "physical_action");
  const payloadDirection = payloadString(decisionPayload, "direction");
  const direction = movement.direction || payloadDirection;
  const externalAdmission =
    payloadRecord(decisionPayload, "external_admission") || payloadRecord(intentPayload, "external_admission");
  const externalAdmissionMode = payloadString(externalAdmission || {}, "mode");
  const externalAdmissionSource = payloadString(externalAdmission || {}, "source");
  const hardwareSuppressed =
    payloadBoolean(decisionPayload, "hardware_actions_suppressed") || payloadBoolean(intentPayload, "hardware_side_effects_enabled") === false;

  if (movement.state === "suppressed") {
    const rows: MovementExplanation[] = [
      {
        label: "Suppressed Because",
        value: suppressionReasonDescription(suppressionReason)
      },
      {
        label: "Gate",
        value: "No gate command was sent because this was classified as duplicate/session evidence, not a new authorised movement."
      },
      {
        label: "Presence",
        value: "Presence was left unchanged because no new entry or exit was confirmed."
      }
    ];
    if (suppressionReason) {
      rows.push({ label: "Recorded Rule", value: suppressionReason });
    }
    return rows;
  }

  const rows: MovementExplanation[] = [];
  if (externalAdmissionMode === "arrival") {
    rows.push({
      label: "External Admission",
      value: "An unknown vehicle was denied while the gate was closed, then the gate opened outside IACS while Protect still showed the vehicle present."
    });
  } else if (externalAdmissionMode === "departure") {
    rows.push({
      label: "External Departure",
      value: "This exit was linked to the active externally admitted vehicle session, so it did not create another unauthorised-plate alert."
    });
  }
  if (externalAdmissionSource) {
    rows.push({ label: "External Source", value: titleCase(externalAdmissionSource) });
  }

  if (decisionSource) {
    rows.push({ label: "Decision Source", value: decisionSourceDescription(decisionSource) });
  } else if (movement.decision) {
    rows.push({ label: "Decision Source", value: `Authorisation was recorded as ${titleCase(movement.decision)}.` });
  } else {
    rows.push({ label: "Decision Source", value: "No detailed decision source was recorded for this movement." });
  }

  if (direction === "entry") {
    rows.push({ label: "Direction", value: "Resolved as IN." });
  } else if (direction === "exit") {
    rows.push({ label: "Direction", value: "Resolved as OUT." });
  } else if (direction === "denied") {
    rows.push({ label: "Direction", value: "No physical direction was committed because access was denied." });
  } else {
    rows.push({ label: "Direction", value: "No direction was recorded for this movement." });
  }

  if (movement.gate_command_required === true) {
    rows.push({
      label: "Gate",
      value: !movement.gate_commands.length
        ? "A gate command was required, but no command record is available."
        : physicalAction === "gate.open"
        ? "A gate-open command was required for this granted entry."
        : "A physical gate command was required by the movement saga."
    });
  } else if (movement.gate_command_required == null) {
    rows.push({ label: "Gate", value: "The recorded movement does not establish whether a gate command was required." });
  } else if (hardwareSuppressed) {
    rows.push({
      label: "Gate",
      value: externalAdmissionMode === "arrival"
        ? "No gate command was sent by IACS; the gate was already opening or open from an external action."
        : "No gate command was sent because this movement was replayed or recovered with hardware side effects disabled."
    });
  } else if (movement.decision === "denied") {
    rows.push({ label: "Gate", value: "No gate command was sent because the access decision was denied." });
  } else if (direction === "exit") {
    rows.push({ label: "Gate", value: "No gate command was required because the movement was resolved as an exit." });
  } else {
    rows.push({ label: "Gate", value: "No gate command was required for this movement." });
  }

  if (movement.presence_committed) {
    rows.push({ label: "Presence", value: "A presence update is recorded as committed for this movement." });
  } else if (externalAdmissionMode) {
    rows.push({ label: "Presence", value: "No person presence was changed; this unknown plate is tracked through its active vehicle movement session." });
  } else if (movement.reconciliation_required) {
    rows.push({ label: "Presence", value: "Presence is pending because this movement needs reconciliation." });
  } else if (movement.state === "failed") {
    rows.push({ label: "Presence", value: "Presence was not committed because the movement failed." });
  } else {
    rows.push({ label: "Presence", value: "Presence has not been committed yet." });
  }

  if (movement.failure_detail) {
    rows.push({ label: "Failure Detail", value: movement.failure_detail });
  }
  return rows;
}

function suppressionReasonDescription(reason: string | null): string {
  switch (reason) {
    case "exact_known_vehicle_plate_already_resolved_in_debounce_window":
      return "The same known plate had already been resolved inside the debounce window, so this read was treated as a trailing camera echo.";
    case "exact_known_vehicle_plate_already_resolved_in_gate_cycle":
      return "The same known plate had already been resolved during the current gate cycle, so this read was treated as duplicate gate-cycle evidence.";
    case "visitor_pass_plate_already_resolved_in_debounce_window":
      return "A visitor-pass plate had already been resolved inside the debounce window, so this read was treated as duplicate visitor evidence.";
    case "vehicle_session_already_active":
      return "The plate or camera evidence matched an active movement session, so this read was folded into that existing movement instead of creating another one.";
    default:
      return reason ? `Suppressed by ${titleCase(reason)}.` : "The movement was suppressed, but the detailed suppression rule was not recorded.";
  }
}

function decisionSourceDescription(source: string): string {
  switch (source) {
    case "access_denied":
      return "Access was denied, so no physical movement was authorised.";
    case "camera_tiebreaker":
      return "Camera evidence was used to break a presence/gate-state tie.";
    case "default_entry_no_person":
      return "No known person was matched, so the movement defaulted to an entry classification for auditing.";
    case "external_gate_open":
      return "The gate opened outside IACS while Protect still showed the unknown vehicle at the gate.";
    case "external_vehicle_session":
      return "An active externally admitted unknown-vehicle session was used to resolve this movement.";
    case "gate_malfunction_vehicle_history":
      return "Gate malfunction handling used the previous vehicle movement history to infer direction.";
    case "gate_state":
      return "The captured gate state was used as the direction source.";
    case "payload":
      return "The source payload supplied the direction.";
    case "presence":
      return "The current presence record was used to infer direction.";
    case "presence_over_gate_state":
      return "Presence evidence overrode the captured open-gate state.";
    case "visitor_pass_presence":
      return "Visitor-pass departure state was used to resolve the movement as an exit.";
    default:
      return `Resolved by ${titleCase(source)}.`;
  }
}

function payloadString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function payloadBoolean(payload: Record<string, unknown>, key: string): boolean | null {
  const value = payload[key];
  return typeof value === "boolean" ? value : null;
}

function payloadRecord(payload: Record<string, unknown>, key: string): Record<string, unknown> | null {
  const value = payload[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function statusTone(movement: MovementRecord): BadgeTone {
  const display = movementSagaDisplay(movement);
  return display?.tone || "gray";
}

function commandTone(command: GateCommand): BadgeTone {
  if (command.requires_reconciliation || command.state === "reconciliation_required") return "amber";
  if (["failed", "rejected"].includes(command.state)) return "red";
  if (["accepted", "reconciled"].includes(command.state)) return "green";
  if (command.state === "leased") return "blue";
  return "gray";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed";
}
