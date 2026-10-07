import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { AccessEvent, Person, RealtimeMessage, UserAccount, Vehicle } from "../api/types";
import { useDirectoryIdentities, useDirectoryPage } from "../features/directory/reads";
import { useHistoryPage } from "../views/useHistoryPage";
import { useShellRefresh } from "./useShellRefresh";

const admin = { id: "admin-one", role: "admin" } as UserAccount;
const noPeople: Person[] = [];
const noVehicles: Vehicle[] = [];
const selectedIds = ["off-page"];
const message = (type: string, payload = {}): RealtimeMessage => ({ type, payload, created_at: "2026-10-07T12:00:00Z" });
const page = <T,>(items: T[], next_cursor: string | null = null) => ({ items, total: 75, next_cursor });
const setters = () => ({ presence: vi.fn(), expectedPresence: vi.fn(), events: vi.fn(), anomalies: vi.fn(), people: vi.fn(), vehicles: vi.fn(), groups: vi.fn(), schedules: vi.fn(), integrationStatus: vi.fn(), maintenanceStatus: vi.fn() });
function clock() {
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  return () => { now += 6000; };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

for (const kind of ["people", "vehicles"] as const) {
  it(`refreshes the filtered second ${kind} page and off-page selection from a committed update`, async () => {
    const advance = clock();
    let changed = false;
    const row = (id: string, value: string) => kind === "people" ? { id, display_name: value } : { id, registration_number: value };
    const first = row("first-page", "Unchanged first page");
    const read = vi.spyOn(api, "get").mockImplementation(async (path) => {
      const url = new URL(path, "http://fixture.local");
      if (url.pathname === `/api/v1/${kind}`) {
        if (url.searchParams.has("ids")) return page([row("off-page", changed ? "Updated selected" : "Original selected")]);
        if (url.searchParams.has("cursor")) return page([row("second-page", changed ? "Updated page" : "Original page")]);
        return page([first], "second-page");
      }
      if (url.pathname === "/api/v1/people" || url.pathname === "/api/v1/vehicles") return page([]);
      return [];
    });
    const { result } = renderHook(() => {
      const [people, setPeople] = React.useState(noPeople);
      const [vehicles, setVehicles] = React.useState(noVehicles);
      const shell = useShellRefresh(kind, admin, { ...setters(), people: setPeople, vehicles: setVehicles });
      const seed = kind === "people" ? people : vehicles;
      const directory = useDirectoryPage(kind, seed, { query: "filter", refreshToken: shell.dataRefreshToken });
      const selected = useDirectoryIdentities(kind, seed, selectedIds, shell.dataRefreshToken);
      return { shell, directory, selected };
    });
    await act(async () => { await result.current.shell.initialRefresh(); });
    await waitFor(() => expect(result.current.directory.items).toEqual([first]));
    act(() => result.current.directory.next());
    await waitFor(() => expect(result.current.directory.items).toEqual([row("second-page", "Original page")]));
    const before = read.mock.calls.filter(([path]) => path.includes("cursor=second-page")).length;
    changed = true;
    advance();
    await act(async () => {
      const event = kind === "people" ? message("audit.log.created", { log: { action: "person.update" } }) : message("vehicle_information.updated", { vehicle_id: "second-page" });
      await result.current.shell.refreshRealtime(result.current.shell.selectionForEvent(event));
    });
    await waitFor(() => expect(result.current.directory.items).toEqual([row("second-page", "Updated page")]));
    await waitFor(() => expect(result.current.selected.items).toEqual([row("off-page", "Updated selected")]));
    expect(result.current.directory.canPrevious).toBe(true);
    expect(read.mock.calls.filter(([path]) => path.includes("cursor=second-page"))).toHaveLength(before + 1);
    expect(read.mock.calls.filter(([path]) => path.includes("cursor=second-page")).at(-1)?.[0]).toContain("q=filter");
  });
}

it("refreshes a feature even when another requested shell resource fails", async () => {
  const advance = clock();
  let changed = false;
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/api/v1/groups" && changed) throw new Error("Synthetic group outage");
    if (path.startsWith("/api/v1/people?")) return page([{ id: "person", display_name: changed ? "Updated" : "Original" }]);
    if (path.startsWith("/api/v1/vehicles?")) return page([]);
    return [];
  });
  const { result } = renderHook(() => {
    const shell = useShellRefresh("people", admin, setters());
    const directory = useDirectoryPage("people", noPeople, { refreshToken: shell.dataRefreshToken });
    return { shell, directory };
  });
  await act(async () => { await result.current.shell.initialRefresh(); });
  await waitFor(() => expect(result.current.directory.items[0]?.display_name).toBe("Original"));
  changed = true;
  advance();
  await act(async () => {
    await expect(result.current.shell.refreshRealtime(result.current.shell.selectionForEvent(message("audit.log.created", { action: "person.update" })))).rejects.toThrow("Synthetic group outage");
  });
  await waitFor(() => expect(result.current.directory.items[0]?.display_name).toBe("Updated"));
  expect(result.current.shell.readState).toBe("stale");
  expect(result.current.shell.failedKeys).toContain("groups");
});

