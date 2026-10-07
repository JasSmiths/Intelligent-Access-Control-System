import type { ActionConfigByType } from "../../api/generated/workflows";
import { ACTION_METADATA, isAutomationActionType } from "../../api/generated/workflows";
import type { AutomationCatalogResponse } from "../../api/workflows";

type ReferenceConfig = ActionConfigByType["notification.enable"];
type GarageConfig = ActionConfigByType["garage_door.open"];
type EditorProps = {
  action: {type: string; config: Record<string, unknown>};
  notificationRules: Array<{id: string; name: string}>;
  garageDoors: AutomationCatalogResponse["garage_doors"];
  onChange: (config: Record<string, unknown>) => void;
};

function NotificationReferenceEditor({config, rules, onChange}: {
  config: ReferenceConfig;
  rules: Array<{id: string; name: string}>;
  onChange: (config: Partial<ReferenceConfig>) => void;
}) {
  return (
    <label className="field compact-field">
      <span>Notification rule</span>
      <select value={config.notification_rule_id} onChange={(event) => onChange({notification_rule_id: event.target.value})}>
        <option value="">Select notification</option>
        {rules.map((rule) => <option key={rule.id} value={rule.id}>{rule.name}</option>)}
      </select>
    </label>
  );
}

function GarageTargetsEditor({config, doors, onChange}: {
  config: GarageConfig;
  doors: AutomationCatalogResponse["garage_doors"];
  onChange: (config: Partial<GarageConfig>) => void;
}) {
  return (
    <div className="workflow-target-chips">
      {doors.map((door) => {
        const selected = config.target_entity_ids.includes(door.entity_id);
        return (
          <button className={selected ? "workflow-target-chip selected" : "workflow-target-chip"} key={door.entity_id}
            onClick={() => onChange({target_entity_ids: selected
              ? config.target_entity_ids.filter((id) => id !== door.entity_id)
              : [...config.target_entity_ids, door.entity_id]})} type="button">
            <strong>Garage</strong>{door.name}
          </button>
        );
      })}
    </div>
  );
}

type Editor = (props: EditorProps) => React.ReactNode;
type EditorId = typeof ACTION_METADATA[keyof typeof ACTION_METADATA]["editor"];
const editors = {
  none: () => null,
  notification_reference: ({action, notificationRules, onChange}) => (
    <NotificationReferenceEditor config={{
      notification_rule_id: typeof action.config.notification_rule_id === "string" ? action.config.notification_rule_id : "",
      notification_rule_name: typeof action.config.notification_rule_name === "string" ? action.config.notification_rule_name : "",
    }} rules={notificationRules} onChange={onChange} />
  ),
  garage_targets: ({action, garageDoors, onChange}) => (
    <GarageTargetsEditor config={{
      target_entity_ids: Array.isArray(action.config.target_entity_ids)
        ? action.config.target_entity_ids.filter((value): value is string => typeof value === "string") : [],
    }} doors={garageDoors} onChange={onChange} />
  ),
} satisfies Record<EditorId, Editor>;

export function AutomationActionConfigEditor(props: EditorProps) {
  if (!isAutomationActionType(props.action.type)) {
    return <p role="alert">This retained action is unsupported. Replace it before saving or enabling the workflow.</p>;
  }
  return editors[ACTION_METADATA[props.action.type].editor](props);
}
