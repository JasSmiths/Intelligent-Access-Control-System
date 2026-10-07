import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import * as directory from "../../api/directory";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import type { VisitorPass } from "../passes/types";
import { useReportSubjects } from "./useReportSubjects";
const remote = { id: "after-500", visitor_name: "Remote Visitor", visitor_phone: "07700123999" } as VisitorPass;
function setup() {
  vi.spyOn(directory, "readDirectory").mockResolvedValue({ items: [], total: 0, next_cursor: null });
  const signals = createReadSignals("test");
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
  return { signals, wrapper };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("searches the server with limit8 beyond the first500 and hydrates only the selected pass while closed", async () => {
  const { signals, wrapper } = setup(); let removed = false;
  const read = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.includes("/after-500")) { if (removed) throw new ApiError("Not found", 404, {}); return remote; }
    return [remote];
  });
  const { result, rerender } = renderHook(({ open, selected }) => useReportSubjects("07700123999", open, "", selected), { wrapper, initialProps: { open: false, selected: "" } });
  expect(read).not.toHaveBeenCalled();
  rerender({ open: true, selected: "" });
  await waitFor(() => expect(result.current.visitorPasses).toEqual([remote]));
  expect(read.mock.calls.map(([path]) => path)).toEqual(["/api/v1/visitor-passes?limit=8&q=07700123999"]);
  rerender({ open: false, selected: remote.id });
  await waitFor(() => expect(result.current.selectedPass).toEqual(remote));
  expect(read.mock.calls[1][0]).toBe("/api/v1/visitor-passes/after-500");
  removed = true;
  await act(async () => { await expect(signals.publish({ type: "refresh", reason: "manual", resetHistory: false })).rejects.toThrow("Not found"); });
  expect(result.current.selectedPass).toBeNull(); expect(result.current.missingPassId).toBe(remote.id);
  expect(read.mock.calls.filter(([path]) => path.includes("?"))).toHaveLength(1);
});
it("keeps a selected-detail failure through independent successful search and clears it on successful detail retry", async () => {
  const { signals, wrapper } = setup(); let failed = true;
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.includes("/after-500")) { if (failed) throw new Error("Selected detail offline"); return remote; }
    return [remote];
  });
  const { result, rerender } = renderHook(({ query }) => useReportSubjects(query, true, "", remote.id), { wrapper, initialProps: { query: "" } });
  await waitFor(() => expect(result.current.error).toBe("Selected detail offline"));
  rerender({ query: "07700123999" });
  await waitFor(() => expect(directory.readDirectory).toHaveBeenLastCalledWith("people", expect.objectContaining({ q: "07700123999" }), expect.anything()));
  expect(result.current.visitorPasses).toEqual([remote]); expect(result.current.error).toBe("Selected detail offline");
  failed = false;
  await act(async () => { await signals.publish({ type: "refresh", reason: "manual", resetHistory: false }); });
  expect(result.current.selectedPass).toEqual(remote); expect(result.current.error).toBe("");
});

it("retires failed selected-detail state when the subject is deselected or changed to a person", async () => {
  const { wrapper } = setup();
  vi.spyOn(directory, "lookupDirectory").mockResolvedValue([]);
  vi.spyOn(api, "get").mockRejectedValue(new Error("Selected detail offline"));
  const { result, rerender } = renderHook(({ personId, passId }) => useReportSubjects("", false, personId, passId), { wrapper, initialProps: { personId: "", passId: remote.id } });
  await waitFor(() => expect(result.current.error).toBe("Selected detail offline"));
  rerender({ personId: "", passId: "" });
  expect(result.current.error).toBe(""); expect(result.current.selectedPass).toBeNull();
  rerender({ personId: "person", passId: "" });
  await waitFor(() => expect(directory.lookupDirectory).toHaveBeenCalledWith("people", ["person"], expect.anything()));
  expect(result.current.error).toBe("");
});
