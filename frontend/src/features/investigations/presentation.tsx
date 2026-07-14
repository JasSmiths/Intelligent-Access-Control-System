import {
  AlertCircle,
  Ban,
  CheckCircle2,
  CircleDashed,
  Clock3,
  HelpCircle,
  MinusCircle,
  XCircle
} from "lucide-react";
import type React from "react";
import type { ActivityEpisode, InvestigationOutcome } from "./types";

const OUTCOMES: Record<InvestigationOutcome, { label: string; className: string; icon: React.ElementType }> = {
  succeeded: { label: "Succeeded", className: "success", icon: CheckCircle2 },
  blocked: { label: "Blocked", className: "blocked", icon: Ban },
  skipped: { label: "Skipped", className: "skipped", icon: MinusCircle },
  failed: { label: "Failed", className: "failed", icon: XCircle },
  pending: { label: "Pending", className: "pending", icon: Clock3 },
  cancelled: { label: "Cancelled", className: "cancelled", icon: AlertCircle },
  unknown: { label: "Incomplete", className: "unknown", icon: HelpCircle }
};

export function normalizeOutcome(value: string | null | undefined): InvestigationOutcome {
  const normalized = (value ?? "").trim().toLowerCase();
  if (["success", "successful", "ok", "completed", "verified", "succeeded"].includes(normalized)) return "succeeded";
  if (["blocked", "denied", "withheld", "rejected_by_policy"].includes(normalized)) return "blocked";
  if (["skipped", "suppressed", "ignored", "not_applicable"].includes(normalized)) return "skipped";
  if (["failure", "failed", "error", "rejected", "attempted_rejected"].includes(normalized)) return "failed";
  if (["pending", "running", "accepted", "accepted_unverified"].includes(normalized)) return "pending";
  if (["cancelled", "canceled"].includes(normalized)) return "cancelled";
  return "unknown";
}

export function OutcomeLabel({ outcome }: { outcome: string | null | undefined }) {
  const value = normalizeOutcome(outcome);
  const config = OUTCOMES[value];
  const Icon = config.icon;
  return (
    <span className={`investigation-outcome ${config.className}`} data-outcome={value}>
      <Icon aria-hidden="true" size={14} />
      {config.label}
    </span>
  );
}

export function episodeSubject(episode: ActivityEpisode) {
  const subject = episode.entities?.find((entity) => ["device", "gate", "garage_door", "person", "vehicle"].includes(entity.type));
  return subject?.label || episode.automation?.name || episode.source || "System activity";
}

export function dispatchStateExplanation(dispatchState: string | null | undefined) {
  switch (dispatchState) {
    case "withheld":
      return { label: "Command not sent", detail: "IACS decided not to send a device command." };
    case "attempted_rejected":
      return { label: "Dispatch attempted but not accepted", detail: "IACS attempted dispatch; the integration or device rejected it or was unavailable." };
    case "accepted_unverified":
      return { label: "Command accepted, state unverified", detail: "The integration accepted the command, but no matching device-state confirmation was recorded." };
    case "verified":
      return { label: "Command verified", detail: "The command was sent and a matching result was recorded." };
    case "not_applicable":
      return { label: "No device command expected", detail: "This activity did not require a device command." };
    default:
      return { label: "Command evidence incomplete", detail: "The available evidence does not establish whether a command was sent." };
  }
}

export function dispatchExplanation(episode: ActivityEpisode) {
  return dispatchStateExplanation(episode.dispatch_state);
}

export function formatInvestigationTime(value: string, timezone: string, includeDate = false) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.valueOf())) return value;
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone,
      ...(includeDate ? { day: "2-digit", month: "short" } : {}),
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    }).format(timestamp);
  } catch {
    return timestamp.toLocaleString();
  }
}

export function formatExactEvidenceTime(value: string, timezone: string) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.valueOf())) return `${value} (${timezone})`;
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone,
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short"
    }).format(timestamp);
  } catch {
    return `${timestamp.toISOString()} (${timezone})`;
  }
}

export function formatDuration(durationMs: number | null | undefined) {
  if (durationMs == null) return null;
  if (durationMs < 1_000) return `${Math.round(durationMs)} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1_000).toFixed(durationMs < 10_000 ? 1 : 0)} s`;
  return `${Math.round(durationMs / 60_000)} min`;
}

export function CorrelationNote({ episode }: { episode: ActivityEpisode }) {
  const confidence = episode.correlation?.confidence ?? episode.correlation_confidence;
  if (!confidence || confidence === "exact" || confidence === "strong") return null;
  return (
    <span className="investigation-correlation-note">
      <CircleDashed aria-hidden="true" size={13} />
      {confidence === "inferred" ? "Inferred association" : "Standalone event — not correlated to a trace"}
    </span>
  );
}
