import React from "react";
import {fireEvent, render, screen} from "@testing-library/react";
import {expect, it, vi} from "vitest";
import type {NotificationIntegration, NotificationRule} from "../../api/workflows";
import {NotificationActionCard} from "./NotificationActionCard";
import {bindNewRecipientReferences, buildNotificationActionMethods, cloneNotificationRule, createWorkflowAction, notificationRecipientsNeedReview, workflowRulePayload} from "./notificationModel";

const previous = "a".repeat(64), current = "b".repeat(64);
const integration: NotificationIntegration = {id: "mobile", name: "Mobile", provider: "Apprise", configured: true,
  apprise_revision: current, endpoints: [{id: "apprise:1", provider: "Apprise", label: "Current C", detail: "Destination", apprise_revision: current}]};
const selected = () => createWorkflowAction("mobile", {target_mode: "selected", target_ids: ["apprise:1"], apprise_revision: previous});

it("preserves saved bindings through clone, payload and unrelated edits", () => {
  const action = selected();
  const rule: NotificationRule = {id: "rule", name: "Original", trigger_event: "synthetic", conditions: [], actions: [action], is_active: true};
  expect(cloneNotificationRule(rule).actions[0].apprise_revision).toBe(previous);
  expect(workflowRulePayload({...rule, name: "Renamed"}).actions[0].apprise_revision).toBe(previous);
  expect(bindNewRecipientReferences(action, {...action, message_template: "Edited"}, integration).apprise_revision).toBe(previous);
});

it("binds a newly selected action to the selection catalog", () => {
  const method = buildNotificationActionMethods([integration], null).mobile[0];
  const action = createWorkflowAction(method.actionType, {target_mode: method.targetMode,
    target_ids: method.defaultTargetIds, apprise_revision: method.apprise_revision});
  expect(action.apprise_revision).toBe(current);
  expect(notificationRecipientsNeedReview(action, integration)).toBe(false);
});

it("shows stale recipients for deliberate replacement without relabelling B as C", () => {
  const onChange = vi.fn(), onRemove = vi.fn();
  render(<NotificationActionCard action={selected()} integration={integration} actionableOptions={[]}
    cameras={[]} isGateMalfunctionWorkflow={false} stageOptions={[]} variables={[]} onChange={onChange} onRemove={onRemove} />);
  expect(screen.getByRole("alert")).toHaveTextContent("destination list changed");
  expect(screen.queryByText("Current C")).not.toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name: "Remove action"}));
  expect(onRemove).toHaveBeenCalledOnce();
});

it("binds positional visibility even in all mode, without binding unrestricted broadcast", () => {
  const all = createWorkflowAction("mobile", {target_mode: "all", apprise_revision: previous});
  expect(notificationRecipientsNeedReview(all, integration)).toBe(false);
  const restricted = {...all, variable_recipients: {message_template: [{occurrence: 0, name: "FirstName", target_ids: ["apprise:1"]}]}};
  expect(notificationRecipientsNeedReview(restricted, integration)).toBe(true);
  expect(bindNewRecipientReferences(all, restricted, integration).apprise_revision).toBe(current);
});
