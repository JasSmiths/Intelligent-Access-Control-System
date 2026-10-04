import { afterEach, expect, it, vi } from "vitest";
import { missedExitRecoveryApi, recoveryAttemptQuery } from "./missedExitRecovery";
import type { Person } from "./types";
afterEach(() => vi.unstubAllGlobals());
it("encodes filters and bounds paging to one hundred records", () => {
  const query = new URLSearchParams(recoveryAttemptQuery({ owner_id: "owner / 1", registration_number: " AB 12 ", method: "phone_automatic", outcome: "denied", from_at: "2026-10-02T08:00:00Z", offset: -9, limit: 101 }));
  expect(Object.fromEntries(query)).toEqual({ owner_id: "owner / 1", registration_number: "AB 12", method: "phone_automatic", outcome: "denied", from_at: "2026-10-02T08:00:00Z", offset: "0", limit: "100" });
});
it("uses abortable typed reads for attempts and details", async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response("{}")); vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  await missedExitRecoveryApi.attempts({ offset: 25, limit: 25 }, { signal: controller.signal });
  await missedExitRecoveryApi.attempt("attempt / 1", { signal: controller.signal });
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual(["/api/v1/missed-exit-recovery/attempts?offset=25&limit=25", "/api/v1/missed-exit-recovery/attempts/attempt%20%2F%201"]);
  expect(fetcher.mock.calls.every(([, options]) => options.signal === controller.signal)).toBe(true);
});
const person = { id: "synthetic-person", display_name: "Synthetic Resident" } as Person;
const payload = { missed_exit_recovery_enabled: true, missed_exit_recovery_tracker_entity_id: "device_tracker.synthetic" };
it("confirms the exact owner configuration before saving and never sends hardware or notifications", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"confirmation_token":"synthetic-token"}')).mockResolvedValueOnce(new Response("{}")); vi.stubGlobal("fetch", fetcher);
  await missedExitRecoveryApi.saveOwner(person, payload);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/action-confirmations");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ action: "person.update", payload: { person_id: person.id, ...payload } });
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/people/synthetic-person");
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ ...payload, confirmation_token: "synthetic-token" });
});
it("does not mutate owner configuration when confirmation is rejected", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{"detail":"Administrator required"}', { status: 403 })); vi.stubGlobal("fetch", fetcher);
  await expect(missedExitRecoveryApi.saveOwner(person, payload)).rejects.toThrow("Administrator required");
  expect(fetcher).toHaveBeenCalledTimes(1);
});
