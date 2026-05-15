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

  React.useEffect(() => {
    void loadMovements();
  }, [loadMovements, refreshToken]);

  const visibleMovements = movements.filter((movement) => {
    const category = movementCategory(movement);
    const textMatches =
      matches(movement.registration_number || "", query) ||
      matches(movement.source, query) ||
      matches(movement.failure_detail || "", query);
    return (filter === "all" || filter === category) && textMatches;
  });

  const selectMovement = async (movement: MovementRecord) => {
    setSelected(movement);
    setActionError(null);
    try {
      const detail = await api.get<MovementRecord>(`/api/v1/access/movements/${movement.id}`);
      setSelected(detail);
    } catch (detailError) {
      setActionError(errorMessage(detailError));
    }
  };

  const requestReconciliation = async () => {
    if (!selected) return;
    setActionError(null);
    try {
      const updated = await api.post<MovementRecord>(`/api/v1/access/movements/${selected.id}/reconciliation-required`, {
        reason: "Operator requested reconciliation from Movement detail."
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

        <MovementDetail movement={selected} actionError={actionError} onRequestReconciliation={requestReconciliation} />
      </div>
    </section>
  );
}

function MovementDetail({
  movement,
  actionError,
  onRequestReconciliation
}: {
  movement: MovementRecord | null;
  actionError: string | null;
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
        <button className="secondary-button" type="button" onClick={() => void onRequestReconciliation()}>
          <ShieldAlert size={15} /> Mark Needs Reconciliation
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
