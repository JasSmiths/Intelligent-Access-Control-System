import * as directoryApi from "../api/directory";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { integrationsApi, type GateCommandReceipt } from "../api/integrations";
import type { AccessEvent, ActionConfirmation, ExpectedPresenceSummary, IntegrationStatus, Person, Presence, UserAccount, Vehicle } from "../api/types";
import contract from "../api/fixtures/gateCommandReceipts.generated.json";
import { CommandReceiptDetails } from "../features/integrations/CommandReceiptDetails";
import { Dashboard } from "./DashboardView";
import { formatTime } from "../features/dashboard/model";

const admin = { id: "synthetic-admin", first_name: "Synthetic", last_name: "Admin", role: "admin" } as UserAccount;
const confirmation = { confirmation_id: "synthetic-intent", confirmation_token: "synthetic-token", action: "gate.open", expires_at: "2026-09-12T15:00:00Z" };
const storageKey = `iacs-dashboard-commands:${admin.id}:${admin.role}`;
const entry = { entity_id: "entry", name: "Entry gate", state: "closed", enabled: true };
const secondary = { entity_id: "secondary", name: "Secondary gate", state: "closed", enabled: true };
const integrationStatus: IntegrationStatus = {
  configured: true, connected: true, gate_entity_id: null, default_media_player: null,
  last_gate_state: "unknown", gate_entities: [entry, secondary]
};
const receipt = (name: string) => contract.cases.find((item) => item.name === name)!.outcome as GateCommandReceipt;
const props = { presence: [], expectedPresence: null, events: [], anomalies: [], integrationStatus,
  maintenanceStatus: null, people: [], vehicles: [], refresh: vi.fn().mockResolvedValue(undefined), currentUser: admin, navigateToView: vi.fn(), onMaintenanceStatusChanged: vi.fn() };
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function clickGate(name: string) {
  const row = screen.getByText(name, { selector: "strong" }).closest(".gate-row") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: "Closed" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: `Open ${name}` })));
}
beforeEach(() => {
  vi.spyOn(directoryApi, "listVehicles").mockResolvedValue({ items: [], total: 0, next_cursor: null });
  vi.spyOn(directoryApi, "lookupVehicleRegistrations").mockResolvedValue([]);
  vi.spyOn(directoryApi, "lookupPeople").mockResolvedValue([]);
  vi.useFakeTimers();
  sessionStorage.clear();
  props.refresh.mockClear();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.spyOn(integrationsApi, "confirmGateOpen").mockResolvedValue(confirmation);
  vi.spyOn(integrationsApi, "getGateCommandByIntent").mockResolvedValue(receipt("accepted_unverified"));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); });

