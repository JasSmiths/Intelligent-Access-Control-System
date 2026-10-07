import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as directoryApi from "../../api/directory";
import type { AccessEvent, Person, Presence, Vehicle } from "../../api/types";
import { useDashboardDirectory } from "./useDashboardDirectory";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("reads only visible identities outside the shell page and uses the server vehicle count", async () => {
  const person = { id: "person-after-first-page", display_name: "Later Person" } as Person;
  const vehicle = { id: "vehicle-after-first-page", registration_number: "LATER01", person_ids: [person.id] } as Vehicle;
  const people = vi.spyOn(directoryApi, "lookupPeople").mockResolvedValue([person]);
  const vehicles = vi.spyOn(directoryApi, "lookupVehicleRegistrations").mockResolvedValue([vehicle]);
  const count = vi.spyOn(directoryApi, "listVehicles").mockResolvedValue({ items: [vehicle], total: 2000, next_cursor: null });
  const props = {
    people: [], vehicles: [], expectedPresence: null,
    presence: [{ person_id: person.id, display_name: person.display_name, state: "present", last_changed_at: null }] as Presence[],
    events: [{ id: "event", registration_number: "later-01", direction: "entry" }] as AccessEvent[]
  };
  const { result } = renderHook(() => useDashboardDirectory(props));
  await act(async () => {});
  expect(vehicles).toHaveBeenCalledWith(["LATER01"], expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(people).toHaveBeenCalledWith([person.id], expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(count).toHaveBeenCalledWith({ active: true, limit: 1 }, expect.anything());
  expect(result.current.people).toEqual([person]);
  expect(result.current.vehicles).toEqual([vehicle]);
  expect(result.current.activeVehicleCount).toBe(2000);
});

it("ignores identities returned after the dashboard unmounts", async () => {
  let resolve!: (vehicles: Vehicle[]) => void;
  vi.spyOn(directoryApi, "listVehicles").mockResolvedValue({ items: [], total: 0, next_cursor: null });
  const read = vi.spyOn(directoryApi, "lookupVehicleRegistrations").mockReturnValue(new Promise((yes) => { resolve = yes; }));
  const people = vi.spyOn(directoryApi, "lookupPeople").mockResolvedValue([]);
  const props = { people: [], vehicles: [], presence: [], expectedPresence: null, events: [] };
  const { unmount } = renderHook(() => useDashboardDirectory(props));
  const signal = read.mock.calls[0][1]!.signal!;
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { resolve([]); });
  expect(people).not.toHaveBeenCalled();
});

it("refreshes outside-page identities when the shell invalidates unchanged seed rows", async () => {
  const person = { id: "later", display_name: "Before refresh" } as Person;
  const read = vi.spyOn(directoryApi, "lookupPeople").mockResolvedValueOnce([person]).mockResolvedValue([{ ...person, display_name: "After refresh" }]);
  vi.spyOn(directoryApi, "lookupVehicleRegistrations").mockResolvedValue([]);
  vi.spyOn(directoryApi, "listVehicles").mockResolvedValue({ items: [], total: 2000, next_cursor: null });
  const seed = { people: [], vehicles: [], events: [], expectedPresence: null,
    presence: [{ person_id: person.id, display_name: person.display_name, state: "present", last_changed_at: null }] as Presence[] };
  const { result, rerender } = renderHook(({ refreshToken }) => useDashboardDirectory({ ...seed, refreshToken }), { initialProps: { refreshToken: 0 } });
  await act(async () => {});
  expect(result.current.people[0]?.display_name).toBe("Before refresh");
  rerender({ refreshToken: 1 });
  await act(async () => {});
  expect(result.current.people[0]?.display_name).toBe("After refresh");
  expect(read).toHaveBeenCalledTimes(2);
});
