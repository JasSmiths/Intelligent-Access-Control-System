import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";

afterEach(() => vi.unstubAllGlobals());
it("retains structured error status and receipt while preserving the existing readable message", async () => {
  const payload = { detail: "Controller response lost", accepted: false, delivery: "unknown", command_id: "synthetic-command" };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 503 })));
  const error = await api.post("/api/v1/integrations/gate/open", {}).catch((value: unknown) => value);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ status: 503, payload, message: "503 Request failed: Controller response lost" });
});
it.each([
  { body: "upstream unavailable", expected: "503 Request failed: upstream unavailable", payload: null },
  { body: JSON.stringify({ detail: [{ loc: ["body", "target"], msg: "Required" }] }), expected: "503 Request failed: body.target: Required", payload: { detail: [{ loc: ["body", "target"], msg: "Required" }] } }
])("preserves readable error detail for $body", async ({ body, expected, payload }) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 503 })));
  await expect(api.get("/api/v1/synthetic")).rejects.toMatchObject({ message: expected, status: 503, payload });
});
it("does not turn a network failure into a receipt or retry the request", async () => {
  const lost = new TypeError("Failed to fetch");
  const fetcher = vi.fn().mockRejectedValue(lost);
  vi.stubGlobal("fetch", fetcher);
  await expect(api.post("/api/v1/integrations/gate/open", {})).rejects.toBe(lost);
  expect(fetcher).toHaveBeenCalledOnce();
});
