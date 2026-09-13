import { AlertTriangle, Bell, Bot, Car, Construction, DoorOpen, Home, MessageCircle, Monitor, PlugZap, ShieldCheck, Smartphone, Trophy, UserPlus, Volume2 } from "lucide-react";
import React from "react";
import type { Person, UserAccount } from "../../api/types";
import type { NotificationAction, NotificationActionableOption, NotificationActionType, NotificationCondition, NotificationConditionType, NotificationEndpoint, NotificationGateMalfunctionStage, NotificationGateMalfunctionStageOption, NotificationIntegration, NotificationRule, NotificationTargetMode, NotificationTriggerGroup, PresenceConditionMode } from "../../api/workflows";
import { displayUserName, titleCase } from "../../lib/format";
import { notificationChannelMeta } from "../../lib/notifications";
import type { BadgeTone } from "../../ui/primitives";
import type { TwoPaneCategory, WorkflowTriggerCategory } from "./model";
import { draftId, groupRulesByTrigger, normalizeIdentityName, renderWorkflowTemplate, stringifyTemplateValue } from "./model";

type NotificationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: NotificationRule[];
};

export type NotificationActionMethod = {
  id: string;
  actionType: NotificationActionType;
  label: string;
  provider: string;
  detail: string;
  icon: React.ElementType;
  tone: BadgeTone;
  targets: NotificationEndpoint[];
  targetMode: NotificationTargetMode;
  requiresTarget: boolean;
  defaultTargetIds: string[];
  unavailableReason?: string;
};

const defaultWorkflowActionTemplates: Record<NotificationActionType, Pick<NotificationAction, "title_template" | "message_template">> = {
  mobile: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName. Gate status: @GateStatus."
  },
  in_app: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName."
  },
  voice: {
    title_template: "",
    message_template: "@FirstName has arrived at the gate."
  },
  discord: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName. Gate status: @GateStatus."
  },
  whatsapp: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName. Gate status: @GateStatus."
  }
};

const vehicleTtsPhonetics: Record<string, string> = {
  BMW: "bee em double you",
  BYD: "bee why dee",
  GMC: "gee em see",
  MG: "em gee",
  VW: "vee double you",
  DS: "dee ess"
};

const vehicleTtsPhoneticPattern = new RegExp(
  `\\b(${Object.keys(vehicleTtsPhonetics).sort((left, right) => right.length - left.length).join("|")})\\b`
);

export function groupNotificationRulesByTriggerCategory(
  rules: NotificationRule[],
  triggerGroups: NotificationTriggerGroup[]
): NotificationRuleCategory[] {
  const categoryByTrigger = new Map<string, WorkflowTriggerCategory>();
  triggerGroups.forEach((group, order) => {
    const category = {
      id: group.id,
      label: group.label,
      icon: notificationTriggerGroupIcon(group.id),
      order,
    };
    group.events.forEach((event) => {
      categoryByTrigger.set(event.value, category);
    });
  });

  return groupRulesByTrigger(rules, categoryByTrigger, (rule) => rule.trigger_event, Bell);
}

export function notificationTriggerGroupIcon(groupId: string) {
  if (groupId === "ai_agents") return Bot;
  if (groupId === "compliance") return ShieldCheck;
  if (groupId === "gate_actions") return DoorOpen;
  if (groupId === "gate_malfunctions") return AlertTriangle;
  if (groupId === "integrations") return PlugZap;
  if (groupId === "leaderboard") return Trophy;
  if (groupId === "maintenance_mode") return Construction;
  if (groupId === "vehicle_detections") return Car;
  if (groupId === "visitor_pass") return UserPlus;
  return Bell;
}