it("targets the selected gate, saves its recovery ID before POST, and never invents physical opening", async () => {
  const pending = deferred<GateCommandReceipt>();
  const send = vi.spyOn(integrationsApi, "openGate").mockImplementation(() => {
    expect(sessionStorage.getItem(storageKey)).toContain("synthetic-intent");
    return pending.promise;
  });
  render(<Dashboard {...props} />);
  await clickGate("Secondary gate");
  const payload = { reason: "Dashboard Secondary gate open command", target_device_key: "secondary" };
  expect(integrationsApi.confirmGateOpen).toHaveBeenCalledWith(payload, "Secondary gate");
  expect(send).toHaveBeenCalledWith(payload, "synthetic-token", expect.anything());
  expect(screen.queryByText("Opening")).not.toBeInTheDocument();
  await act(async () => pending.resolve(receipt("accepted_unverified")));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText("Request accepted · Physical state not verified")).toBeInTheDocument();
  expect(screen.queryByText(/Physical open verified/)).not.toBeInTheDocument();
  expect(send).toHaveBeenCalledOnce();
});
it("renders mixed 503 target results and only rechecks with GET", async () => {
  const partial = receipt("partial_entry_verified");
  vi.mocked(integrationsApi.getGateCommandByIntent).mockResolvedValue(partial);
  const send = vi.spyOn(integrationsApi, "openGate").mockRejectedValue(new ApiError("503 Request failed", 503, partial));
  render(<Dashboard {...props} integrationStatus={{ ...integrationStatus, gate_entities: [], current_gate_state: "closed" }} />);
  await clickGate("All configured access gates");
  expect(integrationsApi.confirmGateOpen).toHaveBeenCalledWith({ reason: "Dashboard All configured access gates open command" }, "All configured access gates");
  expect(screen.getByText("Request accepted · Physical open verified")).toBeInTheDocument();
  expect(screen.getByText("Delivery unknown · Physical state not verified")).toBeInTheDocument();
  expect(screen.getByText(/Entry admission verified/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Dismiss result" })).not.toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Check action result" })));
  expect(integrationsApi.getGateCommandByIntent).toHaveBeenCalledTimes(2);
  expect(send).toHaveBeenCalledOnce();
});
it("retains a conclusive partial 503 without turning accepted targets into unknown or repeating POST", async () => {
  const partial = receipt("partial_entry_verified_other_rejected");
  vi.mocked(integrationsApi.getGateCommandByIntent).mockResolvedValue(partial);
  const send = vi.spyOn(integrationsApi, "openGate").mockRejectedValue(new ApiError("Some targets rejected the command", 503, partial));
  render(<Dashboard {...props} integrationStatus={{ ...integrationStatus, gate_entities: [], current_gate_state: "closed" }} />);
  await clickGate("All configured access gates");
  expect(screen.getByText(/Partial command delivery/)).toBeInTheDocument();
  expect(screen.getByText("Request accepted · Physical open verified")).toBeInTheDocument();
  expect(screen.getByText("Request rejected · Physical state not verified")).toBeInTheDocument();
  expect(screen.queryByText(/Delivery unknown/)).not.toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Check action result" })));
  expect(integrationsApi.getGateCommandByIntent).toHaveBeenCalledTimes(2);
  expect(send).toHaveBeenCalledOnce();
});
it("retains an intent after transport loss and a 404, then recovers after remount without repeating POST", async () => {
  const send = vi.spyOn(integrationsApi, "openGate").mockRejectedValue(new TypeError("response lost"));
  vi.mocked(integrationsApi.getGateCommandByIntent).mockRejectedValue(new ApiError("Not found", 404, { detail: "Not found" }));
  const view = render(<Dashboard {...props} />);
  await clickGate("Entry gate");
  expect(screen.getByText(/does not establish that it was not sent/)).toBeInTheDocument();
  const row = screen.getByText("Entry gate", { selector: "strong" }).closest(".gate-row") as HTMLElement;
  expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  view.unmount();
  vi.mocked(integrationsApi.getGateCommandByIntent).mockResolvedValue(receipt("selected_open_verified"));
  await act(async () => { render(<Dashboard {...props} />); });
  expect(screen.getByText("Request accepted · Physical open verified")).toBeInTheDocument();
  expect(send).toHaveBeenCalledOnce();
  expect(integrationsApi.confirmGateOpen).toHaveBeenCalledOnce();
});
it("keeps the browser index advisory when storage fails and checks the backend receipt without repeating POST", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Synthetic storage unavailable"); });
  const send = vi.spyOn(integrationsApi, "openGate").mockResolvedValue(receipt("accepted_unverified"));
  render(<Dashboard {...props} />);
  await clickGate("Entry gate");
  expect(screen.getByText(/This browser cannot retain action IDs across reloads/)).toBeInTheDocument();
  expect(screen.getByText("Request accepted · Physical state not verified")).toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Check action result" })));
  expect(integrationsApi.getGateCommandByIntent).toHaveBeenLastCalledWith("synthetic-intent", expect.anything());
  expect(send).toHaveBeenCalledOnce();
});
it("does not issue a command when its confirmation resolves after the account changes", async () => {
  const approval = deferred<ActionConfirmation>();
  vi.mocked(integrationsApi.confirmGateOpen).mockReturnValue(approval.promise);
  const send = vi.spyOn(integrationsApi, "openGate");
  const view = render(<Dashboard {...props} />);
  await clickGate("Entry gate");
  view.rerender(<Dashboard {...props} currentUser={{ ...admin, role: "standard" }} />);
  await act(async () => approval.resolve(confirmation));
  expect(send).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(storageKey)).toBeNull();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("aborts old-account receipt reads and ignores their late result", async () => {
  sessionStorage.setItem(storageKey, JSON.stringify([{ intentId: "old-intent", kind: "gate", deviceKey: "entry", action: "open" }]));
  const old = deferred<GateCommandReceipt>();
  vi.mocked(integrationsApi.getGateCommandByIntent).mockReturnValue(old.promise);
  const view = render(<Dashboard {...props} />);
  const signal = vi.mocked(integrationsApi.getGateCommandByIntent).mock.calls[0][1]!.signal!;
  view.rerender(<Dashboard {...props} currentUser={{ ...admin, id: "another-admin" }} />);
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve(receipt("selected_open_verified")));
  expect(screen.queryByText(/Entry admission verified/)).not.toBeInTheDocument();
});
it("retains a sent intent and cancels response handling and delayed refresh when leaving the dashboard", async () => {
  const pending = deferred<GateCommandReceipt>();
  const send = vi.spyOn(integrationsApi, "openGate").mockReturnValue(pending.promise);
  const view = render(<Dashboard {...props} />);
  await clickGate("Entry gate");
  const signal = send.mock.calls[0][2]!.signal!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { pending.resolve(receipt("selected_open_verified")); vi.advanceTimersByTime(5000); });
  expect(props.refresh).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(storageKey)).toContain("synthetic-intent");
});
it.each(contract.cases)("renders the paired $name physical/admission distinction", ({ outcome }) => {
  render(<CommandReceiptDetails receipt={outcome as GateCommandReceipt} />);
  if (outcome.admission_verified) expect(screen.getByText(/Entry admission verified/)).toBeInTheDocument();
  else expect(screen.queryByText(/Entry admission verified/)).not.toBeInTheDocument();
  expect(screen.queryAllByText(/Physical open verified/)).toHaveLength(outcome.target_receipts.filter((target) => target.verified && target.state === "open").length);
  expect(screen.queryAllByText(/Awaiting reconciliation/)).toHaveLength(outcome.target_receipts.filter((target) => target.requires_reconciliation).length);
  expect(screen.queryAllByText(/Partial command delivery/)).toHaveLength(outcome.delivery === "partial" ? 1 : 0);
});

