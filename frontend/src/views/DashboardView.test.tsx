import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { integrationsApi, type GateCommandReceipt } from "../api/integrations";
import type { ActionConfirmation, IntegrationStatus, UserAccount } from "../api/types";
import contract from "../api/fixtures/gateCommandReceipts.generated.json";
import { CommandReceiptDetails, Dashboard } from "./DashboardView";

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

it("opens durable command history only on request and recovers with no browser metadata or POST", async () => {
  const pages = (await import("../api/fixtures/gateReceiptPages.generated.json")).default;
  const list = vi.spyOn(integrationsApi, "getGateCommands").mockResolvedValue(pages.gates as Awaited<ReturnType<typeof integrationsApi.getGateCommands>>);
  const read = vi.spyOn(integrationsApi, "getGateCommand").mockResolvedValue(pages.gates.items[0] as GateCommandReceipt & { command_id: string });
  const send = vi.spyOn(integrationsApi, "openGate");
  render(<Dashboard {...props} />);
  expect(list).not.toHaveBeenCalled();
  expect(sessionStorage.length).toBe(0);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Command history" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: `Inspect command ${pages.gates.items[0].command_id}` })));
  expect(screen.getByText("Request accepted · Physical open verified")).toBeInTheDocument();
  expect(read).toHaveBeenCalledOnce();
  expect(send).not.toHaveBeenCalled();
  expect(integrationsApi.confirmGateOpen).not.toHaveBeenCalled();
});
