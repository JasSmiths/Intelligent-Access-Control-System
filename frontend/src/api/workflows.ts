import { api, createActionConfirmation } from "./client";
import type { NotificationChannelId, NotificationTriggerOption, UnifiProtectCamera, UserAccount } from "./types";

export type NotificationActionType = NotificationChannelId;
export type NotificationConditionType = "schedule" | "presence";
export type PresenceConditionMode = "no_one_home" | "someone_home" | "person_home";
export type NotificationTargetMode = "all" | "many" | "selected";
export type NotificationGateMalfunctionStage = "initial" | "30m" | "60m" | "2hrs" | "fubar" | "resolved";
export type NotificationEndpoint = { id: string; provider: string; label: string; detail: string };
export type NotificationIntegration = { id: NotificationChannelId; name: string; provider: string; configured: boolean; endpoints: NotificationEndpoint[] };
export type NotificationCondition = { id: string; type: NotificationConditionType; schedule_id?: string; mode?: PresenceConditionMode; person_id?: string };
export type NotificationAction = { id: string; type: NotificationActionType; target_mode: NotificationTargetMode; target_ids: string[]; title_template: string; message_template: string; gate_malfunction_stages: NotificationGateMalfunctionStage[]; media: { attach_camera_snapshot: boolean; camera_id: string }; actionable: { enabled: boolean; action: string } };
export type NotificationRule = { id: string; name: string; trigger_event: string; conditions: NotificationCondition[]; actions: NotificationAction[]; is_active: boolean; last_fired_at?: string | null; created_at?: string; updated_at?: string };
export type NotificationVariable = { name: string; token: string; label: string };
export type NotificationVariableGroup = { group: string; items: NotificationVariable[] };
export type NotificationTriggerGroup = { id: string; label: string; events: NotificationTriggerOption[] };
export type NotificationActionableOption = { value: string; label: string; description: string };
export type NotificationActionableGroup = { trigger_event: string; actions: NotificationActionableOption[] };
export type NotificationGateMalfunctionStageOption = { value: NotificationGateMalfunctionStage; label: string };
export type NotificationCatalogResponse = { triggers: NotificationTriggerGroup[]; variables: NotificationVariableGroup[]; integrations: NotificationIntegration[]; actionable_notifications?: NotificationActionableGroup[]; gate_malfunction_stages?: NotificationGateMalfunctionStageOption[]; mock_context: Record<string, string> };

export type AutomationNode = { id: string; type: string; config: Record<string, unknown> };
export type AutomationAction = AutomationNode & { reason_template?: string };
export type AutomationRule = { id: string; name: string; description: string; is_active: boolean; triggers: AutomationNode[]; trigger_keys: string[]; conditions: AutomationNode[]; actions: AutomationAction[]; next_run_at?: string | null; last_fired_at?: string | null; run_count: number; last_run_status?: string | null; last_error?: string | null; created_at?: string | null; updated_at?: string | null };
export type AutomationCatalogItem = { type: string; label: string; description?: string; scopes?: string[]; enabled?: boolean; disabled?: boolean; disabled_reason?: string | null; integration_action?: boolean; integration_provider?: string; integration_provider_label?: string; integration_action_key?: string; default_config?: Record<string, unknown> };
export type AutomationIntegrationCatalog = { id: string; label: string; description?: string; enabled?: boolean; disabled_reason?: string | null; actions: AutomationCatalogItem[] };
export type AutomationCatalogGroup = { id: string; label: string; triggers?: AutomationCatalogItem[]; conditions?: AutomationCatalogItem[]; actions?: AutomationCatalogItem[]; integrations?: AutomationIntegrationCatalog[] };
export type AutomationVariable = NotificationVariable & { scope?: string; trigger_types?: string[] };
export type AutomationVariableGroup = { group: string; scope?: string; items: AutomationVariable[] };
export type AutomationCatalogResponse = { triggers: AutomationCatalogGroup[]; conditions: AutomationCatalogGroup[]; actions: AutomationCatalogGroup[]; variables: AutomationVariableGroup[]; notification_rules: Array<{ id: string; name: string; trigger_event: string }>; garage_doors: Array<{ entity_id: string; name: string; schedule_id?: string | null }>; mock_context: Record<string, string> };
export type AutomationRulePayload = Pick<AutomationRule, "name" | "description" | "triggers" | "conditions" | "actions" | "is_active">;
export type NotificationRulePayload = Pick<NotificationRule, "name" | "trigger_event" | "conditions" | "actions" | "is_active">;

type ConfirmationTarget = { target_entity: string; target_id?: string; target_label?: string; reason: string };

async function confirmedPost<T>(path: string, action: string, payload: Record<string, unknown>, target: ConfirmationTarget): Promise<T> {
  const confirmation = await createActionConfirmation(action, payload, target);
  return api.post<T>(path, { ...payload, confirmation_token: confirmation.confirmation_token });
}
async function confirmedPatch<T>(path: string, action: string, payload: Record<string, unknown>, target: ConfirmationTarget): Promise<T> {
  const confirmation = await createActionConfirmation(action, payload, target);
  return api.patch<T>(path, { ...payload, confirmation_token: confirmation.confirmation_token });
}
async function confirmedDelete(path: string, action: string, payload: Record<string, unknown>, target: ConfirmationTarget): Promise<void> {
  const confirmation = await createActionConfirmation(action, payload, target);
  await api.delete(path, { confirmation_token: confirmation.confirmation_token });
}

