import * as directoryApi from "../api/directory";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AccessEvent, Anomaly, IntegrationStatus, UserAccount } from "../api/types";
import { Dashboard } from "./DashboardView";

const integrationStatus = { configured: true, connected: true, gate_entities: [], garage_door_entities: [] } as unknown as IntegrationStatus;
const props = {
  presence: [], expectedPresence: null, events: [], anomalies: [], integrationStatus,
  maintenanceStatus: null, people: [], vehicles: [], refresh: vi.fn(),
  currentUser: { id: "dashboard-presentation", role: "admin", first_name: "Test" } as UserAccount,
  navigateToView: vi.fn(), onMaintenanceStatusChanged: vi.fn()
};
beforeEach(() => {
  vi.spyOn(directoryApi, "listVehicles").mockResolvedValue({ items: [], total: 0, next_cursor: null });
  vi.spyOn(directoryApi, "lookupVehicleRegistrations").mockResolvedValue([]);
  vi.spyOn(directoryApi, "lookupPeople").mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); sessionStorage.clear(); });

it.each([true, false])("links grouped=%s alerts to a durable record UUID", (grouped) => {
  const memberId = "d882ffcb-4a35-4c77-bda5-fb8c1b0bab31";
  render(<Dashboard {...props} anomalies={[{
    id: grouped ? "group:unauthorized_plate:2026-10-05:TEST123" : memberId,
    alert_ids: [memberId], grouped, type: "unauthorized_plate", severity: "warning",
    status: "open", message: "Unauthorised Plate, Access Denied",
    created_at: "2026-10-05T12:00:00Z", count: 1, registration_number: "TEST123"
  } as Anomaly]} />);
  fireEvent.click(screen.getByRole("button", { name: /Unauthorised Plate, Access Denied/ }));
  expect(props.navigateToView).toHaveBeenLastCalledWith("alerts", { search: `?alert=${memberId}` });
});

it.each([
  { severity: "warning", title: "Action needed", tone: "attention" },
  { severity: "critical", title: "Critical alerts", tone: "degraded" }
] as const)("does not present $severity alerts as a healthy site", ({ severity, title, tone }) => {
  render(<Dashboard {...props} anomalies={[{
    id: "alert", type: "access_denied", severity, status: "open", message: "Access requires review",
    created_at: "2026-10-04T00:00:00Z", count: 1, registration_number: "TEST"
  } as Anomaly]} />);
  const status = screen.getByText(title, { selector: "strong" }).closest(".site-status-main");
  expect(status).toHaveClass(tone);
  expect(status).not.toHaveClass("normal");
  expect(screen.queryByText("All systems normal")).not.toBeInTheDocument();
});

it("keeps unavailable integration status distinct from a clear alert feed", () => {
  render(<Dashboard {...props} integrationStatus={null} />);
  expect(screen.getByText("Device status unavailable").closest(".site-status-main")).toHaveClass("attention");
  expect(screen.getByText("No actionable alerts")).toBeInTheDocument();
  expect(screen.queryByText(/Latest \d+ events? from your feed/)).not.toBeInTheDocument();
});

it("connects overview navigation to the existing read-only views", () => {
  render(<Dashboard {...props} />);
  const recent = screen.getByRole("heading", { name: "Recent Events" }).closest(".card") as HTMLElement;
  fireEvent.click(within(recent).getByRole("button", { name: "View all" }));
  expect(props.navigateToView).toHaveBeenLastCalledWith("events");
  fireEvent.click(screen.getByRole("button", { name: "People" }));
  expect(props.navigateToView).toHaveBeenLastCalledWith("people");
  fireEvent.click(screen.getByRole("button", { name: "Manage" }));
  expect(props.navigateToView).toHaveBeenLastCalledWith("settings_gates");
});

it("labels feed source counts accurately and shows the actual recent event count", () => {
  render(<Dashboard {...props} events={[{
    id: "recent", source: "camera", occurred_at: new Date().toISOString(),
    registration_number: "TEST", direction: "entry", decision: "granted"
  } as AccessEvent]} />);
  expect(screen.getByText("Event sources")).toBeInTheDocument();
  expect(screen.queryByText("Live sources")).not.toBeInTheDocument();
  expect(screen.getByText("Latest 1 event from your feed")).toBeInTheDocument();
});
