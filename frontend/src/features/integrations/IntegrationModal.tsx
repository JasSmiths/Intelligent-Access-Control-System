import React from "react";
import { ReadSignalsContext } from "../../lib/readSignals";
import { integrationDefinitions, type IntegrationDefinition } from "./catalog";
import type { ProviderEditorProps } from "./editorTypes";
import { AppriseEditor } from "./providers/ApprisePanel";
import { ESPHomeEditor } from "./providers/ESPHomePanel";
import { HomeAssistantEditor } from "./providers/HomeAssistantPanel";
import { ICloudCalendarEditor } from "./providers/ICloudCalendarPanel";
import { ScalarSettingsEditor } from "./providers/ScalarSettingsEditor";
import { UnifiProtectEditor } from "./providers/UnifiProtectEditor";

const providerEditors: Readonly<Record<string, React.ComponentType<ProviderEditorProps>>> = {
  home_assistant: HomeAssistantEditor,
  apprise: AppriseEditor,
  esphome: ESPHomeEditor,
  icloud_calendar: ICloudCalendarEditor,
  unifi_protect: UnifiProtectEditor
};

export function assertManagedEditorCoverage(definitions: readonly IntegrationDefinition[], registeredKeys: readonly string[]) {
  const expected = new Set(definitions.filter((definition) => definition.editor === "managed").map((definition) => definition.key));
  const registered = new Set(registeredKeys);
  const missing = [...expected].filter((key) => !registered.has(key));
  const extra = [...registered].filter((key) => !expected.has(key));
  if (missing.length || extra.length) throw new Error(`Integration editor coverage: missing [${missing.join(", ")}], extra [${extra.join(", ")}]`);
}
export const managedEditorKeys = Object.keys(providerEditors);
assertManagedEditorCoverage(integrationDefinitions(null, {}, null, [], ""), managedEditorKeys);

// Account/provider identity owns the whole editor. Tabs and refreshed settings do
// not replace its draft; the caller guards an operator's provider switch first.
export function IntegrationModal(props: ProviderEditorProps) {
  const signals = React.useContext(ReadSignalsContext);
  const Editor = props.definition.editor === "scalar" ? ScalarSettingsEditor : providerEditors[props.definition.key];
  if (!Editor) throw new Error(`Missing managed integration editor: ${props.definition.key}`);
  const account = signals?.accountKey ?? `${props.currentUser.id}:${props.currentUser.role}`;
  return <Editor key={`${account}:${props.currentUser.id}:${props.currentUser.role}:${props.definition.key}`} {...props} />;
}
