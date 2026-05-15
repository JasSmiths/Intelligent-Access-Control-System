import React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  MoveHorizontal,
  RefreshCw,
  ShieldAlert
} from "lucide-react";

import {
  api,
  Badge,
  BadgeTone,
  EmptyState,
  formatDate,
  matches,
  movementSagaDisplay,
  Toolbar,
  titleCase
} from "../shared";

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

type MovementFilter = "all" | "pending" | "confirmed" | "needs_reconciliation" | "failed" | "suppressed";

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
];

export function MovementsView({ query, refreshToken }: { query: string; refreshToken: number }) {
  const [movements, setMovements] = React.useState<MovementRecord[]>([]);
  const [selected, setSelected] = React.useState<MovementRecord | null>(null);
  const [filter, setFilter] = React.useState<MovementFilter>("all");
  const [loading, setLoading] = React.useState(true);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);

  const loadMovements = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await api.get<MovementRecord[]>("/api/v1/access/movements?limit=250");
      setMovements(rows);
      setSelected((current) => {
        if (!current) return rows[0] ?? null;
        return rows.find((row) => row.id === current.id) ?? rows[0] ?? null;
      });
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMovementDetail = React.useCallback(
    async (movement: MovementRecord, options: { optimistic?: boolean; quiet?: boolean } = {}) => {
      if (options.optimistic !== false) {
        setSelected(movement);
      }
      if (!options.quiet) {
        setActionError(null);
      }
      setDetailLoading(true);
      try {
        const detail = await api.get<MovementRecord>(`/api/v1/access/movements/${movement.id}`);
        setSelected((current) => (current?.id === movement.id ? detail : current));
        setMovements((current) => current.map((row) => (row.id === detail.id ? { ...row, ...detail } : row)));
      } catch (detailError) {
        if (!options.quiet) {
          setActionError(errorMessage(detailError));
        }
      } finally {
        setDetailLoading(false);
      }
    },
    []
  );

  React.useEffect(() => {
    void loadMovements();
  }, [loadMovements, refreshToken]);

  const selectedNeedsDetail = Boolean(selected && !hasMovementDetail(selected));

  React.useEffect(() => {
    if (!selected || !selectedNeedsDetail) return;
    void loadMovementDetail(selected, { optimistic: false, quiet: true });
  }, [loadMovementDetail, selected, selectedNeedsDetail]);

  const visibleMovements = movements.filter((movement) => {
    const category = movementCategory(movement);
    const textMatches =
      matches(movement.registration_number || "", query) ||
      matches(movement.source, query) ||
      matches(movement.failure_detail || "", query) ||
      matches(movementExplanationSearchText(movement), query);
    return (filter === "all" || filter === category) && textMatches;
  });

  const selectMovement = async (movement: MovementRecord) => {
    await loadMovementDetail(movement);
  };

  const requestReconciliation = async () => {
    if (!selected) return;
    setActionError(null);
    try {
      const updated = await api.post<MovementRecord>(`/api/v1/access/movements/${selected.id}/reconciliation-required`, {
        reason: "Operator flagged movement for review from Movement detail."
      });
      setSelected(updated);
      setMovements((current) => current.map((movement) => (movement.id === updated.id ? updated : movement)));
    } catch (requestError) {
      setActionError(errorMessage(requestError));
    }
  };

  return (
    <section className="view-stack movements-view">
      <Toolbar title="Movements" count={visibleMovements.length} icon={MoveHorizontal}>
        <button className="icon-button" type="button" onClick={loadMovements} disabled={loading} title="Refresh movements">
          <RefreshCw size={16} />
        </button>
      </Toolbar>

      <div className="movement-filters" role="tablist" aria-label="Movement status filters">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            className={filter === item.key ? "active" : ""}
            type="button"
            onClick={() => setFilter(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {error ? <div className="callout danger"><AlertTriangle size={16} /> {error}</div> : null}

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
                    onClick={() => void selectMovement(movement)}
                  >
                    <td>
                      <strong>{movement.registration_number || "Unknown"}</strong>
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
          {!loading && !visibleMovements.length ? <EmptyState icon={Clock3} label="No movements match this filter." /> : null}
        </div>

        <MovementDetail
          movement={selected}
          actionError={actionError}
          detailLoading={detailLoading}
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
  onRequestReconciliation
}: {
  movement: MovementRecord | null;
  actionError: string | null;
  detailLoading: boolean;
  onRequestReconciliation: () => Promise<void>;
}) {
  if (!movement) {
    return (
      <aside className="movement-detail-panel">
        <EmptyState icon={MoveHorizontal} label="Select a movement." />
      </aside>
    );
  }
  const display = movementSagaDisplay(movement);
  const explanations = movementExplanations(movement);
  const loadingDetailPayload = detailLoading && !hasMovementDetail(movement);
  return (
    <aside className="movement-detail-panel">
      <div className="movement-detail-head">
        <div>
          <span className="eyebrow">{movement.source}</span>
          <h2>{movement.registration_number || "Unknown plate"}</h2>
          <p>{formatDate(movement.occurred_at)}</p>
        </div>
        {display ? <Badge tone={display.tone}>{display.label}</Badge> : null}
      </div>

      <div className="movement-detail-actions">
        <button
          aria-label="Flag this movement for operator review. This does not change the gate, presence, or hardware state."
          className="secondary-button"
          title="Flags this movement for operator review. It does not change the gate, presence, or hardware state."
          type="button"
          onClick={() => void onRequestReconciliation()}
        >
          <ShieldAlert size={15} /> Flag for Review
        </button>
      </div>
      {actionError ? <div className="callout danger"><AlertTriangle size={16} /> {actionError}</div> : null}

      <div className="movement-detail-grid">
        <DetailTile label="Saga State" value={titleCase(movement.state)} tone={statusTone(movement)} />
        <DetailTile label="Direction" value={movement.direction || "--"} />
        <DetailTile label="Presence" value={movement.presence_committed ? "Committed" : "Pending"} />
        <DetailTile label="Gate" value={movement.gate_command_required ? "Required" : "Not Required"} />
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
        )) : <EmptyState icon={CheckCircle2} label="No gate command was required." />}
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

