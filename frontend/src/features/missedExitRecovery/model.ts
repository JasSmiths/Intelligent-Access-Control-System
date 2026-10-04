export const recoveryMethods = ["phone_automatic", "camera_automatic", "resident_confirmation", "none"] as const;
export function recoveryTrackerMessage(reason: string | null | undefined): string {
  if (!reason) return "";
  if (reason === "saved_notify_service_missing") return "Configure this resident's phone notification destination in People before automatic tracker selection.";
  if (["notify_service_shared", "device_name_collision", "multiple_mobile_app_registrations", "multiple_eligible_trackers"].includes(reason)) return "More than one phone association was found. Select the correct tracker manually.";
  if (reason === "tracker_assigned_elsewhere") return "This tracker is already assigned to another resident. Review their configuration in People.";
  if (["registry_unavailable", "discovery_unavailable", "saved_notify_service_unavailable"].includes(reason)) return "Home Assistant phone discovery is unavailable. Refresh or enter the tracker entity manually.";
  if (["tracker_unavailable", "eligible_tracker_unavailable", "disabled"].includes(reason)) return "The phone tracker is unavailable or disabled in Home Assistant. Check the phone integration before using recovery.";
  if (["not_mobile_app", "not_iphone", "not_gps", "registry_link_missing", "registry_link_inconsistent"].includes(reason)) return "This entity is not a verified iPhone GPS tracker. Check its Home Assistant mobile app integration.";
  return "No verified phone association was found. Select a tracker or enter its entity manually.";
}
export function recoveryLabel(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
export function filterDate(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? undefined : date.toISOString();
}
// The API also sanitizes these records. Keep browser diagnostics restricted to
// decision/check summaries, ages, accuracy and distances; never print raw objects.
export function safeDiagnosticRows(value: Record<string, unknown>, prefix = ""): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];
  for (const [key, item] of Object.entries(value)) {
    if (/(?:latitude|longitude|coordinates|gps|token|secret|payload|url|action_id)/i.test(key)) continue;
    const fieldLabel = diagnosticLabel(key);
    const label = prefix ? `${prefix} · ${fieldLabel}` : fieldLabel;
    if (item && typeof item === "object" && !Array.isArray(item)) {
      rows.push(...safeDiagnosticRows(item as Record<string, unknown>, label));
    } else if (typeof item === "boolean" || typeof item === "number" || item === null ||
      (typeof item === "string" && /(?:status|reason|outcome|method|policy|entity|check|source|state|direction|at|age|accuracy|distance|error)/i.test(key))) {
      rows.push({ label, value: item === null ? "Unavailable" : typeof item === "boolean" ? item ? "Yes" : "No" : String(item).slice(0, 300) });
    }
  }
  return rows;
}

function diagnosticLabel(key: string) {
  const labels: Record<string, string> = {
    latest_age_at_capture_s: "Latest sample age at capture (seconds)",
    latest_age_at_decision_s: "Latest sample age at decision (seconds)",
    latest_distance_m: "Latest distance to gate (metres)",
    latest_accuracy_m: "Latest accuracy radius (metres)",
    away_sample_count: "Away samples", away_span_s: "Away observation span (seconds)",
    approach_sample_count: "Approach samples", approach_span_s: "Approach observation span (seconds)",
    progress_beyond_uncertainty_m: "Progress beyond uncertainty (metres)",
    outward_leg: "Outward leg detected", gate_state: "Gate state",
    gate_observation_age_s: "Gate observation age (seconds)",
    camera_direction: "Camera direction", camera_clear: "Camera clear",
    camera_elapsed_ms: "Camera evaluation duration (milliseconds)",
    phone_evaluation_ms: "Phone evaluation duration (milliseconds)"
  };
  return labels[key] ?? recoveryLabel(key);
}
