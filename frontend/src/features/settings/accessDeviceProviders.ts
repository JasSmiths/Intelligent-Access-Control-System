import { Home, Zap } from "lucide-react";
import type React from "react";
import { api } from "../../api/client";
import type { AccessDevice } from "../../api/types";
import { coverMatchesKind, type AccessDeviceDiscoveryItem, type AccessDeviceKind } from "./accessDeviceModel";

type Binding = AccessDevice["bindings"][number];
export type AccessDeviceProviderDescriptor = {
  key: string;
  label: string;
  metricLabel: string;
  bindingLabel: string;
  icon: React.ElementType;
  discovery: (kind: AccessDeviceKind, signal: AbortSignal) => Promise<AccessDeviceDiscoveryItem[]>;
  selection: (binding: Binding | undefined, options: AccessDeviceDiscoveryItem[]) => string;
  binding: (selection: string, options: AccessDeviceDiscoveryItem[]) => Omit<Binding, "provider">;
  bindingPath: (deviceId: string) => string;
  confirmationReason: string;
};

async function discover(path: string, kind: AccessDeviceKind, signal: AbortSignal) {
  const payload = await api.get<{ cover_entities: AccessDeviceDiscoveryItem[] }>(path, { signal });
  return (payload.cover_entities ?? []).filter((cover) => coverMatchesKind(cover, kind));
}

// Canonical enumeration controls discovery, rendering and binding-save order.
// Configured primary/failover settings remain independent command precedence.
export const accessDeviceProviders = [
  {
    key: "home_assistant", label: "Home Assistant", metricLabel: "HA mapped", bindingLabel: "Home Assistant cover", icon: Home,
    discovery: (kind, signal) => discover("/api/v1/integrations/home-assistant/entities", kind, signal),
    selection: (binding) => binding?.external_id ?? "",
    binding: (selection, options) => {
      const externalId = String(options.find((option) => option.entity_id === selection)?.metadata?.external_id ?? selection);
      return { external_id: externalId, enabled: Boolean(externalId), config: {} };
    },
    bindingPath: (deviceId) => `/api/v1/access-devices/${encodeURIComponent(deviceId)}/bindings/home_assistant`,
    confirmationReason: "Update Home Assistant binding"
  },
  {
    key: "esphome", label: "ESPHome", metricLabel: "ESPHome mapped", bindingLabel: "ESPHome cover", icon: Zap,
    discovery: (kind, signal) => discover("/api/v1/integrations/esphome/entities", kind, signal),
    selection: (binding, options) => {
      if (!binding) return "";
      const deviceId = String(binding.config.device_id ?? "");
      return options.find((option) => String(option.metadata?.external_id ?? option.entity_id) === binding.external_id && (!deviceId || String(option.metadata?.device_id ?? "") === deviceId))?.entity_id ?? binding.external_id;
    },
    binding: (selection, options) => {
      const match = options.find((option) => option.entity_id === selection);
      const externalId = String(match?.metadata?.external_id ?? selection);
      return { external_id: externalId, enabled: Boolean(externalId), config: { ...(match?.metadata ?? {}) } };
    },
    bindingPath: (deviceId) => `/api/v1/access-devices/${encodeURIComponent(deviceId)}/bindings/esphome`,
    confirmationReason: "Update ESPHome binding"
  }
] as const satisfies readonly AccessDeviceProviderDescriptor[];
export type AccessDeviceProviderKey = typeof accessDeviceProviders[number]["key"];

export function accessDeviceProviderLabel(key: string) {
  return accessDeviceProviders.find((provider) => provider.key === key)?.label ?? "No failover";
}

export function updateDeviceBinding(device: AccessDevice, provider: AccessDeviceProviderDescriptor, selection: string, options: AccessDeviceDiscoveryItem[]): AccessDevice {
  return { ...device, bindings: [...device.bindings.filter((binding) => binding.provider !== provider.key), { provider: provider.key, ...provider.binding(selection, options) }] };
}
