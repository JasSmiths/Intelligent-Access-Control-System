import React from "react";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as directory from "../../api/directory";
import * as client from "../../api/client";
import { integrationsApi, type RecoveryTrackerDiscovery } from "../../api/integrations";
import { missedExitRecoveryApi } from "../../api/missedExitRecovery";
import type { Person, SettingsSaveReceipt, SystemSetting } from "../../api/types";
import { RecoveryConfiguration } from "./RecoveryConfiguration";

const owner = { id: "owner-a", display_name: "Synthetic A", home_assistant_mobile_app_notify_service: "notify.mobile_app_first", missed_exit_recovery_enabled: false, missed_exit_recovery_tracker_entity_id: null } as Person;
const second = { ...owner, id: "owner-b", display_name: "Synthetic B", missed_exit_recovery_enabled: true, missed_exit_recovery_tracker_entity_id: "device_tracker.second" };
function rows(enabled = false, latitude: number | null = null, longitude: number | null = null) {
  return Object.entries({ missed_exit_recovery_enabled: enabled, missed_exit_recovery_gate_latitude: latitude, missed_exit_recovery_gate_longitude: longitude }).map(([key, value]) => ({ key, value })) as SystemSetting[];
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
const globalToggle = () => screen.getByRole("combobox", { name: "Global recovery" });
const latitude = () => screen.getByRole("spinbutton", { name: "Gate latitude" });
const longitude = () => screen.getByRole("spinbutton", { name: "Gate longitude" });
const ownerToggle = () => screen.getByRole("combobox", { name: "Owner recovery opt-in" });
const tracker = () => screen.getByRole("textbox", { name: "Home Assistant phone tracker entity" });
let signals = createReadSignals("test");
function serverPeople(people: Person[]) {
  vi.mocked(directory.readDirectory).mockResolvedValue({ items: people, total: people.length, next_cursor: null });
  vi.mocked(directory.lookupDirectory).mockImplementation(async (_kind, ids) => people.filter((item) => ids.includes(item.id)));
}
function configuration(people?: Person[]) {
  if (people) serverPeople(people);
  return <ReadSignalsContext.Provider value={signals}><RecoveryConfiguration /></ReadSignalsContext.Provider>;
}
function invalidate(people?: Person[]) {
  if (people) serverPeople(people);
  void signals.publish({ type: "refresh", reason: "manual", resetHistory: false }).catch(() => undefined);
}
const matched: RecoveryTrackerDiscovery = { status: "complete", reason: null,
  trackers: [{ entity_id: "device_tracker.renamed_iphone", name: "Synthetic iPhone", available: true, eligible: true, reason: null }],
  mappings: [{ person_id: owner.id, notify_service_id: owner.home_assistant_mobile_app_notify_service, suggested_tracker_entity_id: "device_tracker.renamed_iphone", status: "matched", reason: null }] };
function editGlobal() {
  fireEvent.change(globalToggle(), { target: { value: "true" } });
  fireEvent.change(latitude(), { target: { value: "51.5" } });
  fireEvent.change(longitude(), { target: { value: "-0.1" } });
}
function editOwner() {
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  fireEvent.change(ownerToggle(), { target: { value: "true" } });
  fireEvent.change(tracker(), { target: { value: "device_tracker.first" } });
}
beforeEach(() => {
  signals = createReadSignals("test");
  vi.spyOn(directory, "readDirectory").mockResolvedValue({ items: [owner, second], total: 2, next_cursor: null });
  vi.spyOn(directory, "lookupDirectory").mockImplementation(async (_kind, ids) => [owner, second].filter((item) => ids.includes(item.id)));
  vi.spyOn(client.api, "get").mockImplementation(async <T,>() => rows() as T);
  vi.spyOn(integrationsApi, "getRecoveryTrackers").mockResolvedValue({ status: "complete", reason: null, trackers: [], mappings: [] });
  vi.spyOn(client.api, "patch").mockResolvedValue(rows(true, 51.5, -0.1));
  vi.spyOn(client, "createActionConfirmation").mockResolvedValue({ confirmation_id: "synthetic", confirmation_token: "synthetic", action: "settings.update", expires_at: "" });
  vi.spyOn(missedExitRecoveryApi, "saveOwner").mockResolvedValue({ ...owner, missed_exit_recovery_enabled: true, missed_exit_recovery_tracker_entity_id: "device_tracker.first" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("preserves global drafts through identical realtime settings responses", async () => {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  editGlobal();
  await act(async () => invalidate());
  expect(globalToggle()).toHaveValue("true"); expect(latitude()).toHaveValue(51.5); expect(longitude()).toHaveValue(-0.1);
  expect(integrationsApi.getRecoveryTrackers).toHaveBeenCalledTimes(1);
});

it("keeps editing usable while a background reload is in flight and retains drafts on reload failure", async () => {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  const pending = deferred<SystemSetting[]>(); vi.mocked(client.api.get).mockReturnValueOnce(pending.promise);
  await act(async () => invalidate());
  expect(latitude()).not.toBeDisabled();
  editGlobal();
  await act(async () => pending.resolve(rows()));
  vi.mocked(client.api.get).mockRejectedValueOnce(new Error("Refresh unavailable"));
  await act(async () => invalidate());
  expect(screen.getByRole("alert")).toHaveTextContent("Refresh unavailable");
  expect(latitude()).not.toBeDisabled(); expect(latitude()).toHaveValue(51.5);
});

it("loads initial and clean changed configuration from the server", async () => {
  vi.mocked(client.api.get).mockResolvedValueOnce(rows(true, 51, -1));
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  expect(globalToggle()).toHaveValue("true"); expect(latitude()).toHaveValue(51);
  vi.mocked(client.api.get).mockResolvedValueOnce(rows(false, 52, -2));
  await act(async () => invalidate());
  expect(globalToggle()).toHaveValue("false"); expect(latitude()).toHaveValue(52);
});

it("preserves drafts during save and fences a stale read after successful save", async () => {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  editGlobal();
  const saved = deferred<SettingsSaveReceipt>(); vi.mocked(client.api.patch).mockReturnValueOnce(saved.promise);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save global configuration" })));
  const stale = deferred<SystemSetting[]>(); vi.mocked(client.api.get).mockReturnValueOnce(stale.promise);
  await act(async () => invalidate());
  expect(globalToggle()).toHaveValue("true"); expect(latitude()).toHaveValue(51.5);
  await act(async () => saved.resolve({ saved: true, settings: rows(true, 51.5, -0.1), changed_keys: [], activation: { state: "not_required", services: {} } }));
  await act(async () => stale.resolve(rows()));
  expect(globalToggle()).toHaveValue("true"); expect(latitude()).toHaveValue(51.5);
  expect(screen.getByRole("status")).toHaveTextContent("Global recovery settings saved");
  vi.mocked(client.api.get).mockResolvedValueOnce(rows(false, 52, -2));
  await act(async () => invalidate());
  expect(latitude()).toHaveValue(52); expect(globalToggle()).toHaveValue("false");
});

it("keeps a rejected global save editable across later refreshes", async () => {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  editGlobal(); vi.mocked(client.api.patch).mockRejectedValueOnce(new Error("Save rejected"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save global configuration" })));
  await act(async () => invalidate());
  expect(screen.getByRole("alert")).toHaveTextContent("Save rejected"); expect(latitude()).toHaveValue(51.5); expect(globalToggle()).toHaveValue("true");
});

it("preserves owner drafts across refreshed owner objects and loads explicit owner switches", async () => {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  editOwner();
  await act(async () => invalidate([{ ...owner }, { ...second }]));
  expect(ownerToggle()).toHaveValue("true"); expect(tracker()).toHaveValue("device_tracker.first");
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: second.id } });
  expect(ownerToggle()).toHaveValue("true"); expect(tracker()).toHaveValue("device_tracker.second");
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  expect(tracker()).toHaveValue("device_tracker.first");
});

it("preserves owner drafts during save and displays the saved response until directory refresh catches up", async () => {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  editOwner(); const pending = deferred<Person>(); vi.mocked(missedExitRecoveryApi.saveOwner).mockReturnValueOnce(pending.promise);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save owner configuration" })));
  await act(async () => invalidate([{ ...owner }, second]));
  expect(tracker()).toHaveValue("device_tracker.first");
  const saved = { ...owner, missed_exit_recovery_enabled: true, missed_exit_recovery_tracker_entity_id: "device_tracker.first" };
  await act(async () => pending.resolve(saved));
  expect(ownerToggle()).toHaveValue("true"); expect(tracker()).toHaveValue("device_tracker.first");
  await act(async () => invalidate([{ ...owner }, second]));
  expect(tracker()).toHaveValue("device_tracker.first");
  vi.mocked(directory.lookupDirectory).mockResolvedValue([saved]);
  await act(async () => invalidate([saved, second]));
  const changed = { ...saved, missed_exit_recovery_tracker_entity_id: "device_tracker.changed" };
  vi.mocked(directory.lookupDirectory).mockResolvedValue([changed]);
  await act(async () => invalidate([changed, second]));
  expect(tracker()).toHaveValue("device_tracker.changed");
});

it("retains rejected owner drafts through directory refreshes", async () => {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  editOwner(); vi.mocked(missedExitRecoveryApi.saveOwner).mockRejectedValueOnce(new Error("Owner save rejected"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save owner configuration" })));
  await act(async () => invalidate([{ ...owner }, second]));
  expect(screen.getByRole("alert")).toHaveTextContent("Owner save rejected"); expect(tracker()).toHaveValue("device_tracker.first");
});

it("offers the exact matched tracker without saving or enabling recovery", async () => {
  vi.mocked(integrationsApi.getRecoveryTrackers).mockResolvedValueOnce(matched);
  await act(async () => render(configuration()));
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  expect(tracker()).toHaveValue("device_tracker.renamed_iphone"); expect(ownerToggle()).toHaveValue("false");
  expect(screen.getByText(/matched to this owner's saved notification destination/)).toBeInTheDocument();
  expect(missedExitRecoveryApi.saveOwner).not.toHaveBeenCalled(); expect(client.api.patch).not.toHaveBeenCalled();
});

it("preserves configured trackers, manual changes and deliberate clearing on discovery refresh", async () => {
  vi.mocked(integrationsApi.getRecoveryTrackers).mockResolvedValue(matched);
  const configured = { ...owner, missed_exit_recovery_tracker_entity_id: "device_tracker.configured" };
  await act(async () => render(configuration([configured, second])));
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  expect(tracker()).toHaveValue("device_tracker.configured");
  fireEvent.change(tracker(), { target: { value: "device_tracker.manual" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Refresh phone trackers" })));
  expect(tracker()).toHaveValue("device_tracker.manual");
  fireEvent.change(tracker(), { target: { value: "" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Refresh phone trackers" })));
  expect(tracker()).toHaveValue("");
});

it.each(["ambiguous", "not_found", "unavailable"] as const)("keeps manual selection when association is %s", async (status) => {
  vi.mocked(integrationsApi.getRecoveryTrackers).mockResolvedValueOnce({ ...matched, mappings: [{ ...matched.mappings[0], status, suggested_tracker_entity_id: null, reason: "No unique association" }] });
  await act(async () => render(configuration()));
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  expect(tracker()).toHaveValue("");
  fireEvent.change(screen.getByRole("combobox", { name: "Discovered phone tracker" }), { target: { value: "device_tracker.renamed_iphone" } });
  expect(tracker()).toHaveValue("device_tracker.renamed_iphone"); expect(ownerToggle()).toHaveValue("false");
});

it("does not use a stale discovery response after the saved notification destination changes", async () => {
  const pending = deferred<RecoveryTrackerDiscovery>(); vi.mocked(integrationsApi.getRecoveryTrackers).mockReturnValueOnce(pending.promise);
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  vi.mocked(directory.lookupDirectory).mockResolvedValueOnce([{ ...owner, home_assistant_mobile_app_notify_service: "notify.mobile_app_changed" }]);
  await act(async () => invalidate([{ ...owner, home_assistant_mobile_app_notify_service: "notify.mobile_app_changed" }, second]));
  await act(async () => pending.resolve(matched));
  expect(tracker()).toHaveValue("");
});

it("revokes an untouched automatic suggestion when the saved destination changes", async () => {
  vi.mocked(integrationsApi.getRecoveryTrackers).mockResolvedValueOnce(matched);
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(configuration()); });
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  expect(tracker()).toHaveValue("device_tracker.renamed_iphone");
  vi.mocked(directory.lookupDirectory).mockResolvedValueOnce([{ ...owner, home_assistant_mobile_app_notify_service: "notify.mobile_app_changed" }]);
  await act(async () => invalidate([{ ...owner, home_assistant_mobile_app_notify_service: "notify.mobile_app_changed" }, second]));
  expect(tracker()).toHaveValue("");
});

it("aborts and ignores discovery after unmount", async () => {
  const pending = deferred<RecoveryTrackerDiscovery>(); vi.mocked(integrationsApi.getRecoveryTrackers).mockReturnValueOnce(pending.promise);
  const view = render(configuration());
  const signal = vi.mocked(integrationsApi.getRecoveryTrackers).mock.calls[0][0]!.signal!;
  view.unmount(); expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve(matched));
  expect(missedExitRecoveryApi.saveOwner).not.toHaveBeenCalled();
});

it("hides ineligible entities from phone choices and explains a missing destination", async () => {
  vi.mocked(integrationsApi.getRecoveryTrackers).mockResolvedValueOnce({ ...matched,
    trackers: [{ ...matched.trackers[0], eligible: false, reason: "not_gps" }],
    mappings: [{ ...matched.mappings[0], status: "not_found", suggested_tracker_entity_id: null, reason: "saved_notify_service_missing" }] });
  await act(async () => render(configuration()));
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  expect(screen.queryByRole("option", { name: /Synthetic iPhone/ })).not.toBeInTheDocument();
  expect(screen.getByText(/Configure this resident's phone notification destination in People/)).toBeInTheDocument();
  expect(screen.queryByText("saved_notify_service_missing")).not.toBeInTheDocument();
  fireEvent.change(tracker(), { target: { value: "device_tracker.manual" } });
  expect(tracker()).toHaveValue("device_tracker.manual");
});

it("shows discovery failure despite a previous association and removes the proposal hint after save", async () => {
  vi.mocked(integrationsApi.getRecoveryTrackers).mockResolvedValueOnce(matched);
  await act(async () => render(configuration()));
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: owner.id } });
  vi.mocked(missedExitRecoveryApi.saveOwner).mockResolvedValueOnce({ ...owner, missed_exit_recovery_tracker_entity_id: "device_tracker.renamed_iphone" });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save owner configuration" })));
  expect(screen.queryByText(/Save owner configuration to apply it/)).not.toBeInTheDocument();
  vi.mocked(integrationsApi.getRecoveryTrackers).mockRejectedValueOnce(new Error("Synthetic unavailable"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Refresh phone trackers" })));
  expect(screen.getByText(/Unable to discover phone trackers/)).toBeInTheDocument();
  expect(tracker()).toHaveValue("device_tracker.renamed_iphone");
});
