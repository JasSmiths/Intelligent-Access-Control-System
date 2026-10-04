import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { DoorOpen } from "lucide-react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as client from "../api/client";
import type { UserAccount } from "../api/types";
import type { AccessDeviceEligibility } from "../api/integrations";
import { AccessDevicesSettingsView, UsersView } from "./SettingsViews";

const settingState = vi.hoisted(() => ({ values: { gate_admission_device_key: "", gate_control_provider: "home_assistant", gate_failover_provider: "none" }, save: vi.fn() }));
vi.mock("../lib/settings", async () => ({ ...await vi.importActual("../lib/settings"), useSettings: () => ({ ...settingState, loading: false, error: "" }) }));
const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;
const devices = [
  { id: "entry-id", key: "entry", kind: "gate", name: "Entry Gate", enabled: true, open_for_access: true, sort_order: 0, bindings: [], schedule_id: null, commandable: true, admission_eligible: true },
  { id: "secondary-id", key: "secondary", kind: "gate", name: "Secondary Gate", enabled: true, open_for_access: false, sort_order: 1, bindings: [], schedule_id: null, commandable: true, admission_eligible: false },
  { id: "disabled-id", key: "disabled", kind: "gate", name: "Disabled Gate", enabled: false, open_for_access: true, sort_order: 2, bindings: [], schedule_id: null, commandable: false, admission_eligible: false }
] as AccessDeviceEligibility[];
const props = { kind: "gate" as const, title: "Gates", icon: DoorOpen, currentUser: admin, refreshToken: 0, schedules: [] };
beforeEach(() => {
  settingState.values.gate_admission_device_key = "";
  settingState.save.mockReset();
  vi.spyOn(client.api, "get").mockImplementation(async (path) => path.startsWith("/api/v1/access-devices") ? devices : { cover_entities: [] });
  vi.spyOn(client, "createActionConfirmation").mockResolvedValue({ confirmation_id: "synthetic-intent", confirmation_token: "synthetic-token", action: "settings.update", expires_at: "" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("starts unselected despite eligible-looking devices and only saves the explicit operator choice", async () => {
  await act(async () => { render(<AccessDevicesSettingsView {...props} />); });
  const select = screen.getByRole("combobox", { name: "Automatic-admission entry gate" });
  expect(select).toHaveValue("");
  expect(settingState.save).not.toHaveBeenCalled();
  expect(within(select).queryByRole("option", { name: "Disabled Gate" })).not.toBeInTheDocument();
  expect(within(select).queryByRole("option", { name: "Secondary Gate" })).not.toBeInTheDocument();
  await act(async () => fireEvent.change(select, { target: { value: "entry" } }));
  expect(client.createActionConfirmation).toHaveBeenCalledWith("settings.update", { values: { gate_admission_device_key: "entry" } }, expect.objectContaining({ target_id: "gate_admission_device_key", target_label: "Entry Gate" }));
  expect(settingState.save).toHaveBeenCalledWith({ gate_admission_device_key: "entry" }, { confirmationToken: "synthetic-token" });
  expect(screen.getByText("Entry gate selection saved.")).toBeInTheDocument();
});
it("retains and displays a rejected server selection without pretending it was saved", async () => {
  settingState.values.gate_admission_device_key = "";
  settingState.save.mockRejectedValue(new Error("The selected gate is not commandable."));
  await act(async () => { render(<AccessDevicesSettingsView {...props} />); });
  await act(async () => fireEvent.change(screen.getByRole("combobox", { name: "Automatic-admission entry gate" }), { target: { value: "entry" } }));
  expect(screen.getByText("The selected gate is not commandable.")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Automatic-admission entry gate" })).toHaveValue("");
  expect(screen.queryByText("Entry gate selection saved.")).not.toBeInTheDocument();
});
it("permits explicit clearing and keeps a missing saved target visible until the operator changes it", async () => {
  settingState.values.gate_admission_device_key = "removed-entry";
  await act(async () => { render(<AccessDevicesSettingsView {...props} />); });
  const select = screen.getByRole("combobox", { name: "Automatic-admission entry gate" });
  expect(select).toHaveValue("removed-entry");
  expect(within(select).getByRole("option", { name: "removed-entry (not eligible or unverified)" })).toBeInTheDocument();
  expect(settingState.save).not.toHaveBeenCalled();
  await act(async () => fireEvent.change(select, { target: { value: "" } }));
  expect(settingState.save).toHaveBeenCalledWith({ gate_admission_device_key: "" }, { confirmationToken: "synthetic-token" });
});
it("keeps entry selection read-only for standard users and absent from garage settings", async () => {
  const view = render(<AccessDevicesSettingsView {...props} currentUser={{ ...admin, role: "standard" }} />);
  await act(async () => {});
  expect(screen.getByRole("combobox", { name: "Automatic-admission entry gate" })).toBeDisabled();
  view.rerender(<AccessDevicesSettingsView {...props} kind="garage_door" />);
  await act(async () => {});
  expect(screen.queryByRole("combobox", { name: "Automatic-admission entry gate" })).not.toBeInTheDocument();
  expect(client.createActionConfirmation).not.toHaveBeenCalled();
});

it("does not infer eligibility when an older response omits the authoritative flag", async () => {
  vi.mocked(client.api.get).mockImplementation(async (path) => path.startsWith("/api/v1/access-devices")
    ? devices.map(({ admission_eligible: _eligible, commandable: _commandable, ...device }) => device) : { cover_entities: [] });
  settingState.values.gate_admission_device_key = "entry";
  await act(async () => { render(<AccessDevicesSettingsView {...props} />); });
  const select = screen.getByRole("combobox", { name: "Automatic-admission entry gate" });
  expect(select).toHaveValue("entry");
  expect(within(select).queryByRole("option", { name: "Entry Gate" })).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("not confirmed eligible");
  expect(settingState.save).not.toHaveBeenCalled();
});

const account: UserAccount = {
  id: "synthetic-admin", username: "alex", first_name: "Alex", last_name: "Morgan", full_name: "Alex Morgan",
  profile_photo_data_url: null, email: null, mobile_phone_number: null, role: "admin", is_active: true,
  last_login_at: null, person_id: null, preferences: { sidebarCollapsed: false },
  created_at: "2026-10-04T12:00:00Z", updated_at: "2026-10-04T12:00:00Z"
};

function pendingUserExit(dialog: HTMLElement) {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  let finish!: () => void;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  dialog.style.animationName = "console-modal-in";
  for (const element of [dialog, dialog.closest(".modal-backdrop")!]) {
    Object.defineProperty(element, "getAnimations", { configurable: true, value: () => [{ finished }] });
  }
  return finish;
}

it("propagates a saved current user before exit completes even when navigation unmounts the editor", async () => {
  const savedUser = { ...account, first_name: "Alexandra", full_name: "Alexandra Morgan" };
  const onCurrentUserUpdated = vi.fn();
  vi.mocked(client.api.get).mockImplementation(async (path) => path === "/api/v1/users" ? [account] : []);
  vi.spyOn(client.api, "patch").mockResolvedValue(savedUser);
  const view = render(<UsersView currentUser={account} onCurrentUserUpdated={onCurrentUserUpdated} refreshToken={0} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const dialog = screen.getByRole("dialog", { name: "Edit User" });
  const finishExit = pendingUserExit(dialog);
  fireEvent.change(within(dialog).getByLabelText("First name"), { target: { value: "Alexandra" } });
  await act(async () => { fireEvent.submit(dialog); });

  expect(onCurrentUserUpdated).toHaveBeenCalledExactlyOnceWith(savedUser);
  expect(dialog.closest(".modal-backdrop")).toHaveAttribute("data-closing", "true");
  expect(within(dialog).getByRole("button", { name: "Saving..." })).toBeDisabled();
  view.unmount();
  await act(async () => { finishExit(); });
  expect(onCurrentUserUpdated).toHaveBeenCalledExactlyOnceWith(savedUser);
});

it("publishes the generated password before exit and retains its saved-user label after closing", async () => {
  const savedUser = { ...account, id: "synthetic-created", username: "sam", first_name: "Sam", last_name: "River", full_name: "Sam River" };
  const onCurrentUserUpdated = vi.fn();
  vi.mocked(client.api.get).mockImplementation(async (path) => path === "/api/v1/users" ? [account] : []);
  vi.spyOn(client.api, "post").mockResolvedValue({ user: savedUser, temporary_password: "synthetic-password" });
  render(<UsersView currentUser={account} onCurrentUserUpdated={onCurrentUserUpdated} refreshToken={0} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Add User" }));
  const dialog = screen.getByRole("dialog", { name: "Add User" });
  const finishExit = pendingUserExit(dialog);
  fireEvent.change(within(dialog).getByLabelText("First name"), { target: { value: "Sam" } });
  fireEvent.change(within(dialog).getByLabelText("Last name"), { target: { value: "River" } });
  fireEvent.change(within(dialog).getByLabelText("Username"), { target: { value: "sam" } });
  await act(async () => { fireEvent.submit(dialog); });

  expect(screen.getByText("Temporary password for Sam River")).toBeInTheDocument();
  expect(screen.getByText("synthetic-password")).toBeInTheDocument();
  expect(dialog.closest(".modal-backdrop")).toHaveAttribute("data-closing", "true");
  expect(onCurrentUserUpdated).not.toHaveBeenCalled();
  await act(async () => { finishExit(); });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText("Temporary password for Sam River")).toBeInTheDocument();
});
