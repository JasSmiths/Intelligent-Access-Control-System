import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { Anomaly } from "../api/types";
import { AlertsView } from "./AlertsView";

const alert = (id: string, message: string): Anomaly => ({
  id, alert_ids: [id], grouped: false, type: "outside_schedule", severity: "warning",
  status: "open", message, registration_number: "TEST123", count: 1, local_date: null,
  created_at: "2026-09-28T12:00:00Z", first_seen_at: "2026-09-28T12:00:00Z",
  last_seen_at: "2026-09-28T12:00:00Z", resolved_at: null, resolved_by_user_id: null,
  resolved_by: null, resolution_note: null, snapshot_url: null,
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("loads a second older alert from same-route search navigation", async () => {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { value: vi.fn(), configurable: true });
  const fetcher = vi.fn(async (path: string) => {
    if (path.startsWith("/api/v1/alerts/history?")) return new Response(JSON.stringify({
      items: [], next_cursor: null, as_of: "2026-09-28T12:00:00Z",
    }));
    if (path === "/api/v1/alerts/id-1") return new Response(JSON.stringify(alert("id-1", "First alert")));
    if (path === "/api/v1/alerts/id-2") return new Response(JSON.stringify(alert("id-2", "Second alert")));
    throw new Error(`Unexpected URL: ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const props = { refreshDashboard: async () => undefined, refreshToken: 0, resetToken: 0 };
  const { rerender } = render(<AlertsView {...props} targetId={null} />);
  await waitFor(() => expect(screen.getByText("No alerts recorded yet.")).toBeInTheDocument());
  rerender(<AlertsView {...props} targetId="id-1" />);
  await screen.findByText("First alert");
  rerender(<AlertsView {...props} targetId="id-2" />);
  await screen.findByText("Second alert");
  expect(screen.queryByText("First alert")).not.toBeInTheDocument();
  expect(fetcher.mock.calls.map(([path]) => path)).toContain("/api/v1/alerts/id-2");
});

it("supports keyboard navigation through status tabs and associates the result panel", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({
    items: [], next_cursor: null, as_of: "2026-10-04T12:00:00Z",
  })));
  vi.stubGlobal("fetch", fetcher);
  render(<AlertsView refreshDashboard={async () => undefined} refreshToken={0} resetToken={0} targetId={null} />);
  await screen.findByText("No alerts recorded yet.");

  const open = screen.getByRole("tab", { name: "Open" });
  const resolved = screen.getByRole("tab", { name: "Resolved" });
  const all = screen.getByRole("tab", { name: "All" });
  open.focus();
  fireEvent.keyDown(open, { key: "ArrowRight" });
  expect(resolved).toHaveFocus();
  expect(resolved).toHaveAttribute("aria-selected", "true");
  expect(open).toHaveAttribute("tabindex", "-1");
  expect(screen.getByRole("tabpanel", { name: "Resolved" })).toHaveAttribute("id", resolved.getAttribute("aria-controls"));

  fireEvent.keyDown(resolved, { key: "End" });
  expect(all).toHaveFocus();
  fireEvent.keyDown(all, { key: "ArrowRight" });
  expect(open).toHaveFocus();
  fireEvent.keyDown(open, { key: "ArrowLeft" });
  expect(all).toHaveFocus();
  fireEvent.keyDown(all, { key: "Home" });
  expect(open).toHaveFocus();
  await screen.findByText("No alerts recorded yet.");
});