function at(day: number, hour: number, minute: number) {
  return new Date(2026, 8, day, hour, minute).toISOString();
}

function directoryPerson(overrides: Partial<Person> & Pick<Person, "id" | "display_name">): Person {
  return {
    first_name: overrides.display_name.split(" ")[0] ?? "",
    last_name: "",
    pronouns: null,
    profile_photo_data_url: null,
    group_id: null,
    group: null,
    category: null,
    schedule_id: null,
    schedule: null,
    is_active: true,
    notes: null,
    garage_door_entity_ids: [],
    missed_exit_recovery_enabled: false,
    missed_exit_recovery_tracker_entity_id: null,
    home_assistant_mobile_app_notify_service: null,
    home_assistant_presence_input_boolean_entity_ids: [],
    home_assistant_presence_input_boolean_entry_action: "turn_on",
    home_assistant_presence_input_boolean_exit_action: "turn_off",
    vehicles: [],
    ...overrides
  };
}

function accessEvent(overrides: Partial<AccessEvent> & Pick<AccessEvent, "id" | "direction" | "occurred_at" | "registration_number">): AccessEvent {
  return {
    decision: "granted",
    confidence: 1,
    source: "lpr",
    timing_classification: "on_time",
    anomaly_count: 0,
    visitor_pass_id: null,
    visitor_name: null,
    visitor_pass_mode: null,
    external_admission_mode: null,
    external_admission_source: null,
    snapshot_url: null,
    snapshot_captured_at: null,
    snapshot_bytes: null,
    snapshot_width: null,
    snapshot_height: null,
    snapshot_camera: null,
    movement_saga: null,
    ...overrides
  };
}

it("keeps an activated snapshot through viewport changes and supports keyboard access at every width", () => {
  const event = accessEvent({ id: "snapshot-event", direction: "entry", occurred_at: "2026-09-23T12:00:00Z", registration_number: "TEST123", snapshot_url: "/test-snapshot.jpg" });
  const view = render(<Dashboard {...props} events={[event]} />);
  const row = screen.getByRole("button", { name: /Toggle .*snapshot/i });
  fireEvent.click(row);
  expect(row).toHaveAttribute("aria-expanded", "true");
  fireEvent(window, new Event("resize"));
  view.rerender(<Dashboard {...props} events={[{ ...event }]} />);
  expect(row).toHaveAttribute("aria-expanded", "true");
  expect(row.querySelector("img")).toHaveAttribute("src", "/test-snapshot.jpg");
  fireEvent.keyDown(row, { key: "Escape" });
  expect(row).toHaveAttribute("aria-expanded", "false");
  fireEvent.keyDown(row, { key: "Enter" });
  expect(row).toHaveAttribute("aria-expanded", "true");
  view.rerender(<Dashboard {...props} events={[]} />);
  expect(screen.queryByRole("button", { name: /Toggle .*snapshot/i })).not.toBeInTheDocument();
});

