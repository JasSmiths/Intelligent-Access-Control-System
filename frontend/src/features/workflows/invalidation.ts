import type { RealtimeMessage } from "../../api/types";
import { auditResource } from "../../lib/realtime";
export function workflowEventMatches(event: RealtimeMessage) {
  return event.type.startsWith("automation.run.") || event.type.startsWith("schedule.") || ["access_device", "home_assistant", "automation_rule", "notification_rule", "person", "vehicle", "schedule", "settings", "user", "apprise"].includes(auditResource(event));
}
export function automationRunEventMatches(event: RealtimeMessage) {
  return event.type.startsWith("automation.run.") || auditResource(event) === "automation_rule";
}
export function notificationCameraEventMatches(event: RealtimeMessage) {
  return ["settings", "unifi_protect"].includes(auditResource(event));
}

export function notificationRunEventMatches(event: RealtimeMessage) {
  return event.type.startsWith("notification.");
}
