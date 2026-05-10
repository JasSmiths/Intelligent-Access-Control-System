import { RealtimeMessage } from "../shared";
import { LogsWorkspace } from "./logExplorer/LogsWorkspace";

export function LogsView({
  logs,
  onClearRealtime,
  refreshToken
}: {
  logs: RealtimeMessage[];
  onClearRealtime: () => void;
  refreshToken: number;
}) {
  return <LogsWorkspace logs={logs} onClearRealtime={onClearRealtime} refreshToken={refreshToken} />;
}
