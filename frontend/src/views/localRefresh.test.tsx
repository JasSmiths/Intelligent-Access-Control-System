import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Anomaly, Person, Schedule, UserAccount, Vehicle } from "../api/types";
import { createReadSignals, ReadSignalsContext } from "../lib/readSignals";
import { useOperationalReads } from "../app/operationalReads";
import { CircleDot } from "lucide-react";
import { PassesView } from "./PassesView";
import { UsersView } from "./UsersView";
import { AccessDevicesSettingsView } from "./AccessDevicesSettingsView";
import type { VisitorPass } from "../features/passes/types";
import { AlertsView } from "./AlertsView";
import { RecoveryConfiguration } from "../features/missedExitRecovery/RecoveryConfiguration";
import { PeopleView } from "./PeopleView";
import { VehiclesView } from "./VehiclesView";
import { SchedulesView } from "../features/schedules/SchedulesView";

const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;
const person = { id: "person", first_name: "Ash", last_name: "Smith", display_name: "Ash Smith", vehicles: [], is_active: true, garage_door_entity_ids: [], home_assistant_presence_input_boolean_entity_ids: [] } as unknown as Person;
const vehicle = { id: "vehicle", registration_number: "TEST123", person_ids: [], is_active: true } as unknown as Vehicle;
const schedule = { id: "schedule", name: "Synthetic schedule", description: "", time_blocks: [] } as unknown as Schedule;
const page = <T,>(items: T[]) => ({ items, total: items.length, next_cursor: null });
function deferred<T>() { let reject!: (error: Error) => void; const promise = new Promise<T>((_, no) => { reject = no; }); return { promise, reject }; }
function compose(children: React.ReactNode | ((refreshAlerts: () => Promise<void>) => React.ReactNode)) {
  const signals = createReadSignals("synthetic");
  function Owners() {
    const { refreshAlerts } = useOperationalReads("people", admin, signals, { anomalies: () => {}, integrationStatus: () => {}, maintenanceStatus: () => {} });
    return <ReadSignalsContext.Provider value={signals}>{typeof children === "function" ? children(refreshAlerts) : children}</ReadSignalsContext.Provider>;
  }
  return render(<Owners />);
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("finishes a person save locally while maintenance is blocked, without misreporting its later rejection", async () => {
  const maintenance = deferred<unknown>(); let changed = false;
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.includes("maintenance")) return maintenance.promise;
    if (path.startsWith("/api/v1/people?")) return page([changed ? { ...person, first_name: "Updated", display_name: "Updated Smith" } : person]);
    if (path.startsWith("/api/v1/vehicles?")) return page([]);
    return [];
  });
  vi.spyOn(api, "post").mockResolvedValue({ confirmation_token: "inert" });
  const save = vi.spyOn(api, "patch").mockImplementation(async () => { changed = true; return person; });
  compose(<PeopleView garageDoors={[]} query="" />);
  fireEvent.click(await screen.findByRole("button", { name: /Unassigned.*1 person/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit person Ash Smith" }));
  fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Updated" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
  await screen.findByRole("button", { name: "Edit person Updated Smith" });
  expect(save).toHaveBeenCalledOnce();
  expect(screen.getByText("Person saved.")).toBeInTheDocument();
  expect(screen.queryByText(/list could not be refreshed/)).not.toBeInTheDocument();
  expect(get.mock.calls.filter(([path]) => path.includes("maintenance"))).toHaveLength(1);
  await act(async () => maintenance.reject(new Error("Unrelated maintenance offline")));
  expect(screen.getByText("Person saved.")).toBeInTheDocument();
  expect(screen.queryByText(/list could not be refreshed/)).not.toBeInTheDocument();
});
it("retries a failed people page through its feature owners without fetching operational state", async () => {
  let failed = true;
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.startsWith("/api/v1/people?")) { if (failed) throw new Error("People offline"); return page([person]); }
    return [];
  });
  compose(<PeopleView garageDoors={[]} query="" />);
  await screen.findAllByText("People offline");
  const operational = () => get.mock.calls.filter(([path]) => path.includes("maintenance") || path.includes("alerts?") || path.includes("gate/status")).length;
  expect(operational()).toBe(3);
  failed = false; fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  fireEvent.click(await screen.findByRole("button", { name: /Unassigned.*1 person/ }));
  await screen.findByRole("button", { name: "Edit person Ash Smith" });
  expect(operational()).toBe(3);
});
for (const kind of ["Vehicle", "Schedule"] as const) {
  it(`keeps an accepted ${kind.toLowerCase()} deletion distinct from a failed local reload and removes the stale delete target`, async () => {
    let deleted = false;
    const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path.startsWith("/api/v1/vehicles?")) { if (deleted && kind === "Vehicle") throw new Error("Directory offline"); return page([vehicle]); }
      if (path === "/api/v1/schedules") { if (deleted && kind === "Schedule") throw new Error("Schedule offline"); return kind === "Schedule" ? [schedule] : []; }
        return [];
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(api, "post").mockResolvedValue({ confirmation_token: "inert" });
    const remove = vi.spyOn(api, "delete").mockImplementation(async () => { deleted = true; });
    compose(kind === "Vehicle" ? <VehiclesView canRefreshInformation={false} query="" /> : <SchedulesView query="" />);
    const label = kind === "Vehicle" ? "TEST123" : "Synthetic schedule";
    if (kind === "Vehicle") fireEvent.click(await screen.findByRole("button", { name: /Unassigned.*1 vehicle/ }));
    fireEvent.click(await screen.findByRole("button", { name: `Delete ${label}` }));
    await screen.findByText(`${kind} deleted, but the list could not be refreshed. Try again to reload the list.`);
    expect(screen.getByText(`${kind} deleted.`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: `Delete ${label}` })).not.toBeInTheDocument();
    expect(remove).toHaveBeenCalledOnce();
    expect(get.mock.calls.filter(([path]) => path.includes("maintenance"))).toHaveLength(1);
  });
}

