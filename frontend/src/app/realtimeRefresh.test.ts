import { expect, it } from "vitest";
import type { RealtimeMessage, ViewKey } from "../api/types";
import { shellDataKeysForView } from "./navigation";
import { refreshSelectionForEvent } from "./realtimeRefresh";

function event(type: string, payload = {}): RealtimeMessage { return { type, payload, created_at: "" }; }
function select(message: RealtimeMessage, view: ViewKey) { return refreshSelectionForEvent(message, view, shellDataKeysForView(view, null)); }
it("refreshes schedules alone on the schedule route and skips unrelated workflow mutations", () => {
  expect(select(event("audit.log.created", { log: { action: "schedule.update" } }), "schedules")).toEqual({ keys: new Set(["schedules"]), route: false });
  expect(select(event("audit.log.created", { action: "notification_rule.update" }), "schedules")).toEqual({ keys: new Set(), route: false });
});
it("updates both notification rules and automation catalog references without shell queries", () => {
  for (const view of ["settings_notifications", "settings_automations"] as const) {
    expect(select(event("audit.log.created", { action: "notification_rule.delete" }), view)).toEqual({ keys: new Set(), route: true });
  }
});
it("refreshes only alerts for an alert change while retaining lifecycle refresh ownership", () => {
  expect(select(event("alerts.updated"), "dashboard")).toEqual({ keys: new Set(["anomalies"]), route: false });
  expect(select(event("alerts.updated"), "alerts")).toEqual({ keys: new Set(["anomalies"]), route: true });
});
it("does not duplicate domain event refreshes already owned by passes and charts", () => {
  expect(select(event("visitor_pass.updated"), "passes")).toEqual({ keys: new Set(), route: false });
  expect(select(event("access_event.finalized"), "top_charts").route).toBe(false);
  expect(select(event("access_event.finalized"), "movements").route).toBe(true);
});
it("keeps integration catalogs and schedule consumers current after configuration changes", () => {
  expect(select(event("audit.log.created", { action: "settings.update" }), "settings_notifications").route).toBe(true);
  expect(select(event("schedule.override_created"), "settings_gates")).toEqual({ keys: new Set(["schedules"]), route: true });
  expect(select(event("audit.log.created", { log: { action: "person.update" } }), "settings_automations")).toEqual({ keys: new Set(["people", "vehicles"]), route: true });
});
it("ignores unrelated telemetry and retains audit changes in investigations", () => {
  expect(select(event("telemetry.sample"), "dashboard")).toEqual({ keys: new Set(), route: false });
  expect(select(event("audit.log.created", { action: "gate.command" }), "logs").route).toBe(true);
});
it("invalidates schedule default policy and integration options at their owners", () => {
  expect(select(event("audit.log.created", { action: "settings.update" }), "schedules")).toEqual({ keys: new Set(["schedules", "maintenanceStatus"]), route: true });
  expect(select(event("audit.log.created", { action: "access_device.update" }), "settings_automations")).toEqual({ keys: new Set(["people"]), route: true });
});
