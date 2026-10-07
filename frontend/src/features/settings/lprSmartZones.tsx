import React from "react";

import { api } from "../../api/client";
import type { UnifiProtectCamera } from "../../api/types";
import type { SettingFieldDefinition } from "../../lib/settings";

const GATE_LPR_CAMERA_NAME = "gate lpr";

const GATE_LPR_CAMERA_DEVICE = "942A6FD09D64";

export type GateLprSmartZonesState = {
  loading: boolean;
  error: string;
  camera: UnifiProtectCamera | null;
  zones: UnifiProtectCamera["smart_detect_zones"];
};

export function useGateLprSmartZones(enabled: boolean): GateLprSmartZonesState {
  const [state, setState] = React.useState<GateLprSmartZonesState>({
    loading: false,
    error: "",
    camera: null,
    zones: []
  });

  React.useEffect(() => {
    if (!enabled) {
      setState({ loading: false, error: "", camera: null, zones: [] });
      return;
    }
    let active = true;
    setState((current) => ({ ...current, loading: true, error: "" }));
    api.get<{ cameras: UnifiProtectCamera[] }>("/api/v1/integrations/unifi-protect/cameras")
      .then((payload) => {
        if (!active) return;
        const camera = findGateLprCamera(payload.cameras);
        setState({
          loading: false,
          error: "",
          camera,
          zones: camera?.smart_detect_zones ?? []
        });
      })
      .catch((loadError) => {
        if (!active) return;
        setState({
          loading: false,
          error: loadError instanceof Error ? loadError.message : "Unable to load UniFi Protect cameras.",
          camera: null,
          zones: []
        });
      });
    return () => {
      active = false;
    };
  }, [enabled]);

  return state;
}

function findGateLprCamera(cameras: UnifiProtectCamera[]) {
  return cameras.find((camera) => normalizeCameraIdentifier(camera.name) === GATE_LPR_CAMERA_NAME)
    ?? cameras.find((camera) => normalizeCameraIdentifier(camera.mac) === normalizeCameraIdentifier(GATE_LPR_CAMERA_DEVICE))
    ?? cameras.find((camera) => {
      const label = normalizeCameraIdentifier(camera.name);
      return label.includes("gate") && label.includes("lpr");
    })
    ?? null;
}

function normalizeCameraIdentifier(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

export function GateLprSmartZoneField({
  field,
  state,
  value,
  onChange
}: {
  field: SettingFieldDefinition;
  state: GateLprSmartZonesState;
  value: string;
  onChange: (value: string) => void;
}) {
  const selected = firstSettingListValue(value);
  const zones = uniqueGateLprSmartZones(state.zones);
  const selectedZone = zones.find((zone) => normalizeSmartZoneName(zone.name) === normalizeSmartZoneName(selected));
  const selectValue = selectedZone?.name ?? selected;
  const disabled = state.loading || Boolean(state.error) || !state.camera || zones.length === 0;
  const status = gateLprSmartZoneStatus(state);
  return (
    <label className="field">
      <span>{field.label}</span>
      <select value={selectValue} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        <option value="">{state.loading ? "Loading zones..." : "Select smart zone"}</option>
        {selected && !selectedZone ? <option value={selected}>{selected}</option> : null}
        {zones.map((zone) => (
          <option key={`${zone.id ?? zone.name}:${zone.name}`} value={zone.name}>
            {zone.name}
          </option>
        ))}
      </select>
      <small className="field-hint">{status}</small>
    </label>
  );
}

function firstSettingListValue(value: string) {
  return value.replace(/,/g, "\n").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)[0] ?? "";
}

function uniqueGateLprSmartZones(zones: UnifiProtectCamera["smart_detect_zones"]) {
  const seen = new Set<string>();
  return zones.filter((zone) => {
    const name = String(zone.name ?? "").trim();
    const normalized = normalizeSmartZoneName(name);
    if (!normalized || seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
}

function normalizeSmartZoneName(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

function gateLprSmartZoneStatus(state: GateLprSmartZonesState) {
  if (state.loading) return "Loading Gate LPR smart zones from UniFi Protect.";
  if (state.error) return `UniFi Protect zones unavailable: ${state.error}`;
  if (!state.camera) return "Gate LPR camera was not found.";
  if (state.zones.length === 0) return "Gate LPR camera has no smart detect zones.";
  return `Gate LPR camera: ${state.camera.name}.`;
}
