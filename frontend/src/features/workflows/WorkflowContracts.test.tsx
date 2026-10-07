import React from "react";
import {fireEvent, render, screen} from "@testing-library/react";
import {describe, expect, it, vi} from "vitest";
import {ACTION_METADATA, defaultActionConfig, defaultNotificationConfig, NOTIFICATION_CHANNELS} from "../../api/generated/workflows";
import type {NotificationAction, NotificationRule} from "../../api/workflows";
import {AutomationActionConfigEditor} from "./AutomationActionConfigEditor";
import {NotificationWorkflowEditor} from "./NotificationEditor";
import {cloneNotificationRule, createWorkflowAction, notificationActionWithSupportedActionable, renderWorkflowPreview, workflowRulePayload} from "./notificationModel";

describe("generated workflow contracts", () => {
  it("returns fresh nested defaults and retains each channel's existing templates", () => {
    const targets = defaultActionConfig("garage_door.open");
    targets.target_entity_ids.push("cover.modified");
    expect(defaultActionConfig("garage_door.open").target_entity_ids).toEqual([]);
    const channel = defaultNotificationConfig("mobile");
    channel.media.camera_id = "modified";
    expect(defaultNotificationConfig("mobile").media.camera_id).toBe("");
    expect(defaultNotificationConfig("voice").message_template).toBe("@FirstName has arrived at the gate.");
    for (const type of NOTIFICATION_CHANNELS) {
      expect(createWorkflowAction(type)).toMatchObject(defaultNotificationConfig(type));
    }
  });

  it("binds every generated editor identity to explicit typed controls", () => {
    for (const [type, metadata] of Object.entries(ACTION_METADATA)) {
      const onChange = vi.fn();
      const {unmount} = render(<AutomationActionConfigEditor action={{type, config: metadata.editor === "notification_reference"
        ? {notification_rule_id: "selected-rule", notification_rule_name: "Selected"} : {}}}
        notificationRules={[{id: "rule", name: "Notice"}, {id: "selected-rule", name: "Selected"}]} garageDoors={[{entity_id: "cover.garage", name: "Main"}]}
        onChange={onChange} />);
      if (metadata.editor === "notification_reference") {
        expect(screen.getByRole("combobox")).toHaveValue("selected-rule");
        fireEvent.change(screen.getByRole("combobox"), {target: {value: "rule"}});
        expect(onChange).toHaveBeenCalledWith({notification_rule_id: "rule"});
      } else if (metadata.editor === "garage_targets") {
        fireEvent.click(screen.getByRole("button", {name: /GarageMain/}));
        expect(onChange).toHaveBeenCalledWith({target_entity_ids: ["cover.garage"]});
      } else {
        expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      }
      unmount();
    }
  });

  it("keeps minimal unknown actions readable through trigger change, preview, removal and replacement", () => {
    const unknown = {id: "old", type: "retired.channel", custom: {ordered: [2, 1]}, media: null} as unknown as NotificationAction;
    const original = JSON.parse(JSON.stringify(unknown));
    let observed: NotificationRule | undefined;
    function Editor() {
      const [rule, setRule] = React.useState<NotificationRule>({
        id: "retained-rule", name: "Retained", trigger_event: "authorized_entry",
        is_active: false, conditions: [], actions: [unknown],
      });
      observed = rule;
      return <NotificationWorkflowEditor rule={rule} actionableOptions={[]} cameras={[]} feedback={null}
        gateMalfunctionStageOptions={[]} integrations={[]} people={[]} schedules={[]} saving={false} testing={false}
        trigger={undefined} variables={[]} previewActions={renderWorkflowPreview(rule.actions, {})}
        onUpdate={setRule} onAddAction={() => setRule((current) => ({...current, actions: [createWorkflowAction("in_app")]}))}
        onAddCondition={vi.fn()} onCancel={vi.fn()} onDelete={vi.fn()} onSave={vi.fn()} onSendTest={vi.fn()}
        onShowTrigger={() => setRule((current) => ({...current, trigger_event: "gate_malfunction",
          actions: current.actions.map((action) => notificationActionWithSupportedActionable(action, []))}))} />;
    }
    render(<Editor />);
    expect(screen.getByRole("alert")).toHaveTextContent("unsupported");
    fireEvent.click(screen.getByRole("button", {name: /Authori[sz]ed Entry/i}));
    expect(observed?.trigger_event).toBe("gate_malfunction");
    expect(observed?.actions[0]).toEqual(original);
    expect(workflowRulePayload(cloneNotificationRule(observed!)).actions[0]).toEqual(original);
    expect(screen.getByLabelText("Live notification preview")).toHaveTextContent("This retained action is unsupported.");
    fireEvent.click(screen.getByRole("button", {name: "Remove action"}));
    expect(observed?.actions).toEqual([]);
    fireEvent.click(screen.getByRole("button", {name: /Add Action/i}));
    expect(observed?.actions[0].type).toBe("in_app");
    expect(unknown).toEqual(original);
  });
});
