import { secretSettingKeys } from "../../../lib/settings";
import { EditorDialog } from "../../../ui/EditorDialog";
import { IntegrationFeedbackPanel, ProviderSettingsGrid } from "../components";
import type { ProviderEditorProps } from "../editorTypes";
import { useIntegrationSettingsEditor } from "../useIntegrationSettingsEditor";

export function ScalarSettingsEditor(props: ProviderEditorProps) {
  const editor = useIntegrationSettingsEditor(props);
  return <EditorDialog title={props.definition.title} description={props.loading ? "Loading settings..." : props.definition.description} dirty={editor.dirty} busy={editor.busy} onClose={props.onClose} onGuardChange={props.onGuardChange}>
    {({ closeAccepted }) => <form className="integration-settings-form" onSubmit={async (event) => { event.preventDefault(); if (await editor.save()) void closeAccepted(); }}>
      <ProviderSettingsGrid fields={props.definition.fields} form={editor.form} disabled={!editor.canManage || editor.busy || props.loading} isConfiguredSecret={(key) => secretSettingKeys.has(key) && Boolean(props.values[key])} onChange={editor.update} />
      {editor.feedback ? <IntegrationFeedbackPanel feedback={editor.feedback} /> : null}
      <div className="modal-actions"><button className="secondary-button" disabled={!editor.canManage || editor.busy || props.loading} onClick={editor.test} type="button">{editor.operation === "test" ? "Testing..." : "Test Connection"}</button><button className="primary-button" disabled={!editor.canManage || editor.busy || props.loading} type="submit">{editor.operation === "save" ? "Saving..." : "Save"}</button></div>
    </form>}
  </EditorDialog>;
}
