import React from "react";
import { CircleDot } from "lucide-react";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { AccessDevice, UserAccount } from "../../api/types";
import { AccessDevicesSettingsView } from "../../views/AccessDevicesSettingsView";
import { AccessDeviceEditor } from "./AccessDeviceEditor";
import { accessDeviceProviders, type AccessDeviceProviderDescriptor } from "./accessDeviceProviders";
import { saveAccessDevice } from "./accessDeviceSave";
import { useAccessDeviceBindings } from "./useAccessDeviceBindings";

const device: AccessDevice = { id: "device", key: "synthetic_gate", kind: "gate", name: "Synthetic gate", enabled: true, schedule_id: null, open_for_access: true, sort_order: 0, bindings: [] };
const third: AccessDeviceProviderDescriptor = {
  key: "synthetic_cover", label: "Synthetic provider", bindingLabel: "Synthetic cover", metricLabel: "Synthetic mapped", icon: CircleDot,
  discovery: async (_kind, signal) => (await api.get<{ covers: Array<{ entity_id: string; name: string; metadata: Record<string, unknown> }> }>("/api/v1/inert-third/covers", { signal })).covers,
  selection: (binding, options) => options.find((option) => option.metadata?.controller_id === binding?.external_id)?.entity_id ?? binding?.external_id ?? "",
  binding: (selection, options) => {
    const selected = options.find((option) => option.entity_id === selection);
    return { external_id: String(selected?.metadata?.controller_id ?? selection), enabled: Boolean(selection), config: { relay_pin: selected?.metadata?.relay_pin } };
  },
  bindingPath: (id) => `/api/v1/access-devices/${id}/bindings/synthetic_cover`, confirmationReason: "Update synthetic cover binding"
};
const extended = [...accessDeviceProviders, third];
const singleProvider = [accessDeviceProviders[0]];
const events = () => true;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
function fixture() {
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => path.includes("inert-third") ? { covers: [{ entity_id: "selection-third", name: "Third cover", metadata: { controller_id: "external-third", relay_pin: 7 } }] } : { cover_entities: [] });
  let confirmations = 0;
  const post = vi.spyOn(api, "post").mockImplementation(async () => ({ confirmation_token: `inert-${++confirmations}` }));
  const patch = vi.spyOn(api, "patch").mockResolvedValue(device);
  const put = vi.spyOn(api, "put").mockResolvedValue(device);
  return { get, post, patch, put };
}
function Harness({ initial = device, providers = extended }: { initial?: AccessDevice; providers?: readonly AccessDeviceProviderDescriptor[] }) {
  const owner = useAccessDeviceBindings("gate", events, "synthetic-account", providers);
  const [draft, setDraft] = React.useState(initial); const [receipt, setReceipt] = React.useState("");
  return <><AccessDeviceEditor device={draft} deviceIcon={CircleDot} disabled={false} discovery={owner.state} providers={owner.providers} schedules={[]} scheduleLabel="Default policy" saving={false} onDelete={() => {}} onSave={() => { void owner.save(draft, () => {}).then((result) => setReceipt(result.failedPhase ?? "Saved")); }} onUpdate={(patch) => setDraft((current) => ({ ...current, ...patch }))} onUpdateBinding={(provider, value) => setDraft((current) => owner.update(current, provider, value))} /><p>{receipt}</p></>;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("discovers, renders, converts and confirms a third descriptor through the real generic owner and editor", async () => {
  const { get, post, patch, put } = fixture();
  render(<Harness />);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
  const field = screen.getByLabelText(/Synthetic cover/);
  await waitFor(() => expect(document.querySelector('option[value="selection-third"]')).not.toBeNull());
  fireEvent.change(field, { target: { value: "selection-third" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Device" }));
  await screen.findByText("Saved", { exact: true });
  expect(get.mock.calls.map(([path]) => path)).toEqual(["/api/v1/integrations/home-assistant/entities", "/api/v1/integrations/esphome/entities", "/api/v1/inert-third/covers"]);
  expect(patch).toHaveBeenCalledWith("/api/v1/access-devices/device", expect.objectContaining({ schedule_id: "", confirmation_token: "inert-1" }));
  expect(put.mock.calls.map(([path]) => path)).toEqual(["/api/v1/access-devices/device/bindings/home_assistant", "/api/v1/access-devices/device/bindings/esphome", "/api/v1/access-devices/device/bindings/synthetic_cover"]);
  expect(put).toHaveBeenLastCalledWith("/api/v1/access-devices/device/bindings/synthetic_cover", { external_id: "external-third", enabled: true, config: { relay_pin: 7 }, confirmation_token: "inert-4" });
  expect(post.mock.calls.map(([, payload]) => (payload as { payload: { provider?: string } }).payload.provider ?? "device")).toEqual(["device", "home_assistant", "esphome", "synthetic_cover"]);
});
it("retains a missing saved ESPHome choice and its config until an explicit operator change", async () => {
  const { put } = fixture();
  const initial = { ...device, bindings: [{ provider: "esphome", external_id: "cover.saved", enabled: true, config: { device_id: "saved-controller", retained: "metadata" } }] };
  render(<Harness initial={initial} providers={accessDeviceProviders} />);
  expect(await screen.findByLabelText(/ESPHome cover/)).toHaveValue("cover.saved");
  fireEvent.click(screen.getByRole("button", { name: "Save Device" }));
  await screen.findByText("Saved", { exact: true });
  expect(put).toHaveBeenLastCalledWith("/api/v1/access-devices/device/bindings/esphome", expect.objectContaining({ external_id: "cover.saved", config: { device_id: "saved-controller", retained: "metadata" } }));
});
it("preserves HA identity and ESPHome composite selection/config conversion, including blank binding deletion", async () => {
  const { get, put } = fixture();
  get.mockResolvedValue({ cover_entities: [{ entity_id: "controller::cover.gate", name: "Gate", kind: "gate", metadata: { device_id: "controller", external_id: "cover.gate", retained: 1 } }] });
  render(<Harness providers={accessDeviceProviders} />);
  await waitFor(() => expect(document.querySelector('option[value="controller::cover.gate"]')).not.toBeNull());
  fireEvent.change(screen.getByLabelText(/Home Assistant cover/), { target: { value: "controller::cover.gate" } });
  fireEvent.change(screen.getByLabelText(/ESPHome cover/), { target: { value: "controller::cover.gate" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Device" })); await screen.findByText("Saved", { exact: true });
  expect(put.mock.calls[0][1]).toEqual(expect.objectContaining({ external_id: "cover.gate", config: {} }));
  expect(put.mock.calls[1][1]).toEqual(expect.objectContaining({ external_id: "cover.gate", config: { device_id: "controller", external_id: "cover.gate", retained: 1 } }));
  fireEvent.change(screen.getByLabelText(/ESPHome cover/), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Device" }));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(4));
  expect(put.mock.calls[3][1]).toEqual(expect.objectContaining({ external_id: "", enabled: false, config: {} }));
});
it("records accepted device and binding phases before a later rejection and obtains fresh confirmations on retry", async () => {
  const { post, put } = fixture();
  put.mockImplementation(async (path) => { if (path.endsWith("esphome")) throw new Error("Synthetic binding rejected"); return device; });
  const accepted = vi.fn();
  const first = await saveAccessDevice(device, accepted, () => true);
  expect(first.accepted).toEqual(["Device settings", "Home Assistant binding"]);
  expect(first.failedPhase).toBe("ESPHome binding"); expect(first.error).toBe("Synthetic binding rejected");
  expect(accepted.mock.calls.map(([, phase]) => phase)).toEqual(first.accepted);
  put.mockResolvedValue(device);
  const retry = await saveAccessDevice(device, accepted, () => true);
  expect(retry.failedPhase).toBeNull(); expect(post).toHaveBeenCalledTimes(6);
  expect(put.mock.calls[2][1]).toEqual(expect.objectContaining({ confirmation_token: "inert-5" }));
});
it("aborts and fences old discovery completion after account replacement", async () => {
  const { get } = fixture(); const old = deferred<{ cover_entities: unknown[] }>();
  get.mockReturnValueOnce(old.promise).mockResolvedValue({ cover_entities: [{ entity_id: "cover.new_gate", name: "New gate", kind: "gate" }] });
  const { result, rerender } = renderHook(({ identity }) => useAccessDeviceBindings("gate", events, identity, singleProvider), { initialProps: { identity: "first" } });
  await waitFor(() => expect(get).toHaveBeenCalledOnce()); const signal = get.mock.calls[0][1]?.signal;
  rerender({ identity: "second" });
  await waitFor(() => expect(result.current.state.home_assistant?.options[0]?.entity_id).toBe("cover.new_gate"));
  expect(signal?.aborted).toBe(true);
  await act(async () => old.resolve({ cover_entities: [{ entity_id: "cover.old_gate", name: "Old gate", kind: "gate" }] }));
  expect(result.current.state.home_assistant?.options[0]?.entity_id).toBe("cover.new_gate");
});
it("retains an in-flight accepted binding receipt but starts no later write after editor disposal", async () => {
  const { post, put } = fixture(); const pending = deferred<AccessDevice>(); put.mockReturnValueOnce(pending.promise);
  const { result, unmount } = renderHook(() => useAccessDeviceBindings("gate", events, "first"));
  const accepted = vi.fn(); const save = result.current.save(device, accepted);
  await waitFor(() => expect(put).toHaveBeenCalledOnce()); unmount();
  await act(async () => pending.resolve(device)); const receipt = await save;
  expect(receipt.accepted).toEqual(["Device settings", "Home Assistant binding"]); expect(receipt.stopped).toBe(true);
  expect(accepted).toHaveBeenCalledTimes(1); expect(put).toHaveBeenCalledTimes(1); expect(post).toHaveBeenCalledTimes(2);
});

it("fences scalar, binding and delete changes through the real view while confirmation is blocked", async () => {
  const { get, post, patch, put } = fixture(); const confirmation = deferred<{ confirmation_token: string }>();
  get.mockImplementation(async (path) => path.startsWith("/api/v1/access-devices?") ? [device] : path.endsWith("/entities") ? { cover_entities: [] } : []);
  post.mockReturnValueOnce(confirmation.promise);
  const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined); const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AccessDevicesSettingsView kind="gate" title="Gates" icon={CircleDot} currentUser={{ id: "synthetic", role: "admin" } as UserAccount} />);
  const name = await screen.findByLabelText("Display name"); const key = screen.getByLabelText("Internal key");
  fireEvent.click(screen.getByRole("button", { name: "Save Device" }));
  await waitFor(() => expect(name).toBeDisabled());
  expect(key).toBeDisabled(); expect(screen.getByLabelText(/Home Assistant cover/)).toBeDisabled();
  expect(screen.getByRole("button", { name: /^Remove$/ })).toBeDisabled();
  fireEvent.change(name, { target: { value: "Must not replace saved draft" } });
  fireEvent.change(key, { target: { value: "must_not_replace_key" } });
  fireEvent.change(screen.getByLabelText(/Home Assistant cover/), { target: { value: "cover.must_not_replace" } });
  fireEvent.click(screen.getByRole("button", { name: /^Remove$/ }));
  expect(confirm).not.toHaveBeenCalled(); expect(remove).not.toHaveBeenCalled(); expect(patch).not.toHaveBeenCalled();
  await act(async () => confirmation.resolve({ confirmation_token: "inert-first" }));
  await screen.findByText("Device saved.");
  expect(patch).toHaveBeenCalledWith("/api/v1/access-devices/device", expect.objectContaining({ name: "Synthetic gate", key: "synthetic_gate" }));
  expect(put.mock.calls[0][1]).toEqual(expect.objectContaining({ external_id: "", enabled: false }));
  expect(name).toHaveValue("Synthetic gate"); expect(key).toHaveValue("synthetic_gate");
});
