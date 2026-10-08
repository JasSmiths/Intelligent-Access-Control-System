import React from "react";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import { useCalendarRunRecovery } from "./providers/useCalendarRunRecovery";
import { IntegrationModal } from "./IntegrationModal";
import { integrationDefinitions } from "./catalog";
import type { UserAccount } from "../../api/types";
import type { ICloudCalendarSyncRun } from "../../api/integrations";
const identity = "00000000-0000-4000-8000-000000000001";
const key = "iacs-calendar-run:admin:admin";
const admin = { id: "admin", role: "admin" } as UserAccount;
const receipt: ICloudCalendarSyncRun = { id: identity, started_at: null, finished_at: null, triggered_by_user_id: null, events_scanned: 0, events_matched: 0, passes_created: 0, passes_updated: 0, passes_cancelled: 0, passes_skipped: 0, error: null, status: "partial", trigger_source: "workflow", account_count: 2, account_results: [{ account_id: "complete", status: "ok" }], recovery: { original_origin: "workflow", original_actor_user_id: "admin", workflow_operation_id: identity, recovery_actor_user_id: null, account_ids: ["complete", "remaining"], completed_account_ids: ["complete"], remaining_account_ids: ["remaining"], can_resume: true, busy: false, coverage: { requested_start: "2026-10-08T00:00:00Z", requested_end: "2026-10-23T00:00:00Z", effective_start: "2026-10-08T00:00:00Z", effective_end: "2026-10-23T23:59:59Z", start_date: "2026-10-08", end_date: "2026-10-23", vendor_timezone: "Europe/London" } } };
function saveIds(original = identity, selected = identity) { sessionStorage.setItem(key, JSON.stringify({ original_request_id: original, selected_run_id: selected })); }
function wrapper({ children }: { children: React.ReactNode }) { return <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>; }
let signals = createReadSignals("first");
afterEach(() => { cleanup(); vi.restoreAllMocks(); sessionStorage.clear(); signals = createReadSignals("first"); });
it("persists only the client-known UUID before any POST and restores via its exact read", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue(receipt);
  const { result, unmount } = renderHook(() => useCalendarRunRecovery("admin", "admin"), { wrapper });
  let saved = ""; act(() => { saved = result.current.prepareNew(); });
  expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual({ original_request_id: saved, selected_run_id: saved }); expect(JSON.parse(JSON.stringify([...Array(sessionStorage.length)].map((_, i) => sessionStorage.key(i))))).toEqual([key]);
  await waitFor(() => expect(get).toHaveBeenCalledWith(`/api/v1/integrations/icloud-calendar/sync/${saved}`, expect.objectContaining({ signal: expect.any(AbortSignal) })));
  unmount(); get.mockClear(); renderHook(() => useCalendarRunRecovery("admin", "admin"), { wrapper });
  await waitFor(() => expect(get).toHaveBeenCalledOnce()); expect(get.mock.calls[0][0].endsWith(saved)).toBe(true);
});
it("retains the accepted checkpoint and known ID when a later exact read fails", async () => {
  saveIds(); let failed = false;
  vi.spyOn(api, "get").mockImplementation(async () => { if (failed) throw new ApiError("Run metadata offline", 503, {}); return receipt; });
  const { result } = renderHook(() => useCalendarRunRecovery("admin", "admin"), { wrapper });
  await waitFor(() => expect(result.current.run).toEqual(receipt)); failed = true;
  await act(async () => { await expect(result.current.refresh()).rejects.toThrow("Run metadata offline"); });
  expect(result.current.run).toEqual(receipt); expect(result.current.knownId).toBe(identity); expect(JSON.parse(sessionStorage.getItem(key)!).original_request_id).toBe(identity);
});
function mount() {
  const definition = integrationDefinitions(null, {}, null, [], "").find((item) => item.key === "icloud_calendar")!;
  return render(<ReadSignalsContext.Provider value={signals}><IntegrationModal definition={definition} currentUser={admin} initialTab="general" values={{}} loading={false} onClose={vi.fn()} onChanged={vi.fn().mockRejectedValue(new Error("Page offline"))} /></ReadSignalsContext.Provider>);
}
function fixture() {
  return vi.spyOn(api, "get").mockImplementation(async (path) => path.endsWith("/accounts") ? { accounts: [{ id: "account", display_name: "Inert Calendar", apple_id: "inert@example.invalid", is_active: true, status: "connected" }], recent_sync_runs: [{ ...receipt, id: "unrelated-recent" }] } : receipt);
}
it("shows the exact saved origin/checkpoints and explicitly confirms same-run recovery without targeting recent history", async () => {
  saveIds(); fixture(); const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const post = vi.spyOn(api, "post").mockImplementation(async (path) => path.endsWith("action-confirmations") ? { confirmation_token: "inert" } : receipt);
  mount(); const resume = await screen.findByRole("button", { name: "Resume This Sync" }); await waitFor(() => expect(resume).toBeEnabled()); fireEvent.click(resume);
  await screen.findByText("Calendar recovery receipt retained");
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("1 completed accounts will be skipped; 1 remaining accounts"));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("leaves the original workflow result unchanged"));
  expect(post.mock.calls[0][1]).toMatchObject({ action: "icloud_calendar.sync.resume", payload: { run_id: identity } });
  expect(post.mock.calls[1]).toEqual([`/api/v1/integrations/icloud-calendar/sync/${identity}/resume`, { confirmation_token: "inert" }]);
  expect(JSON.parse(sessionStorage.getItem(key)!).original_request_id).toBe(identity); expect(post.mock.calls.every(([path]) => !path.includes("unrelated-recent"))).toBe(true);
});
it("keeps the ID before a lost start response and retries only that known request after editor remount", async () => {
  const get = fixture(); let postedId = "";
  get.mockImplementation(async (path) => path.endsWith("/accounts") ? { accounts: [{ id: "account", display_name: "Inert Calendar", apple_id: "inert@example.invalid", is_active: true, status: "connected" }], recent_sync_runs: [] } : { ...receipt, id: postedId });
  const post = vi.spyOn(api, "post").mockImplementation(async (path, body) => { postedId = (body as { run_id: string }).run_id; expect(JSON.parse(sessionStorage.getItem(key)!).original_request_id).toBe(postedId); throw new Error("Lost HTTP response"); });
  const mounted = mount(); const sync = await screen.findByRole("button", { name: "Sync Calendars Now" }); await waitFor(() => expect(sync).toBeEnabled()); fireEvent.click(sync);
  await screen.findByText("Calendar sync response unavailable"); expect(post).toHaveBeenCalledOnce(); mounted.unmount();
  post.mockClear(); mount(); await screen.findByText(postedId);
  await waitFor(() => expect(get.mock.calls.some(([path]) => path.endsWith(`/sync/${postedId}`))).toBe(true)); expect(post).not.toHaveBeenCalled();
});

