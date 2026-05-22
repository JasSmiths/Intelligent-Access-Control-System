import { LogsWorkspace } from "./logExplorer/LogsWorkspace";

export function LogsView({
  refreshToken
}: {
  refreshToken: number;
}) {
  return <LogsWorkspace refreshToken={refreshToken} />;
}
