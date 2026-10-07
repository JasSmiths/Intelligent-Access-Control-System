import type { SettingsMap, UserAccount } from "../../api/types";
import type { EditorCloseGuard } from "../../ui/EditorDialog";
import type { IntegrationDefinition, ProtectIntegrationTab } from "./catalog";

export type ProviderEditorProps = {
  definition: IntegrationDefinition;
  currentUser: UserAccount;
  initialTab: ProtectIntegrationTab;
  values: SettingsMap;
  loading: boolean;
  onClose: () => void;
  onGuardChange?: (guard: EditorCloseGuard | null) => void;
  onChanged: () => Promise<void>;
};
