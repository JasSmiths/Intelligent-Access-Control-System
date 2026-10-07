import * as React from "react";
import { lookupVehicleInformation, type VehicleInformation } from "../../api/vehicleInformation";
import { normalizePlateInput } from "./model";

export function useVehicleInformation(registration: string, initialRegistration: string | null,
  onInformation: (information: VehicleInformation) => void, enabled: boolean) {
  const apply = React.useRef(onInformation);
  apply.current = onInformation;
  const [state, setState] = React.useState({ status: "idle", message: "" });
  React.useEffect(() => {
    const plate = normalizePlateInput(registration);
    if (!enabled || plate.length < 2 || plate === normalizePlateInput(initialRegistration ?? "")) {
      setState({ status: "idle", message: "" });
      return;
    }
    const controller = new AbortController();
    setState({ status: "loading", message: "Looking up vehicle information" });
    const timer = window.setTimeout(async () => {
      try {
        const information = await lookupVehicleInformation(plate, { signal: controller.signal });
        if (controller.signal.aborted) return;
        apply.current(information);
        const found = Object.values(information.providers).some((provider) => provider.status === "found");
        const unavailable = Object.entries(information.providers)
          .filter(([, provider]) => ["failed", "deferred", "not_found"].includes(provider.status))
          .map(([name, provider]) => `${name.toUpperCase()}: ${provider.status.replaceAll("_", " ")}`);
        setState({ status: unavailable.length ? "error" : found ? "found" : "idle",
          message: unavailable.join(" · ") || (found ? "Vehicle information applied" : "") });
      } catch (error) {
        if (!controller.signal.aborted) setState({ status: "error", message: error instanceof Error ? error.message : "Vehicle lookup failed" });
      }
    }, 850);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [registration, initialRegistration, enabled]);
  return state;
}