export function notificationActionCategories(): TwoPaneCategory[] {
  return (["mobile", "whatsapp", "discord", "voice", "in_app"] as NotificationActionType[])
    .map((id) => {
      const meta = notificationChannelMeta[id];
      return { id, label: meta.label, count: 0, icon: meta.icon };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function buildNotificationActionMethods(
  integrations: NotificationIntegration[],
  currentUserPerson: Person | null
): Record<NotificationActionType, NotificationActionMethod[]> {
  const integrationById = new Map(integrations.map((integration) => [integration.id, integration]));
  const mobileIntegration = integrationById.get("mobile");
  const voiceIntegration = integrationById.get("voice");
  const inAppIntegration = integrationById.get("in_app");
  const discordIntegration = integrationById.get("discord");
  const whatsappIntegration = integrationById.get("whatsapp");
  const mobileEndpoints = concreteNotificationEndpoints(mobileIntegration?.endpoints ?? []);
  const homeAssistantMobileTargets = mobileEndpoints.filter((endpoint) => endpoint.id.startsWith("home_assistant_mobile:"));
  const appriseTargets = mobileEndpoints.filter((endpoint) => endpoint.id.startsWith("apprise:"));
  const currentUserTarget = currentUserPerson?.home_assistant_mobile_app_notify_service
    ? `home_assistant_mobile:${currentUserPerson.home_assistant_mobile_app_notify_service}`
    : "";
  const mobileMethods: NotificationActionMethod[] = [];

  if (homeAssistantMobileTargets.length) {
    mobileMethods.push({
      id: "home_assistant_mobile",
      actionType: "mobile",
      label: "Home Assistant",
      provider: "Home Assistant",
      detail: homeAssistantMobileTargets.length
        ? `${homeAssistantMobileTargets.length} mobile app target${homeAssistantMobileTargets.length === 1 ? "" : "s"}`
        : "No mobile app notify services discovered",
      icon: Home,
      tone: "blue",
      targets: homeAssistantMobileTargets,
      targetMode: "selected",
      requiresTarget: true,
      defaultTargetIds: homeAssistantMobileTargets.some((target) => target.id === currentUserTarget) ? [currentUserTarget] : [],
    });
  }

  for (const endpoint of appriseTargets) {
    mobileMethods.push({
      id: endpoint.id,
      actionType: "mobile",
      label: endpoint.label,
      provider: "Apprise",
      detail: endpoint.detail || "Configured Apprise destination",
      icon: Smartphone,
      tone: "blue",
      targets: [endpoint],
      targetMode: "selected",
      requiresTarget: true,
      defaultTargetIds: [endpoint.id],
    });
  }

  const voiceTargets = concreteNotificationEndpoints(voiceIntegration?.endpoints ?? []);
  const voiceMethods = providerNotificationMethods({
    actionType: "voice",
    detail: (count) => count ? `${count} media player target${count === 1 ? "" : "s"}` : "No media players discovered",
    id: "home_assistant_tts",
    icon: Volume2,
    integration: voiceIntegration,
    label: "Home Assistant",
    provider: "Home Assistant TTS",
    targets: voiceTargets,
    tone: "amber",
    unavailableReason: "Home Assistant TTS is configured, but no media_player targets are available.",
  });

  const dashboardEndpoint = inAppIntegration?.endpoints[0] ?? {
    id: "dashboard",
    provider: "Dashboard",
    label: "All signed-in dashboards",
    detail: "Realtime in-app notification stream",
  };
  const inAppMethods: NotificationActionMethod[] = [
    {
      id: "dashboard",
      actionType: "in_app",
      label: "Dashboard",
      provider: "Dashboard",
      detail: dashboardEndpoint.detail || "Realtime in-app notification stream",
      icon: Monitor,
      tone: "green",
      targets: [dashboardEndpoint],
      targetMode: "all",
      requiresTarget: false,
      defaultTargetIds: [dashboardEndpoint.id],
    },
  ];

  const discordTargets = concreteNotificationEndpoints(discordIntegration?.endpoints ?? []);
  const discordMethods = providerNotificationMethods({
    actionType: "discord",
    detail: (count) => count ? `${count} channel${count === 1 ? "" : "s"} available` : "No Discord channels discovered",
    id: "discord",
    icon: MessageCircle,
    integration: discordIntegration,
    label: "Discord",
    provider: "Discord",
    targets: discordTargets,
    tone: "purple",
    unavailableReason: "Discord is configured, but no channels are available yet.",
    wildcardId: "discord:*",
  });

  const whatsappTargets = concreteNotificationEndpoints(whatsappIntegration?.endpoints ?? []);
  const whatsappMethods = providerNotificationMethods({
    actionType: "whatsapp",
    detail: (count) => count ? `${count} Admin target${count === 1 ? "" : "s"} available` : "No Admin users with mobile numbers",
    id: "whatsapp",
    icon: MessageCircle,
    integration: whatsappIntegration,
    label: "WhatsApp",
    provider: "WhatsApp",
    targets: whatsappTargets,
    tone: "green",
    unavailableReason: "WhatsApp is configured, but no active Admin users have mobile phone numbers.",
    wildcardId: "whatsapp:*",
  });

  return {
    discord: discordMethods.sort(sortNotificationMethods),
    in_app: inAppMethods.sort(sortNotificationMethods),
    mobile: mobileMethods.sort(sortNotificationMethods),
    whatsapp: whatsappMethods.sort(sortNotificationMethods),
    voice: voiceMethods.sort(sortNotificationMethods),
  };
}

type ProviderNotificationMethodConfig = Pick<NotificationActionMethod, "actionType" | "id" | "icon" | "label" | "provider" | "targets" | "tone"> & { detail: (targetCount: number) => string; integration?: NotificationIntegration; unavailableReason: string; wildcardId?: string };

function providerNotificationMethods({ detail, integration, targets, unavailableReason, wildcardId, ...method }: ProviderNotificationMethodConfig): NotificationActionMethod[] {
  const wildcardConfigured = wildcardId ? integration?.endpoints.some((endpoint) => endpoint.id === wildcardId) : false;
  if (!wildcardConfigured && !targets.length && !integration?.configured) return [];
  return [{
    ...method,
    detail: detail(targets.length),
    targets,
    targetMode: "selected",
    requiresTarget: true,
    defaultTargetIds: targets[0]?.id ? [targets[0].id] : [],
    unavailableReason: targets.length ? undefined : unavailableReason,
  }];
}

function sortNotificationMethods(a: NotificationActionMethod, b: NotificationActionMethod) {
  return `${a.label} ${a.detail}`.localeCompare(`${b.label} ${b.detail}`);
}

export function concreteNotificationEndpoints(endpoints: NotificationEndpoint[]) {
  return endpoints.filter((endpoint) => !endpoint.id.endsWith(":*"));
}

export function findCurrentUserPerson(people: Person[], currentUser: UserAccount): Person | null {
  const eligible = people.filter((person) => person.is_active && person.home_assistant_mobile_app_notify_service);
  const userFirstLast = normalizeIdentityName(`${currentUser.first_name} ${currentUser.last_name}`);
  if (userFirstLast) {
    const primary = eligible.filter((person) => normalizeIdentityName(`${person.first_name} ${person.last_name}`) === userFirstLast);
    if (primary.length === 1) return primary[0];
    if (primary.length > 1) return null;
  }

  const userDisplay = normalizeIdentityName(currentUser.full_name || displayUserName(currentUser));
  if (!userDisplay) return null;
  const exactNameMatches = eligible.filter((person) => normalizeIdentityName(person.display_name) === userDisplay);
  return exactNameMatches.length === 1 ? exactNameMatches[0] : null;
}

export function notificationActionTargetChips(action: NotificationAction, integration?: NotificationIntegration) {
  if (action.target_mode === "all") {
    const aggregate = integration?.endpoints.find((endpoint) => endpoint.id.endsWith(":*")) ?? integration?.endpoints[0];
    return [
      {
        id: `${action.id}:all`,
        provider: aggregate?.provider ?? notificationChannelMeta[action.type].label,
        label: aggregate?.label ?? (action.type === "in_app" ? "All signed-in dashboards" : "All configured endpoints"),
        unavailable: !integration?.configured && action.type !== "in_app",
      },
    ];
  }

  if (!action.target_ids.length) {
    return [
      {
        id: `${action.id}:none`,
        provider: notificationChannelMeta[action.type].label,
        label: "No targets selected",
        unavailable: true,
      },
    ];
  }

  return action.target_ids.map((targetId) => {
    const endpoint = integration?.endpoints.find((item) => item.id === targetId);
    if (endpoint) {
      return { id: targetId, provider: endpoint.provider, label: endpoint.label, unavailable: false };
    }
    if (targetId.startsWith("whatsapp:number:")) {
      const value = targetId.replace(/^whatsapp:number:/, "");
      return { id: targetId, provider: "WhatsApp", label: value || "Dynamic phone number", unavailable: false };
    }
    return {
      id: targetId,
      provider: providerLabelForNotificationTarget(targetId, integration),
      label: unavailableNotificationTargetLabel(targetId),
      unavailable: true,
    };
  });
}

function providerLabelForNotificationTarget(targetId: string, integration?: NotificationIntegration) {
  if (targetId.startsWith("apprise:")) return "Apprise";
  if (targetId.startsWith("discord:")) return "Discord";
  if (targetId.startsWith("whatsapp:")) return "WhatsApp";
  if (targetId.startsWith("home_assistant_mobile:") || targetId.startsWith("home_assistant_tts:")) return "Home Assistant";
  if (targetId === "dashboard") return "Dashboard";
  return integration?.provider ?? "Target";
}

function unavailableNotificationTargetLabel(targetId: string) {
  const raw = targetId.includes(":") ? targetId.split(":").slice(1).join(":") : targetId;
  return `${raw || "Unknown target"} unavailable`;
}

export function createWorkflowDraft(): NotificationRule {
  return {
    id: draftId("workflow"),
    name: "New Notification",
    trigger_event: "",
    conditions: [],
    actions: [],
    is_active: true
  };
}

export function createWorkflowAction(
  type: NotificationActionType,
  overrides: Partial<Pick<NotificationAction, "target_mode" | "target_ids">> = {}
): NotificationAction {
  const templates = defaultWorkflowActionTemplates[type];
  return {
    id: draftId("action"),
    type,
    target_mode: overrides.target_mode ?? "all",
    target_ids: overrides.target_ids ?? [],
    title_template: templates.title_template,
    message_template: templates.message_template,
    gate_malfunction_stages: [],
    media: { attach_camera_snapshot: false, camera_id: "" },
    actionable: { enabled: false, action: "" }
  };
}

export function cloneNotificationRule(rule: NotificationRule): NotificationRule {
  return normalizeNotificationRule(JSON.parse(JSON.stringify(rule)) as Partial<NotificationRule>);
}

export function workflowRulePayload(rule: NotificationRule) {
  const normalized = normalizeNotificationRule(rule);
  return {
    name: normalized.name.trim() || "Notification Workflow",
    trigger_event: normalized.trigger_event,
    conditions: normalized.conditions,
    actions: normalized.actions,
    is_active: normalized.is_active
  };
}

function normalizeNotificationRule(rule: Partial<NotificationRule>): NotificationRule {
  const rawTrigger = stringifyTemplateValue(rule.trigger_event);
  const actions = Array.isArray(rule.actions) ? rule.actions.map(normalizeNotificationAction) : [];
  return {
    id: stringifyTemplateValue(rule.id) || draftId("workflow"),
    name: stringifyTemplateValue(rule.name) || "Notification Workflow",
    trigger_event: rawTrigger,
    conditions: Array.isArray(rule.conditions) ? rule.conditions.map(normalizeNotificationCondition) : [],
    actions,
    is_active: rule.is_active !== false,
    last_fired_at: rule.last_fired_at ?? null,
    created_at: rule.created_at,
    updated_at: rule.updated_at,
  };
}

function normalizeNotificationCondition(condition: Partial<NotificationCondition>): NotificationCondition {
  const rawType = stringifyTemplateValue(condition.type);
  const type: NotificationConditionType = rawType === "presence" ? "presence" : "schedule";
  return {
    id: stringifyTemplateValue(condition.id) || draftId("condition"),
    type,
    schedule_id: stringifyTemplateValue(condition.schedule_id),
    mode: normalizePresenceConditionMode(condition.mode),
    person_id: stringifyTemplateValue(condition.person_id),
  };
}

function normalizePresenceConditionMode(value: unknown): PresenceConditionMode {
  if (value === "no_one_home" || value === "person_home" || value === "someone_home") return value;
  return "someone_home";
}

export function normalizeGateMalfunctionStages(value: unknown): NotificationGateMalfunctionStage[] {
  if (!Array.isArray(value)) return [];
  const stages: NotificationGateMalfunctionStage[] = [];
  value.forEach((item) => {
    const stage = stringifyTemplateValue(item);
    if (isGateMalfunctionStage(stage) && !stages.includes(stage)) stages.push(stage);
  });
  return stages;
}

function isGateMalfunctionStage(value: string): value is NotificationGateMalfunctionStage {
  return value === "initial" || value === "30m" || value === "60m" || value === "2hrs" || value === "fubar" || value === "resolved";
}

function normalizeNotificationAction(action: Partial<NotificationAction>): NotificationAction {
  const rawType = stringifyTemplateValue(action.type);
  const type = isNotificationActionType(rawType) ? rawType : "in_app";
  const templates = defaultWorkflowActionTemplates[type];
  return {
    id: stringifyTemplateValue(action.id) || draftId("action"),
    type,
    target_mode: normalizeNotificationTargetMode(action.target_mode),
    target_ids: Array.isArray(action.target_ids) ? action.target_ids.map(stringifyTemplateValue).filter(Boolean) : [],
    title_template: stringifyTemplateValue(action.title_template) || templates.title_template,
    message_template: stringifyTemplateValue(action.message_template) || templates.message_template,
    gate_malfunction_stages: normalizeGateMalfunctionStages(action.gate_malfunction_stages),
    media: normalizeNotificationMedia(action.media),
    actionable: normalizeNotificationActionable(action.actionable),
  };
}

function isNotificationActionType(value: string): value is NotificationActionType {
  return value === "mobile" || value === "in_app" || value === "voice" || value === "discord" || value === "whatsapp";
}

function normalizeNotificationTargetMode(value: unknown): NotificationTargetMode {
  if (value === "many" || value === "selected" || value === "all") return value;
  return "all";
}

export function normalizeNotificationMedia(media: unknown): NotificationAction["media"] {
  const raw = media && typeof media === "object" ? media as Partial<NotificationAction["media"]> : {};
  return {
    attach_camera_snapshot: raw.attach_camera_snapshot === true,
    camera_id: stringifyTemplateValue(raw.camera_id),
  };
}

export function normalizeNotificationActionable(actionable: unknown): NotificationAction["actionable"] {
  const raw = actionable && typeof actionable === "object" ? actionable as Partial<NotificationAction["actionable"]> : {};
  const action = stringifyTemplateValue(raw.action);
  return {
    enabled: raw.enabled === true && action === "gate.open",
    action: action === "gate.open" ? action : "",
  };
}

export function notificationActionWithSupportedActionable(action: NotificationAction, options: NotificationActionableOption[]) {
  const normalized = normalizeNotificationAction(action);
  if (!normalized.actionable.enabled) return normalized;
  if (options.some((option) => option.value === normalized.actionable.action)) return normalized;
  return { ...normalized, actionable: { enabled: false, action: "" } };
}

export function notificationActionableLabel(action: string) {
  if (action === "gate.open") return "Open Gate";
  return titleCase(action || "Action");
}

export function notificationSeverityTone(value: string): BadgeTone {
  if (value === "critical") return "red";
  if (value === "warning") return "amber";
  if (value === "info") return "blue";
  return "gray";
}

export function renderWorkflowPreview(actions: NotificationAction[], context: Record<string, string>, triggerEvent = "", stageOptions: NotificationGateMalfunctionStageOption[] = []) {
  return actions.map(normalizeNotificationAction).map((action) => {
    const generated = triggerEvent === "gate_malfunction"
      ? gateMalfunctionPreviewContent(action.type, context, stageOptions)
      : null;
    const title = generated?.title ?? renderWorkflowTemplate(action.title_template, context);
    const message = generated?.message ?? renderWorkflowTemplate(action.message_template, context);
    return {
      ...action,
      title,
      message,
      phoneticsApplied: action.type === "voice" && hasVehicleTtsPhoneticMatch(message),
    };
  });
}

function gateMalfunctionPreviewContent(actionType: NotificationActionType, context: Record<string, string>, stageOptions: NotificationGateMalfunctionStageOption[] = []) {
  const stage = isGateMalfunctionStage(context.MalfunctionStage) ? context.MalfunctionStage : "initial";
  const stageLabel = stageOptions.find((item) => item.value === stage)?.label ?? titleCase(stage.replace("fubar", "FUBAR").replace("hrs", " hours").replace("m", " minutes"));
  const title = stage === "resolved" ? "Gate malfunction resolved" : `Gate Malfunction - ${stageLabel}`;
  const body = gateMalfunctionPlainPreviewBody(stage);
  return {
    title,
    message: actionType === "voice" ? `Attention. ${body}` : body,
  };
}

function gateMalfunctionPlainPreviewBody(stage: NotificationGateMalfunctionStage) {
  if (stage === "initial") return "The gate has malfunctioned and is stuck open. Alfred is trying to resolve it.";
  if (stage === "30m") return "The gate is still stuck open. Alfred is still working on it.";
  if (stage === "60m") return "The gate has been stuck open for about an hour. It is not looking good, but Alfred is still on the case.";
  if (stage === "2hrs") return "The gate has been stuck open for over two hours. Alfred has not been able to fix it yet.";
  if (stage === "fubar") return "The gate is still stuck open and Alfred has run out of automatic fixes. Please check the gate when you can.";
  return "The gate malfunction has been resolved and the gate is closed again.";
}

function hasVehicleTtsPhoneticMatch(message: string) {
  return vehicleTtsPhoneticPattern.test(message);
}
