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
