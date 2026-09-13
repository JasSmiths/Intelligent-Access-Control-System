import { afterEach, expect, it, vi } from "vitest";
import { workflowApi, type AutomationRun } from "./workflows";
// Generated copy; the backend contract test enforces exact canonical bytes.
import contract from "./fixtures/automationRecovery.generated.json";

afterEach(() => vi.unstubAllGlobals());

it("reads the paired run and its stable action operation without issuing a mutation", async () => {
  const paired: AutomationRun = contract;
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ items: [contract], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify(contract)));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  const page = await workflowApi.getAutomationRuns(undefined, { signal: controller.signal });
  const detail = await workflowApi.getAutomationRun(paired.id, { signal: controller.signal });
  expect(page.items[0]).toEqual(paired);
  expect(detail.action_states[0].operation_id).toBe(paired.action_states[0].operation_id);
  expect(fetcher.mock.calls).toEqual([
    ["/api/v1/automations/runs?limit=25", { credentials: "include", signal: controller.signal }],
    [`/api/v1/automations/runs/${paired.id}`, { credentials: "include", signal: controller.signal }],
  ]);
  expect(JSON.stringify(detail)).not.toContain("rule_fingerprint");
});

it("uses the server's opaque pagination cursor and encodes identifiers", async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ items: [], next_cursor: null })));
  vi.stubGlobal("fetch", fetcher);
  await workflowApi.getAutomationRuns("synthetic/cursor");
  await workflowApi.getAutomationRun("synthetic/run");
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/automations/runs?limit=25&before_id=synthetic%2Fcursor", "/api/v1/automations/runs/synthetic%2Frun",
  ]);
});
