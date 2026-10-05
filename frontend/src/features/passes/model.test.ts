import { expect, it } from "vitest";
import { visitorPassLogDetails } from "./model";
import type { VisitorPass, VisitorPassLogEntry } from "./types";

const visitorPass: VisitorPass = {
  id: "manual-pass", visitor_name: "Visitor", pass_type: "duration", visitor_phone: null,
  expected_time: "2026-10-05T10:00:00Z", window_minutes: 30, valid_from: "2026-10-05T10:00:00Z",
  valid_until: "2026-10-05T12:00:00Z", window_start: "2026-10-05T10:00:00Z", window_end: "2026-10-05T12:00:00Z",
  status: "active", creation_source: "manual", source_reference: null, created_by_user_id: null, created_by: null,
  arrival_time: null, departure_time: null, number_plate: "AB12CDE", vehicle_make: null, vehicle_colour: null,
  duration_on_site_seconds: null, duration_human: null, arrival_event_id: null, departure_event_id: null,
  telemetry_trace_id: null, created_at: "2026-10-05T10:00:00Z", updated_at: "2026-10-05T10:00:00Z"
};

function auditLog(overrides: Partial<VisitorPassLogEntry>): VisitorPassLogEntry {
  return {
    id: "pass-audit", timestamp: "2026-10-05T10:00:00Z", category: "visitor_pass",
    action: "visitor_pass.update", actor: "User", actor_user_id: null, actor_user_label: null,
    target_entity: "visitor_pass", target_id: visitorPass.id, target_label: visitorPass.visitor_name,
    diff: {}, metadata: {}, outcome: "success", level: "info", trace_id: null, request_id: null,
    ...overrides
  };
}

it("keeps an unknown historical service actor and its recorded changes readable", () => {
  const diff = {
    old: { number_plate: "AB12CDE", status: "scheduled" },
    new: { number_plate: "XY34ZZZ", status: "active" }
  };
  const log = auditLog({ action: "visitor_pass.legacy_import", actor: "Imported Service", diff });

  const details = visitorPassLogDetails(log, visitorPass);

  expect(details.description).toBe("Imported Service changed this Visitor Pass.");
  expect(details.fields).toEqual([
    { label: "Status", value: "Scheduled -> Active" },
    { label: "Registration", value: "AB12CDE -> XY34ZZZ" }
  ]);
  expect(log.diff).toEqual(diff);
});

it("uses the recorded current user label for a pass update", () => {
  const log = auditLog({
    actor_user_id: "operator-id", actor_user_label: "Alex Operator",
    diff: { old: { number_plate: "AB12CDE" }, new: { number_plate: "XY34ZZZ" } }
  });

  expect(visitorPassLogDetails(log, visitorPass)).toMatchObject({
    title: "Pass Updated",
    description: "Alex Operator updated Visitor's Visitor Pass.",
    fields: [{ label: "Registration", value: "AB12CDE -> XY34ZZZ" }]
  });
});
