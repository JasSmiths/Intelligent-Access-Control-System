import React from "react";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import type { RealtimeMessage } from "../../api/types";
import type { AccessDeviceDiscoveryItem, AccessDeviceKind } from "./accessDeviceModel";
import { accessDeviceProviders, type AccessDeviceProviderDescriptor } from "./accessDeviceProviders";

export type AccessDeviceDiscovery = Record<string, { options: AccessDeviceDiscoveryItem[]; error: string }>;
export function useAccessDeviceDiscovery(kind: AccessDeviceKind, eventMatches: (event: RealtimeMessage) => boolean, providers: readonly AccessDeviceProviderDescriptor[] = accessDeviceProviders, identity?: unknown) {
  const [state, setState] = React.useState<AccessDeviceDiscovery>({});
  const [loading, setLoading] = React.useState(true);
  React.useLayoutEffect(() => { setState({}); setLoading(true); }, [identity, kind, providers]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    const results = await Promise.all(providers.map(async (provider) => {
      try { return { key: provider.key, options: await provider.discovery(kind, signal), error: "" }; }
      catch (error) { return { key: provider.key, options: null, error: error instanceof Error ? error.message : `Unable to discover ${provider.label} covers.` }; }
    }));
    if (signal.aborted) return;
    setState((current) => Object.fromEntries(results.map((result) => [result.key, { options: result.options ?? current[result.key]?.options ?? [], error: result.error }])));
    setLoading(false);
    const failed = results.find((result) => result.error);
    if (failed) throw new Error(failed.error);
  }, [identity, kind, providers]);
  const refresh = useCoordinatedRead(read, eventMatches);
  return { providers, state, loading, refresh };
}
