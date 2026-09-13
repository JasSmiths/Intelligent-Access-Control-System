import { afterEach, describe, expect, it, vi } from "vitest";
import type { Schedule } from "./types";
import { schedulesApi } from "./schedules";
import { workflowApi, type AutomationRule, type NotificationRule } from "./workflows";

const schedule: Schedule = { id: "schedule-1", name: "Office", description: null, time_blocks: { "0": [{ start: "09:00", end: "17:00" }] }, created_at: "", updated_at: "" };
const payload = { name: schedule.name, description: schedule.description, time_blocks: schedule.time_blocks };
const automation: AutomationRule = { id: "automation-1", name: "Arrival", description: "", is_active: false, triggers: [], trigger_keys: [], conditions: [], actions: [], run_count: 0 };
const notification: NotificationRule = { id: "notification-1", name: "Notify", trigger_event: "arrival", is_active: false, conditions: [], actions: [] };
const cases = [
  { action: "schedule.create", run: () => schedulesApi.save(payload), method: "POST", path: "/api/v1/schedules", confirmationPayload: payload, mutationPayload: payload },
  { action: "schedule.update", run: () => schedulesApi.save(payload, schedule), method: "PATCH", path: "/api/v1/schedules/schedule-1", confirmationPayload: { ...payload, schedule_id: schedule.id }, mutationPayload: payload },
  { action: "schedule.delete", run: () => schedulesApi.delete(schedule), method: "DELETE", path: "/api/v1/schedules/schedule-1", confirmationPayload: { schedule_id: schedule.id }, mutationPayload: {} },
  { action: "automation_rule.update", run: () => workflowApi.toggleAutomationRule(automation, true), method: "PATCH", path: "/api/v1/automations/rules/automation-1", confirmationPayload: { is_active: true }, mutationPayload: { is_active: true } },
  { action: "notification_rule.test", run: () => workflowApi.testNotificationRule(notification, notification), method: "POST", path: "/api/v1/notifications/rules/test", confirmationPayload: { rule: notification }, mutationPayload: { rule: notification } },
];
afterEach(() => vi.unstubAllGlobals());
describe.each(cases)("$action boundary", (testCase) => {
  it("obtains confirmation before issuing the exact mutation", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ confirmation_token: "test-confirmation" }))).mockResolvedValueOnce(new Response("{}"));
    vi.stubGlobal("fetch", fetcher);
    await testCase.run();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/action-confirmations");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ action: testCase.action, payload: testCase.confirmationPayload });
    expect(fetcher.mock.calls[1][0]).toBe(testCase.path);
    expect(fetcher.mock.calls[1][1].method).toBe(testCase.method);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ ...testCase.mutationPayload, confirmation_token: "test-confirmation" });
  });
  it("makes zero mutation requests when confirmation/role authorization fails", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Admin required" }), { status: 403 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(testCase.run()).rejects.toThrow("Admin required");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/action-confirmations");
  });
});
it("loads notification lists without requesting cameras or snapshots", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response("[]")));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  await workflowApi.getNotificationData({ signal: controller.signal });
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual(["/api/v1/notifications/catalog", "/api/v1/notifications/rules"]);
  expect(fetcher.mock.calls.every(([, options]) => options.signal === controller.signal)).toBe(true);
});
