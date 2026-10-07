import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { UserAccount } from "../api/types";
import { createReadSignals, ReadSignalsContext } from "../lib/readSignals";
import { IntegrationsView } from "./IntegrationsView";
const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;
function fixture() {
  let changed = false;
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.endsWith("/unifi-protect/status")) { if (changed) throw new Error("Protect read offline"); return { configured: false, connected: false }; }
    if (path.endsWith("/icloud-calendar/accounts")) { if (changed) throw new Error("Calendar read offline"); return { accounts: [{ id: "cloud", apple_id: "synthetic@example.invalid", display_name: "Synthetic calendar", status: "connected", is_active: true }], recent_sync_runs: [] }; }
    if (path.endsWith("/apprise/urls")) return { urls: changed ? [] : [{ index: 0, type: "Pushover", scheme: "pover", preview: "Synthetic preview" }] };
    if (path.includes("/settings")) return [];
    return {};
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (path) => {
    if (path.includes("action-confirmations")) return { confirmation_token: "inert" };
    changed = true;
    return { status: "ok", account_count: 1, events_scanned: 0, passes_created: 0, passes_updated: 0, passes_cancelled: 0, passes_skipped: 0 };
  });
  const patch = vi.spyOn(api, "patch").mockImplementation(async () => { changed = true; return []; });
  const remove = vi.spyOn(api, "delete").mockImplementation(async () => { changed = true; return { urls: [] }; });
  const signals = createReadSignals("synthetic");
  render(<ReadSignalsContext.Provider value={signals}><IntegrationsView currentUser={admin} /></ReadSignalsContext.Provider>);
  return { get, post, patch, remove, signals };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("closes an accepted settings save while retaining a later Protect read error", async () => {
  const { patch } = fixture();
  fireEvent.click(await screen.findByRole("button", { name: /DVLA Lookup/ }));
  const modal = screen.getByRole("dialog", { name: "DVLA Lookup" });
  fireEvent.change(within(modal).getByRole("textbox", { name: /Test VRN/ }), { target: { value: "SYN123" } });
  fireEvent.click(within(modal).getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(patch).toHaveBeenCalledOnce();
  expect(screen.getByText("Protect read offline")).toBeInTheDocument();
  expect(screen.queryByText("Unable to save settings")).not.toBeInTheDocument();
});
it("retains an accepted Apprise removal when unrelated provider reads fail", async () => {
  const { remove, signals } = fixture();
  fireEvent.click(await screen.findByRole("button", { name: /Apprise/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove Pushover URL" }));
  await screen.findByText("No notification URLs configured");
  await waitFor(() => expect(screen.queryByText("Apprise URL update failed")).not.toBeInTheDocument());
  expect(remove).toHaveBeenCalledOnce();
  await waitFor(() => expect(screen.getByText("Protect read offline")).toBeInTheDocument());
  await expect(signals.publish({ type: "refresh", reason: "manual", resetHistory: false })).rejects.toThrow("Protect read offline");
});
it("shows accepted calendar sync independently of its failed metadata reload", async () => {
  const { post } = fixture();
  fireEvent.click(await screen.findByRole("button", { name: /iCloud Calendar/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Sync Calendars Now" }));
  await screen.findByText("Calendar sync complete");
  expect(screen.getByText("Calendar read offline")).toBeInTheDocument();
  expect(screen.queryByText("Calendar sync failed")).not.toBeInTheDocument();
  expect(post.mock.calls.filter(([path]) => path.endsWith("/icloud-calendar/sync"))).toHaveLength(1);
});
it("keeps a real settings mutation rejection visible and leaves the editor open", async () => {
  const { patch, get } = fixture(); patch.mockRejectedValueOnce(new Error("Settings rejected"));
  fireEvent.click(await screen.findByRole("button", { name: /DVLA Lookup/ }));
  const modal = screen.getByRole("dialog", { name: "DVLA Lookup" });
  fireEvent.click(within(modal).getByRole("button", { name: /^Save$/ }));
  await screen.findByText("Unable to save settings");
  expect(screen.getByText("Settings rejected")).toBeInTheDocument();
  expect(modal).toBeInTheDocument();
  expect(get.mock.calls.filter(([path]) => path.endsWith("/unifi-protect/status"))).toHaveLength(1);
});
