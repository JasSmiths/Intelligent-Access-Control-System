import { CalendarDays, CheckCircle2, Clock3, Lock, Plus, ShieldCheck, Trash2 } from "lucide-react";
import React from "react";
import { createActionConfirmation } from "../../api/client";
import { schedulesApi } from "../../api/schedules";
import type { Schedule } from "../../api/types";
import { matches, scheduleDays } from "../../lib/format";
import { useSettings } from "../../lib/settings";
import { Badge, EmptyState } from "../../ui/primitives";
import { scheduleDayHasBlocks, scheduleHasBlocks, scheduleSummary } from "./model";
import { ScheduleEditor } from "./ScheduleEditor";

export function SchedulesView({
  schedules,
  query,
  refreshToken,
  refresh
}: {
  schedules: Schedule[];
  query: string;
  refreshToken: number;
  refresh: () => Promise<void>;
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedSchedule, setSelectedSchedule] = React.useState<Schedule | null>(null);
  const [error, setError] = React.useState("");
  const [policySaved, setPolicySaved] = React.useState("");
  const [policySaving, setPolicySaving] = React.useState(false);
  const accessSettings = useSettings("access", refreshToken);
  const defaultPolicy = String(accessSettings.values.schedule_default_policy ?? "allow").toLowerCase() === "deny" ? "deny" : "allow";
  const filtered = schedules.filter((schedule) =>
    matches(schedule.name, query) ||
    matches(schedule.description ?? "", query) ||
    matches(scheduleSummary(schedule.time_blocks), query)
  );

  React.useEffect(() => {
    if (!policySaved) return undefined;
    const timer = window.setTimeout(() => setPolicySaved(""), 5200);
    return () => window.clearTimeout(timer);
  }, [policySaved]);

  const openCreate = () => {
    setSelectedSchedule(null);
    setModalOpen(true);
  };

  const openEdit = (schedule: Schedule) => {
    setSelectedSchedule(schedule);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedSchedule(null);
  };

  const deleteSchedule = async (schedule: Schedule) => {
    if (!window.confirm(`Delete ${schedule.name}?`)) return;
    setError("");
    try {
      await schedulesApi.delete(schedule);
      await refresh();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete schedule");
    }
  };

  const updateDefaultPolicy = async (policy: "allow" | "deny") => {
    if (policy === defaultPolicy || policySaving) return;
    setError("");
    setPolicySaved("");
    setPolicySaving(true);
    try {
      const values = { schedule_default_policy: policy };
      const confirmation = await createActionConfirmation("settings.update", { values }, {
        target_entity: "SystemSetting",
        target_label: "Schedule default policy",
        reason: "Update default access schedule policy"
      });
      await accessSettings.save(values, { confirmationToken: confirmation.confirmation_token });
      setPolicySaved("Default policy saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save default policy");
    } finally {
      setPolicySaving(false);
    }
  };

  return (
    <section className="view-stack schedules-page">
      <div className="users-hero schedules-hero card">
        <div className="schedules-hero-main">
          <div>
            <span className="eyebrow">Access Control</span>
            <h1>Schedules</h1>
            <p>Reusable weekly access templates for people, vehicles, gates, and garage doors.</p>
          </div>
          <button className="primary-button" onClick={openCreate} type="button">
            <Plus size={17} /> New Schedule
          </button>
        </div>
        <section className="schedule-policy-card" aria-labelledby="schedule-default-policy-title">
          <div className="schedule-policy-copy">
            <div className="schedule-card-icon">
              <ShieldCheck size={18} />
            </div>
            <div>
              <h2 id="schedule-default-policy-title">Default Policy</h2>
              <p>Used when a person, vehicle, gate, or garage door has no schedule assigned.</p>
            </div>
          </div>
          <div className="schedule-policy-actions" role="group" aria-label="No schedule default policy">
            <button
              aria-label="Always Allow"
              aria-pressed={defaultPolicy === "allow"}
              className={defaultPolicy === "allow" ? "schedule-policy-option active allow" : "schedule-policy-option allow"}
              disabled={accessSettings.loading || policySaving}
              onClick={() => updateDefaultPolicy("allow")}
              type="button"
            >
              <CheckCircle2 size={16} />
              <span className="policy-label-full">Always Allow</span>
              <span className="policy-label-short">Allow</span>
            </button>
            <button
              aria-label="Never Allow"
              aria-pressed={defaultPolicy === "deny"}
              className={defaultPolicy === "deny" ? "schedule-policy-option active deny" : "schedule-policy-option deny"}
              disabled={accessSettings.loading || policySaving}
              onClick={() => updateDefaultPolicy("deny")}
              type="button"
            >
              <Lock size={16} />
              <span className="policy-label-full">Never Allow</span>
              <span className="policy-label-short">Deny</span>
            </button>
          </div>
          <div className="schedule-policy-status">
            {policySaving ? (
              <Badge tone="gray">Saving</Badge>
            ) : policySaved ? (
              <span className="schedule-policy-saved-pill">
                <Badge tone="green">Saved</Badge>
              </span>
            ) : null}
          </div>
        </section>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {accessSettings.error ? <div className="auth-error inline-error">{accessSettings.error}</div> : null}

      <div className="schedule-card-grid">
        {filtered.length ? filtered.map((schedule) => (
          <article className="card schedule-card" key={schedule.id}>
            <button className="schedule-card-main" onClick={() => openEdit(schedule)} type="button">
              <div className="schedule-card-icon">
                <Clock3 size={18} />
              </div>
              <div className="schedule-card-copy">
                <strong>{schedule.name}</strong>
                <span>{schedule.description || scheduleSummary(schedule.time_blocks)}</span>
              </div>
              <Badge tone={scheduleHasBlocks(schedule.time_blocks) ? "green" : "amber"}>
                {scheduleSummary(schedule.time_blocks)}
              </Badge>
            </button>
            <div className="schedule-card-days" aria-hidden="true">
              {scheduleDays.map((day, index) => (
                <span
                  className={scheduleDayHasBlocks(schedule.time_blocks, index) ? "active" : ""}
                  key={day}
                >
                  {day.slice(0, 1)}
                </span>
              ))}
            </div>
            <div className="schedule-card-actions">
              <button className="secondary-button" onClick={() => openEdit(schedule)} type="button">
                <CalendarDays size={15} /> Edit
              </button>
              <button className="icon-button danger" onClick={() => deleteSchedule(schedule)} type="button" aria-label={`Delete ${schedule.name}`}>
                <Trash2 size={15} />
              </button>
            </div>
          </article>
        )) : (
          <div className="card schedule-empty-card">
            <EmptyState icon={Clock3} label="No schedules match this view" />
          </div>
        )}
      </div>

      {modalOpen ? (
        <ScheduleEditor
          mode={selectedSchedule ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          schedule={selectedSchedule}
          setPageError={setError}
        />
      ) : null}
    </section>
  );
}
