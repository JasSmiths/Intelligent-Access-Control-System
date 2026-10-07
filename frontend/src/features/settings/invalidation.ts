import type { RealtimeMessage } from "../../api/types";
import { auditResource } from "../../lib/realtime";
export const settingsEventMatches = (event: RealtimeMessage) => auditResource(event) === "settings";
