import type { RealtimeMessage } from "../../api/types";
import { auditResource } from "../../lib/realtime";
export const integrationEventMatches = (event: RealtimeMessage) => ["access_device", "esphome", "apprise", "home_assistant", "settings"].includes(auditResource(event));