it("retains filtered history page two and signals new activity after a failed shell read", async () => {
  const advance = clock();
  let changed = false;
  const read = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.startsWith("/api/v1/events/history?")) {
      const older = new URL(path, "http://fixture.local").searchParams.has("cursor");
      return { items: [{ id: older ? "older" : changed ? "latest" : "original" }], next_cursor: older ? null : "older", as_of: "2026-10-07T12:00:00Z" };
    }
    if (path === "/api/v1/events?limit=40" && changed) throw new Error("Synthetic summary outage");
    return [];
  });
  const { result } = renderHook(() => {
    const shell = useShellRefresh("events", admin, setters());
    const history = useHistoryPage<AccessEvent>("/api/v1/events/history", new URLSearchParams({ decision: "granted" }), shell.dataRefreshToken);
    return { shell, history };
  });
  await act(async () => { await result.current.shell.initialRefresh(); });
  await waitFor(() => expect(result.current.history.items[0]?.id).toBe("original"));
  act(() => result.current.history.next());
  await waitFor(() => expect(result.current.history.items[0]?.id).toBe("older"));
  changed = true;
  advance();
  await act(async () => { await expect(result.current.shell.refreshRealtime(result.current.shell.selectionForEvent(message("access_event.finalized")))).rejects.toThrow("Synthetic summary outage"); });
  expect(result.current.history.newActivity).toBe(true);
  expect(result.current.history.index).toBe(1);
  expect(result.current.history.items[0]?.id).toBe("older");
  expect(read.mock.calls.filter(([path]) => path.startsWith("/api/v1/events/history?"))).toHaveLength(2);
  act(() => result.current.history.refresh());
  await waitFor(() => expect(result.current.history.items[0]?.id).toBe("latest"));
  expect(result.current.history.newActivity).toBe(false);
  expect(read.mock.calls.at(-1)?.[0]).toContain("decision=granted");
});

it("coalesces a burst during a blocked shell refresh into one trailing feature invalidation", async () => {
  const advance = clock();
  let blocked = false;
  const gate = deferred<unknown>();
  const read = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/api/v1/groups" && blocked) return gate.promise;
    if (path.startsWith("/api/v1/people?")) return page([{ id: "person", display_name: "Current" }]);
    if (path.startsWith("/api/v1/vehicles?")) return page([]);
    return [];
  });
  const { result } = renderHook(() => {
    const shell = useShellRefresh("people", admin, setters());
    const directory = useDirectoryPage("people", noPeople, { query: "shown", refreshToken: shell.dataRefreshToken });
    return { shell, directory };
  });
  await act(async () => { await result.current.shell.initialRefresh(); });
  await waitFor(() => expect(result.current.directory.items).toHaveLength(1));
  const featureReads = () => read.mock.calls.filter(([path]) => path.includes("q=shown")).length;
  const before = featureReads();
  advance(); blocked = true;
  const event = message("audit.log.created", { action: "person.update" });
  let first!: Promise<void>;
  let trailing!: Promise<void>[];
  act(() => {
    first = result.current.shell.refreshRealtime(result.current.shell.selectionForEvent(event));
    trailing = Array.from({ length: 100 }, () => result.current.shell.refreshRealtime(result.current.shell.selectionForEvent(event)));
  });
  expect(featureReads()).toBe(before);
  advance(); blocked = false;
  await act(async () => { gate.resolve([]); await first; await Promise.all(trailing); });
  await waitFor(() => expect(result.current.shell.dataRefreshToken).toBe(2));
  await waitFor(() => expect(featureReads()).toBeGreaterThan(before));
  expect(featureReads()).toBeLessThanOrEqual(before + 2);
});