it("shows empty presence popovers when nobody is inside or has exited today", () => {
  render(<Dashboard {...props} />);
  expect(within(screen.getByLabelText("People inside now")).getByText("Nobody is inside right now.")).toBeInTheDocument();
  expect(within(screen.getByLabelText("People who exited today")).getByText("No exits recorded today.")).toBeInTheDocument();
  expect(within(screen.getByLabelText("Expected arrivals today")).getByText("No expected arrivals learned for today yet.")).toBeInTheDocument();
});

it("lists the people inside now and who exited today in the presence hover popovers", () => {
  vi.setSystemTime(new Date(2026, 8, 23, 15, 0, 0));
  const ada = directoryPerson({
    id: "ada",
    display_name: "Ada Lovelace",
    profile_photo_url: "/api/v1/people/ada/photo",
    vehicles: [{ registration_number: "ada1" } as Vehicle]
  });
  const grace = directoryPerson({ id: "grace", display_name: "Grace Hopper" });
  const sam = directoryPerson({ id: "sam", display_name: "Sam Rivera" });
  const katherine = directoryPerson({
    id: "katherine",
    display_name: "Katherine Johnson",
    profile_photo_url: "/api/v1/people/katherine/photo"
  });
  const joan = directoryPerson({ id: "joan", display_name: "Joan Clarke" });
  const margaret = directoryPerson({ id: "margaret", display_name: "Margaret Hamilton" });
  const people = [ada, grace, sam, katherine, joan, margaret];
  const presence: Presence[] = [
    { person_id: "ada", display_name: "Ada Lovelace", state: "present", last_changed_at: at(23, 9, 4) },
    { person_id: "grace", display_name: "Grace Hopper", state: "present", last_changed_at: at(22, 18, 0) },
    { person_id: "sam", display_name: "Sam Rivera", state: "present", last_changed_at: null },
    { person_id: "katherine", display_name: "Katherine Johnson", state: "exited", last_changed_at: at(23, 11, 30) }
  ];
  const vehicles = [
    { id: "kat-vehicle", registration_number: "kat1", person_id: "katherine", person_ids: ["katherine"] } as Vehicle,
    { id: "shared-vehicle", registration_number: "SHARE1", person_ids: ["margaret", "joan"] } as Vehicle,
    { id: "fallback-vehicle", registration_number: "FB1", owner: "Fallback Owner" } as Vehicle
  ];
  const events = [
    accessEvent({ id: "kat-exit", direction: "exit", occurred_at: at(23, 11, 30), registration_number: "KAT1" }),
    accessEvent({ id: "share-exit", direction: "exit", occurred_at: at(23, 12, 15), registration_number: "SHARE1" }),
    accessEvent({ id: "visitor-exit", direction: "exit", occurred_at: at(23, 13, 0), registration_number: "VIS1", visitor_name: "Guest: Alan Turing" }),
    accessEvent({ id: "unknown-exit", direction: "exit", occurred_at: at(23, 10, 0), registration_number: "UNK1" }),
    accessEvent({ id: "fallback-exit", direction: "exit", occurred_at: at(23, 9, 30), registration_number: "FB1" }),
    accessEvent({ id: "ada-entry", direction: "entry", occurred_at: at(23, 9, 4), registration_number: "ADA1" }),
    accessEvent({ id: "old-exit", direction: "exit", occurred_at: at(22, 16, 0), registration_number: "OLD1", visitor_name: "Old Exit" })
  ];
  const expectedPresence: ExpectedPresenceSummary = {
    date: "2026-09-23",
    timezone: "UTC",
    generated_at: at(23, 8, 0),
    count: 1,
    learning: true,
    coverage: { regular_candidates: 1, learned_candidates: 1, learning_population: 1, ratio: 1 },
    people: [{
      person_id: "ada",
      display_name: "Ada Lovelace",
      confidence: 0.9,
      evidence_days: 4,
      observed_weekdays: 3,
      typical_arrival: "08:15",
      typical_departure: null
    }]
  };
  render(<Dashboard {...props} events={events} expectedPresence={expectedPresence} people={people} presence={presence} vehicles={vehicles} />);

  const inside = screen.getByLabelText("People inside now");
  expect(inside).toHaveClass("has-tooltip");
  expect(within(inside).getByText("3")).toBeInTheDocument();
  expect(within(inside).getByText("3 people")).toBeInTheDocument();
  expect(within(inside).getByText("Ada Lovelace")).toBeInTheDocument();
  expect(within(inside).getByText(`Since ${formatTime(at(23, 9, 4))}`)).toBeInTheDocument();
  expect(within(inside).getByText("Since Sep 22")).toBeInTheDocument();
  expect(within(inside).getByText("Currently inside")).toBeInTheDocument();
  expect(inside.querySelector("img")).toHaveAttribute("src", "/api/v1/people/ada/photo?variant=thumb");
  expect(within(inside).queryByText("Katherine Johnson")).not.toBeInTheDocument();
  const insideNames = within(inside).getAllByText(/^(Ada Lovelace|Grace Hopper|Sam Rivera)$/).map((node) => node.textContent);
  expect(insideNames).toEqual(["Ada Lovelace", "Grace Hopper", "Sam Rivera"]);

  const exitedToday = screen.getByLabelText("People who exited today");
  expect(within(exitedToday).getByText("5")).toBeInTheDocument();
  expect(within(exitedToday).getByText("5 exits")).toBeInTheDocument();
  expect(within(exitedToday).getByText("Alan Turing")).toBeInTheDocument();
  expect(within(exitedToday).getByText(`Exited at ${formatTime(at(23, 13, 0))}`)).toBeInTheDocument();
  expect(within(exitedToday).getByText("Joan Clarke, Margaret Hamilton")).toBeInTheDocument();
  expect(within(exitedToday).getByText("Katherine Johnson")).toBeInTheDocument();
  expect(within(exitedToday).getByText("Unknown")).toBeInTheDocument();
  expect(within(exitedToday).getByText(`UNK1 · ${formatTime(at(23, 10, 0))}`)).toBeInTheDocument();
  expect(within(exitedToday).getByText("Fallback Owner")).toBeInTheDocument();
  expect(within(exitedToday).queryByText("Old Exit")).not.toBeInTheDocument();
  expect(within(exitedToday).queryByText("Ada Lovelace")).not.toBeInTheDocument();
  expect(exitedToday.querySelector("img")).toHaveAttribute("src", "/api/v1/people/katherine/photo?variant=thumb");
  const exitedNames = within(exitedToday).getAllByText(/^(Alan Turing|Joan Clarke, Margaret Hamilton|Katherine Johnson|Unknown|Fallback Owner)$/).map((node) => node.textContent);
  expect(exitedNames).toEqual(["Alan Turing", "Joan Clarke, Margaret Hamilton", "Katherine Johnson", "Unknown", "Fallback Owner"]);

  const expected = screen.getByLabelText("Expected arrivals today");
  expect(within(expected).getByText("Usually 08:15")).toBeInTheDocument();
  expect(within(expected).getAllByText("Learning").length).toBeGreaterThan(0);

  fireEvent.click(inside);
  expect(inside).toHaveClass("tooltip-open");
  fireEvent.keyDown(inside, { key: "Escape" });
  expect(inside).not.toHaveClass("tooltip-open");
  fireEvent.click(exitedToday);
  expect(exitedToday).toHaveClass("tooltip-open");
  fireEvent.mouseLeave(exitedToday);
  expect(exitedToday).not.toHaveClass("tooltip-open");
});

it("keeps the inside popover to the first six people", () => {
  vi.setSystemTime(new Date(2026, 8, 23, 16, 0, 0));
  const people = Array.from({ length: 7 }, (_, index) => directoryPerson({ id: `p${index}`, display_name: `Person ${index}` }));
  const presence: Presence[] = people.map((person, index) => ({
    person_id: person.id,
    display_name: person.display_name,
    state: "present",
    last_changed_at: at(23, 8 + index, 0)
  }));
  render(<Dashboard {...props} people={people} presence={presence} />);
  const inside = screen.getByLabelText("People inside now");
  expect(within(inside).getByText("7 people")).toBeInTheDocument();
  expect(within(inside).getByText("Person 6")).toBeInTheDocument();
  expect(within(inside).queryByText("Person 0")).not.toBeInTheDocument();
  expect(within(inside).getByText("+1 more inside")).toBeInTheDocument();
});

it("keeps durable command history off the dashboard", () => {
  const list = vi.spyOn(integrationsApi, "getGateCommands");
  render(<Dashboard {...props} />);
  expect(screen.queryByRole("button", { name: "Command history" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Command history" })).not.toBeInTheDocument();
  expect(list).not.toHaveBeenCalled();
});
