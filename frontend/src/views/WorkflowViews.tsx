import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Camera,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Command,
  ClipboardPaste,
  Construction,
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  File as FileIcon,
  FileImage,
  FileText,
  Gauge,
  GitBranch,
  HardHat,
  Home,
  Key,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  Loader2,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  MoreHorizontal,
  Play,
  PlugZap,
  Plus,
  Paperclip,
  Pencil,
  RefreshCcw,
  RefreshCw,
  Search,
  Send,
  Smile,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Split,
  Sparkles,
  Sun,
  Terminal,
  Ticket,
  Trash2,
  Trophy,
  Type,
  Unlock,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X,
  Zap
} from "lucide-react";

import {
  api,
  Badge,
  BadgeTone,
  createActionConfirmation,
  displayUserName,
  formatDate,
  fromDateTimeLocal,
  NotificationChannelId,
  notificationChannelMeta,
  notificationEventLabel,
  NotificationTriggerOption,
  Person,
  Schedule,
  titleCase,
  toDateTimeLocal,
  Toolbar,
  TooltipPositionState,
  UnifiProtectCamera,
  UserAccount,
  Vehicle
} from "../shared";



export const VariableRichTextEditor = React.lazy(() => import("../VariableRichTextEditor"));

export type NotificationActionType = NotificationChannelId;

export type NotificationConditionType = "schedule" | "presence";

export type PresenceConditionMode = "no_one_home" | "someone_home" | "person_home";

export type NotificationTargetMode = "all" | "many" | "selected";

export type NotificationGateMalfunctionStage = "initial" | "30m" | "60m" | "2hrs" | "fubar" | "resolved";

export type NotificationEndpoint = {
  id: string;
  provider: string;
  label: string;
  detail: string;
};

export type NotificationIntegration = {
  id: NotificationChannelId;
  name: string;
  provider: string;
  configured: boolean;
  endpoints: NotificationEndpoint[];
};

export type NotificationCondition = {
  id: string;
  type: NotificationConditionType;
  schedule_id?: string;
  mode?: PresenceConditionMode;
  person_id?: string;
};

export type NotificationAction = {
  id: string;
  type: NotificationActionType;
  target_mode: NotificationTargetMode;
  target_ids: string[];
  title_template: string;
  message_template: string;
  gate_malfunction_stages: NotificationGateMalfunctionStage[];
  media: {
    attach_camera_snapshot: boolean;
    camera_id: string;
  };
  actionable: {
    enabled: boolean;
    action: string;
  };
};

export type NotificationRule = {
  id: string;
  name: string;
  trigger_event: string;
  conditions: NotificationCondition[];
  actions: NotificationAction[];
  is_active: boolean;
  last_fired_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type NotificationVariable = {
  name: string;
  token: string;
  label: string;
};

export type NotificationVariableGroup = {
  group: string;
  items: NotificationVariable[];
};

export type NotificationTriggerGroup = {
  id: string;
  label: string;
  events: NotificationTriggerOption[];
};

export type NotificationActionableOption = {
  value: string;
  label: string;
  description: string;
};

export type NotificationActionableGroup = {
  trigger_event: string;
  actions: NotificationActionableOption[];
};

export type NotificationGateMalfunctionStageOption = {
  value: NotificationGateMalfunctionStage;
  label: string;
};

export type NotificationCatalogResponse = {
  triggers: NotificationTriggerGroup[];
  variables: NotificationVariableGroup[];
  integrations: NotificationIntegration[];
  actionable_notifications?: NotificationActionableGroup[];
  gate_malfunction_stages?: NotificationGateMalfunctionStageOption[];
  mock_context: Record<string, string>;
};

export type NotificationStatusFilter = "all" | "active" | "inactive";

export type NotificationFilterCounts = Record<NotificationStatusFilter, number>;

export type NotificationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: NotificationRule[];
};

export type WorkflowRuleMenuState = {
  id: string;
  left: number;
  top: number;
};

export type WorkflowRuleStatusFeedback = {
  nonce: number;
  ruleId: string;
  status: "paused" | "resumed";
};

export type AutomationNode = {
  id: string;
  type: string;
  config: Record<string, unknown>;
};

export type AutomationAction = AutomationNode & {
  reason_template?: string;
};

export type AutomationRule = {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  triggers: AutomationNode[];
  trigger_keys: string[];
  conditions: AutomationNode[];
  actions: AutomationAction[];
  next_run_at?: string | null;
  last_fired_at?: string | null;
  run_count: number;
  last_run_status?: string | null;
  last_error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type AutomationCatalogItem = {
  type: string;
  label: string;
  description?: string;
  scopes?: string[];
  enabled?: boolean;
  disabled?: boolean;
  disabled_reason?: string | null;
  integration_action?: boolean;
  integration_provider?: string;
  integration_provider_label?: string;
  integration_action_key?: string;
  default_config?: Record<string, unknown>;
};

export type AutomationIntegrationCatalog = {
  id: string;
  label: string;
  description?: string;
  enabled?: boolean;
  disabled_reason?: string | null;
  actions: AutomationCatalogItem[];
};

export type AutomationCatalogGroup = {
  id: string;
  label: string;
  triggers?: AutomationCatalogItem[];
  conditions?: AutomationCatalogItem[];
  actions?: AutomationCatalogItem[];
  integrations?: AutomationIntegrationCatalog[];
};

export type AutomationVariable = NotificationVariable & {
  scope?: string;
  trigger_types?: string[];
};

export type AutomationVariableGroup = {
  group: string;
  scope?: string;
  items: AutomationVariable[];
};

export type AutomationCatalogResponse = {
  triggers: AutomationCatalogGroup[];
  conditions: AutomationCatalogGroup[];
  actions: AutomationCatalogGroup[];
  variables: AutomationVariableGroup[];
  notification_rules: Array<{ id: string; name: string; trigger_event: string }>;
  garage_doors: Array<{ entity_id: string; name: string; schedule_id?: string | null }>;
  mock_context: Record<string, string>;
};

export type AutomationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: AutomationRule[];
};

