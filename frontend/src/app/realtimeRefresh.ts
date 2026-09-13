import type { RealtimeMessage, ViewKey } from "../api/types";
import { isRecord, stringPayload } from "../lib/format";
import type { ShellDataKey } from "./navigation";
import type { RefreshSelection } from "./refreshCoordinator";

type Impact = { keys: ShellDataKey[]; views?: ViewKey[] };
const accessImpact: Impact = {
  keys: ["presence", "expectedPresence", "events", "vehicles", "people", "anomalies"],
  views: ["movements", "alerts", "logs"]
};
const settingsViews: ViewKey[] = ["schedules", "settings_general", "settings_gates", "settings_garage_doors", "settings_auth", "settings_lpr", "settings_zones", "integrations", "alfred_training", "settings_notifications", "settings_automations"];
const auditImpacts: Record<string, Impact> = {
  access_device: { keys: ["integrationStatus", "people", "schedules"], views: ["settings_gates", "settings_garage_doors", "settings_automations", "settings_notifications", "integrations"] },
  esphome: { keys: ["integrationStatus"], views: ["settings_gates", "settings_garage_doors", "integrations"] },
  apprise: { keys: [], views: ["settings_notifications", "integrations"] },
  home_assistant: { keys: ["integrationStatus"], views: ["settings_gates", "settings_garage_doors", "settings_notifications", "settings_automations", "integrations"] },
  automation_rule: { keys: [], views: ["settings_automations"] },
  notification_rule: { keys: [], views: ["settings_notifications", "settings_automations"] },
  dependency_updates: { keys: [], views: ["integrations"] },
  group: { keys: ["groups", "people", "vehicles", "expectedPresence"] },
  person: { keys: ["people", "vehicles", "groups", "presence", "expectedPresence"], views: ["settings_notifications", "settings_automations", "passes"] },
  schedule: { keys: ["schedules", "people", "vehicles", "groups", "integrationStatus", "expectedPresence"], views: ["settings_gates", "settings_garage_doors", "settings_notifications", "settings_automations"] },
  settings: { keys: ["integrationStatus", "maintenanceStatus", "schedules", "expectedPresence"], views: settingsViews },
  user: { keys: [], views: ["users", "settings_notifications", "settings_automations"] },
  vehicle: { keys: ["vehicles", "people", "events", "presence", "expectedPresence"], views: ["settings_automations", "movements", "top_charts"] },
  visitor_pass: { keys: ["expectedPresence", "presence", "events"], views: ["movements"] },
};

// Passes and Top Charts already consume compact domain events directly. Do not
// also increment their refresh token for those events (that caused duplicate reads).
export function refreshSelectionForEvent(event: RealtimeMessage, view: ViewKey, required: Set<ShellDataKey>): RefreshSelection {
  let impact: Impact | undefined;
  if (event.type === "access_event.finalized" || event.type === "access_event.finalize_failed") impact = accessImpact;
  else if (event.type === "alerts.updated") impact = { keys: ["anomalies"], views: ["alerts"] };
  else if (event.type.startsWith("automation.run.")) impact = { keys: [], views: ["settings_automations", "logs"] };
  else if (event.type.startsWith("visitor_pass.")) impact = auditImpacts.visitor_pass;
  else if (event.type.startsWith("schedule.")) impact = auditImpacts.schedule;
  else if (event.type === "audit.log.created") {
    const log = isRecord(event.payload.log) ? event.payload.log : event.payload;
    impact = auditImpacts[stringPayload(log.action).split(".")[0]];
    if (view === "logs") return { keys: new Set(), route: true };
  }
  return {
    keys: new Set((impact?.keys ?? []).filter((key) => required.has(key))),
    route: impact?.views?.includes(view) ?? false
  };
}
