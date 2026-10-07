import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { listPeople, lookupPeople, lookupVehicleRegistrations } from "./directory";
afterEach(() => vi.restoreAllMocks());
it("sends server search, active filtering, and bounded identity selection", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], total: 0, next_cursor: null });
  await listPeople({ q: "Ash & Zoe", active: false, ids: ["ash", "zoe"], limit: 50 });
  const query = new URL(get.mock.calls[0][0], "http://synthetic").searchParams;
  expect(query.get("q")).toBe("Ash & Zoe");
  expect(query.get("active")).toBe("false");
  expect(query.getAll("ids")).toEqual(["ash", "zoe"]);
  expect(query.get("limit")).toBe("50");
});
it("chunks only the explicitly selected IDs into requests of at most 200", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], total: 0, next_cursor: null });
  const ids = Array.from({ length: 205 }, (_, index) => String(index));
  await lookupPeople([...ids, ids[0]]);
  expect(get).toHaveBeenCalledTimes(2);
  expect(get.mock.calls.map(([path]) => new URL(path, "http://synthetic").searchParams.getAll("ids").length)).toEqual([200, 5]);
});
it("normalizes exact registrations and does not issue a request for an empty selection", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], total: 0, next_cursor: null });
  await lookupVehicleRegistrations([]);
  expect(get).not.toHaveBeenCalled();
  await lookupVehicleRegistrations([" AB12 CDE ", "ab12cde"]);
  expect(new URL(get.mock.calls[0][0], "http://synthetic").searchParams.getAll("registrations")).toEqual(["AB12CDE"]);
});