it("settles one alert history reload plus the shared alert read while unrelated maintenance remains blocked", async () => {
  const maintenance = deferred<unknown>(); let resolved = false;
  const alert = { id: "alert", alert_ids: ["alert"], grouped: false, type: "outside_schedule", severity: "warning", status: "open", message: "Synthetic alert", registration_number: "TEST123", count: 1, created_at: "2026-10-07T12:00:00Z", first_seen_at: "2026-10-07T12:00:00Z", last_seen_at: "2026-10-07T12:00:00Z" } as Anomaly;
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.includes("maintenance")) return maintenance.promise;
    if (path.includes("alerts/history?")) return { items: resolved ? [] : [alert], next_cursor: null, as_of: "2026-10-07T12:00:00Z" };
    return [];
  });
  const save = vi.spyOn(api, "patch").mockImplementation(async () => { resolved = true; return {}; });
  compose((refreshAlerts) => <AlertsView targetId={null} refreshAlerts={refreshAlerts} />);
  fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));
  fireEvent.click(screen.getByRole("button", { name: "Resolve Alert" }));
  await screen.findByText("No alerts recorded yet.");
  expect(save).toHaveBeenCalledOnce();
  expect(get.mock.calls.filter(([path]) => path.includes("alerts/history?"))).toHaveLength(2);
  expect(get.mock.calls.filter(([path]) => path.includes("alerts?status=open"))).toHaveLength(2);
  expect(get.mock.calls.filter(([path]) => path.includes("maintenance"))).toHaveLength(1);
  await act(async () => maintenance.reject(new Error("Maintenance offline")));
  expect(screen.queryByText(/Alert saved, but/)).not.toBeInTheDocument();
});
it("refreshes recovery owner identities locally after an accepted save while maintenance remains blocked", async () => {
  const maintenance = deferred<unknown>(); let changed = false;
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.includes("maintenance")) return maintenance.promise;
    if (path.startsWith("/api/v1/people?")) return page([changed ? { ...person, missed_exit_recovery_enabled: true } : person]);
    if (path.includes("recovery-trackers")) return { status: "complete", trackers: [], mappings: [] };
    return [];
  });
  vi.spyOn(api, "post").mockResolvedValue({ confirmation_token: "inert" });
  const save = vi.spyOn(api, "patch").mockImplementation(async () => { changed = true; return { ...person, missed_exit_recovery_enabled: true }; });
  compose(<RecoveryConfiguration />);
  await screen.findByRole("option", { name: "Ash Smith" });
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: person.id } });
  fireEvent.change(screen.getByRole("combobox", { name: "Owner recovery opt-in" }), { target: { value: "true" } });
  fireEvent.click(screen.getByRole("button", { name: "Save owner configuration" }));
  await screen.findByText("Owner recovery settings saved.");
  await waitFor(() => expect(screen.getByRole("button", { name: "Save owner configuration" })).toBeEnabled());
  expect(save).toHaveBeenCalledOnce();
  expect(get.mock.calls.filter(([path]) => path.includes("maintenance"))).toHaveLength(1);
  expect(screen.queryByText(/directory could not be refreshed/)).not.toBeInTheDocument();
  await act(async () => maintenance.reject(new Error("Maintenance offline")));
  expect(screen.queryByText(/directory could not be refreshed/)).not.toBeInTheDocument();
});

