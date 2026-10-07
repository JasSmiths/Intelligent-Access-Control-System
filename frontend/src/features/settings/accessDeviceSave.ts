import { api, createActionConfirmation } from "../../api/client";
import type { AccessDevice } from "../../api/types";
import { accessDeviceProviders, type AccessDeviceProviderDescriptor } from "./accessDeviceProviders";

export type AccessDeviceSaveReceipt = {
  saved: AccessDevice | null;
  accepted: string[];
  failedPhase: string | null;
  error: string;
  stopped: boolean;
};

// Each accepted response becomes a receipt before any later confirmation/write.
// Leaving this editor stops later writes; it cannot undo an accepted phase.
export async function saveAccessDevice(
  device: AccessDevice,
  publishAccepted: (saved: AccessDevice, phase: string) => void,
  isCurrent: () => boolean,
  providers: readonly AccessDeviceProviderDescriptor[] = accessDeviceProviders
): Promise<AccessDeviceSaveReceipt> {
  const receipt: AccessDeviceSaveReceipt = { saved: null, accepted: [], failedPhase: null, error: "", stopped: false };
  let phase = "Device settings";
  const current = () => { if (isCurrent()) return true; receipt.stopped = true; return false; };
  const accept = (saved: AccessDevice) => { receipt.saved = saved; receipt.accepted.push(phase); if (isCurrent()) publishAccepted(saved, phase); };
  try {
    if (!current()) return receipt;
    const payload = { key: device.key, kind: device.kind, name: device.name, enabled: device.enabled, schedule_id: device.schedule_id || "", open_for_access: device.open_for_access, sort_order: device.sort_order };
    const confirmation = await createActionConfirmation("access_device.update", { device_id: device.id, ...payload }, { target_entity: "AccessDevice", target_id: device.id, target_label: device.name, reason: "Update access device" });
    if (!current()) return receipt;
    accept(await api.patch<AccessDevice>(`/api/v1/access-devices/${encodeURIComponent(device.id)}`, { ...payload, confirmation_token: confirmation.confirmation_token }));
    for (const provider of providers) {
      phase = `${provider.label} binding`;
      if (!current()) return receipt;
      const binding = device.bindings.find((item) => item.provider === provider.key);
      const bindingPayload = { external_id: binding?.external_id ?? "", enabled: Boolean(binding?.external_id), config: binding?.config ?? {} };
      const saved = receipt.saved!;
      const bindingConfirmation = await createActionConfirmation("access_device.binding.update", { device_id: saved.id, provider: provider.key, ...bindingPayload }, { target_entity: "AccessDevice", target_id: saved.id, target_label: saved.name, reason: provider.confirmationReason });
      if (!current()) return receipt;
      accept(await api.put<AccessDevice>(provider.bindingPath(saved.id), { ...bindingPayload, confirmation_token: bindingConfirmation.confirmation_token }));
    }
  } catch (error) {
    receipt.failedPhase = phase;
    receipt.error = error instanceof Error ? error.message : `Unable to save ${phase.toLowerCase()}.`;
  }
  return receipt;
}
