import type { RealtimeMessage } from "../../api/types";
import { auditResource } from "../../lib/realtime";
export function reportsEventMatches(event: RealtimeMessage) {
  return event.type === "access_event.finalized" || event.type === "access_event.updated" || event.type === "access_event.finalize_failed" || event.type === "vehicle_information.updated" || event.type.startsWith("visitor_pass.") || event.type.startsWith("schedule.") || ["person", "vehicle", "visitor_pass", "group", "schedule", "settings"].includes(auditResource(event));
}
