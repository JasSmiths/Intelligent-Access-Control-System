import type { RealtimeMessage } from "../../api/types";
import { auditResource } from "../../lib/realtime";
import type { DirectoryKind } from "../../api/directory";

export function directoryEventMatches(kind: DirectoryKind, event: RealtimeMessage) {
  if (event.type === "vehicle_information.updated" || event.type === "access_event.finalized" || event.type === "access_event.updated" || event.type === "access_event.finalize_failed" || event.type.startsWith("schedule.")) return true;
  const resource = auditResource(event);
  return ["group", "person", "vehicle", "schedule"].includes(resource) || kind === "people" && resource === "access_device";
}
export function groupsEventMatches(event: RealtimeMessage) { return ["group", "person"].includes(auditResource(event)); }
export function schedulesEventMatches(event: RealtimeMessage) { return event.type.startsWith("schedule.") || ["schedule", "settings", "access_device"].includes(auditResource(event)); }
