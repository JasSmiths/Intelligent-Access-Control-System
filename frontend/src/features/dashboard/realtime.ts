import type { AccessEvent, MovementSagaSummary, RealtimeMessage } from "../../api/types";
import { isRecord, numberPayload, stringPayload } from "../../lib/format";
export function accessEventFromRealtime(event: RealtimeMessage): AccessEvent | null {
  if (event.type !== "access_event.finalized") return null;
  const eventId = stringPayload(event.payload.event_id);
  const registrationNumber = stringPayload(event.payload.registration_number);
  const direction = stringPayload(event.payload.direction);
  const decision = stringPayload(event.payload.decision);
  const source = stringPayload(event.payload.source);
  const occurredAt = stringPayload(event.payload.occurred_at);
  const timingClassification = stringPayload(event.payload.timing_classification);
  if (!eventId || !registrationNumber || !isAccessDirection(direction) || !isAccessDecision(decision) || !source || !occurredAt) {
    return null;
  }
  return {
    id: eventId,
    registration_number: registrationNumber,
    direction,
    decision,
    confidence: numberPayload(event.payload.confidence),
    source,
    occurred_at: occurredAt,
    timing_classification: timingClassification || "unknown",
    anomaly_count: numberPayload(event.payload.anomaly_count),
    visitor_pass_id: stringPayload(event.payload.visitor_pass_id) || null,
    visitor_name: stringPayload(event.payload.visitor_name) || null,
    visitor_pass_mode: stringPayload(event.payload.visitor_pass_mode) || null,
    external_admission_mode: externalAdmissionMode(event.payload.external_admission_mode),
    external_admission_source: stringPayload(event.payload.external_admission_source) || null,
    snapshot_url: stringPayload(event.payload.snapshot_url) || null,
    snapshot_captured_at: stringPayload(event.payload.snapshot_captured_at) || null,
    snapshot_bytes: nullableNumber(event.payload.snapshot_bytes),
    snapshot_width: nullableNumber(event.payload.snapshot_width),
    snapshot_height: nullableNumber(event.payload.snapshot_height),
    snapshot_camera: stringPayload(event.payload.snapshot_camera) || null,
    movement_saga: movementSagaFromPayload(event.payload.movement_saga)
  };
}
function externalAdmissionMode(value: unknown): "arrival" | "departure" | null {
  const mode = stringPayload(value);
  return mode === "arrival" || mode === "departure" ? mode : null;
}
function movementSagaFromPayload(value: unknown): MovementSagaSummary | null {
  if (!isRecord(value)) return null;
  const state = stringPayload(value.state);
  if (!state) return null;
  const gate = isRecord(value.gate) ? value.gate : null;
  return {
    id: stringPayload(value.id) || null,
    state,
    reconciliation_required: value.reconciliation_required === true,
    gate_command_required: value.gate_command_required === true,
    presence_committed: value.presence_committed === true,
    failure_detail: stringPayload(value.failure_detail) || null,
    updated_at: stringPayload(value.updated_at) || null,
    detail: stringPayload(value.detail) || null,
    gate: gate ? {
      command_id: stringPayload(gate.command_id) || null,
      accepted: typeof gate.accepted === "boolean" ? gate.accepted : null,
      state: stringPayload(gate.state) || null,
      detail: stringPayload(gate.detail) || null,
      mechanically_confirmed: gate.mechanically_confirmed === true,
      requires_reconciliation: gate.requires_reconciliation === true
    } : null
  };
}

function isAccessDirection(value: string): value is AccessEvent["direction"] {
  return ["entry", "exit", "denied"].includes(value);
}
function isAccessDecision(value: string): value is AccessEvent["decision"] {
  return ["granted", "denied"].includes(value);
}
function nullableNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
