import React from "react";
import type { RealtimeMessage } from "../../api/types";
import type { AccessDevice } from "../../api/types";
import { useEditorLifetime } from "../../ui/useEditorLifetime";
import type { AccessDeviceKind } from "./accessDeviceModel";
import { accessDeviceProviders, updateDeviceBinding, type AccessDeviceProviderDescriptor } from "./accessDeviceProviders";
import { saveAccessDevice } from "./accessDeviceSave";
import { useAccessDeviceDiscovery } from "./useAccessDeviceDiscovery";

export function useAccessDeviceBindings(kind: AccessDeviceKind, eventMatches: (event: RealtimeMessage) => boolean, identity: unknown, providers: readonly AccessDeviceProviderDescriptor[] = accessDeviceProviders) {
  const lifetime = useEditorLifetime(identity);
  const discovery = useAccessDeviceDiscovery(kind, eventMatches, providers, identity);
  const update = React.useCallback((device: AccessDevice, key: string, selection: string) => {
    const provider = providers.find((item) => item.key === key);
    return provider ? updateDeviceBinding(device, provider, selection, discovery.state[key]?.options ?? []) : device;
  }, [providers, discovery.state]);
  const save = React.useCallback((device: AccessDevice, publishAccepted: (saved: AccessDevice, phase: string) => void) => saveAccessDevice(device, publishAccepted, lifetime.isCurrent, providers), [lifetime, providers]);
  return { ...discovery, update, save, lifetime };
}
