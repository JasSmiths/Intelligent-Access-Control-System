import React from "react";
import { confirmedPatch, isMutationNotStarted } from "../../../api/integrations";
import type { SettingsMap, SettingsSaveReceipt, UserAccount } from "../../../api/types";
import { settingsActivationWarning } from "../../../lib/settings";
import { ReadSignalsContext } from "../../../lib/readSignals";
import type { LlmProviderKey } from "../../../lib/format";
import { useEditorLifetime } from "../../../ui/useEditorLifetime";
import { CameraAiProviderSelector } from "../catalog";

export function CameraAiProviderSettings({ currentUser, values, loading, onChanged }: { currentUser: UserAccount; values: SettingsMap; loading: boolean; onChanged: () => Promise<void> }) {
  const signals = React.useContext(ReadSignalsContext);
  const identity = React.useMemo(() => ({ signals, user: currentUser.id, role: currentUser.role }), [signals, currentUser.id, currentUser.role]);
  const lifetime = useEditorLifetime(identity);
  const pending = React.useRef(false);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  React.useLayoutEffect(() => { pending.current = false; setSaving(false); setError(""); }, [lifetime]);
  const select = async (provider: LlmProviderKey) => {
    if (currentUser.role !== "admin" || loading || pending.current || !lifetime.isCurrent()) return;
    pending.current = true; setSaving(true); setError("");
    try {
      const payload = { values: { llm_provider: provider } };
      const result = await confirmedPatch<SettingsSaveReceipt>("/api/v1/settings", "settings.update", payload, { target_entity: "SystemSetting", target_id: "llm_provider", target_label: provider, reason: "Update LLM provider" }, payload, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      setError(settingsActivationWarning(result));
      void onChanged().catch(() => undefined);
    } catch (error) { if (lifetime.isCurrent()) setError(error instanceof Error ? error.message : "Unable to save camera AI provider."); }
    finally { if (lifetime.isCurrent()) { pending.current = false; setSaving(false); } }
  };
  return <div><CameraAiProviderSelector saving={currentUser.role !== "admin" || loading || saving} values={values} onChange={select} />{error ? <p role="alert">{error}</p> : null}</div>;
}
