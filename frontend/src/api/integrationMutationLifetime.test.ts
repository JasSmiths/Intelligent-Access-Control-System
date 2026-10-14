import { afterEach, expect, it, vi } from "vitest";
import { confirmedDelete, confirmedPatch, confirmedPost, isMutationNotStarted, removeAppriseUrl } from "./integrations";

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
const options = { target_entity: "Integration", target_label: "Inert provider", reason: "Inert lifetime check" };
afterEach(() => vi.unstubAllGlobals());
it.each([confirmedPost, confirmedPatch, confirmedDelete])("does not even request confirmation for an already disposed owner", async (write) => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const result = await write("/api/v1/inert", "inert.write", {}, options, {}, () => false);
  expect(isMutationNotStarted(result)).toBe(true); expect(fetcher).not.toHaveBeenCalled();
});
it.each([confirmedPost, confirmedPatch, confirmedDelete])("stops before a mutation when the owner is replaced during confirmation", async (write) => {
  const confirmation = deferred<Response>(); let active = true;
  const fetcher = vi.fn().mockReturnValueOnce(confirmation.promise); vi.stubGlobal("fetch", fetcher);
  const result = write("/api/v1/inert", "inert.write", { field: "inert" }, options, {}, () => active);
  expect(fetcher).toHaveBeenCalledOnce(); active = false;
  confirmation.resolve(new Response(JSON.stringify({ confirmation_token: "inert-token" })));
  expect(await result).toEqual({ mutationStarted: false, reason: "owner_disposed" }); expect(fetcher).toHaveBeenCalledOnce();
});
it("continues observing an already-started write after disposal, preserving acceptance and confirmation payload", async () => {
  const response = deferred<Response>(); let active = true;
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ confirmation_token: "inert-token" }))).mockReturnValueOnce(response.promise); vi.stubGlobal("fetch", fetcher);
  const result = confirmedPatch<{ accepted: boolean }>("/api/v1/inert", "inert.write", { values: { field: "draft" } }, options, { values: { field: "draft" } }, () => active);
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2)); active = false;
  response.resolve(new Response(JSON.stringify({ accepted: true })));
  expect(await result).toEqual({ accepted: true });
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ action: "inert.write", payload: { values: { field: "draft" } } });
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ values: { field: "draft" }, confirmation_token: "inert-token" });
  expect(fetcher.mock.calls[1][1].signal).toBeUndefined();
});
it("retains a real provider rejection instead of a stopped-before-write outcome", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ confirmation_token: "inert-token" }))).mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Inert rejection" }), { status: 409 })); vi.stubGlobal("fetch", fetcher);
  await expect(confirmedPost("/api/v1/inert", "inert.write", {}, options)).rejects.toThrow("Inert rejection");
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it("returns accepted Apprise deletion independently of a subsequent metadata read", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ confirmation_token: "inert-token" }))).mockResolvedValueOnce(new Response(JSON.stringify({ urls: [] }))); vi.stubGlobal("fetch", fetcher);
  const selected = { destination_id: "d".repeat(64), revision: "e".repeat(64) };
  expect(await removeAppriseUrl({ index: 2, type: "Inert", scheme: "inert", preview: "safe preview", ...selected })).toEqual({ urls: [] });
  expect(fetcher.mock.calls.map(([, request]) => request.method)).toEqual(["POST", "DELETE"]);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ payload: { index: 2, ...selected } });
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ ...selected, confirmation_token: "inert-token" });
});
