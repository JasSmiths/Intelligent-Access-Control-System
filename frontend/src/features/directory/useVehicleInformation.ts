import * as React from "react";
import { lookupVehicleInformation, type VehicleInformation } from "../../api/vehicleInformation";
import { normalizePlateInput } from "./model";

export function useVehicleInformation(registration: string, initialRegistration: string | null,
  onInformation: (information: VehicleInformation, manual: boolean) => void, enabled: boolean) {
  const apply = React.useRef(onInformation);
  apply.current = onInformation;
  const manualRequested = React.useRef(false);
  const [refreshSequence, setRefreshSequence] = React.useState(0);
  const [state, setState] = React.useState({ status: "idle", message: "", manual: false });
  React.useEffect(() => {
    const manual = manualRequested.current;
    manualRequested.current = false;
    const plate = normalizePlateInput(registration);
    if (!enabled || plate.length < 2 || (!manual && plate === normalizePlateInput(initialRegistration ?? ""))) {
      setState({ status: "idle", message: "", manual: false });
      return;
    }
    const controller = new AbortController();
    setState({ status: "loading", message: manual ? "Looking up vehicle details" : "Looking up vehicle information", manual });
    const timer = window.setTimeout(async () => {
      try {
        const information = await lookupVehicleInformation(plate, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const found = manual
          ? [information.make, information.model, information.colour, information.fuel_type].some((value) => Boolean(value))
          : Object.values(information.providers).some((provider) => provider.status === "found");
        if (!manual || found) apply.current(information, manual);
        const unavailable = Object.entries(information.providers)
          .filter(([, provider]) => ["failed", "deferred", "not_found"].includes(provider.status))
          .map(([name, provider]) => `${name.toUpperCase()}: ${provider.status.replaceAll("_", " ")}`);
        setState({ status: unavailable.length || (manual && !found) ? "error" : found ? "found" : "idle", manual,
          message: unavailable.join(" · ") || (found ? manual ? "Vehicle details filled in. Save changes to keep them." : "Vehicle information applied" : manual ? "No vehicle details were returned." : "") });
      } catch (error) {
        if (!controller.signal.aborted) setState({ status: "error", message: error instanceof Error ? error.message : "Vehicle lookup failed", manual });
      }
    }, manual ? 0 : 850);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [registration, initialRegistration, enabled, refreshSequence]);
  const refresh = () => {
    if (!enabled || normalizePlateInput(registration).length < 2 || state.status === "loading") return;
    manualRequested.current = true;
    setRefreshSequence((sequence) => sequence + 1);
  };
  return { ...state, refresh };
}
