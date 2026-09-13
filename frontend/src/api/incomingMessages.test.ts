import { afterEach, expect, it, vi } from "vitest";
import { incomingMessagesApi, type IncomingMessagePage, type IncomingMessageProvider } from "./incomingMessages";
// Byte-identical to the backend's canonical redacted recovery fixture.
import contract from "./fixtures/incomingRecovery.generated.json";

afterEach(() => vi.unstubAllGlobals());

it.each<IncomingMessageProvider>(["whatsapp", "discord"])("reads the paired %s page and detail using GET and cancellation", async (provider) => {
  const paired: IncomingMessagePage = contract[provider];
  const message = paired.items[0];
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(paired)))
    .mockResolvedValueOnce(new Response(JSON.stringify(message)));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  expect(await incomingMessagesApi.getPage(provider, undefined, { signal: controller.signal })).toEqual(paired);
  expect(await incomingMessagesApi.getDetail(provider, message.id, { signal: controller.signal })).toEqual(message);
  expect(fetcher.mock.calls).toEqual([
    [`/api/v1/integrations/${provider}/incoming?limit=25`, { credentials: "include", signal: controller.signal }],
    [`/api/v1/integrations/${provider}/incoming/${message.id}`, { credentials: "include", signal: controller.signal }],
  ]);
  for (const item of paired.items) {
    expect(item).not.toHaveProperty("envelope");
    expect(item).not.toHaveProperty("routing_context");
    expect(item).not.toHaveProperty("author_provider_id");
    expect(item).not.toHaveProperty("reply_plan");
    expect(item).not.toHaveProperty("claim_token");
  }
});

it("preserves opaque approval identities from the paired response", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(contract.whatsapp))));
  const result = await incomingMessagesApi.getPage("whatsapp");
  expect(result.items[2].result_ids.confirmation_id).toBe("confirm-00000000000000000000000000000022");
  expect(result.items[2].replies[0].delivery).toBe("unknown");
});

it("encodes page and record identifiers within the selected provider scope", async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ items: [], next_cursor: null })));
  vi.stubGlobal("fetch", fetcher);
  await incomingMessagesApi.getPage("whatsapp", "synthetic/cursor");
  await incomingMessagesApi.getDetail("discord", "synthetic/record");
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/integrations/whatsapp/incoming?limit=25&before_id=synthetic%2Fcursor",
    "/api/v1/integrations/discord/incoming/synthetic%2Frecord",
  ]);
});

it("retains denied reads as failures, rather than fabricating empty history", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Admin access required" }), { status: 403 })));
  await expect(incomingMessagesApi.getPage("whatsapp")).rejects.toThrow("Admin access required");
});