export const workflowApi = {
  async getAutomationData(): Promise<{ catalog: AutomationCatalogResponse; rules: AutomationRule[]; users: UserAccount[] }> {
    const [catalog, rules, users] = await Promise.all([api.get<AutomationCatalogResponse>("/api/v1/automations/catalog"), api.get<AutomationRule[]>("/api/v1/automations/rules"), api.get<UserAccount[]>("/api/v1/users")]);
    return { catalog, rules, users };
  },
  saveAutomationRule(rule: AutomationRule, payload: AutomationRulePayload): Promise<AutomationRule> {
    const isCreate = rule.id.startsWith("draft-");
    const target = { target_entity: "AutomationRule", target_id: isCreate ? undefined : rule.id, target_label: payload.name, reason: isCreate ? "Create automation rule" : "Update automation rule" };
    return isCreate ? confirmedPost("/api/v1/automations/rules", "automation_rule.create", payload, target) : confirmedPatch(`/api/v1/automations/rules/${rule.id}`, "automation_rule.update", payload, target);
  },
  deleteAutomationRule(rule: AutomationRule): Promise<void> {
    return confirmedDelete(`/api/v1/automations/rules/${rule.id}`, "automation_rule.delete", { rule_id: rule.id }, { target_entity: "AutomationRule", target_id: rule.id, target_label: rule.name, reason: "Delete automation rule" });
  },
  toggleAutomationRule(rule: AutomationRule, isActive: boolean): Promise<AutomationRule> {
    const payload = { is_active: isActive };
    return confirmedPatch(`/api/v1/automations/rules/${rule.id}`, "automation_rule.update", payload, { target_entity: "AutomationRule", target_id: rule.id, target_label: rule.name, reason: isActive ? "Resume automation rule" : "Pause automation rule" });
  },
  runAutomationDryRun(payload: AutomationRulePayload): Promise<Record<string, unknown>> {
    return api.post<Record<string, unknown>>("/api/v1/automations/dry-run", payload);
  },
  parseAutomationSchedule(text: string): Promise<Record<string, unknown>> {
    return api.post<Record<string, unknown>>("/api/v1/automations/parse-schedule", { text });
  },
  async getNotificationData(): Promise<{ catalog: NotificationCatalogResponse; rules: NotificationRule[]; cameras: UnifiProtectCamera[] }> {
    const [catalog, rules, cameraResult] = await Promise.all([api.get<NotificationCatalogResponse>("/api/v1/notifications/catalog"), api.get<NotificationRule[]>("/api/v1/notifications/rules"), api.get<{ cameras: UnifiProtectCamera[] }>("/api/v1/integrations/unifi-protect/cameras").catch(() => ({ cameras: [] }))]);
    return { catalog, rules, cameras: cameraResult.cameras };
  },
  saveNotificationRule(rule: NotificationRule, payload: NotificationRulePayload): Promise<NotificationRule> {
    const isCreate = rule.id.startsWith("draft-");
    const target = { target_entity: "NotificationRule", target_id: isCreate ? undefined : rule.id, target_label: payload.name, reason: isCreate ? "Create notification workflow" : "Update notification workflow" };
    return isCreate ? confirmedPost("/api/v1/notifications/rules", "notification_rule.create", payload, target) : confirmedPatch(`/api/v1/notifications/rules/${rule.id}`, "notification_rule.update", payload, target);
  },
  deleteNotificationRule(rule: NotificationRule): Promise<void> {
    return confirmedDelete(`/api/v1/notifications/rules/${rule.id}`, "notification_rule.delete", { rule_id: rule.id }, { target_entity: "NotificationRule", target_id: rule.id, target_label: rule.name, reason: "Delete notification workflow" });
  },
  duplicateNotificationRule(payload: NotificationRulePayload): Promise<NotificationRule> {
    return confirmedPost("/api/v1/notifications/rules", "notification_rule.create", payload, { target_entity: "NotificationRule", target_label: payload.name, reason: "Duplicate notification workflow" });
  },
  toggleNotificationRule(rule: NotificationRule, isActive: boolean): Promise<NotificationRule> {
    const payload = { is_active: isActive };
    return confirmedPatch(`/api/v1/notifications/rules/${rule.id}`, "notification_rule.update", payload, { target_entity: "NotificationRule", target_id: rule.id, target_label: rule.name, reason: isActive ? "Resume notification workflow" : "Pause notification workflow" });
  },
  testNotificationRule(rule: NotificationRule, payload: NotificationRulePayload): Promise<void> {
    const body = { rule: payload };
    return confirmedPost("/api/v1/notifications/rules/test", "notification_rule.test", body, { target_entity: "NotificationRule", target_id: rule.id.startsWith("draft-") ? undefined : rule.id, target_label: rule.name || "Draft notification workflow", reason: "Send notification workflow test" });
  }
};
