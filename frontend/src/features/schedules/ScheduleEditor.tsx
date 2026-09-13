import { Car, Clock3, Save, UserRound, Warehouse, X } from "lucide-react";
import React from "react";
import type { ScheduleDependencies } from "../../api/schedules";
import { schedulesApi } from "../../api/schedules";
import type { Schedule } from "../../api/types";
import type { BadgeTone } from "../../ui/primitives";
import { Badge } from "../../ui/primitives";
import { emptyScheduleBlocks, normalizeScheduleBlocks, scheduleSummary } from "./model";
import { WeeklyScheduleGrid } from "./WeeklyScheduleGrid";

export function ScheduleEditor({
  mode,
  onClose,
  onSaved,
  schedule,
  setPageError
}: {
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  schedule: Schedule | null;
  setPageError: (message: string) => void;
}) {
  const [form, setForm] = React.useState({
    name: schedule?.name ?? "",
    description: schedule?.description ?? "",
    time_blocks: normalizeScheduleBlocks(schedule?.time_blocks ?? emptyScheduleBlocks())
  });
  const [dependencies, setDependencies] = React.useState<ScheduleDependencies | null>(null);
  const [dependenciesLoading, setDependenciesLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  React.useEffect(() => {
    const controller = new AbortController();
    setDependencies(null);
    setDependenciesLoading(Boolean(schedule));
    if (schedule) {
      schedulesApi.dependencies(schedule.id, { signal: controller.signal })
        .then((value) => { if (!controller.signal.aborted) setDependencies(value); })
        .catch(() => { if (!controller.signal.aborted) setDependencies(null); })
        .finally(() => { if (!controller.signal.aborted) setDependenciesLoading(false); });
    }
    return () => controller.abort();
  }, [schedule]);

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setPageError("");
    setSubmitting(true);
    const payload = {
      name: form.name,
      description: form.description || null,
      time_blocks: normalizeScheduleBlocks(form.time_blocks)
    };
    try {
      await schedulesApi.save(payload, mode === "edit" ? schedule : null);
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save schedule";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card schedule-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Schedule" : "New Schedule"}</h2>
            <p>{scheduleSummary(form.time_blocks)}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}

        <div className="schedule-modal-grid">
          <div className="schedule-details-panel">
            <label className="field">
              <span>Schedule name</span>
              <div className="field-control">
                <Clock3 size={17} />
                <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
              </div>
            </label>
            <label className="field">
              <span>Description</span>
              <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
            </label>
            <ScheduleDependencyPanel dependencies={dependencies} loading={dependenciesLoading} />
          </div>

          <WeeklyScheduleGrid
            value={form.time_blocks}
            onChange={(timeBlocks) => update("time_blocks", timeBlocks)}
          />
        </div>

        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            <Save size={16} />
            {submitting ? "Saving..." : mode === "edit" ? "Save Schedule" : "Create Schedule"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ScheduleDependencyPanel({
  dependencies,
  loading
}: {
  dependencies: ScheduleDependencies | null;
  loading: boolean;
}) {
  const items = dependencies ? [
    ...dependencies.people.map((item) => ({ ...item, tone: "blue" as BadgeTone })),
    ...dependencies.vehicles.map((item) => ({ ...item, tone: "green" as BadgeTone })),
    ...dependencies.doors.map((item) => ({ ...item, tone: "amber" as BadgeTone }))
  ] : [];

  return (
    <section className="schedule-dependencies">
      <div className="panel-header">
        <h2>In Use By</h2>
        <Badge tone={items.length ? "blue" : "gray"}>{loading ? "loading" : String(items.length)}</Badge>
      </div>
      {loading ? (
        <div className="schedule-dependency-empty">Loading dependencies</div>
      ) : items.length ? (
        <div className="schedule-dependency-list">
          {items.map((item) => (
            <span className={`schedule-dependency-pill ${item.tone}`} key={`${item.kind}-${item.id}`}>
              {dependencyIcon(item.kind)}
              <span>{item.name}</span>
            </span>
          ))}
        </div>
      ) : (
        <div className="schedule-dependency-empty">No assignments</div>
      )}
    </section>
  );
}

function dependencyIcon(kind: string) {
  if (kind === "vehicle") return <Car size={13} />;
  if (kind === "gate" || kind === "garage_door") return <Warehouse size={13} />;
  return <UserRound size={13} />;
}
