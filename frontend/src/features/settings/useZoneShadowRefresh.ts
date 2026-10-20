import React from "react";
import type { RealtimeMessage } from "../../api/types";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { settingsEventMatches } from "./invalidation";

export const ZONE_SHADOW_REPAIR_INTERVAL_MS = 60_000;

export function zoneShadowEventMatches(event: RealtimeMessage): boolean {
  return event.type === "lpr_zone_shadow.observed" || settingsEventMatches(event);
}

// Observation publication is a post-commit hint. Visible-page repair recovers
// lost hints without changing the settings form's invalidation or draft owner.
export function useZoneShadowRefresh(read: (signal: AbortSignal) => Promise<void>) {
  const refresh = useCoordinatedRead(read, zoneShadowEventMatches);
  React.useEffect(() => {
    let timer: number | null = null;
    const clear = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    };
    const schedule = () => {
      clear();
      if (document.visibilityState !== "visible") return;
      timer = window.setTimeout(() => {
        timer = null;
        if (document.visibilityState !== "visible") return;
        void refresh().catch(() => undefined);
        schedule();
      }, ZONE_SHADOW_REPAIR_INTERVAL_MS);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refresh().catch(() => undefined);
      schedule();
    };
    schedule();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      clear();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refresh]);
  return refresh;
}