function movementCategory(movement: MovementRecord): MovementFilter {
  if (movement.reconciliation_required || movement.state === "reconciliation_required") return "needs_reconciliation";
  if (movement.state === "failed") return "failed";
  if (movement.state === "suppressed") return "suppressed";
  if (["observed", "direction_resolved", "physical_command_pending", "physical_command_accepted"].includes(movement.state)) return "pending";
  return "confirmed";
}

function hasMovementDetail(movement: MovementRecord): boolean {
  return Boolean(movement.intent_payload || movement.decision_payload || movement.state_history);
}

function movementExplanationSearchText(movement: MovementRecord): string {
  return movementExplanations(movement)
    .map((item) => `${item.label} ${item.value}`)
    .join(" ");
}

function movementExplanations(movement: MovementRecord): MovementExplanation[] {
  const decisionPayload = movement.decision_payload || {};
  const intentPayload = movement.intent_payload || {};
  const suppressionReason = payloadString(decisionPayload, "suppression_reason") || payloadString(intentPayload, "suppression_reason");
  const decisionSource = payloadString(decisionPayload, "source");
  const physicalAction = payloadString(decisionPayload, "physical_action");
  const payloadDirection = payloadString(decisionPayload, "direction");
  const direction = movement.direction || payloadDirection;
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

  if (movement.gate_command_required) {
    rows.push({
      label: "Gate",
      value: physicalAction === "gate.open"
        ? "A gate-open command was required for this granted entry."
        : "A physical gate command was required by the movement saga."
    });
  } else if (hardwareSuppressed) {
    rows.push({
      label: "Gate",
      value: "No gate command was sent because this movement was replayed or recovered with hardware side effects disabled."
    });
  } else if (movement.decision === "denied") {
    rows.push({ label: "Gate", value: "No gate command was sent because the access decision was denied." });
  } else if (direction === "exit") {
    rows.push({ label: "Gate", value: "No gate command was required because the movement was resolved as an exit." });
  } else {
    rows.push({ label: "Gate", value: "No gate command was required for this movement." });
  }

  if (movement.presence_committed) {
    rows.push({ label: "Presence", value: "Presence was committed after the movement lifecycle reached a confirmed state." });
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
