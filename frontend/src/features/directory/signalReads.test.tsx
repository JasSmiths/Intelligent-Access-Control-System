import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Person, RealtimeMessage, Vehicle } from "../../api/types";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import { useDirectoryIdentities, useDirectoryPage } from "./reads";
import { useGroups } from "./catalogs";
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
const page = <T,>(items: T[], next_cursor: string | null = null) => ({ items, total: 2000, next_cursor });
function setup() {
  let now = 0; vi.spyOn(Date, "now").mockImplementation(() => now);
  const signals = createReadSignals("test");
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
  return { signals, wrapper, advance: () => { now += 6000; } };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
for (const kind of ["people", "vehicles"] as const) {
  it(`${kind}: preserves query/cursor and hydrates selected identities while a catalog fails`, async () => {
    const { signals, wrapper, advance } = setup(); let changed = false;
    const row = (id: string, label: string) => (kind === "people" ? { id, display_name: label, is_active: !changed } : { id, registration_number: label, is_active: !changed }) as Person | Vehicle;
    const read = vi.spyOn(api, "get").mockImplementation(async (path) => {
      const url = new URL(path, "http://fixture.local");
      if (url.pathname === "/api/v1/groups") { if (changed) throw new Error("Catalog offline"); return []; }
      if (url.searchParams.has("ids")) return page([row("selected", changed ? "Fresh selected" : "Selected")]);
      if (url.searchParams.has("cursor")) return page([row("second", changed ? "Fresh page" : "Page two")]);
      return page([row("first", "First")], "page-two");
    });
    const { result } = renderHook(() => ({ directory: useDirectoryPage(kind, [], { query: "keep-filter" }), selected: useDirectoryIdentities(kind, [], ["selected"]), groups: useGroups() }), { wrapper });
    await waitFor(() => expect(result.current.directory.items[0]?.id).toBe("first"));
    act(() => result.current.directory.next());
    await waitFor(() => expect(result.current.directory.items[0]?.id).toBe("second"));
    const before = read.mock.calls.filter(([path]) => path.includes("cursor=page-two")).length;
    changed = true; advance();
    const message: RealtimeMessage = { type: "audit.log.created", payload: { log: { action: "person.update" } } };
    await act(async () => { await expect(signals.publish({ type: "realtime", message })).rejects.toThrow("Catalog offline"); });
    expect(result.current.directory.items).toEqual([row("second", "Fresh page")]);
    expect(result.current.selected.items).toEqual([row("selected", "Fresh selected")]);
    expect(result.current.directory.canPrevious).toBe(true);
    expect(read.mock.calls.filter(([path]) => path.includes("cursor=page-two"))).toHaveLength(before + 1);
    expect(read.mock.calls.filter(([path]) => path.includes("cursor=page-two")).at(-1)?.[0]).toContain("q=keep-filter");
    expect(result.current.groups.error).toBe("Catalog offline");
  });
}
it("awaits actual directory HTTP and retains exactly one trailing read for a 100-event burst", async () => {
  const { signals, wrapper, advance } = setup(); const blocked = deferred<ReturnType<typeof page<Person>>>();
  let active = 0; let maximum = 0; let count = 0;
  const read = vi.spyOn(api, "get").mockImplementation(async () => {
    active += 1; maximum = Math.max(maximum, active); count += 1;
    try { return count === 1 ? await blocked.promise : page([{ id: "fresh", display_name: "Fresh" } as Person]); }
    finally { active -= 1; }
  });
  const { result } = renderHook(() => useDirectoryPage("people", []), { wrapper });
  expect(read).toHaveBeenCalledOnce(); advance();
  let pending!: Promise<void>[];
  act(() => { pending = Array.from({ length: 100 }, () => signals.publish({ type: "realtime", message: { type: "vehicle_information.updated", payload: {} } })); });
  expect(read).toHaveBeenCalledOnce();
  await act(async () => { blocked.resolve(page([{ id: "old", display_name: "Old" } as Person])); await Promise.all(pending); });
  expect(read).toHaveBeenCalledTimes(2); expect(maximum).toBe(1); expect(result.current.items[0]?.id).toBe("fresh");
});
it("aborts a stale mutation-era response before the single queued authoritative refresh", async () => {
  const { signals, wrapper } = setup(); const old = deferred<ReturnType<typeof page<Person>>>();
  const read = vi.spyOn(api, "get").mockReturnValueOnce(old.promise).mockResolvedValue(page([{ id: "saved", display_name: "Saved" } as Person]));
  const { result } = renderHook(() => useDirectoryPage("people", []), { wrapper });
  const oldSignal = read.mock.calls[0][1]!.signal!;
  let pending!: Promise<void>; act(() => { pending = signals.publish({ type: "refresh", reason: "manual", resetHistory: false }); });
  expect(oldSignal.aborted).toBe(true); expect(read).toHaveBeenCalledOnce();
  await act(async () => { old.resolve(page([{ id: "stale", display_name: "Stale" } as Person])); await pending; });
  expect(result.current.items[0]?.id).toBe("saved"); expect(read).toHaveBeenCalledTimes(2);
});