for (const action of ["cancel", "delete"] as const) {
  it(`retains an accepted pass ${action} when the list reload fails`, async () => {
    let changed = false;
    const pass = { id: "visitor", visitor_name: "Synthetic visitor", pass_type: "one-time", status: "active", expected_time: "2026-10-07T12:00:00Z", window_start: "2026-10-07T12:00:00Z", window_end: "2027-10-07T12:00:00Z", created_at: "2026-10-07T12:00:00Z", updated_at: "2026-10-07T12:00:00Z", window_minutes: 30, creation_source: "manual" } as VisitorPass;
    vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path.startsWith("/api/v1/visitor-passes")) { if (changed) throw new Error("Pass read offline"); return [pass]; }
      return [];
    });
    const post = vi.spyOn(api, "post").mockImplementation(async (path) => {
      if (path.includes("action-confirmations")) return { confirmation_token: "inert" };
      changed = true; return { ...pass, status: "cancelled" };
    });
    const remove = vi.spyOn(api, "delete").mockImplementation(async () => { changed = true; });
    compose(<PassesView query="" />);
    fireEvent.click(await screen.findByRole("button", { name: /Synthetic visitor/ }));
    fireEvent.click(screen.getByRole("button", { name: action === "cancel" ? "Cancel pass" : "Delete pass" }));
    const confirmation = screen.getByRole("dialog", { name: action === "cancel" ? "Cancel Visitor Pass?" : "Delete Visitor Pass?" });
    fireEvent.click(within(confirmation).getByRole("button", { name: action === "cancel" ? "Cancel pass" : "Delete pass" }));
    await screen.findByText(`Pass ${action === "cancel" ? "cancelled" : "deleted"}, but the list could not be refreshed. Refresh to retry the read.`);
    await waitFor(() => expect(confirmation).not.toBeInTheDocument());
    if (action === "delete") { expect(screen.queryByRole("dialog", { name: "Synthetic visitor" })).not.toBeInTheDocument(); expect(remove).toHaveBeenCalledOnce(); }
    else { expect(screen.queryByRole("button", { name: "Cancel pass" })).toBeDisabled(); expect(post.mock.calls.filter(([path]) => path.endsWith("/cancel"))).toHaveLength(1); }
  });
}
it("keeps the device save receipt after its authoritative read fails", async () => {
  let changed = false;
  const device = { id: "gate", key: "synthetic", kind: "gate", name: "Synthetic gate", enabled: true, bindings: [], sort_order: 0, open_for_access: true };
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.startsWith("/api/v1/access-devices?")) { if (changed) throw new Error("Device read offline"); return [device]; }
    return [];
  });
  vi.spyOn(api, "post").mockResolvedValue({ confirmation_token: "inert" });
  const save = vi.spyOn(api, "patch").mockImplementation(async () => { changed = true; return device; });
  const bindings = vi.spyOn(api, "put").mockResolvedValue(device);
  compose(<AccessDevicesSettingsView kind="gate" title="Gates" icon={CircleDot} currentUser={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Save Device" }));
  await screen.findByText(/Device saved, but device data could not be refreshed.*Accepted: Device settings; Home Assistant binding; ESPHome binding/);
  expect(screen.getByText("Device saved.")).toBeInTheDocument();
  expect(save).toHaveBeenCalledOnce();
  expect(bindings).toHaveBeenCalledTimes(2); // The two confirmed provider bindings.
});
for (const action of ["delete", "deactivate"] as const) {
  it(`keeps accepted user ${action} state after a failed list read`, async () => {
    let changed = false;
    const user = { id: "other", first_name: "Synthetic", last_name: "User", full_name: "Synthetic User", username: "synthetic", role: "standard", is_active: true } as UserAccount;
    vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path === "/api/v1/users") { if (changed) throw new Error("Users offline"); return [user]; }
      return [];
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(api, "post").mockResolvedValue({ confirmation_token: "inert" });
    const remove = vi.spyOn(api, "delete").mockImplementation(async () => { changed = true; });
    const save = vi.spyOn(api, "patch").mockImplementation(async () => { changed = true; return { ...user, is_active: false }; });
    compose(<UsersView currentUser={admin} onCurrentUserUpdated={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: action === "delete" ? "Delete Synthetic User" : "Deactivate" }));
    await screen.findByText(`User ${action === "delete" ? "deleted" : "updated"}, but the list could not be refreshed. Refresh to retry the read.`);
    if (action === "delete") { expect(screen.queryByRole("button", { name: "Delete Synthetic User" })).not.toBeInTheDocument(); expect(remove).toHaveBeenCalledOnce(); }
    else { expect(screen.getByRole("button", { name: "Activate" })).toBeEnabled(); expect(save).toHaveBeenCalledOnce(); }
  });
}
