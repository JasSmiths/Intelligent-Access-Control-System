import React from "react";
import { confirmedPatch, isMutationNotStarted, testIntegrationSettings } from "../../api/integrations";
import type { SettingsSaveReceipt } from "../../api/types";
import { coerceSettingsPayload, settingsActivationWarning } from "../../lib/settings";
import { useEditorLifetime } from "../../ui/useEditorLifetime";
import { integrationInitialValues, type IntegrationFeedback } from "./catalog";
import type { ProviderEditorProps } from "./editorTypes";

// The settings protocol is shared; the mounted provider owns its draft and operations.
export function useIntegrationSettingsEditor({ definition, values, currentUser, onChanged }: ProviderEditorProps) {
  const lifetime = useEditorLifetime();
  const [form, setForm] = React.useState(() => integrationInitialValues(definition, values));
  const baseline = React.useRef(JSON.stringify(form));
  const pending = React.useRef(false);
  const [operation, setOperation] = React.useState<"save" | "test" | null>(null);
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const canManage = currentUser.role === "admin";
  const update = (key: string, value: string) => {
    if (!pending.current && canManage && lifetime.isCurrent()) setForm((current) => ({ ...current, [key]: value }));
  };
  const save = async (): Promise<boolean> => {
    if (!canManage || pending.current || !lifetime.isCurrent()) return false;
    pending.current = true; setOperation("save"); setFeedback(null);
    try {
      const payload = { values: coerceSettingsPayload(form) };
      const result = await confirmedPatch<SettingsSaveReceipt>("/api/v1/settings", "settings.update", payload, {
        target_entity: "Integration", target_id: definition.key, target_label: definition.title, reason: "Save integration settings"
      }, payload, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return false;
      baseline.current = JSON.stringify(form);
      const warning = settingsActivationWarning(result);
      setFeedback({ tone: warning ? "info" : "success", title: "Settings saved", detail: warning || `${definition.title} settings were accepted.` });
      // Page metadata has its own read errors; it cannot reverse the accepted PATCH.
      void onChanged().catch(() => undefined);
      return !warning;
    } catch (error) {
      if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Unable to save settings", detail: error instanceof Error ? error.message : "Unable to save settings." });
      return false;
    } finally { pending.current = false; if (lifetime.isCurrent()) setOperation(null); }
  };
  const test = async () => {
    if (!canManage || pending.current || !lifetime.isCurrent()) return;
    pending.current = true; setOperation("test");
    setFeedback({ tone: "progress", title: "Testing connection", detail: `Contacting ${definition.title}.`, activeStep: 1 });
    try {
      const result = await testIntegrationSettings({ integration: definition.key, values: coerceSettingsPayload(form) }, definition.title, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      if (!result.ok) throw new Error(result.message);
      setFeedback({ tone: "success", title: "Connection verified", detail: result.message });
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Connection failed", detail: error instanceof Error ? error.message : "Connection test failed." }); }
    finally { pending.current = false; if (lifetime.isCurrent()) setOperation(null); }
  };
  return { form, update, save, test, feedback, setFeedback, lifetime, canManage, operation, busy: operation !== null, dirty: JSON.stringify(form) !== baseline.current };
}