it.each(["old-success", "old-404"])("fences %s exact GET after an accepted final receipt without a realtime signal", async (kind) => {
  saveIds(); let resolve!: (value: ICloudCalendarSyncRun) => void; let reject!: (error: Error) => void;
  const delayed = new Promise<ICloudCalendarSyncRun>((yes, no) => { resolve = yes; reject = no; });
  const get = vi.spyOn(api, "get").mockReturnValue(delayed);
  const { result } = renderHook(() => useCalendarRunRecovery("admin", "admin"), { wrapper });
  await waitFor(() => expect(get).toHaveBeenCalledOnce());
  const accepted = { ...receipt, status: "ok", recovery: { ...receipt.recovery!, remaining_account_ids: [], can_resume: false } };
  act(() => result.current.accept(accepted));
  await act(async () => { if (kind === "old-success") resolve(receipt); else reject(new ApiError("Old admission404", 404, {})); });
  expect(result.current.run).toEqual(accepted); expect(result.current.error).toBe(""); expect(result.current.missing).toBe(false); expect(result.current.reading).toBe(false);
});
it("selects bounded workflow history read-only while retaining and returning to the original lost-response request", async () => {
  const workflowId = "00000000-0000-4000-8000-000000000002"; saveIds();
  vi.spyOn(api, "get").mockImplementation(async (path) => path.endsWith("/accounts") ? { accounts: [{ id: "account", display_name: "Inert Calendar", apple_id: "inert@example.invalid", is_active: true, status: "connected" }], recent_sync_runs: [{ ...receipt, id: workflowId }] } : { ...receipt, id: path.endsWith(workflowId) ? workflowId : identity });
  const post = vi.spyOn(api, "post"); mount();
  fireEvent.click(await screen.findByRole("button", { name: `Read run ${workflowId}` }));
  await screen.findByRole("button", { name: "Resume This Sync" });
  expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual({ original_request_id: identity, selected_run_id: workflowId });
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Read Original Request" }));
  await screen.findByText(identity); expect(JSON.parse(sessionStorage.getItem(key)!).original_request_id).toBe(identity); expect(post).not.toHaveBeenCalled();
});
it("fences run state and late read errors when the signed-in account is replaced", async () => {
  saveIds(); let reject!: (error: Error) => void; const blocked = new Promise<ICloudCalendarSyncRun>((_, no) => { reject = no; });
  const get = vi.spyOn(api, "get").mockReturnValue(blocked);
  const { result, rerender } = renderHook(({ account }) => useCalendarRunRecovery(account, "admin"), { wrapper, initialProps: { account: "admin" } });
  await waitFor(() => expect(get).toHaveBeenCalledOnce()); const signal = get.mock.calls[0][1]?.signal;
  rerender({ account: "replacement" }); expect(result.current.knownId).toBe(""); expect(result.current.run).toBeNull(); expect(signal?.aborted).toBe(true);
  await act(async () => reject(new Error("Old account read failed")));
  expect(result.current.error).toBe(""); expect(result.current.knownId).toBe("");
});
