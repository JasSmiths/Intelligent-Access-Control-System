import type { RealtimeMessage } from "../api/types";
import { isRecord, stringPayload } from "./format";
export function auditResource(event: RealtimeMessage) {
  if (event.type !== "audit.log.created") return "";
  const log = isRecord(event.payload.log) ? event.payload.log : event.payload;
  return stringPayload(log.action).split(".")[0];
}
