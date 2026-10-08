import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import { PassesView } from "../../views/PassesView";
import { useSelectedPassMetadata } from "./useSelectedPassMetadata";
import type { VisitorPass } from "./types";
const visitor = { id: "selected", visitor_name: "Selected Calendar Visitor", pass_type: "one-time", status: "active", creation_source: "icloud_calendar", expected_time: "2026-10-08T12:00:00Z", window_start: "2026-10-08T11:30:00Z", window_end: "2026-10-08T12:30:00Z", created_at: "2026-10-08T08:00:00Z", updated_at: "2026-10-08T08:00:00Z", window_minutes: 30 } as VisitorPass;
function setup() {
  const signals = createReadSignals("first");
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
  let now = Date.now(); vi.spyOn(Date, "now").mockImplementation(() => now);
  const invalidate = async () => { now += 6000; await act(async () => { signals.publish({ type: "realtime", message: { type: "visitor_pass.cancelled", payload: { source: "icloud_calendar", cancelled_count: 1 } } }); }); };
  return { signals, wrapper, invalidate };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("hydrates open details after an aggregate cancellation removes the pass from the filtered list", async () => {
  const { wrapper, invalidate } = setup(); let cancelled = false;
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => path.includes("/selected") ? { ...visitor, status: cancelled ? "cancelled" : "active" } : cancelled ? [] : [visitor]);
  render(<PassesView query="" />, { wrapper });
  fireEvent.click(await screen.findByRole("button", { name: /Selected Calendar Visitor/ }));
  await screen.findByRole("dialog", { name: visitor.visitor_name });
  await waitFor(() => expect(get.mock.calls.filter(([path]) => path.endsWith("/selected"))).toHaveLength(1));
  cancelled = true; await invalidate();
  await screen.findByText("No Visitor Passes match this view");
  const dialog = screen.getByRole("dialog", { name: visitor.visitor_name });
  expect(await within(dialog).findByText("Cancelled")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: /Edit/ })).toBeDisabled();
  expect(get.mock.calls.filter(([path]) => path.endsWith("/selected"))).toHaveLength(2);
  expect(get.mock.calls.every(([path]) => path.startsWith("/api/v1/visitor-passes"))).toBe(true);
});
it("retains an editor draft while selected metadata cancels its pass and blocks saving", async () => {
  const { wrapper, invalidate } = setup(); let cancelled = false;
  vi.spyOn(api, "get").mockImplementation(async (path) => path.includes("/selected") ? { ...visitor, status: cancelled ? "cancelled" : "active" } : cancelled ? [] : [visitor]);
  const post = vi.spyOn(api, "post"); const patch = vi.spyOn(api, "patch");
  render(<PassesView query="" />, { wrapper });
  fireEvent.click(await screen.findByRole("button", { name: /Selected Calendar Visitor/ }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: visitor.visitor_name })).getByRole("button", { name: /Edit/ }));
  const field = await screen.findByLabelText("Visitor name"); fireEvent.change(field, { target: { value: "Retained local draft" } });
  cancelled = true; await invalidate();
  await waitFor(() => expect(screen.getByRole("button", { name: "Save Pass" })).toBeDisabled());
  expect(field).toHaveValue("Retained local draft");
  fireEvent.submit(screen.getByRole("dialog", { name: "Visitor pass" }));
  expect(post).not.toHaveBeenCalled(); expect(patch).not.toHaveBeenCalled();
});
it("deduplicates selected identities and fences a late response after selection/account replacement", async () => {
  const first = createReadSignals("first"), second = createReadSignals("second");
  let resolve!: (row: VisitorPass) => void;
  const blocked = new Promise<VisitorPass>((yes) => { resolve = yes; });
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => path.endsWith("/selected") ? blocked : { ...visitor, id: "replacement" });
  const detail = vi.fn(), editor = vi.fn();
  const mounted = render(<ReadSignalsContext.Provider value={first}><Selected id="selected" detail={detail} editor={editor} /></ReadSignalsContext.Provider>);
  await waitFor(() => expect(get).toHaveBeenCalledOnce());
  const oldSignal = get.mock.calls[0][1]?.signal;
  mounted.rerender(<ReadSignalsContext.Provider value={second}><Selected id="replacement" detail={detail} editor={editor} /></ReadSignalsContext.Provider>);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  expect(oldSignal?.aborted).toBe(true);
  const count = detail.mock.calls.length;
  await act(async () => resolve(visitor));
  expect(detail).toHaveBeenCalledTimes(count); expect(editor).toHaveBeenCalledTimes(count);
});
function Selected({ id, detail, editor }: { id: string; detail: React.Dispatch<React.SetStateAction<VisitorPass | null>>; editor: React.Dispatch<React.SetStateAction<VisitorPass | null>> }) {
  useSelectedPassMetadata(id, id, detail, editor); return null;
}
it("keeps a selected failure local and blocks details actions while allowing close", async () => {
  const { wrapper, signals } = setup();
  vi.spyOn(api, "get").mockImplementation(async (path) => { if (path.endsWith("/selected")) throw new ApiError("Selected metadata offline", 503, {}); return [visitor]; });
  render(<PassesView query="" />, { wrapper });
  fireEvent.click(await screen.findByRole("button", { name: /Selected Calendar Visitor/ }));
  await screen.findAllByText("Selected metadata offline");
  const dialog = screen.getByRole("dialog", { name: visitor.visitor_name });
  expect(within(dialog).getByRole("button", { name: /Edit/ })).toBeDisabled();
  expect(within(dialog).getAllByRole("button", { name: "Close" }).every((button) => !(button as HTMLButtonElement).disabled)).toBe(true);
  await act(async () => { signals.publish({ type: "realtime", message: { type: "visitor_pass.updated", payload: { visitor_pass: visitor } } }); });
  await waitFor(() => expect(within(dialog).getByRole("button", { name: /Edit/ })).toBeEnabled());
  expect(screen.queryByText("Selected Visitor Pass needs attention")).not.toBeInTheDocument();
});

it("does not replace a full cancelled realtime row with a late active selected GET, retaining its draft", async () => {
  const { signals, wrapper } = setup(); let reads = 0; let resolve!: (row: VisitorPass) => void;
  const blocked = new Promise<VisitorPass>((yes) => { resolve = yes; });
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (!path.endsWith("/selected")) return [visitor];
    reads += 1; return reads === 1 ? visitor : blocked;
  });
  render(<PassesView query="" />, { wrapper });
  fireEvent.click(await screen.findByRole("button", { name: /Selected Calendar Visitor/ }));
  await waitFor(() => expect(reads).toBe(1));
  fireEvent.click(within(screen.getByRole("dialog", { name: visitor.visitor_name })).getByRole("button", { name: /Edit/ }));
  await waitFor(() => expect(reads).toBe(2));
  const field = screen.getByLabelText("Visitor name"); fireEvent.change(field, { target: { value: "Retained full-event draft" } });
  await act(async () => { signals.publish({ type: "realtime", message: { type: "visitor_pass.cancelled", payload: { visitor_pass: { ...visitor, status: "cancelled" } } } }); });
  expect(screen.getByRole("button", { name: "Save Pass" })).toBeDisabled();
  await act(async () => resolve(visitor));
  expect(field).toHaveValue("Retained full-event draft"); expect(screen.getByRole("button", { name: "Save Pass" })).toBeDisabled();
});
