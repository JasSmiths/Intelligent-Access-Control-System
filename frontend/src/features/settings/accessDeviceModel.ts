import { Home } from "lucide-react";

import type { AccessDevice } from "../../api/types";

export type AccessDeviceKind = "gate" | "garage_door";

export type AccessDeviceDiscoveryItem = {
  entity_id: string;
  name: string | null;
  state?: string | null;
  kind?: string;
  device_class?: string | null;
  metadata?: Record<string, unknown>;
};

export function bindingSelectionValue(binding: AccessDevice["bindings"][number] | undefined, options: AccessDeviceDiscoveryItem[]) {
  if (!binding) return "";
  const deviceId = String(binding.config?.device_id ?? "");
  const match = options.find((option) => {
    const optionDeviceId = String(option.metadata?.device_id ?? "");
    const optionExternalId = String(option.metadata?.external_id ?? option.entity_id);
    return optionExternalId === binding.external_id && (!deviceId || optionDeviceId === deviceId);
  });
  return match?.entity_id ?? binding.external_id;
}

export function coverMatchesKind(cover: AccessDeviceDiscoveryItem, kind: AccessDeviceKind) {
  if (cover.kind === kind) return true;
  const label = `${cover.entity_id} ${cover.name ?? ""} ${cover.device_class ?? ""}`.toLowerCase();
  return kind === "garage_door" ? label.includes("garage") || label.includes("door") : label.includes("gate");
}

export function deviceHasProviderBinding(device: AccessDevice, provider: string) {
  return device.bindings.some((binding) => binding.provider === provider && Boolean(binding.external_id));
}

export function deviceHasAnyProviderBinding(device: AccessDevice) {
  return device.bindings.some((binding) => Boolean(binding.external_id));
}

export function providerLabel(provider: string) {
  if (provider === "esphome") return "ESPHome";
  if (provider === "home_assistant") return "Home Assistant";
  return "No failover";
}

export function bindingConfigForDiscovery(provider: string, selection: string, options: AccessDeviceDiscoveryItem[]) {
  const match = options.find((option) => option.entity_id === selection);
  if (!match) return {};
  return provider === "esphome" ? { ...(match.metadata ?? {}) } : {};
}
