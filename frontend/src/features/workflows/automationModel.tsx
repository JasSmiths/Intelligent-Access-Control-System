import { Bell, Bot, CalendarDays, Car, Clock3, Construction, DoorOpen, GitBranch, MessageCircle, PlugZap, UserPlus, UserRound, Warehouse } from "lucide-react";
import React from "react";
import type { AutomationAction, AutomationCatalogGroup, AutomationCatalogItem, AutomationNode, AutomationRule, AutomationVariableGroup } from "../../api/workflows";
import type { WorkflowTriggerCategory } from "./model";
import { draftId, groupRulesByTrigger } from "./model";

type AutomationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: AutomationRule[];
};

export function createAutomationDraft(): AutomationRule {
  return {
    id: draftId("automation"),
    name: "New Automation",
    description: "",
    is_active: true,
    triggers: [],
    trigger_keys: [],
    conditions: [],
    actions: [],
    run_count: 0,
    last_run_status: null,
    last_error: null,
  };
}

export function createAutomationNode(kind: "trigger" | "condition" | "action", type: string, meta?: AutomationCatalogItem): AutomationNode | AutomationAction {
  const base = { id: draftId(kind), type, config: defaultAutomationConfig(type, meta) };
  if (kind === "action") return { ...base, reason_template: defaultAutomationReason(type) };
  return base;
}

function defaultAutomationConfig(type: string, meta?: AutomationCatalogItem): Record<string, unknown> {
  if (meta?.default_config) return { ...meta.default_config };
  if (type === "time.every_x") return { interval: 5, unit: "minutes" };
  if (type === "time.specific_datetime") return { run_at: "", recurrence: "none", single_use: true, end_at: "" };
  if (type === "time.cron") return { cron_expression: "0 9 * * *", timezone: "Europe/London", end_at: "" };
  if (type === "time.ai_text") return { natural_text: "", cron_expression: "", timezone: "Europe/London", end_at: "" };
  if (type === "ai.phrase_received") return { phrase: "", match_mode: "contains" };
  if (type.startsWith("webhook.")) return { webhook_key: `webhook-${Math.random().toString(16).slice(2)}${Date.now().toString(16)}` };
  if (type.startsWith("garage_door.")) return { target_entity_ids: [] };
  if (type.startsWith("notification.")) return { notification_rule_id: "" };
  if (type === "integration.icloud_calendar.sync") return { provider: "icloud_calendar", action: "sync_calendars" };
  if (type === "integration.whatsapp.send_message") {
    return {
      provider: "whatsapp",
      action: "send_message",
      target_mode: "selected",
      target_user_ids: [],
      phone_number_template: "",
      message_template: "@Subject",
    };
  }
  return {};
}

function defaultAutomationReason(type: string) {
  if (type === "gate.open") return "Automation opened the gate for @DisplayName.";
  if (type.startsWith("garage_door.")) return "Automation ran @EventType for @DisplayName.";
  if (type.startsWith("maintenance_mode.")) return "Automation changed Maintenance Mode: @Subject.";
  if (type.startsWith("integration.")) return "Automation ran integration action from @EventType.";
  return "Automation action from @EventType.";
}

export function automationRulePayload(rule: AutomationRule) {
  return {
    name: rule.name.trim() || "Automation Rule",
    description: rule.description,
    is_active: rule.is_active,
    triggers: rule.triggers,
    conditions: rule.conditions,
    actions: rule.actions,
  };
}

export function cloneAutomationRule(rule: AutomationRule): AutomationRule {
  return JSON.parse(JSON.stringify(rule)) as AutomationRule;
}

export function groupAutomationRulesByTriggerCategory(
  rules: AutomationRule[],
  triggerGroups: AutomationCatalogGroup[]
): AutomationRuleCategory[] {
  const categoryByTrigger = new Map<string, WorkflowTriggerCategory>();
  triggerGroups.forEach((group, order) => {
    const category = {
      id: group.id,
      label: group.label,
      icon: automationCategoryIcon(group.id),
      order,
    };
    (group.triggers ?? []).forEach((trigger) => {
      categoryByTrigger.set(trigger.type, category);
    });
  });

  return groupRulesByTrigger(
    rules,
    categoryByTrigger,
    (rule) => rule.triggers[0]?.type ?? rule.trigger_keys[0] ?? "",
    GitBranch
  );
}

export function automationVariablesForTrigger(groups: AutomationVariableGroup[], triggerType: string) {
  return groups.flatMap((group) => group.items
    .filter((item) => !triggerType || !item.trigger_types?.length || item.trigger_types.includes(triggerType))
    .map((item) => ({ ...item, group: group.group })));
}

export function automationCategoryIcon(groupId: string) {
  if (groupId.includes("time")) return Clock3;
  if (groupId.includes("vehicle")) return Car;
  if (groupId.includes("maintenance")) return Construction;
  if (groupId.includes("visitor")) return UserPlus;
  if (groupId.includes("webhook")) return PlugZap;
  if (groupId.includes("whatsapp")) return MessageCircle;
  if (groupId.includes("icloud") || groupId.includes("calendar")) return CalendarDays;
  if (groupId.includes("integration")) return PlugZap;
  if (groupId.includes("notification")) return Bell;
  if (groupId.includes("garage")) return Warehouse;
  if (groupId.includes("gate")) return DoorOpen;
  if (groupId.includes("ai")) return Bot;
  return GitBranch;
}

export function automationNodeIcon(type: string) {
  if (type.startsWith("time.")) return Clock3;
  if (type.startsWith("vehicle.")) return Car;
  if (type.startsWith("maintenance_mode.")) return Construction;
  if (type.startsWith("visitor_pass.")) return UserPlus;
  if (type.startsWith("webhook.")) return PlugZap;
  if (type.startsWith("integration.whatsapp")) return MessageCircle;
  if (type.startsWith("integration.icloud_calendar")) return CalendarDays;
  if (type.startsWith("integration.")) return PlugZap;
  if (type.startsWith("notification.")) return Bell;
  if (type.startsWith("garage_door.")) return Warehouse;
  if (type.startsWith("gate.")) return DoorOpen;
  if (type.startsWith("ai.")) return Bot;
  if (type.startsWith("person.")) return UserRound;
  return GitBranch;
}
