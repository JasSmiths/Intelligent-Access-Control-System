import type { ActivityEpisode, ActivityEpisodeDetail, InvestigationFilterCatalog, InvestigationOverview } from "./types";

export const SITE_TIMEZONE = "Europe/London";

export const scheduleBlockedEpisode: ActivityEpisode = {
  episode_id: "trace:arrival-2247",
  kind: "trace",
  trace_id: "arrival-2247",
  occurred_at: "2026-07-13T21:47:03.000Z",
  ended_at: "2026-07-13T21:47:03.180Z",
  duration_ms: 180,
  title: "Open on arrival was blocked",
  summary: "Arrival was detected, but the garage-door schedule had already ended.",
  outcome: "blocked",
  dispatch_state: "withheld",
  reason_code: "schedule_outside_allowed_window",
  severity: "warning",
  category: "automation",
  actor: "automation",
  source: "automation_service",
  correlation: { confidence: "exact", basis: "trace_id" },
  automation: { run_id: "run-arrival-2247", rule_id: "open-on-arrival", name: "Open main garage door on arrival", status: "skipped", trigger: "presence.arrival" },
  entities: [{ type: "garage_door", id: "main-garage", label: "Main garage door" }],
  evidence_count: 5,
  routine: false
};

export const integrationRejectedEpisode: ActivityEpisode = {
  ...scheduleBlockedEpisode,
  episode_id: "trace:integration-rejected",
  trace_id: "integration-rejected",
  occurred_at: "2026-07-13T18:05:00.000Z",
  title: "Garage command was rejected",
  summary: "Dispatch was attempted, but the Home Assistant integration rejected the request.",
  outcome: "failed",
  dispatch_state: "attempted_rejected",
  reason_code: "provider_rejected",
  automation: null
};

export const unverifiedEpisode: ActivityEpisode = {
  ...scheduleBlockedEpisode,
  episode_id: "trace:unverified",
  trace_id: "unverified",
  occurred_at: "2026-07-13T16:10:00.000Z",
  title: "Command accepted without a state confirmation",
  summary: "The integration accepted the command, but the expected open state was not recorded.",
  outcome: "pending",
  dispatch_state: "accepted_unverified",
  reason_code: "state_confirmation_missing",
  automation: null
};

export const successfulEpisode: ActivityEpisode = {
  ...scheduleBlockedEpisode,
  episode_id: "trace:successful",
  trace_id: "successful",
  occurred_at: "2026-07-13T08:00:00.000Z",
  title: "Arrival automation completed",
  summary: "All conditions passed and the device state was verified.",
  outcome: "succeeded",
  dispatch_state: "verified",
  reason_code: "completed"
};

export const skippedEpisode: ActivityEpisode = {
  ...scheduleBlockedEpisode,
  episode_id: "audit:skipped",
  kind: "audit",
  trace_id: null,
  occurred_at: "2026-07-13T07:00:00.000Z",
  title: "Duplicate trigger was skipped",
  summary: "A durable suppression record explains why this trigger was not evaluated again.",
  outcome: "skipped",
  dispatch_state: "not_applicable",
  correlation: { confidence: "none", basis: "standalone_audit" },
  automation: null
};

export const scheduleBlockedDetail: ActivityEpisodeDetail = {
  episode: scheduleBlockedEpisode,
  site_timezone: SITE_TIMEZONE,
  citations: [
    { id: "e-schedule", label: "Schedule condition failed", timestamp: "2026-07-13T21:47:03.120Z" }
  ],
  timeline: [
    {
      id: "e-presence",
      episode_id: scheduleBlockedEpisode.episode_id,
      timestamp: "2026-07-13T21:47:03.000Z",
      timestamp_precision: "millisecond",
      type: "trigger",
      title: "Presence changed to home",
      description: "A committed presence decision initiated the automation evaluation.",
      outcome: "succeeded",
      source: "presence_service",
      raw: { state: "home", access_token: "must-never-render" }
    },
    {
      id: "e-trigger",
      episode_id: scheduleBlockedEpisode.episode_id,
      timestamp: "2026-07-13T21:47:03.040Z",
      type: "automation_trigger",
      title: "Open main garage door on arrival was triggered",
      description: "The presence-arrival trigger matched this enabled rule.",
      outcome: "succeeded",
      source: "automation_service"
    },
    {
      id: "e-schedule",
      episode_id: scheduleBlockedEpisode.episode_id,
      timestamp: "2026-07-13T21:47:03.120Z",
      type: "condition",
      title: "Garage-door schedule condition failed",
      description: "Allowed window 06:00–22:30; evaluated local time 22:47.",
      outcome: "blocked",
      reason_code: "schedule_outside_allowed_window",
      source: "schedule_service",
      command_sent: false,
      raw: { evaluated_local_at: "2026-07-13T22:47:03+01:00", webhook_key: "redact-me" }
    },
    {
      id: "e-decision",
      episode_id: scheduleBlockedEpisode.episode_id,
      timestamp: "2026-07-13T21:47:03.150Z",
      type: "decision",
      title: "Action blocked",
      description: "IACS withheld the open action because the schedule condition failed.",
      outcome: "blocked",
      reason_code: "condition_failed",
      source: "automation_service",
      command_sent: false
    }
  ],
  configuration_context: [
    {
      type: "schedule",
      recorded_at_decision_time: true,
      label: "Garage door permitted schedule",
      value: { allowed_window: "06:00–22:30", evaluated_local_time: "22:47", timezone: SITE_TIMEZONE },
      warning: null
    }
  ],
  raw: { trace_id: "arrival-2247", password: "must-never-render", safe: "retained" }
};

export const filterCatalog: InvestigationFilterCatalog = {
  site_timezone: SITE_TIMEZONE,
  devices: [{ id: "main-garage", value: "main-garage", label: "Main garage door", kind: "garage_door" }],
  automations: [{ id: "open-on-arrival", value: "open-on-arrival", label: "Open main garage door on arrival" }],
  schedules: [{ id: "garage-hours", value: "garage-hours", label: "Garage door permitted schedule" }],
  integrations: [{ value: "home_assistant", label: "Home Assistant" }],
  categories: [{ value: "automation", label: "Automation" }],
  outcomes: ["succeeded", "blocked", "skipped", "failed", "pending", "cancelled", "unknown"].map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) })),
  severities: [{ value: "warning", label: "Warning" }, { value: "error", label: "Error" }],
  actors: [{ value: "automation", label: "Automation" }, { value: "jas", label: "jas" }],
  triggers: [{ value: "presence.arrival", label: "Presence arrival" }]
};

export const defaultOverview: InvestigationOverview = {
  site_timezone: SITE_TIMEZONE,
  resolved_range: { key: "last_24_hours", from: "2026-07-13T10:00:00Z", to: "2026-07-14T10:00:00Z" },
  recent_problems: [scheduleBlockedEpisode, integrationRejectedEpisode],
  incomplete_runs: [unverifiedEpisode],
  repeated_problems: [{ key: "provider_rejected", count: 4, title: "Home Assistant command rejection", reason_code: "provider_rejected", latest_at: integrationRejectedEpisode.occurred_at, episode_id: integrationRejectedEpisode.episode_id }],
  important_activity: [successfulEpisode]
};