export type TwoPaneCategory = {
  id: string;
  label: string;
  count: number;
  icon?: React.ElementType;
  disabled?: boolean;
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

export const fallbackNotificationTriggers: NotificationTriggerGroup[] = [
  {
    id: "ai_agents",
    label: "AI Agents",
    events: [
      { value: "agent_anomaly_alert", label: "AI Anomaly Alert", severity: "critical", description: "The AI agent raises an explicit anomaly alert." }
    ]
  },
  {
    id: "compliance",
    label: "Compliance",
    events: [
      { value: "expired_mot_detected", label: "Expired MOT Detected", severity: "warning", description: "DVLA reports a vehicle MOT status other than Valid or Not Required on arrival." },
      { value: "expired_tax_detected", label: "Expired Tax Detected", severity: "warning", description: "DVLA reports a vehicle tax status other than Taxed or SORN on arrival." }
    ]
  },
  {
    id: "gate_actions",
    label: "Gate Actions",
    events: [
      { value: "garage_door_open_failed", label: "Garage Door Failed", severity: "critical", description: "A linked garage door command failed." },
      { value: "gate_open_failed", label: "Gate Open Failed", severity: "critical", description: "The access decision was granted but the gate command failed." }
    ]
  },
  {
    id: "gate_malfunctions",
    label: "Gate Malfunctions",
    events: [
      { value: "gate_malfunction", label: "Gate Malfunction", severity: "critical", description: "The gate malfunction lifecycle changed stage." }
    ]
  },
  {
    id: "integrations",
    label: "Integrations",
    events: [
      { value: "integration_degraded", label: "Integration Degraded", severity: "warning", description: "A configured integration moved into a degraded or unreachable state." }
    ]
  },
  {
    id: "leaderboard",
    label: "Leaderboard",
    events: [
      { value: "leaderboard_overtake", label: "Leaderboard Overtake", severity: "info", description: "A known vehicle takes the top spot on Top Charts." }
    ]
  },
  {
    id: "maintenance_mode",
    label: "Maintenance Mode",
    events: [
      { value: "maintenance_mode_disabled", label: "Maintenance Mode Disabled", severity: "info", description: "The global automation kill-switch was disabled." },
      { value: "maintenance_mode_enabled", label: "Maintenance Mode Enabled", severity: "warning", description: "The global automation kill-switch was enabled." }
    ]
  },
  {
    id: "vehicle_detections",
    label: "Vehicle Detections",
    events: [
      { value: "authorized_entry", label: "Authorised Vehicle Detected", severity: "info", description: "A known vehicle is granted entry inside its access policy." },
      { value: "duplicate_entry", label: "Duplicate Entry", severity: "warning", description: "A person already marked home is detected entering again." },
      { value: "duplicate_exit", label: "Duplicate Exit", severity: "info", description: "A person already marked away is detected exiting again." },
      { value: "outside_schedule", label: "Outside Schedule", severity: "warning", description: "A known vehicle is denied by schedule or access policy." },
      { value: "unauthorized_plate", label: "Unknown Vehicle Detected", severity: "warning", description: "An unknown or inactive vehicle plate is denied." },
      { value: "visitor_pass_vehicle_arrived", label: "Visitor Pass Vehicle Arrived", severity: "info", description: "A vehicle matched to a Visitor Pass has arrived on site." },
      { value: "visitor_pass_vehicle_exited", label: "Visitor Pass Vehicle Exited", severity: "info", description: "A vehicle matched to a Visitor Pass has left the site." }
    ]
  },
  {
    id: "visitor_pass",
    label: "Visitor Pass",
    events: [
      { value: "visitor_pass_arranged", label: "Visitor Pass Arranged", severity: "info", description: "A WhatsApp visitor completed their Visitor Pass setup." },
      { value: "visitor_pass_cancelled", label: "Visitor Pass Cancelled", severity: "info", description: "A scheduled or active Visitor Pass was cancelled." },
      { value: "visitor_pass_created", label: "Visitor Pass Created", severity: "info", description: "A new Visitor Pass was created." },
      { value: "visitor_pass_expired", label: "Visitor Pass Expired", severity: "warning", description: "A Visitor Pass window elapsed without being used." },
      { value: "visitor_pass_used", label: "Visitor Pass Used", severity: "info", description: "A Visitor Pass was matched to an arriving vehicle." }
    ]
  }
];

export const defaultGateMalfunctionStageOptions: NotificationGateMalfunctionStageOption[] = [
  { value: "initial", label: "Initial malfunction" },
  { value: "30m", label: "30 minutes stuck" },
  { value: "60m", label: "60 minutes stuck" },
  { value: "2hrs", label: "2 hours stuck" },
  { value: "fubar", label: "FUBAR" },
  { value: "resolved", label: "Resolved" },
];

export const canonicalGateMalfunctionTrigger: NotificationTriggerOption = {
  value: "gate_malfunction",
  label: "Gate Malfunction",
  severity: "critical",
  description: "The gate malfunction lifecycle changed stage.",
};

export const fallbackNotificationVariables: NotificationVariableGroup[] = [
  {
    group: "Person",
    items: [
      { name: "FirstName", token: "@FirstName", label: "First name" },
      { name: "LastName", token: "@LastName", label: "Last name" },
      { name: "GroupName", token: "@GroupName", label: "Group name" },
      { name: "ObjectPronoun", token: "@ObjectPronoun", label: "Object pronoun" },
      { name: "PossessiveDeterminer", token: "@PossessiveDeterminer", label: "Possessive determiner" }
    ]
  },
  {
    group: "Vehicle",
    items: [
      { name: "Registration", token: "@Registration", label: "Registration" },
      { name: "VehicleName", token: "@VehicleName", label: "Friendly vehicle name" },
      { name: "VehicleMake", token: "@VehicleMake", label: "Vehicle make" },
      { name: "VehicleType", token: "@VehicleType", label: "Vehicle type" },
      { name: "VehicleColor", token: "@VehicleColor", label: "Vehicle colour" },
      { name: "VehicleColour", token: "@VehicleColour", label: "Vehicle colour" },
      { name: "MotStatus", token: "@MotStatus", label: "MOT status" },
      { name: "MotExpiry", token: "@MotExpiry", label: "MOT expiry" },
      { name: "TaxStatus", token: "@TaxStatus", label: "Tax status" },
      { name: "TaxExpiry", token: "@TaxExpiry", label: "Tax expiry" }
    ]
  },
  {
    group: "Event",
    items: [
      { name: "Time", token: "@Time", label: "Event time" },
      { name: "GateStatus", token: "@GateStatus", label: "Gate status" },
      { name: "Message", token: "@Message", label: "Message" },
      { name: "MaintenanceModeReason", token: "@MaintenanceModeReason", label: "Maintenance mode reason" }
    ]
  },
  {
    group: "Integration",
    items: [
      { name: "IntegrationName", token: "@IntegrationName", label: "Integration name" },
      { name: "IntegrationStatus", token: "@IntegrationStatus", label: "Integration status" },
      { name: "IntegrationReason", token: "@IntegrationReason", label: "Degraded reason" },
      { name: "IntegrationLastConnectedAt", token: "@IntegrationLastConnectedAt", label: "Last connected at" },
      { name: "IntegrationLastFailureAt", token: "@IntegrationLastFailureAt", label: "Last failure at" }
    ]
  },
  {
    group: "Visitor Pass",
    items: [
      { name: "VisitorName", token: "@VisitorName", label: "Visitor name" },
      { name: "VisitorPassName", token: "@VisitorPassName", label: "Visitor Pass name" },
      { name: "VisitorPassRegistration", token: "@VisitorPassRegistration", label: "Visitor Pass registration" },
      { name: "VisitorPassTimeWindow", token: "@VisitorPassTimeWindow", label: "Visitor Pass time window" },
      { name: "VisitorPassVehicleRegistration", token: "@VisitorPassVehicleRegistration", label: "Visitor Pass vehicle registration" },
      { name: "VisitorPassVehicleMake", token: "@VisitorPassVehicleMake", label: "Visitor Pass vehicle make" },
      { name: "VisitorPassVehicleColour", token: "@VisitorPassVehicleColour", label: "Visitor Pass vehicle colour" },
      { name: "VisitorPassDurationOnSite", token: "@VisitorPassDurationOnSite", label: "Visitor Pass duration on site" },
      { name: "VisitorPassOriginalTime", token: "@VisitorPassOriginalTime", label: "Visitor Pass original time" },
      { name: "VisitorPassRequestedTime", token: "@VisitorPassRequestedTime", label: "Visitor Pass requested time" }
    ]
  },
  {
    group: "Leaderboard",
    items: [
      { name: "NewWinnerName", token: "@NewWinnerName", label: "New winner" },
      { name: "OvertakenName", token: "@OvertakenName", label: "Overtaken person" },
      { name: "ReadCount", token: "@ReadCount", label: "Read count" }
    ]
  },
  {
    group: "Malfunction",
    items: [
      { name: "MalfunctionDuration", token: "@MalfunctionDuration", label: "Malfunction duration" },
      { name: "MalfunctionOpenedTime", token: "@MalfunctionOpenedTime", label: "Gate opened time" },
      { name: "MalfunctionFixAttemptTime", token: "@MalfunctionFixAttemptTime", label: "Latest fix attempt time" },
      { name: "MalfunctionFixAttempts", token: "@MalfunctionFixAttempts", label: "Fix attempt count" },
      { name: "MalfunctionResolutionTime", token: "@MalfunctionResolutionTime", label: "Resolution time" },
      { name: "MalfunctionStage", token: "@MalfunctionStage", label: "Malfunction stage" },
      { name: "LastKnownVehicle", token: "@LastKnownVehicle", label: "Last known vehicle" }
    ]
  }
];

export const mockNotificationContext: Record<string, string> = {
  FirstName: "Steph",
  LastName: "Smith",
  DisplayName: "Steph Smith",
  GroupName: "Family",
  ObjectPronoun: "her",
  PossessiveDeterminer: "her",
  Registration: "STEPH26",
  VehicleRegistrationNumber: "STEPH26",
  VehicleName: "2026 Tesla Model Y Dual Motor Long Range",
  VehicleDisplayName: "2026 Tesla Model Y Dual Motor Long Range",
  VehicleMake: "Tesla",
  VehicleType: "Car",
  VehicleModel: "Model Y Dual Motor Long Range",
  VehicleColor: "Pearl white",
  VehicleColour: "Pearl white",
  MotStatus: "Valid",
  MotExpiry: "2026-10-14",
  TaxStatus: "Taxed",
  TaxExpiry: "2027-01-01",
  Time: "18:42",
  GateStatus: "opening",
  Direction: "entry",
  Decision: "granted",
  Source: "Driveway LPR",
  Severity: "Info",
  EventType: "Authorised Entry",
  Subject: "Steph arrived at the gate",
  Message: "Steph arrived in the 2026 Tesla Model Y Dual Motor Long Range.",
  MaintenanceModeReason: "Enabled by Jason from UI",
  IntegrationName: "Home Assistant",
  IntegrationStatus: "Degraded",
  IntegrationReason: "Unable to reach Home Assistant.",
  IntegrationLastConnectedAt: "2026-05-10T18:42:00+00:00",
  IntegrationLastFailureAt: "2026-05-10T18:55:35+00:00",
  VisitorName: "Sarah",
  VisitorPassName: "Sarah",
  VisitorPassRegistration: "PE70DHX",
  VisitorPassTimeWindow: "01 May 2026, 10:00 to 01 May 2026, 18:00",
  VisitorPassVehicleRegistration: "PE70DHX",
  VisitorPassVehicleMake: "Peugeot",
  VisitorPassVehicleColour: "Silver",
  VisitorPassDurationOnSite: "1h 25m",
  VisitorPassOriginalTime: "01 May 2026, 10:00 to 01 May 2026, 18:00",
  VisitorPassRequestedTime: "01 May 2026, 10:00 to 01 May 2026, 20:00",
  MalfunctionDuration: "5m 0s",
  MalfunctionOpenedTime: "2026-04-26T07:30:00+01:00",
  MalfunctionFixAttemptTime: "2026-04-26T07:35:00+01:00",
  MalfunctionFixAttempts: "1",
  MalfunctionResolutionTime: "",
  MalfunctionStage: "initial",
  LastKnownVehicle: "Steph Smith exited in 2026 Tesla Model Y",
  NewWinnerName: "Steph Smith",
  OvertakenName: "Jason Smith",
  ReadCount: "42"
};

export const defaultWorkflowActionTemplates: Record<NotificationActionType, Pick<NotificationAction, "title_template" | "message_template">> = {
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

export const vehicleTtsPhonetics: Record<string, string> = {
  BMW: "bee em double you",
  BYD: "bee why dee",
  GMC: "gee em see",
  MG: "em gee",
  VW: "vee double you",
  DS: "dee ess"
};

export const vehicleTtsPhoneticPattern = new RegExp(
  `\\b(${Object.keys(vehicleTtsPhonetics).sort((left, right) => right.length - left.length).join("|")})\\b`
);

export function AutomationsView({ people, refreshToken, vehicles }: { people: Person[]; refreshToken: number; vehicles: Vehicle[] }) {
  const [catalog, setCatalog] = React.useState<AutomationCatalogResponse | null>(null);
  const [rules, setRules] = React.useState<AutomationRule[]>([]);
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [draft, setDraft] = React.useState<AutomationRule | null>(null);
  const [modal, setModal] = React.useState<"trigger" | "condition" | "action" | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [statusFilter, setStatusFilter] = React.useState<NotificationStatusFilter>("all");
  const [togglingRuleIds, setTogglingRuleIds] = React.useState<Set<string>>(() => new Set());
  const [ruleStatusFeedback, setRuleStatusFeedback] = React.useState<WorkflowRuleStatusFeedback | null>(null);
  const [feedback, setFeedback] = React.useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [dryRun, setDryRun] = React.useState<Record<string, unknown> | null>(null);
  const [error, setError] = React.useState("");
  const prefersReducedMotion = useReducedMotion();
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const triggerByType = React.useMemo(() => new Map((catalog?.triggers ?? []).flatMap((group) => (group.triggers ?? []).map((item) => [item.type, item]))), [catalog]);
  const conditionByType = React.useMemo(() => new Map((catalog?.conditions ?? []).flatMap((group) => (group.conditions ?? []).map((item) => [item.type, item]))), [catalog]);
  const actionByType = React.useMemo(() => new Map((catalog?.actions ?? []).flatMap((group) => (group.actions ?? []).map((item) => [item.type, item]))), [catalog]);
  const activeTriggerType = draft?.triggers[0]?.type ?? "";
  const variables = React.useMemo(() => automationVariablesForTrigger(catalog?.variables ?? [], activeTriggerType), [catalog, activeTriggerType]);
  const previewContext = catalog?.mock_context ?? {};
  const renderedReasons = React.useMemo(() => (draft?.actions ?? []).map((action) => ({
    ...action,
    renderedReason: renderWorkflowTemplate(action.reason_template ?? "", previewContext)
  })), [draft, previewContext]);
  const filterCounts = React.useMemo<NotificationFilterCounts>(() => {
    return rules.reduce<NotificationFilterCounts>((counts, rule) => {
      counts.all += 1;
      if (rule.is_active) counts.active += 1;
      else counts.inactive += 1;
      return counts;
    }, { all: 0, active: 0, inactive: 0 });
  }, [rules]);
  const filteredRules = React.useMemo(() => {
    if (statusFilter === "active") return rules.filter((rule) => rule.is_active);
    if (statusFilter === "inactive") return rules.filter((rule) => !rule.is_active);
    return rules;
  }, [rules, statusFilter]);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextCatalog, nextRules, nextUsers] = await Promise.all([
        api.get<AutomationCatalogResponse>("/api/v1/automations/catalog"),
        api.get<AutomationRule[]>("/api/v1/automations/rules"),
        api.get<UserAccount[]>("/api/v1/users")
      ]);
      setCatalog(nextCatalog);
      setRules(nextRules);
      setUsers(nextUsers);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load automation rules.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
  }, [load, refreshToken]);

  React.useEffect(() => {
    if (!ruleStatusFeedback) return undefined;
    const timeout = window.setTimeout(() => {
      setRuleStatusFeedback((current) => current?.nonce === ruleStatusFeedback.nonce ? null : current);
    }, 3600);
    return () => window.clearTimeout(timeout);
  }, [ruleStatusFeedback]);

  const updateDraft = (updater: (rule: AutomationRule) => AutomationRule) => {
    setDraft((current) => updater(current ?? createAutomationDraft()));
    setDryRun(null);
  };

  const addAutomation = () => {
    setDraft(createAutomationDraft());
    setModal(null);
    setFeedback(null);
    setDryRun(null);
  };

  const save = async () => {
    if (!draft) return;
    if (!draft.triggers.length) {
      setFeedback({ tone: "error", text: "Add at least one trigger before saving." });
      return;
    }
    if (!draft.actions.length) {
      setFeedback({ tone: "error", text: "Add at least one action before saving." });
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const payload = automationRulePayload(draft);
      const isCreate = draft.id.startsWith("draft-");
      const confirmation = await createActionConfirmation(isCreate ? "automation_rule.create" : "automation_rule.update", payload, {
        target_entity: "AutomationRule",
        target_id: isCreate ? undefined : draft.id,
        target_label: payload.name,
        reason: isCreate ? "Create automation rule" : "Update automation rule"
      });
      const saved = draft.id.startsWith("draft-")
        ? await api.post<AutomationRule>("/api/v1/automations/rules", { ...payload, confirmation_token: confirmation.confirmation_token })
        : await api.patch<AutomationRule>(`/api/v1/automations/rules/${draft.id}`, { ...payload, confirmation_token: confirmation.confirmation_token });
      await load();
      setRules((current) => current.map((item) => item.id === saved.id ? saved : item));
      setDraft(null);
      setModal(null);
      setDryRun(null);
      setFeedback({ tone: "success", text: "Automation saved. It will run when its trigger fires." });
    } catch (saveError) {
      setFeedback({ tone: "error", text: saveError instanceof Error ? saveError.message : "Unable to save automation." });
    } finally {
      setSaving(false);
    }
  };

  const deleteRule = async (rule: AutomationRule) => {
    if (rule.id.startsWith("draft-")) {
      setDraft(null);
      return;
    }
    if (!window.confirm(`Delete ${rule.name}?`)) return;
    try {
      const payload = { rule_id: rule.id };
      const confirmation = await createActionConfirmation("automation_rule.delete", payload, {
        target_entity: "AutomationRule",
        target_id: rule.id,
        target_label: rule.name,
        reason: "Delete automation rule"
      });
      await api.delete(`/api/v1/automations/rules/${rule.id}`, {
        confirmation_token: confirmation.confirmation_token
      });
      setDraft(null);
      await load();
      setFeedback({ tone: "success", text: "Automation deleted." });
    } catch (deleteError) {
      setFeedback({ tone: "error", text: deleteError instanceof Error ? deleteError.message : "Unable to delete automation." });
    }
  };

  const toggleActive = async (rule: AutomationRule, isActive: boolean) => {
    if (rule.id.startsWith("draft-")) return;
    setFeedback(null);
    setTogglingRuleIds((current) => {
      const next = new Set(current);
      next.add(rule.id);
      return next;
    });
    try {
      const payload = { is_active: isActive };
      const confirmation = await createActionConfirmation("automation_rule.update", payload, {
        target_entity: "AutomationRule",
        target_id: rule.id,
        target_label: rule.name,
        reason: isActive ? "Resume automation rule" : "Pause automation rule"
      });
      const updated = await api.patch<AutomationRule>(`/api/v1/automations/rules/${rule.id}`, {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      setRules((current) => current.map((item) => item.id === updated.id ? updated : item));
      setDraft((current) => current?.id === updated.id ? updated : current);
      setRuleStatusFeedback({
        nonce: Date.now(),
        ruleId: updated.id,
        status: updated.is_active ? "resumed" : "paused",
      });
    } catch (toggleError) {
      setFeedback({ tone: "error", text: toggleError instanceof Error ? toggleError.message : "Unable to update automation." });
    } finally {
      setTogglingRuleIds((current) => {
        const next = new Set(current);
        next.delete(rule.id);
        return next;
      });
    }
  };

  const runDryRun = async () => {
    if (!draft) return;
    setFeedback({ tone: "info", text: "Running automation dry-run." });
    try {
      const result = await api.post<Record<string, unknown>>("/api/v1/automations/dry-run", automationRulePayload(draft));
      setDryRun(result);
      setFeedback({ tone: "success", text: "Dry-run complete. Actions were previewed only; no sync or device commands were executed." });
    } catch (dryRunError) {
      setFeedback({ tone: "error", text: dryRunError instanceof Error ? dryRunError.message : "Dry-run failed." });
    }
  };

  const parseAiSchedule = async (trigger: AutomationNode) => {
    const text = String(trigger.config.natural_text ?? "").trim();
    if (!text) {
      setFeedback({ tone: "error", text: "Enter a natural-language schedule first." });
      return;
    }
    setFeedback({ tone: "info", text: "Parsing schedule text." });
    try {
      const parsed = await api.post<Record<string, unknown>>("/api/v1/automations/parse-schedule", { text });
      updateDraft((rule) => ({
        ...rule,
        triggers: rule.triggers.map((item) => item.id === trigger.id ? {
          ...item,
          config: {
            ...item.config,
            cron_expression: parsed.cron_expression ?? "",
            timezone: parsed.timezone ?? "Europe/London",
            end_at: parsed.end_at ?? "",
            summary: parsed.summary ?? text
          }
        } : item)
      }));
      setFeedback({ tone: parsed.requires_review ? "error" : "success", text: parsed.requires_review ? "Schedule parsed but needs review." : "Schedule parsed." });
    } catch (parseError) {
      setFeedback({ tone: "error", text: parseError instanceof Error ? parseError.message : "Schedule parsing failed." });
    }
  };

  if (loading) {
    return (
      <section className="view-stack notifications-page workflow-notifications-page">
        <Toolbar title="Automations" count={0} icon={GitBranch} />
        <div className="loading-panel">Loading automation rules</div>
      </section>
    );
  }

  return (
    <section className="view-stack notifications-page workflow-notifications-page">
      <Toolbar title="Automations" count={rules.length} icon={GitBranch}>
        <button className="secondary-button" onClick={addAutomation} type="button">
          <Plus size={15} /> Add Automation
        </button>
      </Toolbar>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {feedback && !draft ? <div className={`notification-feedback ${feedback.tone}`}>{feedback.text}</div> : null}

      <WorkflowStatusFilters
        activeFilter={statusFilter}
        ariaLabel="Automation status filter"
        counts={filterCounts}
        onFilterChange={setStatusFilter}
      />

      <div className="workflow-notification-shell list-only">
        <AutomationWorkflowList
          activeId={draft?.id ?? ""}
          rules={filteredRules}
          ruleStatusFeedback={ruleStatusFeedback}
          statusFilter={statusFilter}
          totalRuleCount={rules.length}
          triggerGroups={catalog?.triggers ?? []}
          togglingRuleIds={togglingRuleIds}
          onDelete={deleteRule}
          onSelect={(rule) => {
            setDraft(cloneAutomationRule(rule));
            setDryRun(null);
            setFeedback(null);
          }}
          onToggleActive={toggleActive}
        />
      </div>

      {draft ? (
        <div className="modal-backdrop workflow-editor-backdrop" role="presentation">
          <div className={modal ? "modal-card workflow-editor-modal selector-mode" : "modal-card workflow-editor-modal"} role="dialog" aria-modal="true">
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.div
                animate={{ y: 0 }}
                className={modal ? "workflow-modal-panel selector" : "workflow-modal-panel editor"}
                exit={prefersReducedMotion ? undefined : { y: -6, transition: { duration: 0.06, ease: "easeOut" } }}
                initial={prefersReducedMotion ? false : { y: 8 }}
                key={modal ?? "editor"}
                transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.13, ease: [0.2, 0, 0, 1] }}
              >
                {modal ? (
                  <AutomationSelectionModal
                    groups={catalog?.[`${modal}s` as "triggers" | "conditions" | "actions"] ?? []}
                    kind={modal}
                    onClose={() => setModal(null)}
                    onSelect={(node) => {
                      updateDraft((rule) => ({
                        ...rule,
                        [modal === "action" ? "actions" : `${modal}s`]: [
                          ...(modal === "action" ? rule.actions : modal === "condition" ? rule.conditions : rule.triggers),
                          node
                        ]
                      } as AutomationRule));
                      setModal(null);
                    }}
                  />
                ) : (
                  <>
                    <div className="modal-header">
                      <div>
                        <h2>{draft.id.startsWith("draft-") ? "Add Automation" : "Edit Automation"}</h2>
                        <p>Build the Trigger, If, and Then flow for autonomous system actions.</p>
                      </div>
                      <button className="icon-button" onClick={() => { setDraft(null); setModal(null); }} type="button" aria-label="Close automation editor"><X size={16} /></button>
                    </div>
                    <div className="workflow-editor-modal-grid">
                      <div className="workflow-editor-column">
                        <section className="notification-editor-panel workflow-builder-panel">
                          <div className="notification-editor-header workflow-editor-header">
                            <div>
                              <span className="eyebrow">Name</span>
                              <input aria-label="Automation name" value={draft.name} onChange={(event) => updateDraft((rule) => ({ ...rule, name: event.target.value }))} />
                            </div>
                            <div className="notification-editor-actions">
                              <label className={draft.is_active ? "notification-switch active" : "notification-switch"}>
                                <input checked={draft.is_active} onChange={(event) => updateDraft((rule) => ({ ...rule, is_active: event.target.checked }))} type="checkbox" />
                                <span>{draft.is_active ? "Active" : "Paused"}</span>
                              </label>
                              <button className="icon-button danger" onClick={() => deleteRule(draft)} type="button" aria-label="Delete automation"><Trash2 size={15} /></button>
                            </div>
                          </div>
                          <label className="field compact-field">
                            <span>Description</span>
                            <input value={draft.description} onChange={(event) => updateDraft((rule) => ({ ...rule, description: event.target.value }))} placeholder="Optional operator note" />
                          </label>
                          <div className="workflow-vertical">
                            <WorkflowBlock badge="When" tone="blue" title="Trigger" required>
                              <AutomationNodeStack
                                actionMeta={actionByType}
                                conditionMeta={conditionByType}
                                garageDoors={catalog?.garage_doors ?? []}
                                kind="trigger"
                                nodes={draft.triggers}
                                people={people}
                                triggerMeta={triggerByType}
                                users={users}
                                vehicles={vehicles}
                                onAdd={() => setModal("trigger")}
                                onChange={(node) => updateDraft((rule) => ({ ...rule, triggers: rule.triggers.map((item) => item.id === node.id ? node : item) }))}
                                onParseAiSchedule={parseAiSchedule}
                                onRemove={(node) => updateDraft((rule) => ({ ...rule, triggers: rule.triggers.filter((item) => item.id !== node.id) }))}
                              />
                            </WorkflowBlock>
                            <WorkflowBlock badge="If" tone="amber" title="Conditions" optional>
                              <AutomationNodeStack
                                actionMeta={actionByType}
                                conditionMeta={conditionByType}
                                garageDoors={catalog?.garage_doors ?? []}
                                kind="condition"
                                nodes={draft.conditions}
                                people={people}
                                triggerMeta={triggerByType}
                                users={users}
                                vehicles={vehicles}
                                onAdd={() => setModal("condition")}
                                onChange={(node) => updateDraft((rule) => ({ ...rule, conditions: rule.conditions.map((item) => item.id === node.id ? node : item) }))}
                                onRemove={(node) => updateDraft((rule) => ({ ...rule, conditions: rule.conditions.filter((item) => item.id !== node.id) }))}
                              />
                            </WorkflowBlock>
                            <WorkflowBlock badge="Then" tone="green" title="Actions" required>
                              <AutomationNodeStack
                                actionMeta={actionByType}
                                conditionMeta={conditionByType}
                                garageDoors={catalog?.garage_doors ?? []}
                                kind="action"
                                nodes={draft.actions}
                                notificationRules={catalog?.notification_rules ?? []}
                                people={people}
                                triggerMeta={triggerByType}
                                users={users}
                                variables={variables}
                                vehicles={vehicles}
                                onAdd={() => setModal("action")}
                                onChange={(node) => updateDraft((rule) => ({ ...rule, actions: rule.actions.map((item) => item.id === node.id ? node as AutomationAction : item) }))}
                                onRemove={(node) => updateDraft((rule) => ({ ...rule, actions: rule.actions.filter((item) => item.id !== node.id) }))}
                              />
                            </WorkflowBlock>
                          </div>
                          <div className="modal-actions workflow-editor-footer">
                            {feedback ? <div className={`notification-feedback workflow-editor-feedback ${feedback.tone}`} role="status">{feedback.text}</div> : null}
                            <button className="secondary-button" onClick={runDryRun} type="button"><Play size={15} /> Dry Run</button>
                            <button className="secondary-button" onClick={() => setDraft(null)} type="button">Cancel</button>
                            <button className="primary-button" onClick={save} disabled={saving} type="button"><Save size={15} /> {saving ? "Saving..." : "Save"}</button>
                          </div>
                        </section>
                      </div>
                      <AutomationPreviewPanel actions={renderedReasons} dryRun={dryRun} />
                    </div>
                  </>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function AutomationWorkflowList({
  activeId,
  rules,
  ruleStatusFeedback,
  statusFilter,
  totalRuleCount,
  triggerGroups,
  togglingRuleIds,
  onDelete,
  onSelect,
  onToggleActive
}: {
  activeId: string;
  rules: AutomationRule[];
  ruleStatusFeedback: WorkflowRuleStatusFeedback | null;
  statusFilter: NotificationStatusFilter;
  totalRuleCount: number;
  triggerGroups: AutomationCatalogGroup[];
  togglingRuleIds: Set<string>;
  onDelete: (rule: AutomationRule) => void | Promise<void>;
  onSelect: (rule: AutomationRule) => void;
  onToggleActive: (rule: AutomationRule, isActive: boolean) => void | Promise<void>;
}) {
  const [openMenu, setOpenMenu] = React.useState<WorkflowRuleMenuState | null>(null);
  const [collapsedCategoryIds, setCollapsedCategoryIds] = React.useState<Set<string>>(() => new Set());
  const groupedRules = React.useMemo(() => groupAutomationRulesByTriggerCategory(rules, triggerGroups), [rules, triggerGroups]);

  React.useEffect(() => {
    if (!openMenu) return undefined;
    const closeOnPointerDown = (event: PointerEvent) => {
      if ((event.target as HTMLElement | null)?.closest("[data-workflow-rule-menu]")) return;
      setOpenMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenMenu(null);
    };
    const closeOnViewportChange = () => {
      setOpenMenu(null);
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [openMenu]);

  React.useEffect(() => {
    setCollapsedCategoryIds(new Set());
    setOpenMenu(null);
  }, [statusFilter]);

  const toggleCategory = (categoryId: string) => {
    setCollapsedCategoryIds((current) => {
      const next = new Set(current);
      if (next.has(categoryId)) next.delete(categoryId);
      else next.add(categoryId);
      return next;
    });
  };

  const toggleRuleMenu = (ruleId: string, button: HTMLButtonElement) => {
    setOpenMenu((current) => {
      if (current?.id === ruleId) return null;
      const rect = button.getBoundingClientRect();
      const menuWidth = 178;
      const menuHeight = 94;
      const gap = 7;
      const left = Math.max(12, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 12));
      const below = rect.bottom + gap;
      const top = below + menuHeight > window.innerHeight - 12
        ? Math.max(12, rect.top - menuHeight - gap)
        : below;
      return { id: ruleId, left, top };
    });
  };

  return (
    <aside className="workflow-rule-table notification-workflow-table automation-workflow-table card" aria-label="Automation rules">
      {rules.length ? (
        <div className="notification-category-stack">
          {groupedRules.map((category) => {
            const Icon = category.icon;
            const collapsed = collapsedCategoryIds.has(category.id);
            const tableId = `automation-category-${category.id}`;
            return (
              <section className="notification-category-folder" key={category.id}>
                <button
                  aria-controls={tableId}
                  aria-expanded={!collapsed}
                  className="notification-category-header"
                  onClick={() => toggleCategory(category.id)}
                  type="button"
                >
                  {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                  <Icon size={16} />
                  <span>
                    <strong>{category.label}</strong>
                  </span>
                  <Badge tone="gray">{category.rules.length}</Badge>
                </button>
                {!collapsed ? (
                  <div className="notification-rule-table-wrap" id={tableId}>
                    <table className="notification-rule-data-table">
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Configuration</th>
                          <th>Last Fired</th>
                          <th>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {category.rules.map((rule) => {
                          const active = activeId === rule.id;
                          const menuOpen = openMenu?.id === rule.id;
                          const statusFeedback = ruleStatusFeedback?.ruleId === rule.id ? ruleStatusFeedback : null;
                          const toggling = togglingRuleIds.has(rule.id);
                          return (
                            <tr className={[active ? "active" : "", rule.is_active ? "" : "paused"].filter(Boolean).join(" ")} key={rule.id}>
                              <td className="notification-rule-name-cell">
                                <button className="notification-rule-name-button" onClick={() => onSelect(rule)} type="button">
                                  <strong>{rule.name}</strong>
                                </button>
                              </td>
                              <td>
                                <span className="notification-config-chips" aria-label="Automation summary">
                                  <NotificationConfigChip count={rule.triggers.length} icon={Zap} label="Triggers" />
                                  <NotificationConfigChip count={rule.conditions.length} icon={Split} label="Conditions" />
                                  <NotificationConfigChip count={rule.actions.length} icon={Play} label="Actions" />
                                </span>
                              </td>
                              <td>
                                <span className="notification-last-fired">{formatCompactLastFired(rule.last_fired_at)}</span>
                              </td>
                              <td className="notification-rule-actions-cell">
                                <span className="notification-rule-actions-cluster">
                                  <span className="notification-rule-status-pill-slot">
                                    {statusFeedback ? (
                                      <span
                                        className={`notification-rule-status-pill ${statusFeedback.status}`}
                                        key={statusFeedback.nonce}
                                        role="status"
                                      >
                                        {statusFeedback.status === "paused" ? "Paused" : "Resumed"}
                                      </span>
                                    ) : null}
                                  </span>
                                  <label className={rule.is_active ? "workflow-rule-toggle active" : "workflow-rule-toggle"} aria-label={`${rule.is_active ? "Pause" : "Activate"} ${rule.name}`}>
                                    <input
                                      checked={rule.is_active}
                                      disabled={toggling}
                                      onChange={(event) => onToggleActive(rule, event.target.checked)}
                                      type="checkbox"
                                    />
                                    <span className="workflow-rule-toggle-track" aria-hidden="true">
                                      <span />
                                    </span>
                                  </label>
                                  <span className="workflow-rule-menu" data-workflow-rule-menu>
                                    <button
                                      aria-expanded={menuOpen}
                                      aria-haspopup="menu"
                                      aria-label={`Options for ${rule.name}`}
                                      className="icon-button workflow-rule-menu-button"
                                      onClick={(event) => toggleRuleMenu(rule.id, event.currentTarget)}
                                      type="button"
                                    >
                                      <MoreHorizontal size={16} />
                                    </button>
                                  </span>
                                </span>
                                {menuOpen ? (
                                  <AutomationRuleMenu
                                    left={openMenu.left}
                                    rule={rule}
                                    top={openMenu.top}
                                    onClose={() => setOpenMenu(null)}
                                    onDelete={onDelete}
                                    onSelect={onSelect}
                                  />
                                ) : null}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : (
        <AutomationWorkflowEmptyState statusFilter={statusFilter} totalRuleCount={totalRuleCount} />
      )}
    </aside>
  );
}

export function AutomationRuleMenu({
  left,
  rule,
  top,
  onClose,
  onDelete,
  onSelect
}: {
  left: number;
  rule: AutomationRule;
  top: number;
  onClose: () => void;
  onDelete: (rule: AutomationRule) => void | Promise<void>;
  onSelect: (rule: AutomationRule) => void;
}) {
  return createPortal(
    <div
      className="workflow-rule-menu-popover notification-rule-menu-popover-fixed"
      data-workflow-rule-menu
      role="menu"
      style={{ left, top }}
    >
      <button
        onClick={() => {
          onClose();
          onSelect(rule);
        }}
        role="menuitem"
        type="button"
      >
        <Pencil size={14} /> Edit
      </button>
      <button
        className="danger"
        onClick={() => {
          onClose();
          onDelete(rule);
        }}
        role="menuitem"
        type="button"
      >
        <Trash2 size={14} /> Delete
      </button>
    </div>,
    document.body
  );
}

export function AutomationWorkflowEmptyState({
  statusFilter,
  totalRuleCount
}: {
  statusFilter: NotificationStatusFilter;
  totalRuleCount: number;
}) {
  const emptyTitle = totalRuleCount === 0
    ? "No automation rules"
    : statusFilter === "active"
      ? "No active automation rules"
      : "No paused automation rules";
  const emptyDetail = totalRuleCount === 0
    ? "Use Add Automation to create the first Trigger / If / Then rule."
    : statusFilter === "active"
      ? "Active automation rules will appear here as soon as they are switched on."
      : "Paused automation rules will appear here as soon as they are switched off.";
  return (
    <div className="notification-empty-list workflow-empty-list">
      <GitBranch size={20} />
      <strong>{emptyTitle}</strong>
      <span>{emptyDetail}</span>
    </div>
  );
}

export function AutomationNodeStack({
  actionMeta,
  conditionMeta,
  garageDoors,
  kind,
  nodes,
  notificationRules = [],
  people,
  triggerMeta,
  users,
  variables = [],
  vehicles,
  onAdd,
  onChange,
  onParseAiSchedule,
  onRemove
}: {
  actionMeta: Map<string, AutomationCatalogItem>;
  conditionMeta: Map<string, AutomationCatalogItem>;
  garageDoors: Array<{ entity_id: string; name: string }>;
  kind: "trigger" | "condition" | "action";
  nodes: Array<AutomationNode | AutomationAction>;
  notificationRules?: Array<{ id: string; name: string }>;
  people: Person[];
  triggerMeta: Map<string, AutomationCatalogItem>;
  users: UserAccount[];
  variables?: Array<AutomationVariable & { group: string }>;
  vehicles: Vehicle[];
  onAdd: () => void;
  onChange: (node: AutomationNode | AutomationAction) => void;
  onParseAiSchedule?: (node: AutomationNode) => void;
  onRemove: (node: AutomationNode | AutomationAction) => void;
}) {
  const metaMap = kind === "trigger" ? triggerMeta : kind === "condition" ? conditionMeta : actionMeta;
  return (
    <div className="workflow-stack">
      {nodes.map((node) => (
        <AutomationNodeCard
          garageDoors={garageDoors}
          key={node.id}
          kind={kind}
          meta={metaMap.get(node.type)}
          node={node}
          notificationRules={notificationRules}
          people={people}
          variables={variables}
          users={users}
          vehicles={vehicles}
          onChange={onChange}
          onParseAiSchedule={onParseAiSchedule}
          onRemove={() => onRemove(node)}
        />
      ))}
      <button className="workflow-add-block" onClick={onAdd} type="button">
        <Plus size={15} /> Add {titleCase(kind)}
      </button>
    </div>
  );
}

export function AutomationNodeCard({
  garageDoors,
  kind,
  meta,
  node,
  notificationRules,
  people,
  users,
  variables,
  vehicles,
  onChange,
  onParseAiSchedule,
  onRemove
}: {
  garageDoors: Array<{ entity_id: string; name: string }>;
  kind: "trigger" | "condition" | "action";
  meta?: AutomationCatalogItem;
  node: AutomationNode | AutomationAction;
  notificationRules: Array<{ id: string; name: string }>;
  people: Person[];
  users: UserAccount[];
  variables: Array<AutomationVariable & { group: string }>;
  vehicles: Vehicle[];
  onChange: (node: AutomationNode | AutomationAction) => void;
  onParseAiSchedule?: (node: AutomationNode) => void;
  onRemove: () => void;
}) {
  const Icon = automationNodeIcon(node.type);
  const updateConfig = (config: Record<string, unknown>) => onChange({ ...node, config: { ...node.config, ...config } });
  const activeWhatsappAdmins = users.filter((user) => user.is_active && user.role === "admin" && user.mobile_phone_number);
  const whatsappTargetMode = String(node.config.target_mode ?? "selected");
  const whatsappSelectedUserIds = Array.isArray(node.config.target_user_ids) ? node.config.target_user_ids.map(String) : [];
  return (
    <article className="workflow-action-card automation-node-card">
      <div className="workflow-card-title">
        <Icon size={16} />
        <span>
          <strong>{meta?.label ?? titleCase(node.type)}</strong>
          <small>{meta?.description ?? node.type}</small>
        </span>
        <button className="icon-button danger" onClick={onRemove} type="button" aria-label={`Remove ${kind}`}><Trash2 size={14} /></button>
      </div>

      {node.type.includes("person.") || node.type.includes("vehicle.") || node.type === "vehicle.known_plate" || node.type === "vehicle.outside_schedule" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field">
            <span>Person</span>
            <select value={String(node.config.person_id ?? "")} onChange={(event) => updateConfig({ person_id: event.target.value })}>
              <option value="">From trigger context</option>
              {people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}
            </select>
          </label>
          <label className="field compact-field">
            <span>Vehicle</span>
            <select value={String(node.config.vehicle_id ?? "")} onChange={(event) => updateConfig({ vehicle_id: event.target.value })}>
              <option value="">From trigger context</option>
              {vehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.registration_number}</option>)}
            </select>
          </label>
        </div>
      ) : null}

      {node.type === "vehicle.unknown_plate" || node.type === "vehicle.known_plate" ? (
        <label className="field compact-field">
          <span>Registration filter</span>
          <input value={String(node.config.registration_number ?? "")} onChange={(event) => updateConfig({ registration_number: event.target.value })} placeholder="Optional plate" />
        </label>
      ) : null}

      {node.type === "time.specific_datetime" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field"><span>Run at</span><input type="datetime-local" value={toDateTimeLocal(String(node.config.run_at ?? ""))} onChange={(event) => updateConfig({ run_at: fromDateTimeLocal(event.target.value) })} /></label>
          <label className="field compact-field"><span>Recurrence</span><select value={String(node.config.recurrence ?? "none")} onChange={(event) => updateConfig({ recurrence: event.target.value, single_use: event.target.value === "none" })}><option value="none">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
          <label className="field compact-field"><span>End date</span><input type="datetime-local" value={toDateTimeLocal(String(node.config.end_at ?? ""))} onChange={(event) => updateConfig({ end_at: fromDateTimeLocal(event.target.value) })} /></label>
        </div>
      ) : null}

      {node.type === "time.every_x" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field"><span>Every</span><input min={1} type="number" value={Number(node.config.interval ?? 5)} onChange={(event) => updateConfig({ interval: Number(event.target.value) })} /></label>
          <label className="field compact-field"><span>Unit</span><select value={String(node.config.unit ?? "minutes")} onChange={(event) => updateConfig({ unit: event.target.value })}><option value="minutes">Minutes</option><option value="hours">Hours</option><option value="days">Days</option></select></label>
        </div>
      ) : null}

      {node.type === "time.cron" || node.type === "time.ai_text" ? (
        <div className="field-grid compact-field-grid">
          {node.type === "time.ai_text" ? <label className="field compact-field wide-field"><span>AI schedule text</span><input value={String(node.config.natural_text ?? "")} onChange={(event) => updateConfig({ natural_text: event.target.value })} placeholder="Every Thursday at 9pm until 4th June" /></label> : null}
          <label className="field compact-field"><span>Cron</span><input value={String(node.config.cron_expression ?? "")} onChange={(event) => updateConfig({ cron_expression: event.target.value })} placeholder="0 21 * * 4" /></label>
          <label className="field compact-field"><span>End date</span><input type="datetime-local" value={toDateTimeLocal(String(node.config.end_at ?? ""))} onChange={(event) => updateConfig({ end_at: fromDateTimeLocal(event.target.value) })} /></label>
          {node.type === "time.ai_text" ? <button className="secondary-button compact" onClick={() => onParseAiSchedule?.(node)} type="button"><Sparkles size={14} /> Parse</button> : null}
        </div>
      ) : null}

      {node.type === "ai.phrase_received" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field"><span>Phrase</span><input value={String(node.config.phrase ?? "")} onChange={(event) => updateConfig({ phrase: event.target.value })} /></label>
          <label className="field compact-field"><span>Match</span><select value={String(node.config.match_mode ?? "contains")} onChange={(event) => updateConfig({ match_mode: event.target.value })}><option value="contains">Contains</option><option value="exact">Exact</option></select></label>
        </div>
      ) : null}

      {node.type.startsWith("webhook.") ? (
        <label className="field compact-field">
          <span>Webhook key</span>
          <input value={String(node.config.webhook_key ?? "")} onChange={(event) => updateConfig({ webhook_key: event.target.value })} placeholder="Ungguessable endpoint key" />
        </label>
      ) : null}

      {node.type.startsWith("notification.") ? (
        <label className="field compact-field">
          <span>Notification rule</span>
          <select value={String(node.config.notification_rule_id ?? "")} onChange={(event) => updateConfig({ notification_rule_id: event.target.value })}>
            <option value="">Select notification</option>
            {notificationRules.map((rule) => <option key={rule.id} value={rule.id}>{rule.name}</option>)}
          </select>
        </label>
      ) : null}

      {node.type === "integration.whatsapp.send_message" ? (
        <div className="automation-integration-action-summary">
          <MessageCircle size={15} />
          <span>
            <strong>WhatsApp</strong>
            <small>Send text to Admin users or a dynamic phone number.</small>
          </span>
          <div className="field-grid compact-field-grid wide-field">
            <label className="field compact-field">
              <span>Target mode</span>
              <select
                value={whatsappTargetMode}
                onChange={(event) => updateConfig({
                  target_mode: event.target.value,
                  target_user_ids: event.target.value === "selected" ? whatsappSelectedUserIds : [],
                })}
              >
                <option value="selected">Selected Admins</option>
                <option value="all">All Admins</option>
                <option value="dynamic">Dynamic number</option>
              </select>
            </label>
          </div>
          {whatsappTargetMode === "selected" ? (
            <div className="workflow-target-chips">
              {activeWhatsappAdmins.length ? activeWhatsappAdmins.map((user) => {
                const selected = whatsappSelectedUserIds.includes(user.id);
                return (
                  <button
                    className={selected ? "workflow-target-chip selected" : "workflow-target-chip"}
                    key={user.id}
                    onClick={() => updateConfig({ target_user_ids: toggleStringList(node.config.target_user_ids, user.id) })}
                    type="button"
                  >
                    <strong>Admin</strong>{displayUserName(user) || user.username}
                  </button>
                );
              }) : <span className="workflow-target-chip unavailable"><strong>Admin</strong>No Admin mobile numbers</span>}
            </div>
          ) : null}
          {whatsappTargetMode === "dynamic" ? (
            <PlainTemplateEditor
              label="Phone number template"
              value={String(node.config.phone_number_template ?? "")}
              variables={variables}
              onChange={(phone_number_template) => updateConfig({ phone_number_template })}
            />
          ) : null}
          <PlainTemplateEditor
            label="Message template"
            multiline
            value={String(node.config.message_template ?? "@Subject")}
            variables={variables}
            onChange={(message_template) => updateConfig({ message_template })}
          />
        </div>
      ) : node.type.startsWith("integration.") ? (
        <div className="automation-integration-action-summary">
          <PlugZap size={15} />
          <span>
            <strong>{String(node.config.provider ?? "Integration").replace(/_/g, " ")}</strong>
            <small>{String(node.config.action ?? node.type).replace(/_/g, " ")}</small>
          </span>
        </div>
      ) : null}

      {node.type.startsWith("garage_door.") ? (
        <div className="workflow-target-chips">
          {garageDoors.map((door) => {
            const selected = Array.isArray(node.config.target_entity_ids) && (node.config.target_entity_ids as unknown[]).includes(door.entity_id);
            return (
              <button className={selected ? "workflow-target-chip selected" : "workflow-target-chip"} key={door.entity_id} onClick={() => updateConfig({ target_entity_ids: toggleStringList(node.config.target_entity_ids, door.entity_id) })} type="button">
                <strong>Garage</strong>{door.name}
              </button>
            );
          })}
        </div>
      ) : null}

      {kind === "action" ? (
        <PlainTemplateEditor
          label="Audit reason"
          multiline
          value={(node as AutomationAction).reason_template ?? ""}
          variables={variables}
          onChange={(reason_template) => onChange({ ...(node as AutomationAction), reason_template })}
        />
      ) : null}
    </article>
  );
}

export function AutomationSelectionModal({
  groups,
  kind,
  onClose,
  onSelect
}: {
  groups: AutomationCatalogGroup[];
  kind: "trigger" | "condition" | "action";
  onClose: () => void;
  onSelect: (node: AutomationNode | AutomationAction) => void;
}) {
  const [activeCategoryId, setActiveCategoryId] = React.useState(groups[0]?.id ?? "");
  const [activeIntegrationId, setActiveIntegrationId] = React.useState("");
  const [searchQuery, setSearchQuery] = React.useState("");
  const query = searchQuery.trim().toLowerCase();
  const itemKey = `${kind}s` as "triggers" | "conditions" | "actions";
  const visibleGroups = groups
    .map((group) => {
      const categoryMatches = matchesSearchText(group.label, query);
      const items = (group[itemKey] ?? []).filter((item) => {
        if (!query || categoryMatches) return true;
        return matchesSearchText(`${item.label} ${item.description ?? ""} ${item.type} ${item.integration_provider_label ?? ""}`, query);
      });
      const integrations = (group.integrations ?? [])
        .map((integration) => {
          const integrationMatches = categoryMatches || matchesSearchText(`${integration.label} ${integration.description ?? ""}`, query);
          const actions = integration.actions.filter((item) => {
            if (!query || integrationMatches) return true;
            return matchesSearchText(`${item.label} ${item.description ?? ""} ${item.type}`, query);
          });
          return { ...integration, actions };
        })
        .filter((integration) => integration.actions.length || matchesSearchText(`${integration.label} ${integration.description ?? ""}`, query));
      return { ...group, [itemKey]: items, integrations };
    })
    .filter((group) => (group[itemKey] ?? []).length || (group.integrations ?? []).length || matchesSearchText(group.label, query));
  React.useEffect(() => {
    if (!visibleGroups.some((group) => group.id === activeCategoryId)) {
      setActiveCategoryId(visibleGroups[0]?.id ?? "");
      setActiveIntegrationId("");
    }
  }, [activeCategoryId, visibleGroups]);
  const activeGroup = visibleGroups.find((group) => group.id === activeCategoryId) ?? visibleGroups[0];
  const activeIntegrations = activeGroup?.integrations ?? [];
  const selectedIntegration = activeIntegrations.find((integration) => integration.id === activeIntegrationId);
  const showIntegrationDrilldown = kind === "action" && activeGroup?.id === "integrations" && activeIntegrations.length > 0;
  return (
    <TwoPaneSelectionModal
      activeCategoryId={activeGroup?.id ?? ""}
      categories={visibleGroups.map((group) => ({
        id: group.id,
        label: group.label,
        count: group.id === "integrations" && group.integrations?.length ? group.integrations.length : (group[itemKey] ?? []).length,
        icon: automationCategoryIcon(group.id)
      }))}
      embedded
      onBack={onClose}
      onCategoryChange={(categoryId) => {
        setActiveCategoryId(categoryId);
        setActiveIntegrationId("");
      }}
      onClose={onClose}
      onSearchChange={setSearchQuery}
      searchPlaceholder={`Search ${kind}s`}
      searchQuery={searchQuery}
      subtitle={`Choose a ${kind} for this automation.`}
      title={`Add ${titleCase(kind)}`}
      wide
    >
      {showIntegrationDrilldown && !selectedIntegration ? (
        <div className="two-pane-card-grid automation-selector-grid">
          {activeIntegrations.map((integration) => {
            const Icon = automationCategoryIcon(integration.id);
            return (
              <button className="two-pane-item-card automation-selector-card" key={integration.id} onClick={() => setActiveIntegrationId(integration.id)} type="button">
                <Icon size={18} />
                <span>
                  <strong>{integration.label}</strong>
                  <small>{integration.description ?? `${integration.actions.length} available ${pluralize("action", integration.actions.length)}`}</small>
                </span>
              </button>
            );
          })}
        </div>
      ) : showIntegrationDrilldown && selectedIntegration ? (
        <div className="automation-drilldown-stack">
          <div className="automation-drilldown-head">
            <button className="secondary-button compact" onClick={() => setActiveIntegrationId("")} type="button">
              <ArrowLeft size={14} /> Integrations
            </button>
            <div>
              <strong>{selectedIntegration.label}</strong>
              <span>{selectedIntegration.description ?? "Choose an integration action."}</span>
            </div>
          </div>
          <div className="two-pane-card-grid automation-selector-grid">
            {selectedIntegration.actions.map((item) => {
              const Icon = automationNodeIcon(item.type);
              const disabled = item.disabled || item.enabled === false;
              const disabledReason = item.disabled_reason || "Integration action is unavailable.";
              return (
                <button
                  className="two-pane-item-card automation-selector-card"
                  disabled={disabled}
                  key={item.type}
                  onClick={() => onSelect(createAutomationNode(kind, item.type, item))}
                  title={disabled ? disabledReason : undefined}
                  type="button"
                >
                  <Icon size={18} />
                  <span><strong>{item.label}</strong><small>{disabled ? disabledReason : item.description ?? item.type}</small></span>
                </button>
              );
            })}
          </div>
        </div>
      ) : activeGroup ? (
        <div className="two-pane-card-grid automation-selector-grid">
          {(activeGroup[itemKey] ?? []).map((item) => {
            const Icon = automationNodeIcon(item.type);
            return (
              <button className="two-pane-item-card automation-selector-card" key={item.type} onClick={() => onSelect(createAutomationNode(kind, item.type, item))} type="button">
                <Icon size={18} />
                <span><strong>{item.label}</strong><small>{item.description ?? item.type}</small></span>
              </button>
            );
          })}
        </div>
      ) : <div className="two-pane-empty">No {kind}s match this search.</div>}
    </TwoPaneSelectionModal>
  );
}

export function AutomationPreviewPanel({
  actions,
  dryRun
}: {
  actions: Array<AutomationAction & { renderedReason: string }>;
  dryRun: Record<string, unknown> | null;
}) {
  const conditionResults = Array.isArray(dryRun?.condition_results) ? dryRun.condition_results as Array<Record<string, unknown>> : [];
  const actionPreviews = Array.isArray(dryRun?.action_previews) ? dryRun.action_previews as Array<Record<string, unknown>> : [];
  return (
    <aside className="notification-preview-panel" aria-label="Automation preview">
      <div className="notification-preview-rail-head">
        <div>
          <strong>Automation Preview</strong>
          <span>Dry runs validate context and conditions only; actions are not executed.</span>
        </div>
      </div>
      <div className="notification-preview-stack">
        {actions.length ? actions.map((action) => (
          <article className="notification-preview-card-inline" key={action.id}>
            <div><Play size={16} /><strong>{titleCase(action.type)}</strong><Badge tone="green">Then</Badge></div>
            <p>{action.renderedReason || action.reason_template || "Default audit reason will be used."}</p>
          </article>
        )) : <div className="notification-endpoint-empty">Add an action to preview automation output.</div>}
        {dryRun ? (
          <article className="notification-preview-card-inline">
            <div><CheckCircle2 size={16} /><strong>Dry Run</strong><Badge tone={dryRun.would_run ? "green" : "amber"}>{dryRun.would_run ? "Would Run" : "Skipped"}</Badge></div>
            <p>{stringifyTemplateValue(dryRun.message) || "Preview only. No automation actions were executed."}</p>
            <p>{conditionResults.length} condition result(s), {actionPreviews.length} action preview(s).</p>
          </article>
        ) : null}
        {actionPreviews.map((preview) => (
          <article className="notification-preview-card-inline" key={String(preview.id ?? preview.type)}>
            <div>
              <Play size={16} />
              <strong>{titleCase(String(preview.type ?? "Action"))}</strong>
              <Badge tone={preview.would_execute ? "blue" : "amber"}>{preview.would_execute ? "Preview Only" : "Skipped"}</Badge>
            </div>
            <p>
              {Array.isArray(preview.missing_variables) && preview.missing_variables.length
                ? `Missing ${preview.missing_variables.join(", ")}.`
                : stringifyTemplateValue(preview.rendered_reason) || "No action was executed during this dry-run."}
            </p>
          </article>
        ))}
      </div>
    </aside>
  );
}

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

export function defaultAutomationConfig(type: string, meta?: AutomationCatalogItem): Record<string, unknown> {
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

export function defaultAutomationReason(type: string) {
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
  const categoryByTrigger = new Map<string, { id: string; label: string; icon: React.ElementType; order: number }>();
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

  const fallbackCategory = {
    id: "other",
    label: "Other",
    icon: GitBranch,
    order: Number.MAX_SAFE_INTEGER,
  };
  const grouped = new Map<string, AutomationRuleCategory & { order: number }>();

  rules.forEach((rule) => {
    const triggerType = rule.triggers[0]?.type ?? rule.trigger_keys[0] ?? "";
    const category = categoryByTrigger.get(triggerType) ?? fallbackCategory;
    const current = grouped.get(category.id);
    if (current) {
      current.rules.push(rule);
    } else {
      grouped.set(category.id, {
        id: category.id,
        label: category.label,
        icon: category.icon,
        order: category.order,
        rules: [rule],
      });
    }
  });

  return Array.from(grouped.values())
    .sort((left, right) => left.order - right.order || left.label.localeCompare(right.label))
    .map(({ order: _order, ...category }) => category);
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

export function toggleStringList(value: unknown, item: string) {
  const current = Array.isArray(value) ? value.map(String) : [];
  return current.includes(item) ? current.filter((entry) => entry !== item) : [...current, item];
}

export function NotificationsView({ currentUser, people, refreshToken, schedules }: { currentUser: UserAccount; people: Person[]; refreshToken: number; schedules: Schedule[] }) {
  const [catalog, setCatalog] = React.useState<NotificationCatalogResponse | null>(null);
  const [rules, setRules] = React.useState<NotificationRule[]>([]);
  const [cameras, setCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [, setSelectedRuleId] = React.useState("");
  const [draft, setDraft] = React.useState<NotificationRule | null>(null);
  const [modal, setModal] = React.useState<"trigger" | "condition" | "action" | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const [statusFilter, setStatusFilter] = React.useState<NotificationStatusFilter>("all");
  const [togglingRuleIds, setTogglingRuleIds] = React.useState<Set<string>>(() => new Set());
  const [ruleStatusFeedback, setRuleStatusFeedback] = React.useState<WorkflowRuleStatusFeedback | null>(null);
  const [feedback, setFeedback] = React.useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [error, setError] = React.useState("");
  const prefersReducedMotion = useReducedMotion();
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const triggerGroups = notificationTriggerGroupsForDisplay(
    catalog?.triggers.length ? catalog.triggers : fallbackNotificationTriggers
  );
  const gateMalfunctionStageOptions = catalog?.gate_malfunction_stages?.length
    ? catalog.gate_malfunction_stages
    : defaultGateMalfunctionStageOptions;
  const variableGroups = catalog?.variables.length ? catalog.variables : fallbackNotificationVariables;
  const variables = React.useMemo(() => variableGroups.flatMap((group) => group.items.map((item) => ({ ...item, group: group.group }))), [variableGroups]);
  const triggerOptions = React.useMemo(() => triggerGroups.flatMap((group) => group.events), [triggerGroups]);
  const triggerByValue = React.useMemo(() => new Map(triggerOptions.map((trigger) => [trigger.value, trigger])), [triggerOptions]);
  const actionableOptionsByTrigger = React.useMemo(() => {
    return new Map((catalog?.actionable_notifications ?? []).map((group) => [group.trigger_event, group.actions]));
  }, [catalog?.actionable_notifications]);
  const activeDraft = draft;
  const workflowModalMode: "editor" | "trigger" | "action" = modal === "trigger" || modal === "action" ? modal : "editor";
  const previewContext = catalog?.mock_context && Object.keys(catalog.mock_context).length ? catalog.mock_context : mockNotificationContext;
  const previewActions = activeDraft
    ? renderWorkflowPreview(activeDraft.actions, previewContext, activeDraft.trigger_event)
    : [];
  const filterCounts = React.useMemo<NotificationFilterCounts>(() => {
    return rules.reduce<NotificationFilterCounts>((counts, rule) => {
      counts.all += 1;
      if (rule.is_active) counts.active += 1;
      else counts.inactive += 1;
      return counts;
    }, { all: 0, active: 0, inactive: 0 });
  }, [rules]);
  const filteredRules = React.useMemo(() => {
    if (statusFilter === "active") return rules.filter((rule) => rule.is_active);
    if (statusFilter === "inactive") return rules.filter((rule) => !rule.is_active);
    return rules;
  }, [rules, statusFilter]);

  const load = React.useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const [nextCatalog, nextRules, cameraResult] = await Promise.all([
        api.get<NotificationCatalogResponse>("/api/v1/notifications/catalog"),
        api.get<NotificationRule[]>("/api/v1/notifications/rules"),
        api.get<{ cameras: UnifiProtectCamera[] }>("/api/v1/integrations/unifi-protect/cameras").catch(() => ({ cameras: [] }))
      ]);
      setCatalog(nextCatalog);
      setRules(nextRules);
      setCameras(cameraResult.cameras);
      setSelectedRuleId((current) => current && nextRules.some((rule) => rule.id === current) ? current : "");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load notification workflows.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
  }, [load, refreshToken]);

  React.useEffect(() => {
    if (!ruleStatusFeedback) return undefined;
    const timeout = window.setTimeout(() => {
      setRuleStatusFeedback((current) => current?.nonce === ruleStatusFeedback.nonce ? null : current);
    }, 3600);
    return () => window.clearTimeout(timeout);
  }, [ruleStatusFeedback]);

  const selectRule = (rule: NotificationRule) => {
    setDraft(cloneNotificationRule(rule));
    setSelectedRuleId(rule.id);
    setModal(null);
    setFeedback(null);
  };

  const updateDraft = (updater: (rule: NotificationRule) => NotificationRule) => {
    setDraft((current) => updater(current ?? createWorkflowDraft()));
  };

  const addWorkflow = () => {
    const next = createWorkflowDraft();
    setDraft(next);
    setSelectedRuleId(next.id);
    setModal(null);
    setFeedback(null);
  };

  const deleteRule = async (rule: NotificationRule) => {
    if (rule.id.startsWith("draft-")) {
      setDraft(null);
      setSelectedRuleId("");
      setModal(null);
      return;
    }
    if (!window.confirm(`Delete ${rule.name}?`)) return;
    setFeedback(null);
    try {
      const payload = { rule_id: rule.id };
      const confirmation = await createActionConfirmation("notification_rule.delete", payload, {
        target_entity: "NotificationRule",
        target_id: rule.id,
        target_label: rule.name,
        reason: "Delete notification workflow"
      });
      await api.delete(`/api/v1/notifications/rules/${rule.id}`, {
        confirmation_token: confirmation.confirmation_token
      });
      await load();
      setDraft(null);
      setSelectedRuleId("");
      setModal(null);
      setFeedback({ tone: "success", text: "Notification workflow deleted." });
    } catch (deleteError) {
      setFeedback({ tone: "error", text: deleteError instanceof Error ? deleteError.message : "Unable to delete notification workflow." });
    }
  };

  const toggleRuleActive = async (rule: NotificationRule, isActive: boolean) => {
    if (rule.id.startsWith("draft-")) return;
    setFeedback(null);
    setTogglingRuleIds((current) => {
      const next = new Set(current);
      next.add(rule.id);
      return next;
    });
    try {
      const payload = { is_active: isActive };
      const confirmation = await createActionConfirmation("notification_rule.update", payload, {
        target_entity: "NotificationRule",
        target_id: rule.id,
        target_label: rule.name,
        reason: isActive ? "Resume notification workflow" : "Pause notification workflow"
      });
      const updated = await api.patch<NotificationRule>(`/api/v1/notifications/rules/${rule.id}`, {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      setRules((current) => current.map((item) => item.id === updated.id ? updated : item));
      setDraft((current) => current?.id === updated.id ? cloneNotificationRule(updated) : current);
      setRuleStatusFeedback({
        nonce: Date.now(),
        ruleId: updated.id,
        status: updated.is_active ? "resumed" : "paused",
      });
    } catch (toggleError) {
      setFeedback({ tone: "error", text: toggleError instanceof Error ? toggleError.message : "Unable to update notification workflow." });
    } finally {
      setTogglingRuleIds((current) => {
        const next = new Set(current);
        next.delete(rule.id);
        return next;
      });
    }
  };

  const duplicateRule = async (rule: NotificationRule) => {
    if (rule.id.startsWith("draft-")) return;
    setFeedback(null);
    const payload = workflowRulePayload({
      ...cloneNotificationRule(rule),
      id: draftId("workflow"),
      name: `${rule.name} Copy`,
      is_active: false,
    });
    try {
      const confirmation = await createActionConfirmation("notification_rule.create", payload, {
        target_entity: "NotificationRule",
        target_label: payload.name,
        reason: "Duplicate notification workflow"
      });
      const created = await api.post<NotificationRule>("/api/v1/notifications/rules", {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      await load();
      setDraft(cloneNotificationRule(created));
      setSelectedRuleId(created.id);
      setModal(null);
      setFeedback({ tone: "success", text: "Notification workflow duplicated and paused for review." });
    } catch (duplicateError) {
      setFeedback({ tone: "error", text: duplicateError instanceof Error ? duplicateError.message : "Unable to duplicate notification workflow." });
    }
  };

  const save = async () => {
    if (!activeDraft) return;
    if (!activeDraft.trigger_event) {
      setFeedback({ tone: "error", text: "Add a trigger before saving this workflow." });
      return;
    }
    if (!activeDraft.actions.length) {
      setFeedback({ tone: "error", text: "Add at least one action before saving this workflow." });
      return;
    }
    setSaving(true);
    setFeedback(null);
    const payload = workflowRulePayload(activeDraft);
    try {
      const isCreate = activeDraft.id.startsWith("draft-");
      const confirmation = await createActionConfirmation(isCreate ? "notification_rule.create" : "notification_rule.update", payload, {
        target_entity: "NotificationRule",
        target_id: isCreate ? undefined : activeDraft.id,
        target_label: payload.name,
        reason: isCreate ? "Create notification workflow" : "Update notification workflow"
      });
      const saved = activeDraft.id.startsWith("draft-")
        ? await api.post<NotificationRule>("/api/v1/notifications/rules", { ...payload, confirmation_token: confirmation.confirmation_token })
        : await api.patch<NotificationRule>(`/api/v1/notifications/rules/${activeDraft.id}`, { ...payload, confirmation_token: confirmation.confirmation_token });
      setDraft(null);
      setSelectedRuleId("");
      setModal(null);
      await load();
      setFeedback({ tone: "success", text: "Notification workflow saved." });
    } catch (saveError) {
      setFeedback({ tone: "error", text: saveError instanceof Error ? saveError.message : "Unable to save notification workflow." });
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async () => {
    if (!activeDraft) return;
    if (!activeDraft.trigger_event) {
      setFeedback({ tone: "error", text: "Add a trigger before sending a test." });
      return;
    }
    if (!activeDraft.actions.length) {
      setFeedback({ tone: "error", text: "Add at least one action before sending a test." });
      return;
    }
    setTesting(true);
    setFeedback({ tone: "info", text: "Sending workflow test through the configured providers." });
    try {
      const payload = { rule: workflowRulePayload(activeDraft) };
      const confirmation = await createActionConfirmation("notification_rule.test", payload, {
        target_entity: "NotificationRule",
        target_id: activeDraft.id.startsWith("draft-") ? undefined : activeDraft.id,
        target_label: activeDraft.name || "Draft notification workflow",
        reason: "Send notification workflow test"
      });
      await api.post("/api/v1/notifications/rules/test", {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      setFeedback({ tone: "success", text: "Workflow test accepted by the configured providers." });
    } catch (testError) {
      setFeedback({ tone: "error", text: testError instanceof Error ? testError.message : "Notification workflow test failed." });
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <section className="view-stack notifications-page workflow-notifications-page">
        <Toolbar title="Notifications" count={0} icon={Bell} />
        <div className="loading-panel">Loading notification workflows</div>
      </section>
    );
  }

  return (
    <section className="view-stack notifications-page workflow-notifications-page">
      <Toolbar title="Notifications" count={rules.length} icon={Bell}>
        <button className="secondary-button" onClick={addWorkflow} type="button">
          <Plus size={15} /> Add Notification
        </button>
      </Toolbar>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {feedback && !activeDraft ? <div className={`notification-feedback ${feedback.tone}`}>{feedback.text}</div> : null}

      <WorkflowStatusFilters
        activeFilter={statusFilter}
        ariaLabel="Notification status filter"
        counts={filterCounts}
        onFilterChange={setStatusFilter}
      />

      <div className="workflow-notification-shell list-only">
        <NotificationWorkflowList
          activeId={activeDraft?.id ?? ""}
          rules={filteredRules}
          statusFilter={statusFilter}
          totalRuleCount={rules.length}
          triggerGroups={triggerGroups}
          ruleStatusFeedback={ruleStatusFeedback}
          togglingRuleIds={togglingRuleIds}
          onDelete={deleteRule}
          onDuplicate={duplicateRule}
          onSelect={selectRule}
          onToggleActive={toggleRuleActive}
        />
      </div>

      {activeDraft ? (
        <div className="modal-backdrop workflow-editor-backdrop" role="presentation">
          <div
            className={workflowModalMode === "editor" ? "modal-card workflow-editor-modal" : "modal-card workflow-editor-modal selector-mode"}
            role="dialog"
            aria-modal="true"
            aria-labelledby={workflowModalMode === "editor" ? "workflow-editor-title" : "two-pane-selection-title"}
          >
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.div
                animate={{ y: 0 }}
                className={workflowModalMode === "editor" ? "workflow-modal-panel editor" : "workflow-modal-panel selector"}
                exit={prefersReducedMotion ? undefined : { y: -6, transition: { duration: 0.06, ease: "easeOut" } }}
                initial={prefersReducedMotion ? false : { y: 8 }}
                key={workflowModalMode}
                transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.13, ease: [0.2, 0, 0, 1] }}
              >
                {workflowModalMode === "trigger" ? (
                  <NotificationTriggerModal
                    embedded
                    groups={triggerGroups}
                    selected={activeDraft.trigger_event}
                    onClose={() => setModal(null)}
                    onSelect={(triggerEvent) => {
                      const supportedActionable = actionableOptionsByTrigger.get(triggerEvent) ?? [];
                      updateDraft((rule) => ({
                        ...rule,
                        trigger_event: triggerEvent,
                        name: rule.name === "New Notification" ? notificationEventLabel(triggerEvent, triggerByValue) : rule.name,
                        actions: rule.actions.map((action) => notificationActionWithSupportedActionable(action, supportedActionable)),
                      }));
                      setModal(null);
                    }}
                  />
                ) : workflowModalMode === "action" ? (
                  <NotificationActionModal
                    embedded
                    currentUser={currentUser}
                    integrations={catalog?.integrations ?? []}
                    people={people}
                    onClose={() => setModal(null)}
                    onSelect={(action) => {
                      updateDraft((rule) => ({ ...rule, actions: [...rule.actions, action] }));
                      setModal(null);
                    }}
                  />
                ) : (
                  <>
                    <div className="modal-header">
                      <div>
                        <h2 id="workflow-editor-title">{activeDraft.id.startsWith("draft-") ? "Add Notification" : "Edit Notification"}</h2>
                        <p>Build the trigger, conditions, and delivery actions for this workflow.</p>
                      </div>
                      <button className="icon-button" onClick={() => { setDraft(null); setSelectedRuleId(""); setModal(null); }} type="button" aria-label="Close notification editor">
                        <X size={16} />
                      </button>
                    </div>
                    <NotificationWorkflowEditor
                      cameras={cameras}
                      feedback={feedback}
                      integrations={catalog?.integrations ?? []}
                      people={people}
                      previewActions={previewActions}
                      rule={activeDraft}
                      saving={saving}
                      schedules={schedules}
                      testing={testing}
                      actionableOptions={actionableOptionsByTrigger.get(activeDraft.trigger_event) ?? []}
                      gateMalfunctionStageOptions={gateMalfunctionStageOptions}
                      trigger={triggerByValue.get(activeDraft.trigger_event)}
                      variables={variables}
                      onAddAction={() => setModal("action")}
                      onAddCondition={() => setModal("condition")}
                      onCancel={() => { setDraft(null); setSelectedRuleId(""); setModal(null); }}
                      onDelete={() => deleteRule(activeDraft)}
                      onSave={save}
                      onSendTest={sendTest}
                      onShowTrigger={() => setModal("trigger")}
                      onUpdate={updateDraft}
                    />
                  </>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      ) : null}

      {activeDraft && modal === "condition" ? (
        <NotificationConditionModal
          people={people}
          schedules={schedules}
          onClose={() => setModal(null)}
          onSelect={(condition) => {
            updateDraft((rule) => ({ ...rule, conditions: [...rule.conditions, condition] }));
            setModal(null);
          }}
        />
      ) : null}
    </section>
  );
}

export function NotificationWorkflowList({
  activeId,
  rules,
  ruleStatusFeedback,
  statusFilter,
  totalRuleCount,
  triggerGroups,
  onDelete,
  onDuplicate,
  onSelect,
  onToggleActive,
  togglingRuleIds
}: {
  activeId: string;
  rules: NotificationRule[];
  ruleStatusFeedback: WorkflowRuleStatusFeedback | null;
  statusFilter: NotificationStatusFilter;
  totalRuleCount: number;
  triggerGroups: NotificationTriggerGroup[];
  onDelete: (rule: NotificationRule) => void | Promise<void>;
  onDuplicate: (rule: NotificationRule) => void | Promise<void>;
  onSelect: (rule: NotificationRule) => void;
  onToggleActive: (rule: NotificationRule, isActive: boolean) => void | Promise<void>;
  togglingRuleIds: Set<string>;
}) {
  const [openMenu, setOpenMenu] = React.useState<WorkflowRuleMenuState | null>(null);
  const [collapsedCategoryIds, setCollapsedCategoryIds] = React.useState<Set<string>>(() => new Set());
  const groupedRules = React.useMemo(() => groupNotificationRulesByTriggerCategory(rules, triggerGroups), [rules, triggerGroups]);

  React.useEffect(() => {
    if (!openMenu) return undefined;
    const closeOnPointerDown = (event: PointerEvent) => {
      if ((event.target as HTMLElement | null)?.closest("[data-workflow-rule-menu]")) return;
      setOpenMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenMenu(null);
    };
    const closeOnViewportChange = () => {
      setOpenMenu(null);
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [openMenu]);

  React.useEffect(() => {
    setCollapsedCategoryIds(new Set());
    setOpenMenu(null);
  }, [statusFilter]);

  const toggleCategory = (categoryId: string) => {
    setCollapsedCategoryIds((current) => {
      const next = new Set(current);
      if (next.has(categoryId)) next.delete(categoryId);
      else next.add(categoryId);
      return next;
    });
  };

  const toggleRuleMenu = (ruleId: string, button: HTMLButtonElement) => {
    setOpenMenu((current) => {
      if (current?.id === ruleId) return null;
      const rect = button.getBoundingClientRect();
      const menuWidth = 178;
      const menuHeight = 136;
      const gap = 7;
      const left = Math.max(12, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 12));
      const below = rect.bottom + gap;
      const top = below + menuHeight > window.innerHeight - 12
        ? Math.max(12, rect.top - menuHeight - gap)
        : below;
      return { id: ruleId, left, top };
    });
  };

  return (
    <aside className="workflow-rule-table notification-workflow-table card" aria-label="Notification workflows">
      {rules.length ? (
        <div className="notification-category-stack">
          {groupedRules.map((category) => {
            const Icon = category.icon;
            const collapsed = collapsedCategoryIds.has(category.id);
            const tableId = `notification-category-${category.id}`;
            return (
              <section className="notification-category-folder" key={category.id}>
                <button
                  aria-controls={tableId}
                  aria-expanded={!collapsed}
                  className="notification-category-header"
                  onClick={() => toggleCategory(category.id)}
                  type="button"
                >
                  {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                  <Icon size={16} />
                  <span>
                    <strong>{category.label}</strong>
                  </span>
                  <Badge tone="gray">{category.rules.length}</Badge>
                </button>
                {!collapsed ? (
                  <div className="notification-rule-table-wrap" id={tableId}>
                    <table className="notification-rule-data-table">
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Configuration</th>
                          <th>Last Fired</th>
                          <th>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {category.rules.map((rule) => {
                          const active = activeId === rule.id;
                          const menuOpen = openMenu?.id === rule.id;
                          const statusFeedback = ruleStatusFeedback?.ruleId === rule.id ? ruleStatusFeedback : null;
                          const toggling = togglingRuleIds.has(rule.id);
                          return (
                            <tr className={[active ? "active" : "", rule.is_active ? "" : "paused"].filter(Boolean).join(" ")} key={rule.id}>
                              <td className="notification-rule-name-cell">
                                <button className="notification-rule-name-button" onClick={() => onSelect(rule)} type="button">
                                  <strong>{rule.name}</strong>
                                </button>
                              </td>
                              <td>
                                <span className="notification-config-chips" aria-label="Workflow summary">
                                  <NotificationConfigChip count={1} icon={Zap} label="Triggers" />
                                  <NotificationConfigChip count={rule.conditions.length} icon={Split} label="Conditions" />
                                  <NotificationConfigChip count={rule.actions.length} icon={Play} label="Actions" />
                                </span>
                              </td>
                              <td>
                                <span className="notification-last-fired">{formatCompactLastFired(rule.last_fired_at)}</span>
                              </td>
                              <td className="notification-rule-actions-cell">
                                <span className="notification-rule-actions-cluster">
                                  <span className="notification-rule-status-pill-slot">
                                    {statusFeedback ? (
                                      <span
                                        className={`notification-rule-status-pill ${statusFeedback.status}`}
                                        key={statusFeedback.nonce}
                                        role="status"
                                      >
                                        {statusFeedback.status === "paused" ? "Paused" : "Resumed"}
                                      </span>
                                    ) : null}
                                  </span>
                                  <label className={rule.is_active ? "workflow-rule-toggle active" : "workflow-rule-toggle"} aria-label={`${rule.is_active ? "Pause" : "Activate"} ${rule.name}`}>
                                    <input
                                      checked={rule.is_active}
                                      disabled={toggling}
                                      onChange={(event) => onToggleActive(rule, event.target.checked)}
                                      type="checkbox"
                                    />
                                    <span className="workflow-rule-toggle-track" aria-hidden="true">
                                      <span />
                                    </span>
                                  </label>
                                  <span className="workflow-rule-menu" data-workflow-rule-menu>
                                    <button
                                      aria-expanded={menuOpen}
                                      aria-haspopup="menu"
                                      aria-label={`Options for ${rule.name}`}
                                      className="icon-button workflow-rule-menu-button"
                                      onClick={(event) => toggleRuleMenu(rule.id, event.currentTarget)}
                                      type="button"
                                    >
                                      <MoreHorizontal size={16} />
                                    </button>
                                  </span>
                                </span>
                                {menuOpen ? (
                                  <NotificationRuleMenu
                                    left={openMenu.left}
                                    rule={rule}
                                    top={openMenu.top}
                                    onClose={() => setOpenMenu(null)}
                                    onDelete={onDelete}
                                    onDuplicate={onDuplicate}
                                    onSelect={onSelect}
                                  />
                                ) : null}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : (
        <NotificationWorkflowEmptyState statusFilter={statusFilter} totalRuleCount={totalRuleCount} />
      )}
    </aside>
  );
}

export function NotificationRuleMenu({
  left,
  rule,
  top,
  onClose,
  onDelete,
  onDuplicate,
  onSelect
}: {
  left: number;
  rule: NotificationRule;
  top: number;
  onClose: () => void;
  onDelete: (rule: NotificationRule) => void | Promise<void>;
  onDuplicate: (rule: NotificationRule) => void | Promise<void>;
  onSelect: (rule: NotificationRule) => void;
}) {
  return createPortal(
    <div
      className="workflow-rule-menu-popover notification-rule-menu-popover-fixed"
      data-workflow-rule-menu
      role="menu"
      style={{ left, top }}
    >
      <button
        onClick={() => {
          onClose();
          onSelect(rule);
        }}
        role="menuitem"
        type="button"
      >
        <Pencil size={14} /> Edit
      </button>
      <button
        onClick={() => {
          onClose();
          onDuplicate(rule);
        }}
        role="menuitem"
        type="button"
      >
        <Copy size={14} /> Duplicate
      </button>
      <button
        className="danger"
        onClick={() => {
          onClose();
          onDelete(rule);
        }}
        role="menuitem"
        type="button"
      >
        <Trash2 size={14} /> Delete
      </button>
    </div>,
    document.body
  );
}

export function WorkflowStatusFilters({
  activeFilter,
  ariaLabel,
  counts,
  onFilterChange
}: {
  activeFilter: NotificationStatusFilter;
  ariaLabel: string;
  counts: NotificationFilterCounts;
  onFilterChange: (filter: NotificationStatusFilter) => void;
}) {
  const options: Array<{ key: NotificationStatusFilter; label: string }> = [
    { key: "all", label: "All" },
    { key: "active", label: "Active" },
    { key: "inactive", label: "Inactive" },
  ];
  return (
    <div className="notification-status-tabs" role="tablist" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          aria-selected={activeFilter === option.key}
          className={activeFilter === option.key ? "active" : ""}
          key={option.key}
          onClick={() => onFilterChange(option.key)}
          role="tab"
          type="button"
        >
          <span>{option.label}</span>
          <Badge tone="gray">{counts[option.key]}</Badge>
        </button>
      ))}
    </div>
  );
}

export function NotificationConfigChip({ count, icon: Icon, label }: { count: number; icon: React.ElementType; label: string }) {
  const tooltipId = React.useId();
  const [tooltipPosition, setTooltipPosition] = React.useState<TooltipPositionState | null>(null);
  const itemName = label === "Triggers" ? "trigger" : label === "Conditions" ? "condition" : "action";
  const tooltip = `${count} ${pluralize(itemName, count)} configured`;

  React.useEffect(() => {
    if (!tooltipPosition) return undefined;
    const hideTooltip = () => setTooltipPosition(null);
    window.addEventListener("resize", hideTooltip);
    window.addEventListener("scroll", hideTooltip, true);
    return () => {
      window.removeEventListener("resize", hideTooltip);
      window.removeEventListener("scroll", hideTooltip, true);
    };
  }, [tooltipPosition]);

  const showTooltip = (target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    const tooltipWidth = 168;
    const tooltipHeight = 48;
    const gap = 8;
    const placement = rect.bottom + gap + tooltipHeight > window.innerHeight - 8 ? "top" : "bottom";
    const left = Math.max(12 + tooltipWidth / 2, Math.min(rect.left + rect.width / 2, window.innerWidth - tooltipWidth / 2 - 12));
    const top = placement === "bottom"
      ? rect.bottom + gap
      : Math.max(12, rect.top - tooltipHeight - gap);
    setTooltipPosition({ left, placement, top });
  };

  return (
    <span
      className="notification-config-chip"
      aria-describedby={tooltipPosition ? tooltipId : undefined}
      aria-label={`${label}: ${count}`}
      onBlur={() => setTooltipPosition(null)}
      onFocus={(event) => showTooltip(event.currentTarget)}
      onMouseEnter={(event) => showTooltip(event.currentTarget)}
      onMouseLeave={() => setTooltipPosition(null)}
      tabIndex={0}
    >
      <Icon size={13} />
      <span>{count}</span>
      {tooltipPosition ? createPortal(
        <span
          className={`notification-config-tooltip ${tooltipPosition.placement}`}
          id={tooltipId}
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          <strong>{label}</strong>
          <span>{tooltip}</span>
        </span>,
        document.body
      ) : null}
    </span>
  );
}

export function NotificationWorkflowEmptyState({
  statusFilter,
  totalRuleCount
}: {
  statusFilter: NotificationStatusFilter;
  totalRuleCount: number;
}) {
  const emptyTitle = totalRuleCount === 0
    ? "No notification workflows"
    : statusFilter === "active"
      ? "No active notification workflows"
      : "No inactive notification workflows";
  const emptyDetail = totalRuleCount === 0
    ? "Use Add Notification to create the first automation."
    : statusFilter === "active"
      ? "Active workflows will appear here as soon as they are switched on."
      : "Paused workflows will appear here as soon as they are switched off.";
  return (
    <div className="notification-empty-list workflow-empty-list">
      <Bell size={20} />
      <strong>{emptyTitle}</strong>
      <span>{emptyDetail}</span>
    </div>
  );
}

export function groupNotificationRulesByTriggerCategory(
  rules: NotificationRule[],
  triggerGroups: NotificationTriggerGroup[]
): NotificationRuleCategory[] {
  const categoryByTrigger = new Map<string, { id: string; label: string; icon: React.ElementType; order: number }>();
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

  const fallbackCategory = {
    id: "other",
    label: "Other",
    icon: Bell,
    order: Number.MAX_SAFE_INTEGER,
  };
  const groups = new Map<string, NotificationRuleCategory & { order: number }>();
  rules.forEach((rule) => {
    const category = categoryByTrigger.get(rule.trigger_event) ?? fallbackCategory;
    const existing = groups.get(category.id);
    if (existing) {
      existing.rules.push(rule);
      return;
    }
    groups.set(category.id, {
      id: category.id,
      label: category.label,
      icon: category.icon,
      order: category.order,
      rules: [rule],
    });
  });

  return Array.from(groups.values())
    .sort((left, right) => left.order - right.order || left.label.localeCompare(right.label))
    .map(({ order: _order, ...category }) => category);
}

export function NotificationWorkflowEditor({
  actionableOptions,
  cameras,
  feedback,
  gateMalfunctionStageOptions,
  integrations,
  people,
  previewActions,
  rule,
  saving,
  schedules,
  testing,
  trigger,
  variables,
  onAddAction,
  onAddCondition,
  onCancel,
  onDelete,
  onSave,
  onSendTest,
  onShowTrigger,
  onUpdate
}: {
  actionableOptions: NotificationActionableOption[];
  cameras: UnifiProtectCamera[];
  feedback: { tone: "success" | "error" | "info"; text: string } | null;
  gateMalfunctionStageOptions: NotificationGateMalfunctionStageOption[];
  integrations: NotificationIntegration[];
  people: Person[];
  previewActions: Array<NotificationAction & { title: string; message: string }>;
  rule: NotificationRule;
  saving: boolean;
  schedules: Schedule[];
  testing: boolean;
  trigger?: NotificationTriggerOption;
  variables: Array<NotificationVariable & { group: string }>;
  onAddAction: () => void;
  onAddCondition: () => void;
  onCancel: () => void;
  onDelete: () => void;
  onSave: () => void;
  onSendTest: () => void;
  onShowTrigger: () => void;
  onUpdate: (updater: (rule: NotificationRule) => NotificationRule) => void;
}) {
  const integrationById = React.useMemo(() => new Map(integrations.map((integration) => [integration.id, integration])), [integrations]);
  const isDraft = rule.id.startsWith("draft-");
  const isGateMalfunctionWorkflow = rule.trigger_event === "gate_malfunction";
  return (
    <div className="workflow-editor-modal-grid">
      <div className="workflow-editor-column">
        <section className="notification-editor-panel workflow-builder-panel">
          <div className="notification-editor-header workflow-editor-header">
            <div>
              <span className="eyebrow">Name</span>
              <input
                aria-label="Workflow name"
                value={rule.name}
                onChange={(event) => onUpdate((current) => ({ ...current, name: event.target.value }))}
              />
            </div>
            {!isDraft ? (
              <div className="notification-editor-actions">
                <label className={rule.is_active ? "notification-switch active" : "notification-switch"}>
                  <input checked={rule.is_active} onChange={(event) => onUpdate((current) => ({ ...current, is_active: event.target.checked }))} type="checkbox" />
                  <span>{rule.is_active ? "Active" : "Paused"}</span>
                </label>
                <button className="icon-button danger" onClick={onDelete} type="button" aria-label="Delete workflow">
                  <Trash2 size={15} />
                </button>
              </div>
            ) : null}
          </div>

          <div className="workflow-vertical">
            <WorkflowBlock badge="When" tone="blue" title="Trigger" required>
              {rule.trigger_event ? (
                <button className="workflow-selected-card" onClick={onShowTrigger} type="button">
                  <CircleDot size={18} />
                  <span>
                    <strong>{trigger?.label ?? notificationEventLabel(rule.trigger_event)}</strong>
                    <small>{trigger?.description ?? "Selected event trigger."}</small>
                  </span>
                  <Badge tone={notificationSeverityTone(trigger?.severity ?? "info")}>{titleCase(trigger?.severity ?? "info")}</Badge>
                </button>
              ) : (
                <button className="workflow-add-block" onClick={onShowTrigger} type="button">
                  <Plus size={15} /> Add Trigger
                </button>
              )}
            </WorkflowBlock>

            <WorkflowBlock badge="And If" tone="amber" title="Conditions" optional>
              <div className="workflow-stack">
                {rule.conditions.map((condition) => (
                  <NotificationConditionCard
                    condition={condition}
                    key={condition.id}
                    people={people}
                    schedules={schedules}
                    onChange={(nextCondition) => onUpdate((current) => ({ ...current, conditions: current.conditions.map((item) => item.id === condition.id ? nextCondition : item) }))}
                    onRemove={() => onUpdate((current) => ({ ...current, conditions: current.conditions.filter((item) => item.id !== condition.id) }))}
                  />
                ))}
                <button className="workflow-add-block" onClick={onAddCondition} type="button">
                  <Plus size={15} /> Add Condition
                </button>
              </div>
            </WorkflowBlock>

            <WorkflowBlock badge="Then" tone="green" title="Actions" required>
              <div className="workflow-stack">
                {rule.actions.map((action) => (
                  <NotificationActionCard
                    action={action}
                    actionableOptions={actionableOptions}
                    cameras={cameras}
                    integration={integrationById.get(action.type)}
                    isGateMalfunctionWorkflow={isGateMalfunctionWorkflow}
                    key={action.id}
                    stageOptions={gateMalfunctionStageOptions}
                    variables={variables}
                    onChange={(nextAction) => onUpdate((current) => ({ ...current, actions: current.actions.map((item) => item.id === action.id ? nextAction : item) }))}
                    onRemove={() => onUpdate((current) => ({ ...current, actions: current.actions.filter((item) => item.id !== action.id) }))}
                  />
                ))}
                <button className="workflow-add-block" onClick={onAddAction} type="button">
                  <Plus size={15} /> Add Action
                </button>
              </div>
            </WorkflowBlock>
          </div>

          <div className="modal-actions workflow-editor-footer">
            {feedback ? <div className={`notification-feedback workflow-editor-feedback ${feedback.tone}`} role="status">{feedback.text}</div> : null}
            <button className="secondary-button" onClick={onSendTest} disabled={testing} type="button">
              <Send size={15} /> {testing ? "Sending..." : "Send Test"}
            </button>
            <button className="secondary-button" onClick={onCancel} type="button">
              Cancel
            </button>
            <button className="primary-button" onClick={onSave} disabled={saving} type="button">
              <Save size={15} /> {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </section>
      </div>
      <NotificationLivePreviewPanel actions={previewActions} />
    </div>
  );
}

export function WorkflowBlock({
  badge,
  children,
  optional,
  required,
  title,
  tone
}: {
  badge: string;
  children: React.ReactNode;
  optional?: boolean;
  required?: boolean;
  title: string;
  tone: BadgeTone;
}) {
  return (
    <section className="workflow-block">
      <div className="workflow-block-head">
        <Badge tone={tone}>{badge}</Badge>
        <strong>{title}</strong>
        <span>{required ? "Required" : optional ? "Optional" : ""}</span>
      </div>
      {children}
    </section>
  );
}

export function NotificationConditionCard({
  condition,
  people,
  schedules,
  onChange,
  onRemove
}: {
  condition: NotificationCondition;
  people: Person[];
  schedules: Schedule[];
  onChange: (condition: NotificationCondition) => void;
  onRemove: () => void;
}) {
  return (
    <article className="workflow-condition-card">
      <div className="workflow-card-title">
        <Clock3 size={16} />
        <strong>{condition.type === "schedule" ? "Schedule" : "Presence"}</strong>
        <button className="icon-button danger" onClick={onRemove} type="button" aria-label="Remove condition">
          <Trash2 size={14} />
        </button>
      </div>
      {condition.type === "schedule" ? (
        <label className="field compact-field">
          <span>Schedule</span>
          <select value={condition.schedule_id ?? ""} onChange={(event) => onChange({ ...condition, schedule_id: event.target.value })}>
            <option value="">Select schedule</option>
            {schedules.map((schedule) => <option key={schedule.id} value={schedule.id}>{schedule.name}</option>)}
          </select>
        </label>
      ) : (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field">
            <span>Presence</span>
            <select value={condition.mode ?? "someone_home"} onChange={(event) => onChange({ ...condition, mode: event.target.value as PresenceConditionMode })}>
              <option value="no_one_home">No one is home</option>
              <option value="someone_home">Someone is home</option>
              <option value="person_home">Specific person is home</option>
            </select>
          </label>
          {condition.mode === "person_home" ? (
            <label className="field compact-field">
              <span>Person</span>
              <select value={condition.person_id ?? ""} onChange={(event) => onChange({ ...condition, person_id: event.target.value })}>
                <option value="">Select person</option>
                {people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}
              </select>
            </label>
          ) : null}
        </div>
      )}
    </article>
  );
}

export type TemplateEditorProps = {
  label: string;
  multiline?: boolean;
  value: string;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (value: string) => void;
};

export class TemplateEditorBoundary extends React.Component<
  { children: React.ReactNode; fallback: React.ReactNode; resetKey: string },
  { hasError: boolean; retryCount: number }
> {
  state = { hasError: false, retryCount: 0 };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidUpdate(previousProps: { resetKey: string }) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, retryCount: 0 });
    }
  }

  componentDidCatch(error: unknown) {
    console.error("Notification template editor failed to render", error);
    if (this.state.retryCount > 0) return;
    window.requestAnimationFrame(() => {
      this.setState((current) => current.hasError
        ? { hasError: false, retryCount: current.retryCount + 1 }
        : null);
    });
  }

  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

export function SafeVariableRichTextEditor(props: TemplateEditorProps) {
  const variableResetKey = React.useMemo(() => props.variables.map((variable) => variable.name).join("\u0000"), [props.variables]);
  const safeProps = {
    ...props,
    value: stringifyTemplateValue(props.value),
  };
  return (
    <TemplateEditorBoundary fallback={<PlainTemplateEditor {...safeProps} />} resetKey={variableResetKey}>
      <React.Suspense fallback={<div className="loading-panel compact">Loading template editor</div>}>
        <VariableRichTextEditor {...safeProps} />
      </React.Suspense>
    </TemplateEditorBoundary>
  );
}

export function PlainTemplateEditor({ label, multiline = false, value, onChange }: TemplateEditorProps) {
  return (
    <label className="field variable-editor-field">
      <span>{label}</span>
      {multiline ? (
        <textarea
          className="template-editor-fallback"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={4}
        />
      ) : (
        <input
          className="template-editor-fallback"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}

export function NotificationActionCard({
  action,
  actionableOptions,
  cameras,
  integration,
  isGateMalfunctionWorkflow,
  stageOptions,
  variables,
  onChange,
  onRemove
}: {
  action: NotificationAction;
  actionableOptions: NotificationActionableOption[];
  cameras: UnifiProtectCamera[];
  integration?: NotificationIntegration;
  isGateMalfunctionWorkflow: boolean;
  stageOptions: NotificationGateMalfunctionStageOption[];
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (action: NotificationAction) => void;
  onRemove: () => void;
}) {
  const meta = notificationChannelMeta[action.type];
  const Icon = meta.icon;
  const supportsTitle = action.type !== "voice" && !isGateMalfunctionWorkflow;
  const supportsMessageTemplate = !isGateMalfunctionWorkflow;
  const supportsMedia = action.type === "mobile" || action.type === "in_app" || action.type === "discord";
  const supportsActionable = action.type === "mobile" && actionableOptions.length > 0;
  const actionMedia = normalizeNotificationMedia(action.media);
  const actionActionable = normalizeNotificationActionable(action.actionable);
  const selectedActionable = actionableOptions.find((item) => item.value === actionActionable.action) ?? actionableOptions[0];
  const selectedCamera = cameras.find((camera) => camera.id === actionMedia.camera_id);
  const cameraSnapshotUrl = selectedCamera
    ? `/api/v1/integrations/unifi-protect/cameras/${selectedCamera.id}/snapshot?width=320&height=180`
    : "";
  const targetChips = notificationActionTargetChips(action, integration);
  const whatsappNumberTargets = action.target_ids
    .filter((target) => target.startsWith("whatsapp:number:"))
    .map((target) => target.replace(/^whatsapp:number:/, ""))
    .join("\n");
  const updateWhatsAppNumberTargets = (value: string) => {
    const manualTargets = value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => `whatsapp:number:${item}`);
    onChange({
      ...action,
      target_mode: "selected",
      target_ids: [
        ...action.target_ids.filter((target) => !target.startsWith("whatsapp:number:")),
        ...manualTargets,
      ],
    });
  };
  const selectedGateStages = normalizeGateMalfunctionStages(action.gate_malfunction_stages);
  const activeGateStages = selectedGateStages.length
    ? selectedGateStages
    : stageOptions.map((stage) => stage.value);
  const toggleGateMalfunctionStage = (stage: NotificationGateMalfunctionStage) => {
    const base = selectedGateStages.length
      ? selectedGateStages
      : stageOptions.map((item) => item.value);
    const next = base.includes(stage)
      ? base.filter((item) => item !== stage)
      : [...base, stage];
    const normalized = normalizeGateMalfunctionStages(next);
    onChange({
      ...action,
      gate_malfunction_stages: normalized.length === stageOptions.length || normalized.length === 0 ? [] : normalized,
    });
  };
  return (
    <article className="workflow-action-card">
      <div className="workflow-card-title">
        <Icon size={16} />
        <span>
          <strong>{meta.label}</strong>
          <small>{meta.description}</small>
        </span>
        <button className="icon-button danger" onClick={onRemove} type="button" aria-label="Remove action">
          <Trash2 size={14} />
        </button>
      </div>

      <div className="workflow-target-chips" aria-label={`${meta.label} selected endpoints`}>
        {targetChips.map((chip) => (
          <span className={chip.unavailable ? "workflow-target-chip unavailable" : "workflow-target-chip"} key={chip.id}>
            <strong>{chip.provider}</strong>
            {chip.label}
          </span>
        ))}
      </div>

      {isGateMalfunctionWorkflow ? (
        <section className="workflow-stage-row" aria-label={`${meta.label} gate malfunction stages`}>
          <div className="workflow-stage-row-head">
            <AlertTriangle size={14} />
            <span>Gate Malfunction Stages</span>
          </div>
          <div className="workflow-stage-toggles">
            {stageOptions.map((stage) => {
              const selected = activeGateStages.includes(stage.value);
              return (
                <button
                  className={selected ? "workflow-stage-toggle selected" : "workflow-stage-toggle"}
                  key={stage.value}
                  onClick={() => toggleGateMalfunctionStage(stage.value)}
                  type="button"
                >
                  {selected ? <Check size={13} /> : null}
                  {stage.label}
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      {action.type === "whatsapp" ? (
        <PlainTemplateEditor
          label="Phone numbers or @Variables"
          multiline
          value={whatsappNumberTargets}
          variables={variables}
          onChange={updateWhatsAppNumberTargets}
        />
      ) : null}

      {supportsTitle ? (
        <SafeVariableRichTextEditor
          label="Title"
          value={action.title_template}
          variables={variables}
          onChange={(title_template) => onChange({ ...action, title_template })}
        />
      ) : null}
      {supportsMessageTemplate ? (
        <SafeVariableRichTextEditor
          label={action.type === "voice" ? "Spoken message" : "Message"}
          multiline
          value={action.message_template}
          variables={variables}
          onChange={(message_template) => onChange({ ...action, message_template })}
        />
      ) : (
        <div className="workflow-generated-copy">
          <Sparkles size={14} />
          <span>LLM generated content</span>
        </div>
      )}

      {supportsMedia ? (
        <section className="workflow-media-row">
          <label className={actionMedia.attach_camera_snapshot ? "notification-switch active" : "notification-switch"}>
            <input
              checked={actionMedia.attach_camera_snapshot}
              onChange={(event) => onChange({ ...action, media: { ...actionMedia, attach_camera_snapshot: event.target.checked } })}
              type="checkbox"
            />
            <span>Camera Screenshot</span>
          </label>
          {actionMedia.attach_camera_snapshot ? (
            <select value={actionMedia.camera_id} onChange={(event) => onChange({ ...action, media: { ...actionMedia, camera_id: event.target.value } })}>
              <option value="">Select camera</option>
              {cameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.name}</option>)}
            </select>
          ) : null}
          {cameraSnapshotUrl ? (
            <div className="workflow-camera-preview">
              <img src={cameraSnapshotUrl} alt={`${selectedCamera?.name ?? "Camera"} snapshot preview`} />
              <span>{selectedCamera?.name ?? "Camera snapshot"}</span>
            </div>
          ) : null}
          {supportsActionable ? (
            <>
              <label className={actionActionable.enabled ? "notification-switch active" : "notification-switch"}>
                <input
                  checked={actionActionable.enabled}
                  onChange={(event) => onChange({
                    ...action,
                    actionable: {
                      enabled: event.target.checked,
                      action: event.target.checked ? selectedActionable.value : actionActionable.action,
                    },
                  })}
                  type="checkbox"
                />
                <span>Actionable Notification</span>
              </label>
              {actionActionable.enabled ? (
                <select
                  value={selectedActionable.value}
                  onChange={(event) => onChange({ ...action, actionable: { enabled: true, action: event.target.value } })}
                  aria-label="Actionable notification action"
                >
                  {actionableOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}
    </article>
  );
}

export function NotificationLivePreviewPanel({
  actions
}: {
  actions: Array<NotificationAction & { title: string; message: string; phoneticsApplied?: boolean }>;
}) {
  return (
    <aside className="notification-preview-panel" aria-label="Live notification preview">
      <div className="notification-preview-rail-head">
        <div>
          <strong id="notification-preview-title">Live Preview</strong>
          <span>Mock context resolves @ variables as you type.</span>
        </div>
      </div>
      {actions.length ? (
        <div className="notification-preview-stack">
          {actions.map((action) => {
            const meta = notificationChannelMeta[action.type];
            const Icon = meta.icon;
            return (
              <article className="notification-preview-card-inline" key={action.id}>
                <div>
                  <Icon size={16} />
                  <strong>{meta.label}</strong>
                  <Badge tone={meta.tone}>{action.target_mode}</Badge>
                  {action.phoneticsApplied ? <span className="phonetic-preview-badge"><Volume2 size={12} /> Phonetics Applied</span> : null}
                </div>
                {action.title ? <h3>{action.title}</h3> : null}
                <p>{action.message}</p>
                {action.media.attach_camera_snapshot ? <span className="preview-media-chip"><Camera size={13} /> Camera Screenshot</span> : null}
                {action.actionable.enabled ? <span className="preview-media-chip"><DoorOpen size={13} /> {notificationActionableLabel(action.actionable.action)}</span> : null}
              </article>
            );
          })}
        </div>
      ) : (
        <div className="notification-endpoint-empty">Add an action to preview the outgoing notification.</div>
      )}
    </aside>
  );
}

export function TwoPaneSelectionModal({
  activeCategoryId,
  backLabel = "Back to editor",
  categories,
  children,
  embedded = false,
  footer,
  onBack,
  onCategoryChange,
  onClose,
  onSearchChange,
  searchPlaceholder = "Search",
  searchQuery,
  subtitle,
  title,
  wide = false
}: {
  activeCategoryId: string;
  backLabel?: string;
  categories: TwoPaneCategory[];
  children: React.ReactNode;
  embedded?: boolean;
  footer?: React.ReactNode;
  onBack?: () => void;
  onCategoryChange: (categoryId: string) => void;
  onClose: () => void;
  onSearchChange: (query: string) => void;
  searchPlaceholder?: string;
  searchQuery: string;
  subtitle: string;
  title: string;
  wide?: boolean;
}) {
  const className = [
    "modal-card",
    "two-pane-selection-modal",
    embedded ? "embedded" : "",
    wide ? "wide" : "",
  ].filter(Boolean).join(" ");
  const content = (
    <div className={className} role={embedded ? undefined : "dialog"} aria-modal={embedded ? undefined : true} aria-labelledby="two-pane-selection-title">
      <div className="two-pane-selection-header">
        <div className="modal-header compact">
          <div>
            <h2 id="two-pane-selection-title">{title}</h2>
            <p>{subtitle}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label={`Close ${title}`}>
            <X size={16} />
          </button>
        </div>
        <label className="two-pane-search">
          <Search size={16} />
          <input
            autoFocus
            placeholder={searchPlaceholder}
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </label>
      </div>

      <div className="two-pane-selection-body">
        <nav className="two-pane-category-list" aria-label={`${title} categories`}>
          {categories.map((category) => {
            const Icon = category.icon;
            return (
              <button
                className={category.id === activeCategoryId ? "two-pane-category active" : "two-pane-category"}
                disabled={category.disabled}
                key={category.id}
                onClick={() => onCategoryChange(category.id)}
                type="button"
              >
                {Icon ? <Icon size={16} /> : null}
                <span>{category.label}</span>
                <Badge tone={category.count ? "blue" : "gray"}>{category.count}</Badge>
              </button>
            );
          })}
        </nav>
        <section className="two-pane-selection-content">{children}</section>
      </div>
      {footer || onBack ? (
        <div className="two-pane-selection-footer">
          {onBack ? (
            <button className="secondary-button two-pane-editor-back" onClick={onBack} type="button">
              <ArrowLeft size={15} /> {backLabel}
            </button>
          ) : null}
          {footer ? <div className="two-pane-selection-footer-actions">{footer}</div> : null}
        </div>
      ) : null}
    </div>
  );
  if (embedded) return content;
  return (
    <div className="modal-backdrop" role="presentation">
      {content}
    </div>
  );
}

export function NotificationTriggerModal({
  embedded = false,
  groups,
  selected,
  onClose,
  onSelect
}: {
  embedded?: boolean;
  groups: NotificationTriggerGroup[];
  selected: string;
  onClose: () => void;
  onSelect: (triggerEvent: string) => void;
}) {
  const sortedGroups = React.useMemo(() => normalizeTriggerGroups(groups, selected), [groups, selected]);
  const initialTriggerCategoryId = sortedGroups.find((group) => group.events.some((event) => event.value === selected))?.id ?? sortedGroups[0]?.id ?? "";
  const [activeCategoryId, setActiveCategoryId] = React.useState(initialTriggerCategoryId);
  const [searchQuery, setSearchQuery] = React.useState("");
  const query = searchQuery.trim().toLowerCase();
  const visibleGroups = React.useMemo(() => {
    return sortedGroups
      .map((group) => {
        const categoryMatches = matchesSearchText(group.label, query);
        const events = categoryMatches
          ? group.events
          : group.events.filter((event) => matchesSearchText(`${event.label} ${event.description} ${event.value}`, query));
        return { ...group, events };
      })
      .filter((group) => group.events.length > 0 || matchesSearchText(group.label, query));
  }, [query, sortedGroups]);
  React.useEffect(() => {
    if (!visibleGroups.length) return;
    if (!visibleGroups.some((group) => group.id === activeCategoryId)) {
      setActiveCategoryId(visibleGroups[0].id);
    }
  }, [activeCategoryId, visibleGroups]);
  const activeGroup = visibleGroups.find((group) => group.id === activeCategoryId) ?? visibleGroups[0];
  const categories = visibleGroups.map((group) => ({
    id: group.id,
    label: group.label,
    count: group.events.length,
    icon: notificationTriggerGroupIcon(group.id),
  }));
  return (
    <TwoPaneSelectionModal
      activeCategoryId={activeGroup?.id ?? ""}
      categories={categories}
      embedded={embedded}
      onBack={embedded ? onClose : undefined}
      onCategoryChange={setActiveCategoryId}
      onClose={onClose}
      onSearchChange={setSearchQuery}
      searchPlaceholder="Search triggers"
      searchQuery={searchQuery}
      subtitle="Choose the event that starts this workflow."
      title="Add Trigger"
    >
      {activeGroup ? (
        <div className="two-pane-card-grid trigger-card-grid">
          {activeGroup.events.map((event) => {
            const isSelected = selected === event.value;
            return (
              <button
                className={isSelected ? "two-pane-item-card selected" : "two-pane-item-card"}
                key={event.value}
                onClick={() => onSelect(event.value)}
                type="button"
              >
                <span>
                  <strong>{event.label}</strong>
                  <small>{event.description}</small>
                </span>
                <Badge tone={isSelected ? "green" : notificationSeverityTone(event.severity)}>{isSelected ? "Selected" : titleCase(event.severity)}</Badge>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="two-pane-empty">No triggers match this search.</div>
      )}
    </TwoPaneSelectionModal>
  );
}

export function NotificationConditionModal({
  people,
  schedules,
  onClose,
  onSelect
}: {
  people: Person[];
  schedules: Schedule[];
  onClose: () => void;
  onSelect: (condition: NotificationCondition) => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card notification-add-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-condition-title">
        <div className="modal-header">
          <div>
            <h2 id="workflow-condition-title">Add Condition</h2>
            <p>Conditions are evaluated together before actions run.</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close condition selector"><X size={16} /></button>
        </div>
        <div className="notification-add-groups">
          <section className="notification-add-group">
            <div className="notification-subtitle"><strong>Condition Types</strong><Badge tone="gray">2</Badge></div>
            <div>
              <button className="notification-add-option" onClick={() => onSelect({ id: draftId("condition"), type: "schedule", schedule_id: schedules[0]?.id ?? "" })} type="button">
                <span><strong>Schedule</strong><small>Only continue when the event time falls inside a selected schedule.</small></span>
                <Clock3 size={18} />
              </button>
              <button className="notification-add-option" onClick={() => onSelect({ id: draftId("condition"), type: "presence", mode: "someone_home", person_id: people[0]?.id ?? "" })} type="button">
                <span><strong>Presence</strong><small>Check whether nobody, somebody, or a specific person is home.</small></span>
                <Users size={18} />
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

export function NotificationActionModal({
  embedded = false,
  currentUser,
  integrations,
  people,
  onClose,
  onSelect
}: {
  embedded?: boolean;
  currentUser: UserAccount;
  integrations: NotificationIntegration[];
  people: Person[];
  onClose: () => void;
  onSelect: (action: NotificationAction) => void;
}) {
  const actionCategories = React.useMemo(() => notificationActionCategories(), []);
  const defaultCategory = actionCategories[0]?.id as NotificationActionType;
  const [activeCategory, setActiveCategory] = React.useState<NotificationActionType>(defaultCategory ?? "in_app");
  const [selectedMethodId, setSelectedMethodId] = React.useState<string | null>(null);
  const [selectedTargetIds, setSelectedTargetIds] = React.useState<Set<string>>(() => new Set());
  const [searchQuery, setSearchQuery] = React.useState("");
  const prefersReducedMotion = useReducedMotion();
  const query = searchQuery.trim().toLowerCase();
  const currentUserPerson = React.useMemo(() => findCurrentUserPerson(people, currentUser), [currentUser, people]);
  const methodsByCategory = React.useMemo(
    () => buildNotificationActionMethods(integrations, currentUserPerson),
    [currentUserPerson, integrations]
  );
  const visibleCategoryRows = React.useMemo(() => {
    return actionCategories
      .map((category) => {
        const categoryMatches = matchesSearchText(category.label, query);
        const methods = (methodsByCategory[category.id as NotificationActionType] ?? []).filter((method) =>
          categoryMatches || matchesSearchText(`${method.label} ${method.provider} ${method.detail}`, query)
        );
        return { ...category, count: methods.length, disabled: false };
      })
      .filter((category) => category.count > 0 || matchesSearchText(category.label, query) || !query);
  }, [actionCategories, methodsByCategory, query]);
  React.useEffect(() => {
    if (!visibleCategoryRows.length) return;
    if (!visibleCategoryRows.some((category) => category.id === activeCategory)) {
      setActiveCategory(visibleCategoryRows[0].id as NotificationActionType);
      setSelectedMethodId(null);
      setSelectedTargetIds(new Set());
    }
  }, [activeCategory, visibleCategoryRows]);

  const activeCategoryMeta = actionCategories.find((category) => category.id === activeCategory) ?? actionCategories[0];
  const categoryMatches = matchesSearchText(activeCategoryMeta?.label ?? "", query);
  const activeMethods = (methodsByCategory[activeCategory] ?? []).filter((method) =>
    categoryMatches || matchesSearchText(`${method.label} ${method.provider} ${method.detail}`, query)
  );
  const selectedMethod = activeMethods.find((method) => method.id === selectedMethodId)
    ?? (selectedMethodId ? (methodsByCategory[activeCategory] ?? []).find((method) => method.id === selectedMethodId) : undefined);
  const targetQuery = query;
  const visibleTargets = selectedMethod
    ? selectedMethod.targets.filter((target) =>
      !targetQuery || matchesSearchText(`${target.label} ${target.detail} ${target.provider} ${target.id}`, targetQuery)
    )
    : [];
  const canConfirm = Boolean(selectedMethod && (!selectedMethod.requiresTarget || selectedTargetIds.size > 0));
  const suggestedTargetId = selectedMethod?.defaultTargetIds[0] ?? "";

  const chooseCategory = (categoryId: string) => {
    setActiveCategory(categoryId as NotificationActionType);
    setSelectedMethodId(null);
    setSelectedTargetIds(new Set());
  };

  const chooseMethod = (method: NotificationActionMethod) => {
    setSelectedMethodId(method.id);
    setSelectedTargetIds(new Set(method.defaultTargetIds));
  };

  const toggleTarget = (targetId: string) => {
    setSelectedTargetIds((current) => {
      const next = new Set(current);
      if (next.has(targetId)) next.delete(targetId);
      else next.add(targetId);
      return next;
    });
  };

  const confirm = () => {
    if (!selectedMethod || !canConfirm) return;
    onSelect(createWorkflowAction(selectedMethod.actionType, {
      target_mode: selectedMethod.targetMode,
      target_ids: selectedMethod.targetMode === "all" ? [] : Array.from(selectedTargetIds),
    }));
  };

  return (
    <TwoPaneSelectionModal
      activeCategoryId={activeCategory}
      categories={visibleCategoryRows}
      embedded={embedded}
      footer={selectedMethod ? (
        <>
          <button className="secondary-button" onClick={() => { setSelectedMethodId(null); setSelectedTargetIds(new Set()); }} type="button">
            <ArrowLeft size={15} /> Back to methods
          </button>
          <button className="primary-button" disabled={!canConfirm} onClick={confirm} type="button">
            <Check size={15} /> Confirm Selection
          </button>
        </>
      ) : null}
      onBack={embedded ? onClose : undefined}
      onCategoryChange={chooseCategory}
      onClose={onClose}
      onSearchChange={setSearchQuery}
      searchPlaceholder={selectedMethod ? "Search targets" : "Search actions"}
      searchQuery={searchQuery}
      subtitle={selectedMethod ? `Choose one or more targets for ${selectedMethod.label}.` : "Choose a delivery method, then select its targets."}
      title="Add Action"
      wide
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.div
          animate={{ x: 0 }}
          className="two-pane-selection-panel"
          exit={prefersReducedMotion ? undefined : { x: selectedMethod ? 6 : -6, transition: { duration: 0.06, ease: "easeOut" } }}
          initial={prefersReducedMotion ? false : { x: selectedMethod ? 8 : -8 }}
          key={selectedMethod ? "targets" : "methods"}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.11, ease: [0.2, 0, 0, 1] }}
        >
          {selectedMethod ? (
            <div className="action-target-step">
              <div className="action-target-step-head">
                <button className="secondary-button compact" onClick={() => { setSelectedMethodId(null); setSelectedTargetIds(new Set()); }} type="button">
                  <ArrowLeft size={14} /> Methods
                </button>
                <div>
                  <strong>{selectedMethod.label}</strong>
                  <span>{selectedMethod.provider}</span>
                </div>
              </div>
              {selectedMethod.unavailableReason ? (
                <div className="two-pane-empty warning">{selectedMethod.unavailableReason}</div>
              ) : null}
              {visibleTargets.length ? (
                <div className="two-pane-card-grid action-target-grid">
                  {visibleTargets.map((target) => {
                    const isSelected = selectedTargetIds.has(target.id) || selectedMethod.targetMode === "all";
                    const isSuggested = target.id === suggestedTargetId;
                    return (
                      <button
                        className={isSelected ? "two-pane-target-tile selected" : "two-pane-target-tile"}
                        key={target.id}
                        onClick={() => selectedMethod.targetMode === "all" ? undefined : toggleTarget(target.id)}
                        type="button"
                      >
                        <span className="target-select-mark">{isSelected ? <Check size={14} /> : null}</span>
                        <span className="target-tile-copy">
                          <span className="target-tile-title-line">
                            <strong>{target.label}</strong>
                            {isSuggested ? <Badge tone="green">Your device</Badge> : <Badge tone="gray">{target.provider}</Badge>}
                          </span>
                          <small>{target.detail || target.id}</small>
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="two-pane-empty">
                  {selectedMethod.targets.length ? "No targets match this search." : "No targets are available for this method."}
                </div>
              )}
            </div>
          ) : activeMethods.length ? (
            <div className="two-pane-card-grid action-method-grid">
              {activeMethods.map((method) => {
                const Icon = method.icon;
                return (
                  <button
                    className={method.unavailableReason ? "two-pane-item-card unavailable" : "two-pane-item-card"}
                    key={method.id}
                    onClick={() => chooseMethod(method)}
                    type="button"
                  >
                    <Icon size={18} />
                    <span>
                      <strong>{method.label}</strong>
                      <small>{method.detail}</small>
                    </span>
                    <Badge tone={method.unavailableReason ? "gray" : method.tone}>{method.provider}</Badge>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="two-pane-empty">
              {query ? "No action methods match this search." : "No methods are configured for this notification channel."}
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </TwoPaneSelectionModal>
  );
}

export function normalizeTriggerGroups(groups: NotificationTriggerGroup[], selected: string): NotificationTriggerGroup[] {
  const normalized = groups
    .map((group) => ({
      ...group,
      events: group.events
        .filter((event) => event.value !== "integration_test" || selected === "integration_test")
        .slice()
        .sort((a, b) => a.label.localeCompare(b.label))
    }))
    .filter((group) => group.events.length > 0)
    .sort((a, b) => a.label.localeCompare(b.label));

  if (selected === "integration_test" && !normalized.some((group) => group.events.some((event) => event.value === selected))) {
    normalized.push({
      id: "integration_test",
      label: "Integration Test",
      events: [
        {
          value: "integration_test",
          label: "Integration Test",
          severity: "info",
          description: "A user-triggered test message retained for this existing workflow.",
        },
      ],
    });
  }

  return normalized.sort((a, b) => a.label.localeCompare(b.label));
}

export function notificationTriggerGroupsForDisplay(groups: NotificationTriggerGroup[]): NotificationTriggerGroup[] {
  let hasGateMalfunctionGroup = false;
  const normalized = groups
    .map((group) => {
      if (group.id === "gate_malfunctions") {
        hasGateMalfunctionGroup = true;
        return {
          ...group,
          events: [canonicalGateMalfunctionTrigger],
        };
      }
      return {
        ...group,
        events: group.events.filter((event) => !legacyGateMalfunctionStage(event.value)),
      };
    })
    .filter((group) => group.events.length > 0);

  if (!hasGateMalfunctionGroup) {
    normalized.push({
      id: "gate_malfunctions",
      label: "Gate Malfunctions",
      events: [canonicalGateMalfunctionTrigger],
    });
  }
  return normalized;
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
  const voiceMethods: NotificationActionMethod[] = [];
  if (voiceTargets.length || voiceIntegration?.configured) {
    voiceMethods.push({
      id: "home_assistant_tts",
      actionType: "voice",
      label: "Home Assistant",
      provider: "Home Assistant TTS",
      detail: voiceTargets.length
        ? `${voiceTargets.length} media player target${voiceTargets.length === 1 ? "" : "s"}`
        : "No media players discovered",
      icon: Volume2,
      tone: "amber",
      targets: voiceTargets,
      targetMode: "selected",
      requiresTarget: true,
      defaultTargetIds: [],
      unavailableReason: voiceTargets.length ? undefined : "Home Assistant TTS is configured, but no media_player targets are available.",
    });
  }

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
  const discordDefault = discordIntegration?.endpoints.find((endpoint) => endpoint.id === "discord:*");
  const discordMethods: NotificationActionMethod[] = [];
  if (discordDefault || discordTargets.length || discordIntegration?.configured) {
    discordMethods.push({
      id: "discord",
      actionType: "discord",
      label: "Discord",
      provider: "Discord",
      detail: discordTargets.length
        ? `${discordTargets.length} channel${discordTargets.length === 1 ? "" : "s"} available`
        : "No Discord channels discovered",
      icon: MessageCircle,
      tone: "purple",
      targets: discordTargets,
      targetMode: "selected",
      requiresTarget: true,
      defaultTargetIds: discordTargets[0]?.id ? [discordTargets[0].id] : [],
      unavailableReason: discordTargets.length ? undefined : "Discord is configured, but no channels are available yet.",
    });
  }

  const whatsappTargets = concreteNotificationEndpoints(whatsappIntegration?.endpoints ?? []);
  const whatsappDefault = whatsappIntegration?.endpoints.find((endpoint) => endpoint.id === "whatsapp:*");
  const whatsappMethods: NotificationActionMethod[] = [];
  if (whatsappDefault || whatsappTargets.length || whatsappIntegration?.configured) {
    whatsappMethods.push({
      id: "whatsapp",
      actionType: "whatsapp",
      label: "WhatsApp",
      provider: "WhatsApp",
      detail: whatsappTargets.length
        ? `${whatsappTargets.length} Admin target${whatsappTargets.length === 1 ? "" : "s"} available`
        : "No Admin users with mobile numbers",
      icon: MessageCircle,
      tone: "green",
      targets: whatsappTargets,
      targetMode: "selected",
      requiresTarget: true,
      defaultTargetIds: whatsappTargets[0]?.id ? [whatsappTargets[0].id] : [],
      unavailableReason: whatsappTargets.length ? undefined : "WhatsApp is configured, but no active Admin users have mobile phone numbers.",
    });
  }

  return {
    discord: discordMethods.sort(sortNotificationMethods),
    in_app: inAppMethods.sort(sortNotificationMethods),
    mobile: mobileMethods.sort(sortNotificationMethods),
    whatsapp: whatsappMethods.sort(sortNotificationMethods),
    voice: voiceMethods.sort(sortNotificationMethods),
  };
}

export function sortNotificationMethods(a: NotificationActionMethod, b: NotificationActionMethod) {
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
  const fallback = eligible.filter((person) => normalizeIdentityName(person.display_name) === userDisplay);
  return fallback.length === 1 ? fallback[0] : null;
}

export function normalizeIdentityName(value: string) {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

export function matchesSearchText(value: string, query: string) {
  if (!query) return true;
  return value.toLowerCase().includes(query);
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

export function providerLabelForNotificationTarget(targetId: string, integration?: NotificationIntegration) {
  if (targetId.startsWith("apprise:")) return "Apprise";
  if (targetId.startsWith("discord:")) return "Discord";
  if (targetId.startsWith("whatsapp:")) return "WhatsApp";
  if (targetId.startsWith("home_assistant_mobile:") || targetId.startsWith("home_assistant_tts:")) return "Home Assistant";
  if (targetId === "dashboard") return "Dashboard";
  return integration?.provider ?? "Target";
}

export function unavailableNotificationTargetLabel(targetId: string) {
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

export function normalizeNotificationRule(rule: Partial<NotificationRule>): NotificationRule {
  const rawTrigger = stringifyTemplateValue(rule.trigger_event);
  const legacyStage = legacyGateMalfunctionStage(rawTrigger);
  const actions = Array.isArray(rule.actions) ? rule.actions.map(normalizeNotificationAction) : [];
  return {
    id: stringifyTemplateValue(rule.id) || draftId("workflow"),
    name: stringifyTemplateValue(rule.name) || "Notification Workflow",
    trigger_event: legacyStage ? "gate_malfunction" : rawTrigger,
    conditions: Array.isArray(rule.conditions) ? rule.conditions.map(normalizeNotificationCondition) : [],
    actions: legacyStage
      ? actions.map((action) => ({ ...action, gate_malfunction_stages: [legacyStage] }))
      : actions,
    is_active: rule.is_active !== false,
    last_fired_at: rule.last_fired_at ?? null,
    created_at: rule.created_at,
    updated_at: rule.updated_at,
  };
}

export function legacyGateMalfunctionStage(value: string): NotificationGateMalfunctionStage | "" {
  if (value === "gate_malfunction_initial") return "initial";
  if (value === "gate_malfunction_30m") return "30m";
  if (value === "gate_malfunction_60m") return "60m";
  if (value === "gate_malfunction_2hrs") return "2hrs";
  if (value === "gate_malfunction_fubar") return "fubar";
  return "";
}

export function normalizeNotificationCondition(condition: Partial<NotificationCondition>): NotificationCondition {
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

export function normalizePresenceConditionMode(value: unknown): PresenceConditionMode {
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

export function isGateMalfunctionStage(value: string): value is NotificationGateMalfunctionStage {
  return value === "initial" || value === "30m" || value === "60m" || value === "2hrs" || value === "fubar" || value === "resolved";
}

export function normalizeNotificationAction(action: Partial<NotificationAction>): NotificationAction {
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

export function isNotificationActionType(value: string): value is NotificationActionType {
  return value === "mobile" || value === "in_app" || value === "voice" || value === "discord" || value === "whatsapp";
}

export function normalizeNotificationTargetMode(value: unknown): NotificationTargetMode {
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

export function stringifyTemplateValue(value: unknown) {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

export function pluralize(word: string, count: number) {
  return count === 1 ? word : `${word}s`;
}

export function notificationSeverityTone(value: string): BadgeTone {
  if (value === "critical") return "red";
  if (value === "warning") return "amber";
  if (value === "info") return "blue";
  return "gray";
}

export function renderWorkflowPreview(actions: NotificationAction[], context: Record<string, string>, triggerEvent = "") {
  return actions.map(normalizeNotificationAction).map((action) => {
    const generated = triggerEvent === "gate_malfunction"
      ? gateMalfunctionPreviewContent(action.type, context)
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

export function gateMalfunctionPreviewContent(actionType: NotificationActionType, context: Record<string, string>) {
  const stage = isGateMalfunctionStage(context.MalfunctionStage) ? context.MalfunctionStage : "initial";
  const stageLabel = defaultGateMalfunctionStageOptions.find((item) => item.value === stage)?.label ?? "Gate Malfunction";
  const title = stage === "resolved" ? "Gate malfunction resolved" : `Gate Malfunction - ${stageLabel}`;
  const body = gateMalfunctionPlainPreviewBody(stage);
  return {
    title,
    message: actionType === "voice" ? `Attention. ${body}` : body,
  };
}

export function gateMalfunctionPlainPreviewBody(stage: NotificationGateMalfunctionStage) {
  if (stage === "initial") return "The gate has malfunctioned and is stuck open. Alfred is trying to resolve it.";
  if (stage === "30m") return "The gate is still stuck open. Alfred is still working on it.";
  if (stage === "60m") return "The gate has been stuck open for about an hour. It is not looking good, but Alfred is still on the case.";
  if (stage === "2hrs") return "The gate has been stuck open for over two hours. Alfred has not been able to fix it yet.";
  if (stage === "fubar") return "The gate is still stuck open and Alfred has run out of automatic fixes. Please check the gate when you can.";
  return "The gate malfunction has been resolved and the gate is closed again.";
}

export function renderWorkflowTemplate(template: string, context: Record<string, string>) {
  return template.replace(/@([A-Za-z][A-Za-z0-9_]*)/g, (_, token: string) => context[token] ?? "").trim();
}

export function hasVehicleTtsPhoneticMatch(message: string) {
  return vehicleTtsPhoneticPattern.test(message);
}

export function draftId(prefix: string) {
  return `draft-${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function formatCompactLastFired(value?: string | null) {
  if (!value) return "never";
  return formatRelativeTime(value);
}

export function formatRelativeTime(value: string) {
  const date = new Date(value);
  const timestamp = date.getTime();
  if (Number.isNaN(timestamp)) return formatDate(value);
  const diffSeconds = Math.round((timestamp - Date.now()) / 1000);
  const absSeconds = Math.abs(diffSeconds);
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["week", 60 * 60 * 24 * 7],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  for (const [unit, seconds] of units) {
    if (absSeconds >= seconds) {
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(diffSeconds / seconds), unit);
    }
  }
  return "just now";
}
