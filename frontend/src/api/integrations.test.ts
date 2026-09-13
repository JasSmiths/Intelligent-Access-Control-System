import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { integrationsApi, isGateCommandReceipt, isDeviceCommandReceipt, coverTargetReceipt } from "./integrations";
// Generated copy: backend projection contract tests enforce byte equality with the canonical fixture.
import contract from "./fixtures/gateCommandReceipts.generated.json";

afterEach(() => vi.unstubAllGlobals());
it.each(contract.cases)("retains every target in the paired $name receipt, including unsuccessful HTTP results", async ({ outcome }) => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(outcome), { status: outcome.accepted ? 200 : 503 }));
  vi.stubGlobal("fetch", fetcher);
  const result = await integrationsApi.openGate({ reason: "Synthetic operator command", target_device_key: "secondary" }, "synthetic-token").catch((error: unknown) => {
    expect(error).toBeInstanceOf(ApiError);
    return (error as ApiError).payload;
  });
  expect(isGateCommandReceipt(result)).toBe(true);
  expect(result).toEqual(outcome);
  expect(fetcher).toHaveBeenCalledOnce();
});
it.each(["secondary", undefined])("binds the same selected/global gate payload to confirmation and POST (%s)", async (target) => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ confirmation_id: "intent", confirmation_token: "token" })))
    .mockResolvedValueOnce(new Response(JSON.stringify(contract.cases[0].outcome)));
  vi.stubGlobal("fetch", fetcher);
  const payload = { reason: "Synthetic command", ...(target ? { target_device_key: target } : {}) };
  const confirmation = await integrationsApi.confirmGateOpen(payload, target || "All configured access gates");
  await integrationsApi.openGate(payload, confirmation.confirmation_token);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ action: "gate.open", payload });
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ ...payload, confirmation_token: "token" });
});
it("recovers gate and cover outcomes using read-only intent lookup and an owned abort signal", async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response("{}"));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  await integrationsApi.getGateCommandByIntent("synthetic/intent", { signal: controller.signal });
  await integrationsApi.getCoverCommandByIntent("synthetic/intent", { signal: controller.signal });
  expect(fetcher.mock.calls).toEqual([
    ["/api/v1/integrations/gate/commands?intent_id=synthetic%2Fintent", { credentials: "include", signal: controller.signal }],
    ["/api/v1/integrations/cover/commands?intent_id=synthetic%2Fintent", { credentials: "include", signal: controller.signal }]
  ]);
});
it("preserves the selected cover and its receipt without broadening to all gates", async () => {
  const target = contract.cases[2].outcome.target_receipts[0];
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ confirmation_id: "intent", confirmation_token: "token" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ target_receipt: target }), { status: 503 }));
  vi.stubGlobal("fetch", fetcher);
  const payload = { reason: "Synthetic close", entity_id: "garage", action: "close" as const };
  const confirmation = await integrationsApi.confirmCoverCommand(payload, "Garage");
  const result = await integrationsApi.commandCover(payload, confirmation.confirmation_token).catch((error: ApiError) => error.payload);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ action: "cover.close", payload });
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ ...payload, confirmation_token: "token" });
  expect(coverTargetReceipt(result)).toEqual(target);
});
it("does not interpret ordinary or incomplete error bodies as command receipts", () => {
  expect(isGateCommandReceipt({ detail: "Maintenance active" })).toBe(false);
  expect(isGateCommandReceipt({ ...contract.cases[0].outcome, target_receipts: [{ state: "open" }] })).toBe(false);
  expect(isDeviceCommandReceipt({ accepted: true, state: "open" })).toBe(false);
  expect(coverTargetReceipt({ target_receipt: null })).toBeNull();
});
it("permits partial only on the aggregate, never on a target or provider receipt", () => {
  const partial = contract.cases.find((item) => item.outcome.delivery === "partial")!.outcome;
  expect(isGateCommandReceipt(partial)).toBe(true);
  const target = partial.target_receipts[0];
  expect(isDeviceCommandReceipt({ ...target, delivery: "partial" })).toBe(false);
  expect(isDeviceCommandReceipt({ ...target, provider_receipts: [{ provider: "home_assistant", delivery: "partial" }] })).toBe(false);
  expect(isGateCommandReceipt({ ...partial, target_receipts: [{ ...target, delivery: "partial" }] })).toBe(false);
});

it("discovers paired gate/cover pages and reads command IDs without an intent or any mutation", async () => {
  const pages = (await import("./fixtures/gateReceiptPages.generated.json")).default;
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(pages.gates)))
    .mockResolvedValueOnce(new Response(JSON.stringify(pages.covers)))
    .mockResolvedValueOnce(new Response(JSON.stringify(pages.gates.items[0])))
    .mockResolvedValueOnce(new Response(JSON.stringify(pages.covers.items[0])));
  vi.stubGlobal("fetch", fetcher);
  const signal = new AbortController().signal;
  expect(await integrationsApi.getGateCommands(undefined, { signal })).toEqual(pages.gates);
  expect(await integrationsApi.getCoverCommands("synthetic/cursor", { signal })).toEqual(pages.covers);
  expect(await integrationsApi.getGateCommand("synthetic/parent", { signal })).toEqual(pages.gates.items[0]);
  expect(await integrationsApi.getCoverCommand("synthetic/child", { signal })).toEqual(pages.covers.items[0]);
  expect(fetcher.mock.calls).toEqual([
    ["/api/v1/integrations/gate/commands?limit=25", { credentials: "include", signal }],
    ["/api/v1/integrations/cover/commands?limit=25&before_id=synthetic%2Fcursor", { credentials: "include", signal }],
    ["/api/v1/integrations/gate/commands/synthetic%2Fparent", { credentials: "include", signal }],
    ["/api/v1/integrations/cover/commands/synthetic%2Fchild", { credentials: "include", signal }]
  ]);
});
