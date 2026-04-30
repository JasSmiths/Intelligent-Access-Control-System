import React from "react";
import { createPortal } from "react-dom";
import ReactDOM from "react-dom/client";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
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
  Trash2,
  Trophy,
  Type,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X,
  Zap
} from "lucide-react";
import "./styles.css";

const VariableRichTextEditor = React.lazy(() => import("./VariableRichTextEditor"));
const MonacoDiffEditor = React.lazy(() => import("@monaco-editor/react").then((module) => ({ default: module.DiffEditor })));

type DoorCommandAction = "open" | "close";
type DashboardCommand = {
  kind: "gate" | "garage_door";
  entity_id?: string;
  label: string;
  action: DoorCommandAction;
};

type ScheduleTimeBlock = {
  start: string;
  end: string;
};

type ScheduleTimeBlocks = Record<string, ScheduleTimeBlock[]>;

type Schedule = {
  id: string;
  name: string;
  description: string | null;
  time_blocks: ScheduleTimeBlocks;
  created_at: string;
  updated_at: string;
};

type VisitorPassStatus = "active" | "scheduled" | "used" | "expired" | "cancelled";

type VisitorPass = {
  id: string;
  visitor_name: string;
  expected_time: string;
  window_minutes: number;
  valid_from: string | null;
  valid_until: string | null;
  window_start: string;
  window_end: string;
  status: VisitorPassStatus;
  creation_source: string;
  source_reference: string | null;
  source_metadata: Record<string, unknown> | null;
  created_by_user_id: string | null;
  created_by: string | null;
  arrival_time: string | null;
  departure_time: string | null;
  number_plate: string | null;
  vehicle_make: string | null;
  vehicle_colour: string | null;
  duration_on_site_seconds: number | null;
  duration_human: string | null;
  arrival_event_id: string | null;
  departure_event_id: string | null;
  telemetry_trace_id: string | null;
  created_at: string;
  updated_at: string;
};

type ScheduleDependencyItem = {
  id: string;
  name: string;
  kind: string;
  entity_id?: string | null;
  registration_number?: string | null;
  owner?: string | null;
};

type ScheduleDependencies = {
  people: ScheduleDependencyItem[];
  vehicles: ScheduleDependencyItem[];
  doors: ScheduleDependencyItem[];
};

type ScheduleCellPoint = {
  day: number;
  slot: number;
};

type ScheduleDragState = {
  active: boolean;
  targetSelected: boolean;
  anchorDay: number;
  anchorSlot: number;
  baseSlots: Set<string>;
};

type ScheduleCopiedBlock = {
  startSlot: number;
  endSlot: number;
};

type ScheduleContextMenu =
  | {
    kind: "selected";
    x: number;
    y: number;
    day: number;
    range: ScheduleCopiedBlock;
  }
  | {
    kind: "empty";
    x: number;
    y: number;
    day: number;
  };

type Presence = {
  person_id: string;
  display_name: string;
  state: "present" | "exited" | "unknown";
  last_changed_at: string | null;
};

type AccessEvent = {
  id: string;
  registration_number: string;
  direction: "entry" | "exit" | "denied";
  decision: "granted" | "denied";
  confidence: number;
  source: string;
  occurred_at: string;
  timing_classification: string;
  anomaly_count: number;
  visitor_pass_id: string | null;
  visitor_name: string | null;
  visitor_pass_mode: string | null;
};

type AlertSeverity = "info" | "warning" | "critical";
type AlertStatus = "open" | "resolved";

type AlertResolver = {
  id: string;
  username: string;
  display_name: string;
};

type Anomaly = {
  id: string;
  alert_ids: string[];
  grouped: boolean;
  event_id?: string | null;
  type: string;
  severity: AlertSeverity;
  status: AlertStatus;
  message: string;
  registration_number: string;
  count: number;
  local_date: string | null;
  created_at: string;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolved_by_user_id: string | null;
  resolved_by: AlertResolver | null;
  resolution_note: string | null;
  snapshot_url?: string | null;
  snapshot_captured_at?: string | null;
  snapshot_bytes?: number | null;
};

type Person = {
  id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  profile_photo_data_url: string | null;
  group_id: string | null;
  group: string | null;
  category: string | null;
  schedule_id: string | null;
  schedule: string | null;
  is_active: boolean;
  notes: string | null;
  garage_door_entity_ids: string[];
  home_assistant_mobile_app_notify_service: string | null;
  vehicles: Vehicle[];
};

type Vehicle = {
  id: string;
  registration_number: string;
  vehicle_photo_data_url?: string | null;
  description: string | null;
  make: string | null;
  model: string | null;
  color?: string | null;
  mot_status?: string | null;
  tax_status?: string | null;
  mot_expiry?: string | null;
  tax_expiry?: string | null;
  last_dvla_lookup_date?: string | null;
  person_id?: string | null;
  owner?: string | null;
  schedule_id?: string | null;
  schedule?: string | null;
  is_active?: boolean;
};

type LeaderboardPerson = {
  id: string | null;
  first_name: string;
  last_name: string;
  display_name: string;
  profile_photo_data_url: string | null;
};

type LeaderboardVehicle = {
  id: string | null;
  registration_number: string;
  vehicle_photo_data_url: string | null;
  make: string;
  model: string;
  color: string;
  description: string;
  display_name: string;
};

type LeaderboardKnownEntry = {
  rank: number;
  registration_number: string;
  read_count: number;
  last_seen_at: string | null;
  vehicle_id: string;
  person_id: string;
  first_name: string;
  display_name: string;
  vehicle_name: string;
  person: LeaderboardPerson;
  vehicle: LeaderboardVehicle;
};

type LeaderboardDvla = {
  status: string;
  vehicle: Record<string, unknown> | null;
  display_vehicle: Record<string, unknown> | null;
  label: string;
  error?: string;
};

type LeaderboardUnknownEntry = {
  rank: number;
  registration_number: string;
  read_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  dvla: LeaderboardDvla;
};

type LeaderboardResponse = {
  known: LeaderboardKnownEntry[];
  unknown: LeaderboardUnknownEntry[];
  top_known: LeaderboardKnownEntry | null;
  generated_at: string;
};

type Group = {
  id: string;
  name: string;
  category: string;
  subtype: string | null;
  description: string | null;
  people_count: number;
};

type IntegrationStatus = {
  configured: boolean;
  gate_entity_id: string | null;
  gate_entities?: HomeAssistantManagedCover[];
  garage_door_entities?: HomeAssistantManagedCover[];
  default_media_player: string | null;
  last_gate_state: string;
  current_gate_state?: string;
  front_door_state?: string;
  back_door_state?: string;
  main_garage_door_state?: string;
  mums_garage_door_state?: string;
};

type ICloudCalendarAccount = {
  id: string;
  apple_id: string;
  display_name: string;
  status: string;
  is_active: boolean;
  last_auth_at: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_summary: Record<string, unknown> | null;
  last_error: string | null;
  created_by_user_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type ICloudCalendarSyncRun = {
  id: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  trigger_source: string;
  triggered_by_user_id: string | null;
  account_count: number;
  events_scanned: number;
  events_matched: number;
  passes_created: number;
  passes_updated: number;
  passes_cancelled: number;
  passes_skipped: number;
  account_results: Record<string, unknown>[];
  error: string | null;
};

type ICloudCalendarPayload = {
  accounts: ICloudCalendarAccount[];
  recent_sync_runs: ICloudCalendarSyncRun[];
};

type ICloudAuthStartResponse = {
  status: "connected" | "requires_2fa";
  requires_2fa?: boolean;
  handshake_id?: string;
  apple_id?: string;
  detail?: string;
  account?: ICloudCalendarAccount;
};

type ICloudAuthVerifyResponse = {
  status: "connected";
  account: ICloudCalendarAccount;
};

type MaintenanceStatus = {
  is_active: boolean;
  enabled_by: string | null;
  enabled_at: string | null;
  source: string | null;
  reason: string | null;
  duration_seconds: number;
  duration_label: string | null;
  ha_entity_id?: string;
};

type RealtimeMessage = {
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

type LprTimingSource = "webhook" | "uiprotect" | "uiprotect_track";

type LprTimingObservation = {
  id: string;
  source: LprTimingSource;
  source_detail: string;
  registration_number: string;
  received_at: string;
  raw_value: string | null;
  candidate_kind: "normalized_plate" | "possible_lpr_field";
  captured_at: string | null;
  event_id: string | null;
  camera_id: string | null;
  camera_name: string | null;
  confidence: number | null;
  confidence_scale: string | null;
  protect_action: string | null;
  protect_model: string | null;
  smart_detect_types: string[] | null;
  payload_path: string | null;
};

const REALTIME_REFRESH_MIN_INTERVAL_MS = 5000;

const REALTIME_DATA_REFRESH_EVENTS = new Set([
  "access_event.finalize_failed",
  "alerts.updated",
  "visitor_pass.created",
  "visitor_pass.updated",
  "visitor_pass.cancelled",
  "visitor_pass.status_changed",
  "visitor_pass.used",
  "visitor_pass.departure_recorded"
]);

type TelemetrySpan = {
  id: string;
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  category: string;
  step_order: number;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  status: string;
  attributes: Record<string, unknown>;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  error: string | null;
};

type TelemetryTrace = {
  trace_id: string;
  name: string;
  category: string;
  status: string;
  level: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  actor: string | null;
  source: string | null;
  registration_number: string | null;
  access_event_id: string | null;
  summary: string | null;
  context: Record<string, unknown>;
  error: string | null;
};

type TelemetryTraceDetail = TelemetryTrace & {
  spans: TelemetrySpan[];
};

type GateMalfunctionTimelineEvent = {
  id: string;
  kind: string;
  occurred_at: string;
  title: string;
  details: Record<string, unknown>;
  attempt_number: number | null;
  notification_trigger: string | null;
  notification_channel: string | null;
  telemetry_span_id: string | null;
  status?: string | null;
};

type GateMalfunctionRecord = {
  id: string;
  gate_entity_id: string;
  gate_name: string | null;
  status: "active" | "resolved" | "fubar" | string;
  opened_at: string;
  declared_at: string;
  resolved_at: string | null;
  fubar_at: string | null;
  fix_attempts_count: number;
  next_attempt_scheduled_at: string | null;
  last_known_vehicle_event_id: string | null;
  last_known_vehicle: string;
  telemetry_trace_id: string | null;
  last_gate_state: string | null;
  last_checked_at: string | null;
  total_downtime_seconds: number;
  summary: string;
  timeline?: GateMalfunctionTimelineEvent[];
};

type AuditLog = {
  id: string;
  timestamp: string;
  category: string;
  action: string;
  actor: string;
  actor_user_id: string | null;
  target_entity: string | null;
  target_id: string | null;
  target_label: string | null;
  diff: Record<string, unknown>;
  metadata: Record<string, unknown>;
  outcome: string;
  level: string;
  trace_id: string | null;
  request_id: string | null;
};

type PaginatedResponse<T> = {
  items: T[];
  next_cursor: string | null;
};

type NotificationToast = {
  id: string;
  title: string;
  body: string;
  event_type: string;
  severity: "info" | "warning" | "critical";
  snapshot_url?: string;
};

type UserRole = "admin" | "standard";

type SystemSetting = {
  key: string;
  category: string;
  value: unknown;
  is_secret: boolean;
  description: string | null;
};

type SettingsMap = Record<string, unknown>;

type HomeAssistantEntity = {
  entity_id: string;
  name: string | null;
  state: string | null;
  device_class?: string | null;
};

type HomeAssistantManagedCover = {
  entity_id: string;
  name: string;
  state?: string | null;
  enabled?: boolean;
  schedule_id?: string | null;
  open_service?: string;
  close_service?: string;
};

type HomeAssistantMobileAppService = {
  service_id: string;
  name: string | null;
  description: string | null;
};

type HomeAssistantMobileAppSuggestion = {
  person_id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  suggested_service_id: string | null;
  suggested_name: string | null;
  confidence: number;
};

type HomeAssistantDiscovery = {
  cover_entities: HomeAssistantEntity[];
  gate_suggestions?: HomeAssistantManagedCover[];
  garage_door_suggestions?: HomeAssistantManagedCover[];
  media_player_entities: HomeAssistantEntity[];
  mobile_app_notification_services: HomeAssistantMobileAppService[];
  mobile_app_notification_mappings: HomeAssistantMobileAppSuggestion[];
};

type HomeAssistantPersonSuggestion = {
  mobile?: {
    id: string;
    label: string;
    confidence: number;
  };
};

type AppriseUrlSummary = {
  id?: string;
  index: number;
  type: string;
  scheme: string;
  preview: string;
};

type DiscordStatus = {
  configured: boolean;
  connected: boolean;
  library_available: boolean;
  guild_count: number;
  channel_count: number;
  default_notification_channel_id: string;
  allow_direct_messages: boolean;
  require_mention: boolean;
  last_error: string | null;
  ready_at: string | null;
};

type DiscordChannel = {
  id: string;
  guild_id: string;
  name: string;
  label: string;
};

type DiscordIdentity = {
  id: string;
  provider_user_id: string;
  provider_display_name: string;
  user_id: string | null;
  user_label: string | null;
  person_id: string | null;
  person_label: string | null;
  last_seen_at: string | null;
};

type NotificationChannelId = "mobile" | "in_app" | "voice" | "discord";
type NotificationActionType = NotificationChannelId;
type NotificationConditionType = "schedule" | "presence";
type PresenceConditionMode = "no_one_home" | "someone_home" | "person_home";
type NotificationTargetMode = "all" | "many" | "selected";

type NotificationEndpoint = {
  id: string;
  provider: string;
  label: string;
  detail: string;
};

type NotificationIntegration = {
  id: NotificationChannelId;
  name: string;
  provider: string;
  configured: boolean;
  endpoints: NotificationEndpoint[];
};

type NotificationCondition = {
  id: string;
  type: NotificationConditionType;
  schedule_id?: string;
  mode?: PresenceConditionMode;
  person_id?: string;
};

type NotificationAction = {
  id: string;
  type: NotificationActionType;
  target_mode: NotificationTargetMode;
  target_ids: string[];
  title_template: string;
  message_template: string;
  media: {
    attach_camera_snapshot: boolean;
    camera_id: string;
  };
};

type NotificationRule = {
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

type NotificationVariable = {
  name: string;
  token: string;
  label: string;
};

type NotificationVariableGroup = {
  group: string;
  items: NotificationVariable[];
};

type NotificationTriggerOption = {
  value: string;
  label: string;
  severity: "info" | "warning" | "critical";
  description: string;
};

type NotificationTriggerGroup = {
  id: string;
  label: string;
  events: NotificationTriggerOption[];
};

type NotificationCatalogResponse = {
  triggers: NotificationTriggerGroup[];
  variables: NotificationVariableGroup[];
  integrations: NotificationIntegration[];
  mock_context: Record<string, string>;
};

type NotificationStatusFilter = "all" | "active" | "inactive";

type NotificationFilterCounts = Record<NotificationStatusFilter, number>;

type NotificationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: NotificationRule[];
};

type WorkflowRuleMenuState = {
  id: string;
  left: number;
  top: number;
};

type NotificationConfigTooltipState = {
  left: number;
  placement: "top" | "bottom";
  top: number;
};

type WorkflowRuleStatusFeedback = {
  nonce: number;
  ruleId: string;
  status: "paused" | "resumed";
};

type AutomationNode = {
  id: string;
  type: string;
  config: Record<string, unknown>;
};

type AutomationAction = AutomationNode & {
  reason_template?: string;
};

type AutomationRule = {
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

type AutomationCatalogItem = {
  type: string;
  label: string;
  description?: string;
  scopes?: string[];
  integration_action?: boolean;
  integration_provider?: string;
  integration_provider_label?: string;
  integration_action_key?: string;
  default_config?: Record<string, unknown>;
};

type AutomationIntegrationCatalog = {
  id: string;
  label: string;
  description?: string;
  actions: AutomationCatalogItem[];
};

type AutomationCatalogGroup = {
  id: string;
  label: string;
  triggers?: AutomationCatalogItem[];
  conditions?: AutomationCatalogItem[];
  actions?: AutomationCatalogItem[];
  integrations?: AutomationIntegrationCatalog[];
};

type AutomationVariable = NotificationVariable & {
  scope?: string;
  trigger_types?: string[];
};

type AutomationVariableGroup = {
  group: string;
  scope?: string;
  items: AutomationVariable[];
};

type AutomationCatalogResponse = {
  triggers: AutomationCatalogGroup[];
  conditions: AutomationCatalogGroup[];
  actions: AutomationCatalogGroup[];
  variables: AutomationVariableGroup[];
  notification_rules: Array<{ id: string; name: string; trigger_event: string }>;
  garage_doors: Array<{ entity_id: string; name: string; schedule_id?: string | null }>;
  mock_context: Record<string, string>;
};

type AutomationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: AutomationRule[];
};

type TwoPaneCategory = {
  id: string;
  label: string;
  count: number;
  icon?: React.ElementType;
  disabled?: boolean;
};

type NotificationActionMethod = {
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

type NotificationPreview = {
  id: string;
  name: string;
  trigger_event: string;
  is_active: boolean;
  conditions: NotificationCondition[];
  actions: Array<NotificationAction & {
    title: string;
    message: string;
    snapshot: { image_url?: string } | null;
  }>;
};

type DvlaLookupResponse = {
  registration_number: string;
  vehicle: {
    make?: string | null;
    model?: string | null;
    colour?: string | null;
    color?: string | null;
  } & Record<string, unknown>;
  display_vehicle?: {
    make?: string | null;
    model?: string | null;
    colour?: string | null;
    color?: string | null;
  } & Record<string, unknown>;
  normalized_vehicle?: {
    registration_number?: string | null;
    make?: string | null;
    colour?: string | null;
    color?: string | null;
    mot_status?: string | null;
    mot_expiry?: string | null;
    tax_status?: string | null;
    tax_expiry?: string | null;
  };
};

type UnifiProtectStatus = {
  configured: boolean;
  connected: boolean;
  last_error: string | null;
  camera_count: number;
  host: string;
  port: number;
  verify_ssl: boolean;
  snapshot_width: number;
  snapshot_height: number;
};

type UnifiProtectCamera = {
  id: string;
  name: string;
  model: string | null;
  state: string | null;
  is_recording: boolean;
  is_recording_enabled: boolean;
  is_video_ready: boolean;
  is_motion_detected: boolean;
  is_smart_detected: boolean;
  last_motion_at: string | null;
  last_smart_detect_at: string | null;
  snapshot_url: string;
  channels: Array<{
    id: string;
    name: string;
    width: number | null;
    height: number | null;
    fps: number | null;
    bitrate: number | null;
    is_rtsp_enabled: boolean;
    is_package: boolean;
  }>;
  feature_flags: {
    has_smart_detect: boolean;
    has_package_camera: boolean;
    has_mic: boolean;
    smart_detect_types: string[];
    smart_detect_audio_types: string[];
  };
  detections: {
    active: string[];
  };
};

type UnifiProtectEvent = {
  id: string;
  type: string;
  camera_id: string;
  camera_name: string;
  start: string | null;
  end: string | null;
  score: number;
  smart_detect_types: string[];
  thumbnail_url: string;
  video_url: string | null;
};

type UnifiProtectAnalysis = {
  camera_id: string;
  provider: string;
  text: string;
  snapshot_retained: boolean;
};

type UnifiProtectUpdateStatus = {
  package: string;
  current_version: string;
  latest_version: string;
  update_available: boolean;
  active_package: {
    mode: string;
    version?: string | null;
    path?: string | null;
    installed_at?: string | null;
  };
  installed_overlays: Array<{ version: string; path: string }>;
  latest_summary?: Record<string, unknown>;
};

type UnifiProtectReleaseNotes = {
  source: string;
  title: string;
  body: string;
  published_at?: string | null;
  html_url?: string | null;
};

type UnifiProtectUpdateAnalysis = {
  package: string;
  current_version: string;
  target_version: string;
  latest_version: string;
  update_available: boolean;
  provider: string;
  analysis: string;
  release_notes: UnifiProtectReleaseNotes;
};

type UnifiProtectBackup = {
  id: string;
  created_at: string;
  reason: string;
  package_version: string;
  settings_count: number;
  size_bytes: number;
  download_url: string;
  active_package?: {
    mode: string;
    version?: string | null;
  };
};

type UnifiProtectUpdateApplyResult = {
  ok: boolean;
  previous_version: string;
  current_version: string;
  target_version: string;
  backup: UnifiProtectBackup;
  verification: {
    package_version?: string;
    camera_count?: number;
    snapshot_bytes?: number;
  };
};

type ProtectIntegrationTab = "general" | "exposes" | "updates";
type IntegrationsPageTab = "integrations" | "updates";

type DependencyRiskStatus = "safe" | "warning" | "breaking" | "unknown";

type DependencyPackage = {
  id: string;
  ecosystem: string;
  package_name: string;
  normalized_name: string;
  current_version: string | null;
  latest_version: string | null;
  dependant_area: string;
  manifest_path: string | null;
  manifest_section: string | null;
  requirement_spec: string | null;
  is_direct: boolean;
  is_enabled: boolean;
  update_available: boolean;
  risk_status: DependencyRiskStatus | string;
  last_checked_at: string | null;
  metadata: Record<string, unknown>;
  latest_analysis: DependencyAnalysis | null;
};

type DependencyAnalysis = {
  id: string;
  dependency_id: string;
  target_version: string;
  provider: string;
  model: string | null;
  verdict: DependencyRiskStatus | string;
  summary_markdown: string;
  changelog_source: string | null;
  changelog_markdown: string | null;
  usage_summary: {
    reference_count?: number;
    references?: Array<{ path: string; line: number; text: string }>;
  };
  breaking_changes: Array<Record<string, unknown>>;
  verification_steps: string[];
  suggested_diff: string | null;
  created_at: string;
};

type DependencyBackup = {
  id: string;
  dependency_id: string | null;
  package_name: string;
  ecosystem: string;
  version: string | null;
  reason: string;
  archive_path: string;
  storage_root: string;
  checksum_sha256: string;
  size_bytes: number;
  created_at: string;
  restored_at: string | null;
  metadata: Record<string, unknown>;
};

type DependencyJob = {
  id: string;
  dependency_id: string | null;
  kind: string;
  status: string;
  phase: string | null;
  actor: string;
  target_version: string | null;
  backup_id: string | null;
  stdout_log_path: string | null;
  started_at: string | null;
  ended_at: string | null;
  result: Record<string, unknown>;
  error: string | null;
  trace_id: string | null;
};

type DependencyCheckAllResult = {
  ok: boolean;
  checked: number;
  failed: number;
  updates: number;
  direct_only: boolean;
  errors: Array<{ dependency_id: string; error: string }>;
  packages?: DependencyPackage[];
};

type DependencyStorageStatus = {
  mode: "local" | "nfs" | "samba" | string;
  mount_source: string;
  mount_options: string;
  config_status: "active" | "pending_reboot" | "error" | string;
  backup_root: string;
  exists: boolean;
  writable: boolean;
  free_bytes: number;
  min_free_bytes: number;
  retention_days?: string;
  ok: boolean;
  detail: string;
};

type DependencyJobEvent = {
  type: string;
  job_id?: string;
  created_at?: string;
  phase?: string;
  message?: string;
  diagnosis?: DependencyFailureDiagnosis;
  result?: Record<string, unknown>;
};

const DEPENDENCY_JOB_EVENT_LIMIT = 200;
const DEPENDENCY_JOB_MESSAGE_LIMIT = 2000;

function compactDependencyJobEvent(event: DependencyJobEvent): DependencyJobEvent {
  if (typeof event.message !== "string" || event.message.length <= DEPENDENCY_JOB_MESSAGE_LIMIT) {
    return event;
  }
  return {
    ...event,
    message: `${event.message.slice(0, DEPENDENCY_JOB_MESSAGE_LIMIT)}... [truncated]`
  };
}

type DependencyFailureDiagnosis = {
  category: string;
  title: string;
  summary: string;
  safe_state: string;
  retry_recommendation: string;
  actions: string[];
  affected_packages: string[];
  command?: string;
  technical_detail?: string;
};

type DependencyConfirmAction =
  | { kind: "apply" }
  | { kind: "restore"; backup: DependencyBackup };

type UserAccount = {
  id: string;
  username: string;
  first_name: string;
  last_name: string;
  full_name: string;
  profile_photo_data_url: string | null;
  email: string | null;
  role: UserRole;
  is_active: boolean;
  last_login_at: string | null;
  person_id: string | null;
  preferences: ProfilePreferences & Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type AuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user: UserAccount | null;
};

type ChatAttachment = {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  kind: "image" | "text" | "document" | string;
  url: string;
  download_url?: string | null;
  source?: string | null;
  created_at?: string | null;
};

type ChatAttachmentDraft = ChatAttachment & {
  uploadState: "uploading" | "ready" | "error";
  preview_url?: string;
  error?: string;
};

type ChatConfirmationAction = {
  type: string;
  confirmationId?: string;
  toolName: string;
  toolArguments: Record<string, unknown>;
  target: string;
  displayTarget: string;
  command: string;
  title: string;
  description: string;
  buttonLabel: string;
  pendingLabel: string;
  statusLabel: string;
  userEcho: string;
  sent?: boolean;
};

type ChatToolActivity = {
  id: string;
  batchId?: string;
  tool: string;
  label: string;
  status: "queued" | "running" | "succeeded" | "failed" | "requires_confirmation";
};

type ChatMessageItem = {
  id: string;
  role: "user" | "assistant";
  text: string;
  attachments?: ChatAttachment[];
  confirmationAction?: ChatConfirmationAction | null;
  streaming?: boolean;
};

type ChatCopyMenu = {
  messageId: string;
  text: string;
  x: number;
  y: number;
};

type ThemeMode = "system" | "light" | "dark";
type ProfilePreferences = {
  sidebarCollapsed: boolean;
};
type ViewKey =
  | "dashboard"
  | "people"
  | "groups"
  | "schedules"
  | "passes"
  | "vehicles"
  | "top_charts"
  | "events"
  | "alerts"
  | "reports"
  | "integrations"
  | "logs"
  | "settings"
  | "settings_general"
  | "settings_auth"
  | "settings_automations"
  | "settings_notifications"
  | "settings_lpr"
  | "users";

type NavigateOptions = {
  replace?: boolean;
  search?: string;
  hash?: string;
};

type NavigateToView = (nextView: ViewKey, options?: NavigateOptions) => void;

const primaryNavItems: Array<{ key: Exclude<ViewKey, "users">; label: string; icon: React.ElementType }> = [
  { key: "dashboard", label: "Dashboard", icon: Home },
  { key: "people", label: "People", icon: UserRound },
  { key: "groups", label: "Groups", icon: Users },
  { key: "schedules", label: "Schedules", icon: Clock3 },
  { key: "passes", label: "Passes", icon: ClipboardPaste },
  { key: "vehicles", label: "Vehicles", icon: Car },
  { key: "top_charts", label: "Top Charts", icon: Trophy },
  { key: "events", label: "Events", icon: CalendarDays },
  { key: "alerts", label: "Alerts", icon: Bell },
  { key: "reports", label: "Reports", icon: BarChart3 },
  { key: "integrations", label: "API & Integrations", icon: PlugZap },
  { key: "logs", label: "Logs", icon: FileText },
  { key: "settings", label: "Settings", icon: Settings }
];

const settingsNavItems: Array<{ key: ViewKey; label: string; icon: React.ElementType }> = [
  { key: "settings_general", label: "General", icon: SlidersHorizontal },
  { key: "settings_auth", label: "Auth & Security", icon: Lock },
  { key: "settings_automations", label: "Automations", icon: GitBranch },
  { key: "settings_notifications", label: "Notifications", icon: Bell },
  { key: "settings_lpr", label: "LPR Tuning", icon: Gauge },
  { key: "users", label: "Users", icon: Users }
];

const viewPaths: Record<ViewKey, string> = {
  dashboard: "/",
  people: "/people",
  groups: "/groups",
  schedules: "/schedules",
  passes: "/passes",
  vehicles: "/vehicles",
  top_charts: "/top-charts",
  events: "/events",
  alerts: "/alerts",
  reports: "/reports",
  integrations: "/integrations",
  logs: "/logs",
  settings: "/settings",
  settings_general: "/settings/general",
  settings_auth: "/settings/auth-security",
  settings_automations: "/settings/automations",
  settings_notifications: "/settings/notifications",
  settings_lpr: "/settings/lpr-tuning",
  users: "/settings/users"
};

const pathViews = Object.entries(viewPaths).reduce<Record<string, ViewKey>>((acc, [viewKey, path]) => {
  acc[path] = viewKey as ViewKey;
  return acc;
}, {});

const groupCategoryOptions = [
  { value: "family", label: "Family" },
  { value: "friends", label: "Friends" },
  { value: "visitors", label: "Visitors" },
  { value: "contractors", label: "Contractors" }
] as const;

function isViewKey(value: string | null): value is ViewKey {
  return Boolean(value && Object.prototype.hasOwnProperty.call(viewPaths, value));
}

function viewFromPath(pathname: string): ViewKey | null {
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return pathViews[normalized] ?? null;
}

function isLprTimingTestPath(pathname: string) {
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return normalized === "/lpr-timing-test";
}

function initialViewFromLocation(): ViewKey {
  const routeView = viewFromPath(window.location.pathname);
  if (routeView) return routeView;
  const storedView = localStorage.getItem("iacs-active-view");
  return isViewKey(storedView) ? storedView : "dashboard";
}

const api = {
  async get<T>(path: string): Promise<T> {
    const response = await fetch(path, { credentials: "include" });
    if (!response.ok) throw await apiError(response);
    return response.json() as Promise<T>;
  },
  async post<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return response.json() as Promise<T>;
  },
  async patch<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return response.json() as Promise<T>;
  },
  async delete<T = void>(path: string): Promise<T> {
    const response = await fetch(path, { method: "DELETE", credentials: "include" });
    if (!response.ok) throw await apiError(response);
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }
};

async function apiError(response: Response) {
  const statusLabel = `${response.status} ${response.statusText || "Request failed"}`;
  let detail: string | null = null;
  const body = await response.text().catch(() => "");

  if (body.trim()) {
    try {
      const payload = JSON.parse(body) as unknown;
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        const record = payload as Record<string, unknown>;
        detail =
          describeApiErrorDetail(record.detail) ||
          describeApiErrorDetail(record.message) ||
          describeApiErrorDetail(record.error);
      } else {
        detail = describeApiErrorDetail(payload);
      }
    } catch {
      detail = body.trim();
    }
  }

  return new Error(detail && detail !== statusLabel ? `${statusLabel}: ${detail}` : statusLabel);
}

function describeApiErrorDetail(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value.map(describeApiErrorDetail).filter((part): part is string => Boolean(part));
    return parts.length ? parts.join("; ") : null;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const message =
      describeApiErrorDetail(record.msg) ||
      describeApiErrorDetail(record.message) ||
      describeApiErrorDetail(record.detail);
    const location = Array.isArray(record.loc)
      ? record.loc.filter(Boolean).join(".")
      : typeof record.loc === "string"
        ? record.loc
        : null;
    if (message && location) return `${location}: ${message}`;
    if (message) return message;
    return JSON.stringify(record);
  }
  return null;
}

function wsUrl(path: string) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${path}`;
}

async function uploadChatAttachment(file: File, sessionId: string | null): Promise<ChatAttachment> {
  const body = new FormData();
  body.append("file", file);
  const suffix = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await fetch(`/api/v1/ai/chat/upload${suffix}`, {
    method: "POST",
    credentials: "include",
    body
  });
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<ChatAttachment>;
}

function clientId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatFileSize(size: number) {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${size} B`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function formatDeviceTargetName(value: string) {
  return value
    .replace(/\*\*/g, "")
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((part) => part ? part[0].toUpperCase() + part.slice(1) : part)
    .join(" ");
}

function cleanChatText(text: string, attachments: ChatAttachment[] = []) {
  const fileLinkReplacement = attachments.length
    ? (attachments.some((attachment) => attachment.kind === "image") ? "the snapshot" : "the attached file")
    : "$1";
  let cleaned = text
    .replace(/\[([^\]]+)\]\((\/api\/v1\/ai\/chat\/files\/[^)]+)\)/g, fileLinkReplacement)
    .replace(/\s*\/api\/v1\/ai\/chat\/files\/[A-Za-z0-9_-]+\b/g, "")
    .replace(/\*\*\*([^*]+)\*\*\*/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*\*/g, "")
    .replace(/\bHome Assistant cover entity ID\b/gi, "device name")
    .replace(/\bHome Assistant entity ID\b/gi, "device name")
    .replace(/\bHome Assistant\b/gi, "the system")
    .replace(/\bcover entity ID\b/gi, "device name")
    .replace(/\bentity ID\b/gi, "device name")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  attachments.forEach((attachment) => {
    if (attachment.source === "system_media" && attachment.filename) {
      cleaned = cleaned.replaceAll(attachment.filename, "the snapshot");
    }
  });
  if (!cleaned && attachments.some((attachment) => attachment.kind === "image")) {
    cleaned = "Here's the latest snapshot.";
  }
  return cleaned;
}

async function copyToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function chatPendingAction(pendingAction: unknown): ChatConfirmationAction | null {
  if (!isRecord(pendingAction)) return null;
  const confirmationId = String(pendingAction.confirmation_id || "").trim();
  const toolName = String(pendingAction.tool_name || "").trim();
  if (!confirmationId || !toolName) return null;
  const target = String(pendingAction.target || toolName.replace(/_/g, " ")).trim();
  const title = String(pendingAction.title || `Confirm ${target}?`);
  const description = String(pendingAction.description || "This action needs confirmation before Alfred continues.");
  const buttonLabel = String(pendingAction.confirm_label || "Confirm");
  return {
    type: toolName,
    confirmationId,
    toolName,
    toolArguments: {},
    target,
    displayTarget: target,
    command: `confirm ${target}`,
    title,
    description,
    buttonLabel,
    pendingLabel: "Confirmed",
    statusLabel: `${buttonLabel} ${target}...`,
    userEcho: `Confirmed: ${target}`
  };
}

function chatConfirmationAction(toolResults: unknown): ChatConfirmationAction | null {
  if (!Array.isArray(toolResults)) return null;
  const result = [...toolResults].reverse().find((item) => isRecord(item) && isRecord(item.output));
  if (!isRecord(result) || !isRecord(result.output) || result.output.requires_confirmation !== true) return null;
  const args = isRecord(result.arguments) ? result.arguments : {};
  const toolName = String(result.name || "").trim();
  const confirmationField = String(result.output.confirmation_field || (toolName === "test_notification_workflow" ? "confirm_send" : "confirm"));
  const toolArguments = { ...args, [confirmationField]: true };
  if (result.name === "open_device") {
    const target = String(result.output.target || args.target || args.entity_id || "").trim();
    if (!target) return null;
    const displayTarget = formatDeviceTargetName(target);
    return {
      type: "open_device",
      toolName,
      toolArguments,
      target,
      displayTarget,
      command: `confirm open ${target}`,
      title: `Open ${displayTarget}?`,
      description: "This will be logged as an Alfred action.",
      buttonLabel: "Confirm",
      pendingLabel: "Confirmed",
      statusLabel: `Opening ${displayTarget}...`,
      userEcho: `Confirmed: open ${displayTarget}`
    };
  }
  if (result.name === "update_schedule") {
    const target = String(result.output.schedule_name || args.schedule_name || args.name || "").trim();
    if (!target) return null;
    const summary = typeof result.output.summary === "string" ? result.output.summary : "the requested times";
    return {
      type: "update_schedule",
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `confirm update ${target} schedule`,
      title: `Update ${target}?`,
      description: `Replace the existing allowed times with ${summary}.`,
      buttonLabel: "Update schedule",
      pendingLabel: "Update confirmed",
      statusLabel: `Updating ${target}...`,
      userEcho: `Confirmed: update ${target}`
    };
  }
  if (result.name === "delete_schedule") {
    const schedule = isRecord(result.output.schedule) ? result.output.schedule : {};
    const target = String(result.output.schedule_name || schedule.name || args.schedule_name || args.name || "").trim();
    if (!target) return null;
    return {
      type: "delete_schedule",
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `confirm delete ${target} schedule`,
      title: `Delete ${target}?`,
      description: String(result.output.detail || "This schedule will be permanently deleted."),
      buttonLabel: "Delete schedule",
      pendingLabel: "Delete confirmed",
      statusLabel: `Deleting ${target}...`,
      userEcho: `Confirmed: delete ${target}`
    };
  }
  if ([
    "create_notification_workflow",
    "update_notification_workflow",
    "delete_notification_workflow",
    "test_notification_workflow"
  ].includes(toolName)) {
    const target = String(result.output.workflow_name || args.rule_name || args.name || "notification workflow").trim();
    const actionVerb = toolName === "create_notification_workflow"
      ? "Create"
      : toolName === "update_notification_workflow"
        ? "Update"
        : toolName === "delete_notification_workflow"
          ? "Delete"
          : "Send test for";
    return {
      type: toolName,
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `${actionVerb.toLowerCase()} ${target}`,
      title: `${actionVerb} ${target}?`,
      description: String(result.output.detail || "This changes notification workflow behaviour."),
      buttonLabel: toolName === "test_notification_workflow" ? "Send test" : actionVerb,
      pendingLabel: "Confirmed",
      statusLabel: `${actionVerb} ${target}...`,
      userEcho: `Confirmed: ${actionVerb.toLowerCase()} ${target}`
    };
  }
  if (toolName) {
    const target = String(result.output.target || result.output.schedule_name || result.output.workflow_name || args.target || args.schedule_name || args.name || toolName.replace(/_/g, " ")).trim();
    return {
      type: toolName,
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `confirm ${toolName}`,
      title: `Confirm ${target}?`,
      description: String(result.output.detail || "This action needs confirmation before Alfred continues."),
      buttonLabel: "Confirm",
      pendingLabel: "Confirmed",
      statusLabel: `Confirming ${target}...`,
      userEcho: `Confirmed: ${target}`
    };
  }
  return null;
}

function applyMaintenanceRealtimeEvent(
  event: RealtimeMessage,
  setMaintenanceStatus: React.Dispatch<React.SetStateAction<MaintenanceStatus | null>>
) {
  if (event.type !== "maintenance_mode.changed") return false;
  const status = maintenanceStatusFromPayload(event.payload);
  if (!status) return false;
  setMaintenanceStatus(status);
  return true;
}

function maintenanceStatusFromPayload(payload: Record<string, unknown>): MaintenanceStatus | null {
  const candidate = isRecord(payload.status) ? payload.status : payload;
  if (typeof candidate.is_active !== "boolean") return null;
  return {
    is_active: candidate.is_active,
    enabled_by: nullableString(candidate.enabled_by),
    enabled_at: nullableString(candidate.enabled_at),
    source: nullableString(candidate.source),
    reason: nullableString(candidate.reason),
    duration_seconds: numberPayload(candidate.duration_seconds),
    duration_label: nullableString(candidate.duration_label),
    ha_entity_id: nullableString(candidate.ha_entity_id) ?? undefined
  };
}

function auditLogFromRealtimePayload(payload: Record<string, unknown>): AuditLog | null {
  const candidate = isRecord(payload.log) ? payload.log : payload;
  if (
    typeof candidate.id !== "string" ||
    typeof candidate.timestamp !== "string" ||
    typeof candidate.category !== "string" ||
    typeof candidate.action !== "string" ||
    typeof candidate.actor !== "string" ||
    typeof candidate.outcome !== "string" ||
    typeof candidate.level !== "string"
  ) {
    return null;
  }
  return {
    id: candidate.id,
    timestamp: candidate.timestamp,
    category: candidate.category,
    action: candidate.action,
    actor: candidate.actor,
    actor_user_id: nullableString(candidate.actor_user_id),
    target_entity: nullableString(candidate.target_entity),
    target_id: nullableString(candidate.target_id),
    target_label: nullableString(candidate.target_label),
    diff: isRecord(candidate.diff) ? candidate.diff : {},
    metadata: isRecord(candidate.metadata) ? candidate.metadata : {},
    outcome: candidate.outcome,
    level: candidate.level,
    trace_id: nullableString(candidate.trace_id),
    request_id: nullableString(candidate.request_id)
  };
}

function applyIntegrationRealtimeEvent(
  event: RealtimeMessage,
  setIntegrationStatus: React.Dispatch<React.SetStateAction<IntegrationStatus | null>>
) {
  if (event.type === "gate.state_changed") {
    const state = stringPayload(event.payload.state);
    const entityId = stringPayload(event.payload.entity_id);
    if (!state) return false;
    setIntegrationStatus((current) => {
      if (!current) return current;
      const gate_entities = updateManagedCoverState(current.gate_entities, entityId, state);
      return { ...current, gate_entities, current_gate_state: state, last_gate_state: state };
    });
    return true;
  }

  if (event.type === "door.state_changed") {
    const door = stringPayload(event.payload.door);
    const entityId = stringPayload(event.payload.entity_id);
    const state = stringPayload(event.payload.state);
    const stateKey = doorStateKey(door);
    if (!state || (!stateKey && door !== "garage_door")) return false;
    setIntegrationStatus((current) => {
      if (!current) return current;
      if (door === "garage_door") {
        return { ...current, garage_door_entities: updateManagedCoverState(current.garage_door_entities, entityId, state) };
      }
      return { ...current, [stateKey]: state };
    });
    return true;
  }

  return false;
}

function accessEventFromRealtime(event: RealtimeMessage): AccessEvent | null {
  if (event.type !== "access_event.finalized") return null;
  const eventId = stringPayload(event.payload.event_id);
  const registrationNumber = stringPayload(event.payload.registration_number);
  const direction = stringPayload(event.payload.direction);
  const decision = stringPayload(event.payload.decision);
  const source = stringPayload(event.payload.source);
  const occurredAt = stringPayload(event.payload.occurred_at);
  const timingClassification = stringPayload(event.payload.timing_classification);
  if (!eventId || !registrationNumber || !isAccessDirection(direction) || !isAccessDecision(decision) || !source || !occurredAt) {
    return null;
  }
  return {
    id: eventId,
    registration_number: registrationNumber,
    direction,
    decision,
    confidence: numberPayload(event.payload.confidence),
    source,
    occurred_at: occurredAt,
    timing_classification: timingClassification || "unknown",
    anomaly_count: numberPayload(event.payload.anomaly_count),
    visitor_pass_id: stringPayload(event.payload.visitor_pass_id) || null,
    visitor_name: stringPayload(event.payload.visitor_name) || null,
    visitor_pass_mode: stringPayload(event.payload.visitor_pass_mode) || null
  };
}

function notificationToastFromRealtime(event: RealtimeMessage): NotificationToast | null {
  if (event.type !== "notification.in_app") return null;
  const title = stringPayload(event.payload.title);
  const body = stringPayload(event.payload.body);
  const eventType = stringPayload(event.payload.event_type);
  const severity = stringPayload(event.payload.severity);
  const snapshot = event.payload.snapshot;
  let snapshotUrl = "";
  if (snapshot && typeof snapshot === "object" && "image_url" in snapshot) {
    snapshotUrl = stringPayload((snapshot as Record<string, unknown>).image_url);
  }
  if (!title && !body) return null;
  return {
    id: `${event.created_at ?? Date.now()}-${eventType}-${Math.random().toString(16).slice(2)}`,
    title: title || titleCase(eventType),
    body,
    event_type: eventType,
    severity: isNotificationSeverity(severity) ? severity : "info",
    snapshot_url: snapshotUrl || undefined,
  };
}

function shouldRefreshDataForRealtimeEvent(event: RealtimeMessage) {
  return REALTIME_DATA_REFRESH_EVENTS.has(event.type);
}

function isNotificationSeverity(value: string): value is NotificationToast["severity"] {
  return ["info", "warning", "critical"].includes(value);
}

function isAccessDirection(value: string): value is AccessEvent["direction"] {
  return ["entry", "exit", "denied"].includes(value);
}

function isAccessDecision(value: string): value is AccessEvent["decision"] {
  return ["granted", "denied"].includes(value);
}

function stringPayload(value: unknown) {
  return typeof value === "string" ? value : "";
}

function nullableString(value: unknown) {
  return typeof value === "string" && value ? value : null;
}

function numberPayload(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function doorStateKey(door: string) {
  const keys: Record<string, keyof IntegrationStatus> = {
    back_door: "back_door_state",
    front_door: "front_door_state",
    main_garage_door: "main_garage_door_state",
    mums_garage_door: "mums_garage_door_state"
  };
  return keys[door] ?? null;
}

function updateManagedCoverState(entities: HomeAssistantManagedCover[] | undefined, entityId: string, state: string) {
  if (!entities?.length || !entityId) return entities;
  return entities.map((entity) => entity.entity_id === entityId ? { ...entity, state } : entity);
}

function App() {
  const [view, setView] = React.useState<ViewKey>(() => initialViewFromLocation());
  const [lprTimingTestActive, setLprTimingTestActive] = React.useState(() => isLprTimingTestPath(window.location.pathname));
  const [theme, setTheme] = useTheme();
  const [authStatus, setAuthStatus] = React.useState<AuthStatus | null>(null);
  const currentUser = authStatus?.user ?? null;
  const [profilePreferences, setProfilePreferences] = useProfilePreferences(currentUser);
  const [presence, setPresence] = React.useState<Presence[]>([]);
  const [events, setEvents] = React.useState<AccessEvent[]>([]);
  const [anomalies, setAnomalies] = React.useState<Anomaly[]>([]);
  const [people, setPeople] = React.useState<Person[]>([]);
  const [vehicles, setVehicles] = React.useState<Vehicle[]>([]);
  const [groups, setGroups] = React.useState<Group[]>([]);
  const [schedules, setSchedules] = React.useState<Schedule[]>([]);
  const [integrationStatus, setIntegrationStatus] = React.useState<IntegrationStatus | null>(null);
  const [maintenanceStatus, setMaintenanceStatus] = React.useState<MaintenanceStatus | null>(null);
  const [realtime, setRealtime] = React.useState<RealtimeMessage[]>([]);
  const [notificationToasts, setNotificationToasts] = React.useState<NotificationToast[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [dashboardRefreshing, setDashboardRefreshing] = React.useState(false);
  const [search, setSearch] = React.useState("");
  const [settingsExpanded, setSettingsExpanded] = React.useState(false);
  const [alertsOpen, setAlertsOpen] = React.useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = React.useState(false);
  const [loggingOut, setLoggingOut] = React.useState(false);
  const [isMobileNavigation, setIsMobileNavigation] = React.useState(() =>
    typeof window !== "undefined" ? window.matchMedia("(max-width: 720px)").matches : false
  );
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const sidebarRef = React.useRef<HTMLElement | null>(null);
  const alertsButtonRef = React.useRef<HTMLButtonElement | null>(null);
  const alertsTrayRef = React.useRef<HTMLDivElement | null>(null);
  const profileMenuRef = React.useRef<HTMLDivElement | null>(null);
  const profileButtonRef = React.useRef<HTMLButtonElement | null>(null);

  const navigateToView = React.useCallback<NavigateToView>((nextView, options) => {
    setLprTimingTestActive(false);
    setView(nextView);
    localStorage.setItem("iacs-active-view", nextView);
    const nextPath = `${viewPaths[nextView]}${options?.search ?? ""}${options?.hash ?? ""}`;
    const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (currentPath !== nextPath) {
      if (options?.replace) {
        window.history.replaceState({ view: nextView }, "", nextPath);
      } else {
        window.history.pushState({ view: nextView }, "", nextPath);
      }
    }
  }, []);

  React.useEffect(() => {
    const onPopState = () => {
      setLprTimingTestActive(isLprTimingTestPath(window.location.pathname));
      const nextView = viewFromPath(window.location.pathname);
      if (nextView) {
        setView(nextView);
        localStorage.setItem("iacs-active-view", nextView);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const refreshAuth = React.useCallback(async () => {
    const status = await api.get<AuthStatus>("/api/v1/auth/status");
    setAuthStatus(status);
  }, []);

  React.useEffect(() => {
    refreshAuth().catch(() => setAuthStatus({ setup_required: false, authenticated: false, user: null }));
  }, [refreshAuth]);

  const refresh = React.useCallback(async () => {
    const [nextPresence, nextEvents, nextAnomalies, nextPeople, nextVehicles, nextGroups, nextSchedules, nextStatus, nextMaintenanceStatus] =
      await Promise.all([
        api.get<Presence[]>("/api/v1/presence"),
        api.get<AccessEvent[]>("/api/v1/events?limit=40"),
        api.get<Anomaly[]>("/api/v1/alerts?status=open&limit=100"),
        api.get<Person[]>("/api/v1/people"),
        api.get<Vehicle[]>("/api/v1/vehicles"),
        api.get<Group[]>("/api/v1/groups"),
        api.get<Schedule[]>("/api/v1/schedules"),
        api.get<IntegrationStatus>("/api/v1/integrations/home-assistant/status"),
        api.get<MaintenanceStatus>("/api/v1/maintenance/status")
      ]);
    setPresence(nextPresence);
    setEvents(nextEvents);
    setAnomalies(nextAnomalies);
    setPeople(nextPeople);
    setVehicles(nextVehicles);
    setGroups(nextGroups);
    setSchedules(nextSchedules);
    setIntegrationStatus(nextStatus);
    setMaintenanceStatus(nextMaintenanceStatus);
    setLoading(false);
  }, []);

  const refreshIntegrationStatus = React.useCallback(async () => {
    setIntegrationStatus(await api.get<IntegrationStatus>("/api/v1/integrations/home-assistant/status"));
  }, []);

  const refreshDashboard = React.useCallback(async () => {
    setDashboardRefreshing(true);
    try {
      await refresh();
    } finally {
      setDashboardRefreshing(false);
    }
  }, [refresh]);

  const realtimeRefreshLastRunRef = React.useRef(0);

  const refreshFromRealtime = React.useCallback(() => {
    const now = Date.now();
    if (now - realtimeRefreshLastRunRef.current < REALTIME_REFRESH_MIN_INTERVAL_MS) return;
    realtimeRefreshLastRunRef.current = now;
    refresh().catch(() => undefined);
  }, [refresh]);

  React.useEffect(() => {
    if (!authStatus?.authenticated || lprTimingTestActive) return;
    refresh().catch(() => setLoading(false));
  }, [authStatus?.authenticated, lprTimingTestActive, refresh]);

  React.useEffect(() => {
    if (!authStatus?.authenticated || lprTimingTestActive) return;
    const timer = window.setInterval(() => {
      refreshIntegrationStatus().catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [authStatus?.authenticated, lprTimingTestActive, refreshIntegrationStatus]);

  React.useEffect(() => {
    if (!authStatus?.authenticated) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let stopped = false;

    const handleMessage = (event: MessageEvent) => {
      const parsed = JSON.parse(event.data) as RealtimeMessage;
      setRealtime((current) => [parsed, ...current].slice(0, 80));
      const notificationToast = notificationToastFromRealtime(parsed);
      if (notificationToast) {
        setNotificationToasts((current) => [notificationToast, ...current].slice(0, 4));
        return;
      }
      if (applyMaintenanceRealtimeEvent(parsed, setMaintenanceStatus)) {
        return;
      }
      if (applyIntegrationRealtimeEvent(parsed, setIntegrationStatus)) {
        return;
      }
      if (parsed.type.startsWith("telemetry.") || parsed.type.startsWith("audit.")) {
        return;
      }
      const finalizedEvent = accessEventFromRealtime(parsed);
      if (finalizedEvent) {
        setEvents((current) => [finalizedEvent, ...current.filter((item) => item.id !== finalizedEvent.id)].slice(0, 40));
        refreshFromRealtime();
        return;
      }
      if (shouldRefreshDataForRealtimeEvent(parsed)) {
        refreshFromRealtime();
      }
    };

    const scheduleReconnect = () => {
      if (stopped || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        openSocket();
      }, 1500);
    };

    const openSocket = () => {
      if (stopped) return;
      const nextSocket = new WebSocket(wsUrl("/api/v1/realtime/ws"));
      socket = nextSocket;
      nextSocket.onmessage = handleMessage;
      nextSocket.onclose = scheduleReconnect;
      nextSocket.onerror = () => nextSocket.close();
    };

    openSocket();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, [authStatus?.authenticated, refreshFromRealtime]);

  React.useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const syncMobileNavigation = () => {
      setIsMobileNavigation(media.matches);
      if (!media.matches) {
        setMobileNavOpen(false);
      }
    };
    syncMobileNavigation();
    media.addEventListener("change", syncMobileNavigation);
    return () => media.removeEventListener("change", syncMobileNavigation);
  }, []);

  React.useEffect(() => {
    if (!mobileNavOpen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileNavOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileNavOpen]);

  React.useEffect(() => {
    if (mobileNavOpen) {
      sidebarRef.current?.scrollTo({ top: 0 });
    }
  }, [mobileNavOpen]);

  React.useEffect(() => {
    if (!authStatus) return;
    if (authStatus.setup_required && window.location.pathname !== "/setup") {
      window.history.replaceState({}, "", "/setup");
    }
    if (!authStatus.setup_required && !authStatus.authenticated && window.location.pathname !== "/login") {
      window.history.replaceState({}, "", "/login");
    }
    if (!authStatus.setup_required && authStatus.authenticated && ["/setup", "/login"].includes(window.location.pathname)) {
      navigateToView(view, { replace: true });
    }
  }, [authStatus, navigateToView, view]);

  const sidebarCollapsed = profilePreferences.sidebarCollapsed;
  const navigationCollapsed = !isMobileNavigation && sidebarCollapsed;
  const navigationExpanded = isMobileNavigation ? mobileNavOpen : !sidebarCollapsed;
  const settingsActive = view === "settings" || view.startsWith("settings_") || view === "users";
  const bellAlerts = React.useMemo(() => anomalies.filter(isBellAlert), [anomalies]);

  const navigateFromNav = React.useCallback((nextView: ViewKey) => {
    navigateToView(nextView);
    if (isMobileNavigation) {
      setMobileNavOpen(false);
    }
  }, [isMobileNavigation, navigateToView]);

  const toggleNavigation = React.useCallback(() => {
    if (isMobileNavigation) {
      setMobileNavOpen((current) => !current);
      return;
    }
    setProfilePreferences({ sidebarCollapsed: !sidebarCollapsed });
  }, [isMobileNavigation, setProfilePreferences, sidebarCollapsed]);

  const handleLogout = React.useCallback(async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    setProfileMenuOpen(false);
    try {
      await api.post<{ status: string }>("/api/v1/auth/logout");
      setAuthStatus({ setup_required: false, authenticated: false, user: null });
      setPresence([]);
      setEvents([]);
      setAnomalies([]);
      setPeople([]);
      setVehicles([]);
      setGroups([]);
      setSchedules([]);
      setIntegrationStatus(null);
      setMaintenanceStatus(null);
      setRealtime([]);
      setNotificationToasts([]);
      setLoading(true);
      setMobileNavOpen(false);
      window.history.replaceState({}, "", "/login");
    } catch (logoutError) {
      window.alert(logoutError instanceof Error ? logoutError.message : "Unable to log out. Please try again.");
    } finally {
      setLoggingOut(false);
    }
  }, [loggingOut]);

  React.useEffect(() => {
    if (settingsActive && !navigationCollapsed) {
      setSettingsExpanded(true);
    }
  }, [settingsActive, navigationCollapsed]);

  React.useEffect(() => {
    if (!alertsOpen) return undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAlertsOpen(false);
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (alertsTrayRef.current?.contains(target) || alertsButtonRef.current?.contains(target)) return;
      setAlertsOpen(false);
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onPointerDown);
    };
  }, [alertsOpen]);

  React.useEffect(() => {
    if (!profileMenuOpen) return undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setProfileMenuOpen(false);
        profileButtonRef.current?.focus();
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (profileMenuRef.current?.contains(target) || profileButtonRef.current?.contains(target)) return;
      setProfileMenuOpen(false);
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onPointerDown);
    };
  }, [profileMenuOpen]);

  if (!authStatus) {
    return <AuthLoading />;
  }

  if (authStatus.setup_required) {
    return <SetupPage onComplete={(user) => setAuthStatus({ setup_required: false, authenticated: true, user })} />;
  }

  if (!authStatus.authenticated || !currentUser) {
    return <LoginPage onLogin={(user) => setAuthStatus({ setup_required: false, authenticated: true, user })} />;
  }

  if (lprTimingTestActive) {
    return <LprTimingTestPage realtime={realtime} currentUser={currentUser} theme={theme} setTheme={setTheme} />;
  }

  return (
    <div className={`${navigationCollapsed ? "app-shell sidebar-collapsed" : "app-shell"}${mobileNavOpen ? " mobile-nav-open" : ""}`}>
      <aside className="sidebar" id="site-sidebar" aria-hidden={isMobileNavigation && !mobileNavOpen} ref={sidebarRef}>
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={28} />
          </div>
          <div className="brand-copy">
            <strong>Intelligent</strong>
            <span>Access Control</span>
          </div>
          <button className="icon-button sidebar-close-button" onClick={() => setMobileNavOpen(false)} type="button" aria-label="Close navigation">
            <X size={16} />
          </button>
        </div>
        <nav className="nav-list" aria-label="Main navigation">
          {primaryNavItems.map((item) => {
            const Icon = item.icon;
            if (item.key === "settings") {
              return (
                <div className="nav-group" key={item.key}>
                  <button
                    className={settingsActive ? "nav-item active" : "nav-item"}
                    onClick={() => {
                      navigateToView("settings");
                      setSettingsExpanded((current) => !current);
                    }}
                    type="button"
                    title={navigationCollapsed ? item.label : undefined}
                    aria-expanded={settingsExpanded && !navigationCollapsed}
                  >
                    <Icon size={18} />
                    <span>{item.label}</span>
                    <ChevronDown className="nav-chevron" size={15} />
                  </button>
                  {settingsExpanded && !navigationCollapsed ? (
                    <div className="nav-submenu">
                      {settingsNavItems.map((subItem) => {
                        const SubIcon = subItem.icon;
                        return (
                          <button
                            className={subItem.key === view ? "nav-item nested active" : "nav-item nested"}
                            key={subItem.key}
                            onClick={() => navigateFromNav(subItem.key)}
                            type="button"
                          >
                            <SubIcon size={16} />
                            <span>{subItem.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            }
            return (
              <button
                key={item.key}
                className={item.key === view ? "nav-item active" : "nav-item"}
                onClick={() => navigateFromNav(item.key)}
                type="button"
                title={navigationCollapsed ? item.label : undefined}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="profile-menu-shell">
            <button
              aria-controls="profile-menu"
              aria-expanded={profileMenuOpen}
              aria-haspopup="menu"
              className="profile-switcher"
              onClick={() => setProfileMenuOpen((current) => !current)}
              ref={profileButtonRef}
              title={navigationCollapsed ? displayUserName(currentUser) : undefined}
              type="button"
            >
              <UserAvatar user={currentUser} />
              <span>
                <strong>{displayUserName(currentUser)}</strong>
                <small>{currentUser.role === "admin" ? "Owner" : "Standard User"}</small>
              </span>
              <ChevronDown size={16} />
            </button>
            {profileMenuOpen ? (
              <div className="profile-menu" id="profile-menu" ref={profileMenuRef} role="menu">
                <button
                  className="profile-menu-item danger"
                  disabled={loggingOut}
                  onClick={handleLogout}
                  role="menuitem"
                  type="button"
                >
                  {loggingOut ? <Loader2 className="spin" size={16} /> : <LogOut size={16} />}
                  <span>{loggingOut ? "Logging out..." : "Logout"}</span>
                </button>
              </div>
            ) : null}
          </div>
          <div className="sidebar-status">
            <span className="dot live" />
            <span>Online</span>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="icon-button topbar-menu"
              type="button"
              aria-controls="site-sidebar"
              aria-expanded={navigationExpanded}
              aria-label={navigationExpanded ? "Collapse navigation sidebar" : "Expand navigation sidebar"}
              onClick={toggleNavigation}
            >
              <Menu size={20} />
            </button>
            <button className="estate-select" type="button" aria-label="Current site">
              <span>Crest House - Main Gate</span>
            </button>
          </div>
          <div className="topbar-actions">
            <label className="search">
              <Search size={16} />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search Anything..." />
            </label>
            <div className="alert-tray-shell">
              <button
                aria-controls="alert-tray"
                aria-expanded={alertsOpen}
                aria-haspopup="dialog"
                aria-label="Open alerts"
                className="icon-button notification-button"
                onClick={() => setAlertsOpen((current) => !current)}
                ref={alertsButtonRef}
                type="button"
              >
                <Bell size={20} />
                {bellAlerts.length ? <span>{Math.min(bellAlerts.length, 99)}</span> : null}
              </button>
              {alertsOpen ? (
                <AlertTray
                  anomalies={bellAlerts}
                  onRefresh={refresh}
                  onViewAll={() => {
                    setAlertsOpen(false);
                    navigateToView("alerts");
                  }}
                  ref={alertsTrayRef}
                />
              ) : null}
            </div>
            <button className="icon-button refresh-button" onClick={() => refreshDashboard().catch(() => undefined)} type="button" aria-label="Refresh" disabled={dashboardRefreshing}>
              <RefreshCcw className={dashboardRefreshing ? "spin" : undefined} size={17} />
            </button>
            <ThemeControl theme={theme} setTheme={setTheme} />
          </div>
        </header>

        {loading ? (
          <div className="loading-panel">Loading live site data</div>
        ) : (
          <View
            view={view}
            search={search}
            presence={presence}
            events={events}
            anomalies={anomalies}
            people={people}
            vehicles={vehicles}
            groups={groups}
            schedules={schedules}
            integrationStatus={integrationStatus}
            maintenanceStatus={maintenanceStatus}
            realtime={realtime}
            onClearRealtime={() => setRealtime([])}
            refresh={refresh}
            currentUser={currentUser}
            navigateToView={navigateToView}
            onCurrentUserUpdated={(user) =>
              setAuthStatus((current) => current ? { ...current, user } : current)
            }
            onMaintenanceStatusChanged={setMaintenanceStatus}
          />
        )}
      </main>
      <NotificationToastStack
        notifications={notificationToasts}
        onDismiss={(id) => setNotificationToasts((current) => current.filter((item) => item.id !== id))}
      />
      <ChatWidget currentUser={currentUser} maintenanceStatus={maintenanceStatus} />
    </div>
  );
}

const AlertTray = React.forwardRef<HTMLDivElement, {
  anomalies: Anomaly[];
  onRefresh: () => Promise<void>;
  onViewAll: () => void;
}>(function AlertTray({ anomalies, onRefresh, onViewAll }, ref) {
  const alertCount = anomalies.length;
  const recentAnomalies = anomalies.slice(0, 8);
  return (
    <div className="alert-tray" id="alert-tray" ref={ref} role="dialog" aria-label="Alerts">
      <div className="alert-tray-header">
        <div>
          <strong>Alerts</strong>
          <span>{alertCount ? `${alertCount} actionable alert${alertCount === 1 ? "" : "s"}` : "No actionable alerts"}</span>
        </div>
        <button className="icon-button" onClick={() => onRefresh().catch(() => undefined)} type="button" aria-label="Refresh alerts">
          <RefreshCcw size={15} />
        </button>
      </div>
      <div className="alert-tray-list">
        {recentAnomalies.length ? recentAnomalies.map((anomaly) => (
          <article className="alert-tray-row" key={anomaly.id}>
            <span className={`alert-tray-icon ${anomaly.severity}`}>
              <AlertTriangle size={17} />
            </span>
            <div>
              <div className="alert-tray-row-head">
                <strong>{titleCase(anomaly.type)}</strong>
                <Badge tone={alertSeverityTone(anomaly.severity)}>{alertSeverityLabel(anomaly.severity)}</Badge>
              </div>
              <p>{anomaly.message}</p>
              <time>{formatDate(anomaly.last_seen_at || anomaly.created_at)}</time>
            </div>
          </article>
        )) : (
          <EmptyState icon={CheckCircle2} label="No actionable alerts" />
        )}
      </div>
      <button className="alert-tray-view-all" onClick={onViewAll} type="button">
        View all alerts
        <ChevronRight size={15} />
      </button>
    </div>
  );
});

function NotificationToastStack({
  notifications,
  onDismiss
}: {
  notifications: NotificationToast[];
  onDismiss: (id: string) => void;
}) {
  React.useEffect(() => {
    if (!notifications.length) return undefined;
    const timers = notifications.map((notification) =>
      window.setTimeout(() => onDismiss(notification.id), 9000)
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [notifications, onDismiss]);

  if (!notifications.length) return null;
  return (
    <div className="notification-toast-stack" aria-live="polite">
      {notifications.map((notification) => (
        <article className={`notification-toast ${notification.severity}`} key={notification.id}>
          {notification.snapshot_url ? <img alt="" src={notification.snapshot_url} /> : null}
          <div>
            <div className="notification-toast-head">
              <Badge tone={notification.severity === "critical" ? "red" : notification.severity === "warning" ? "amber" : "blue"}>
                {notificationEventLabel(notification.event_type)}
              </Badge>
              <button className="icon-button" onClick={() => onDismiss(notification.id)} type="button" aria-label="Dismiss notification">
                <X size={14} />
              </button>
            </div>
            <strong>{notification.title}</strong>
            <p>{notification.body}</p>
          </div>
        </article>
      ))}
    </div>
  );
}

function AuthLoading() {
  return (
    <main className="auth-page">
      <section className="auth-card compact">
        <div className="auth-mark">
          <ShieldCheck size={28} />
        </div>
        <h1>Intelligent</h1>
        <p>Checking secure session</p>
      </section>
    </main>
  );
}

function LprTimingTestPage({
  realtime,
  currentUser,
  theme,
  setTheme
}: {
  realtime: RealtimeMessage[];
  currentUser: UserAccount;
  theme: ThemeMode;
  setTheme: (mode: ThemeMode) => void;
}) {
  const [observations, setObservations] = React.useState<LprTimingObservation[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const processedIdsRef = React.useRef<Set<string>>(new Set());

  const mergeObservation = React.useCallback((observation: LprTimingObservation) => {
    processedIdsRef.current.add(observation.id);
    setObservations((current) => {
      const next = [observation, ...current.filter((item) => item.id !== observation.id)];
      return [...next]
        .sort((left, right) => Date.parse(right.received_at) - Date.parse(left.received_at))
        .slice(0, 1000);
    });
  }, []);

  const loadObservations = React.useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const payload = await api.get<{ observations: LprTimingObservation[] }>("/api/v1/diagnostics/lpr-timing?limit=1000");
      processedIdsRef.current = new Set(payload.observations.map((observation) => observation.id));
      setObservations(payload.observations);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load timing observations");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    loadObservations().catch(() => undefined);
  }, [loadObservations]);

  React.useEffect(() => {
    for (const message of realtime) {
      if (message.type === "lpr_timing.cleared") {
        processedIdsRef.current.clear();
        setObservations([]);
        continue;
      }
      if (message.type !== "lpr_timing.observed") continue;
      const observation = lprTimingObservationFromPayload(message.payload);
      if (!observation || processedIdsRef.current.has(observation.id)) continue;
      mergeObservation(observation);
    }
  }, [mergeObservation, realtime]);

  const comparisons = React.useMemo(() => buildLprTimingComparisons(observations), [observations]);
  const webhookCount = observations.filter((observation) => observation.source === "webhook").length;
  const protectPlateCount = observations.filter((observation) => observation.source === "uiprotect" && observation.registration_number).length;
  const trackPlateCount = observations.filter((observation) => observation.source === "uiprotect_track" && observation.registration_number).length;
  const protectPossibleCount = observations.filter((observation) => observation.source !== "webhook" && !observation.registration_number).length;

  const clearObservations = async () => {
    setError("");
    try {
      await api.delete("/api/v1/diagnostics/lpr-timing");
      processedIdsRef.current.clear();
      setObservations([]);
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : "Unable to clear timing observations");
    }
  };

  return (
    <main className="lpr-test-page">
      <header className="lpr-test-header">
        <div>
          <p className="eyebrow">Diagnostics</p>
          <h1>LPR timing test</h1>
          <p>Server-side arrival timestamps for UniFi Protect and webhook plate reads.</p>
        </div>
        <div className="lpr-test-actions">
          <span className="lpr-test-user">{displayUserName(currentUser)}</span>
          <ThemeControl theme={theme} setTheme={setTheme} />
          <button className="secondary-button" type="button" onClick={() => loadObservations().catch(() => undefined)}>
            <RefreshCcw size={16} />
            Refresh
          </button>
          <button className="secondary-button danger" type="button" onClick={() => clearObservations().catch(() => undefined)}>
            <Trash2 size={16} />
            Clear
          </button>
        </div>
      </header>

      {error ? <div className="auth-error lpr-test-error">{error}</div> : null}

      <section className="lpr-test-summary">
        <article>
          <span>Total</span>
          <strong>{observations.length}</strong>
        </article>
        <article>
          <span>Webhook</span>
          <strong>{webhookCount}</strong>
        </article>
        <article>
          <span>Protect Websocket</span>
          <strong>{protectPlateCount}</strong>
        </article>
        <article>
          <span>Protect Track</span>
          <strong>{trackPlateCount}</strong>
        </article>
        <article>
          <span>Possible Fields</span>
          <strong>{protectPossibleCount}</strong>
        </article>
      </section>

      <section className="lpr-test-card">
        <div className="section-heading">
          <div>
            <h2>Nearest Matches</h2>
            <p>Matched by plate number and source using server arrival time.</p>
          </div>
        </div>
        {comparisons.length ? (
          <div className="lpr-test-table-wrap">
            <table className="data-table lpr-test-table">
              <thead>
                <tr>
                  <th>Plate</th>
                  <th>Winner</th>
                  <th>Delta</th>
                  <th>Webhook arrival</th>
                  <th>Source arrival</th>
                  <th>Source</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {comparisons.slice(0, 30).map((comparison) => (
                  <tr key={`${comparison.protect.id}-${comparison.webhook.id}`}>
                    <td><strong>{comparison.registration_number}</strong></td>
                    <td><Badge tone={comparison.delta_ms < 0 ? "green" : comparison.delta_ms > 0 ? "blue" : "gray"}>{comparison.winner}</Badge></td>
                    <td>{formatLprDelta(comparison.delta_ms)}</td>
                    <td><time title={comparison.webhook.received_at}>{formatExactTimestamp(comparison.webhook.received_at)}</time></td>
                    <td><time title={comparison.protect.received_at}>{formatExactTimestamp(comparison.protect.received_at)}</time></td>
                    <td><Badge tone={lprTimingSourceTone(comparison.protect.source)}>{lprTimingSourceLabel(comparison.protect.source)}</Badge></td>
                    <td>{comparison.protect.source_detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={Clock3} label={loading ? "Loading timing observations" : "Waiting for matching plate reads"} />
        )}
      </section>

      <section className="lpr-test-card">
        <div className="section-heading">
          <div>
            <h2>Raw Observations</h2>
            <p>Every clean plate and every plausible LPR field/value seen from UniFi Protect.</p>
          </div>
        </div>
        {observations.length ? (
          <div className="lpr-test-table-wrap">
            <table className="data-table lpr-test-table">
              <thead>
                <tr>
                  <th>Arrival time</th>
                  <th>Source</th>
                  <th>Plate</th>
                  <th>Raw value</th>
                  <th>Detail</th>
                  <th>Camera</th>
                  <th>Captured/best frame</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {observations.map((observation) => (
                  <tr key={observation.id}>
                    <td>
                      <time title={observation.received_at}>{formatExactTimestamp(observation.received_at)}</time>
                      <small>{observation.received_at}</small>
                    </td>
                    <td><Badge tone={lprTimingSourceTone(observation.source)}>{lprTimingSourceLabel(observation.source)}</Badge></td>
                    <td>
                      {observation.registration_number ? <strong>{observation.registration_number}</strong> : <Badge tone="amber">possible</Badge>}
                      {observation.candidate_kind === "possible_lpr_field" ? <small>not normalized</small> : null}
                    </td>
                    <td><small className="lpr-raw-value">{observation.raw_value || "-"}</small></td>
                    <td>
                      {observation.source_detail}
                      {observation.payload_path ? <small>{observation.payload_path}</small> : null}
                    </td>
                    <td>{observation.camera_name || observation.camera_id || "-"}</td>
                    <td>{observation.captured_at ? <time title={observation.captured_at}>{formatExactTimestamp(observation.captured_at)}</time> : "-"}</td>
                    <td>{formatLprConfidence(observation)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={Clock3} label={loading ? "Loading timing observations" : "No plate observations yet"} />
        )}
      </section>
    </main>
  );
}

type LprTimingComparison = {
  registration_number: string;
  webhook: LprTimingObservation;
  protect: LprTimingObservation;
  delta_ms: number;
  winner: string;
};

function buildLprTimingComparisons(observations: LprTimingObservation[]): LprTimingComparison[] {
  const webhooks = observations.filter((observation) => observation.source === "webhook" && observation.registration_number);
  const protectCandidates = observations.filter((observation) => observation.source !== "webhook" && observation.registration_number);
  const comparisons: LprTimingComparison[] = [];
  const protectsBySourceAndEvent = new Map<string, LprTimingObservation>();

  for (const protect of protectCandidates) {
    const key = [protect.source, protect.event_id || protect.received_at, protect.registration_number].join("|");
    const existing = protectsBySourceAndEvent.get(key);
    if (!existing || Date.parse(protect.received_at) < Date.parse(existing.received_at)) {
      protectsBySourceAndEvent.set(key, protect);
    }
  }

  for (const protect of protectsBySourceAndEvent.values()) {
    const protectTime = Date.parse(protect.received_at);
    if (!Number.isFinite(protectTime)) continue;
    const nearestWebhook = webhooks
      .filter((webhook) => webhook.registration_number === protect.registration_number)
      .map((webhook) => ({ webhook, distance: Math.abs(Date.parse(webhook.received_at) - protectTime) }))
      .filter((candidate) => Number.isFinite(candidate.distance) && candidate.distance <= 10 * 60 * 1000)
      .sort((left, right) => left.distance - right.distance)[0]?.webhook;
    if (!nearestWebhook) continue;
    const delta = protectTime - Date.parse(nearestWebhook.received_at);
    comparisons.push({
      registration_number: protect.registration_number,
      webhook: nearestWebhook,
      protect,
      delta_ms: delta,
      winner: delta < 0 ? lprTimingSourceLabel(protect.source) : delta > 0 ? "Webhook" : "Tie"
    });
  }

  return [...comparisons]
    .sort((left, right) =>
      Math.max(Date.parse(right.webhook.received_at), Date.parse(right.protect.received_at)) -
      Math.max(Date.parse(left.webhook.received_at), Date.parse(left.protect.received_at))
    )
    .slice(0, 60);
}

function lprTimingObservationFromPayload(payload: Record<string, unknown>): LprTimingObservation | null {
  const raw = isRecord(payload.observation) ? payload.observation : payload;
  const id = stringPayload(raw.id);
  const source = stringPayload(raw.source);
  const sourceDetail = stringPayload(raw.source_detail);
  const registrationNumber = stringPayload(raw.registration_number);
  const receivedAt = stringPayload(raw.received_at);
  const candidateKind = stringPayload(raw.candidate_kind);
  if (!id || !isLprTimingSource(source) || !sourceDetail || !receivedAt) return null;
  return {
    id,
    source,
    source_detail: sourceDetail,
    registration_number: registrationNumber,
    received_at: receivedAt,
    raw_value: nullableString(raw.raw_value),
    candidate_kind: candidateKind === "possible_lpr_field" ? "possible_lpr_field" : "normalized_plate",
    captured_at: nullableString(raw.captured_at),
    event_id: nullableString(raw.event_id),
    camera_id: nullableString(raw.camera_id),
    camera_name: nullableString(raw.camera_name),
    confidence: nullableNumber(raw.confidence),
    confidence_scale: nullableString(raw.confidence_scale),
    protect_action: nullableString(raw.protect_action),
    protect_model: nullableString(raw.protect_model),
    smart_detect_types: Array.isArray(raw.smart_detect_types) ? raw.smart_detect_types.filter((item): item is string => typeof item === "string") : null,
    payload_path: nullableString(raw.payload_path)
  };
}

function isLprTimingSource(value: string): value is LprTimingSource {
  return value === "webhook" || value === "uiprotect" || value === "uiprotect_track";
}

function lprTimingSourceLabel(source: LprTimingSource) {
  if (source === "webhook") return "Webhook";
  if (source === "uiprotect_track") return "Protect Track";
  return "Protect WS";
}

function lprTimingSourceTone(source: LprTimingSource): BadgeTone {
  if (source === "webhook") return "blue";
  if (source === "uiprotect_track") return "purple";
  return "green";
}

function nullableNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatExactTimestamp(value: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      fractionalSecondDigits: 3,
      day: "2-digit",
      month: "short",
      timeZoneName: "short"
    }).format(date);
  } catch {
    return date.toISOString();
  }
}

function formatLprDelta(deltaMs: number) {
  const absolute = Math.abs(deltaMs);
  if (absolute < 1000) return `${absolute} ms`;
  return `${(absolute / 1000).toFixed(3)} s`;
}

function formatLprConfidence(observation: LprTimingObservation) {
  if (observation.confidence === null) return "-";
  if (observation.confidence_scale === "0_1") return `${Math.round(observation.confidence * 100)}%`;
  return `${Math.round(observation.confidence)}%`;
}

function LoginPage({ onLogin }: { onLogin: (user: UserAccount) => void }) {
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [rememberMe, setRememberMe] = React.useState(true);
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const user = await api.post<UserAccount>("/api/v1/auth/login", {
        username,
        password,
        remember_me: rememberMe
      });
      clearChatTeaserDismissals();
      onLogin(user);
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : "Invalid credentials");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-mark">
          <ShieldCheck size={30} />
        </div>
        <div>
          <h1>Welcome back</h1>
          <p>Sign in to Crest House access control.</p>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <label className="field">
          <span>Username</span>
          <div className="field-control">
            <UserRound size={17} />
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required />
          </div>
        </label>
        <label className="field">
          <span>Password</span>
          <div className="field-control">
            <Lock size={17} />
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" required />
          </div>
        </label>
        <label className="check-row">
          <input checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} type="checkbox" />
          <span>Remember me on this device</span>
        </label>
        <button className="primary-button auth-submit" disabled={submitting} type="submit">
          <LogIn size={17} />
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}

function SetupPage({ onComplete }: { onComplete: (user: UserAccount) => void }) {
  const [form, setForm] = React.useState({
    username: "",
    first_name: "Jason",
    last_name: "Smith",
    email: "",
    password: ""
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const update = (field: keyof typeof form, value: string) => setForm((current) => ({ ...current, [field]: value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const user = await api.post<UserAccount>("/api/v1/auth/setup", {
        username: form.username,
        first_name: form.first_name,
        last_name: form.last_name,
        email: form.email || null,
        password: form.password
      });
      clearChatTeaserDismissals();
      onComplete(user);
    } catch (setupError) {
      setError(setupError instanceof Error ? setupError.message : "Setup failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <form className="auth-card setup-card" onSubmit={submit}>
        <div className="auth-mark">
          <Shield size={30} />
        </div>
        <div>
          <h1>First-run setup</h1>
          <p>Create the master Admin account. This setup locks once the first user exists.</p>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="field-grid">
          <label className="field">
            <span>First name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} autoComplete="given-name" required />
            </div>
          </label>
          <label className="field">
            <span>Last name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} autoComplete="family-name" required />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Username</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.username} onChange={(event) => update("username", event.target.value)} autoComplete="username" required />
            </div>
          </label>
        </div>
        <label className="field">
          <span>Email</span>
          <div className="field-control">
            <MessageCircle size={17} />
            <input value={form.email} onChange={(event) => update("email", event.target.value)} type="email" autoComplete="email" />
          </div>
        </label>
        <label className="field">
          <span>Password</span>
          <div className="field-control">
            <Key size={17} />
            <input value={form.password} onChange={(event) => update("password", event.target.value)} type="password" autoComplete="new-password" minLength={10} required />
          </div>
        </label>
        <button className="primary-button auth-submit" disabled={submitting} type="submit">
          <Check size={17} />
          {submitting ? "Creating admin..." : "Create Admin"}
        </button>
      </form>
    </main>
  );
}

function View(props: {
  view: ViewKey;
  search: string;
  presence: Presence[];
  events: AccessEvent[];
  anomalies: Anomaly[];
  people: Person[];
  vehicles: Vehicle[];
  groups: Group[];
  schedules: Schedule[];
  integrationStatus: IntegrationStatus | null;
  maintenanceStatus: MaintenanceStatus | null;
  realtime: RealtimeMessage[];
  onClearRealtime: () => void;
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  navigateToView: NavigateToView;
  onCurrentUserUpdated: (user: UserAccount) => void;
  onMaintenanceStatusChanged: (status: MaintenanceStatus) => void;
}) {
  switch (props.view) {
    case "people":
      return <PeopleView garageDoors={props.integrationStatus?.garage_door_entities ?? []} groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
    case "groups":
      return <GroupsView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} />;
    case "schedules":
      return <SchedulesView schedules={props.schedules} query={props.search} refresh={props.refresh} />;
    case "passes":
      return <PassesView query={props.search} realtime={props.realtime} />;
    case "vehicles":
      return <VehiclesView people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
    case "top_charts":
      return <TopChartsView query={props.search} realtime={props.realtime} />;
    case "events":
      return <EventsView events={props.events} query={props.search} />;
    case "alerts":
      return <AlertsView refreshDashboard={props.refresh} />;
    case "reports":
      return <ReportsView events={props.events} presence={props.presence} />;
    case "integrations":
      return <IntegrationsView people={props.people} realtime={props.realtime} schedules={props.schedules} status={props.integrationStatus} />;
    case "logs":
      return <LogsView logs={props.realtime} onClearRealtime={props.onClearRealtime} />;
    case "settings_general":
      return (
        <DynamicSettingsView
          category="general"
          title="General Settings"
          icon={SlidersHorizontal}
          maintenanceStatus={props.maintenanceStatus}
          onMaintenanceStatusChanged={props.onMaintenanceStatusChanged}
        />
      );
    case "settings_auth":
      return <DynamicSettingsView category="auth" title="Auth & Security" icon={Lock} />;
    case "settings_automations":
      return <AutomationsView people={props.people} vehicles={props.vehicles} />;
    case "settings_notifications":
      return <NotificationsView currentUser={props.currentUser} people={props.people} schedules={props.schedules} />;
    case "settings_lpr":
      return <DynamicSettingsView category="lpr" title="LPR Tuning" icon={Gauge} />;
    case "settings":
      return <SettingsView currentUser={props.currentUser} groups={props.groups} schedules={props.schedules} vehicles={props.vehicles} />;
    case "users":
      return <UsersView currentUser={props.currentUser} onCurrentUserUpdated={props.onCurrentUserUpdated} />;
    default:
      return <Dashboard {...props} currentUser={props.currentUser} navigateToView={props.navigateToView} />;
  }
}

function Dashboard({
  presence,
  events,
  anomalies,
  integrationStatus,
  maintenanceStatus,
  people,
  vehicles,
  refresh,
  currentUser,
  navigateToView,
  onMaintenanceStatusChanged
}: {
  presence: Presence[];
  events: AccessEvent[];
  anomalies: Anomaly[];
  integrationStatus: IntegrationStatus | null;
  maintenanceStatus: MaintenanceStatus | null;
  people: Person[];
  vehicles: Vehicle[];
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  navigateToView: NavigateToView;
  onMaintenanceStatusChanged: (status: MaintenanceStatus) => void;
}) {
  const [now, setNow] = React.useState(() => new Date());
  const [simulatorPlate, setSimulatorPlate] = React.useState("");
  const [pendingCommand, setPendingCommand] = React.useState<DashboardCommand | null>(null);
  const [maintenanceDisableOpen, setMaintenanceDisableOpen] = React.useState(false);
  const [maintenanceLoading, setMaintenanceLoading] = React.useState(false);
  const [maintenanceError, setMaintenanceError] = React.useState("");
  const [commandLoading, setCommandLoading] = React.useState(false);
  const [commandError, setCommandError] = React.useState("");
  const maintenanceActive = maintenanceStatus?.is_active === true;
  const present = presence.filter((item) => item.state === "present").length;
  const exited = presence.filter((item) => item.state === "exited").length;
  const unknown = Math.max(presence.length - present - exited, 0);
  const latestEvent = events[0];
  const actionableAlerts = anomalies.filter(isActionableAlert);
  const critical = actionableAlerts.filter((item) => item.severity === "critical").length;
  const warning = actionableAlerts.filter((item) => item.severity === "warning").length;
  const displayEvents = getDashboardEvents(events, vehicles, people);
  const displayAnomalies = getDashboardAnomalies(actionableAlerts);
  const expected = Math.max(people.length, presence.length);
  const todayEvents = events.filter((event) => isToday(event.occurred_at, now));
  const exitedToday = todayEvents.filter((event) => event.direction === "exit").length;
  const deniedToday = todayEvents.filter((event) => event.decision === "denied").length;
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  const liveSources = new Set(events.map((event) => event.source).filter(Boolean)).size;
  const gateEntities = activeManagedCovers(integrationStatus?.gate_entities);
  const garageDoorEntities = activeManagedCovers(integrationStatus?.garage_door_entities);
  const topGateState = gateEntities[0]?.state ?? integrationStatus?.current_gate_state ?? integrationStatus?.last_gate_state ?? "unknown";
  const siteStatusTitle = maintenanceActive ? "Maintenance Mode Enabled" : critical ? "Critical alerts" : warning ? "Action needed" : "All systems normal";
  const siteStatusDetail = maintenanceActive ? "All automated actions disabled" : critical
    ? `${critical} critical alert${critical === 1 ? "" : "s"}`
    : warning
      ? `${warning} warning alert${warning === 1 ? "" : "s"}`
      : "No actionable alerts";
  const greeting = greetingForDate(now);
  const firstName = currentUser.first_name || displayUserName(currentUser).split(" ")[0] || "there";
  const selectedPlate = simulatorPlate || vehicles[0]?.registration_number || "";

  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  React.useEffect(() => {
    if (!simulatorPlate && vehicles[0]) {
      setSimulatorPlate(vehicles[0].registration_number);
      return;
    }
    if (simulatorPlate && !vehicles.some((vehicle) => vehicle.registration_number === simulatorPlate)) {
      setSimulatorPlate(vehicles[0]?.registration_number ?? "");
    }
  }, [simulatorPlate, vehicles]);

  const runDashboardCommand = async () => {
    if (!pendingCommand || commandLoading) return;
    setCommandLoading(true);
    setCommandError("");
    try {
      if (pendingCommand.kind === "gate") {
        await api.post("/api/v1/integrations/gate/open", { reason: "Dashboard Top Gate status command" });
      } else {
        await api.post("/api/v1/integrations/cover/command", {
          entity_id: pendingCommand.entity_id,
          action: pendingCommand.action,
          reason: `Dashboard ${pendingCommand.label} ${pendingCommand.action} command`
        });
      }
      setPendingCommand(null);
      await refresh();
      window.setTimeout(() => refresh().catch(() => undefined), 2500);
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : `Unable to ${pendingCommand.action} ${pendingCommand.label}.`);
    } finally {
      setCommandLoading(false);
    }
  };

  const disableMaintenanceMode = async () => {
    if (maintenanceLoading) return;
    setMaintenanceLoading(true);
    setMaintenanceError("");
    try {
      const status = await api.post<MaintenanceStatus>("/api/v1/maintenance/disable", {
        reason: "Disabled from Dashboard Site Status icon"
      });
      onMaintenanceStatusChanged(status);
      setMaintenanceDisableOpen(false);
      await refresh();
    } catch (error) {
      setMaintenanceError(error instanceof Error ? error.message : "Unable to disable Maintenance Mode.");
    } finally {
      setMaintenanceLoading(false);
    }
  };

  return (
    <section className="dashboard-page">
      <div className="dashboard-intro">
        <div>
          <h1>{greeting}, {firstName}</h1>
          <p>Here's what's happening at Crest House today.</p>
        </div>
        <div className="intro-clock">
          <Clock3 size={18} />
          <span>{formatLongDate(now)}</span>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="card site-status-card">
          <PanelHeader title="Site Status" />
          <div className={maintenanceActive ? "site-status-main maintenance" : "site-status-main"}>
            {maintenanceActive ? (
              <button
                className="maintenance-status-icon"
                onClick={() => {
                  setMaintenanceError("");
                  setMaintenanceDisableOpen(true);
                }}
                type="button"
                aria-label="Disable Maintenance Mode"
              >
                <Construction size={54} strokeWidth={1} />
              </button>
            ) : (
              <ShieldCheck size={54} />
            )}
            <div>
              <strong>{siteStatusTitle}</strong>
              <span>{siteStatusDetail}</span>
            </div>
          </div>
          <div className="status-metrics">
            <StatusMetric label="People tracked" mobileLabel="People" value={String(people.length)} />
            <StatusMetric label="Active vehicles" mobileLabel="Vehicles" value={String(activeVehicles)} />
            <StatusMetric label="Live sources" mobileLabel="Sources" value={String(liveSources)} />
          </div>
        </div>

        <div className="card gate-card">
          <PanelHeader title="Status" action="View all" />
          <div className={maintenanceActive ? "gate-control-section maintenance-disabled" : "gate-control-section"}>
          <div className="gate-list">
            {gateEntities.length ? gateEntities.map((gate) => (
              <GateRow
                icon={Car}
                key={gate.entity_id}
                label={gate.name || "Gate"}
                state={commandLoading && pendingCommand?.kind === "gate" ? "opening" : gate.state ?? topGateState}
                onActionClick={maintenanceActive ? undefined : commandForGate(gate.name || "Gate", gate.state ?? topGateState, setPendingCommand, setCommandError)}
              />
            )) : (
              <GateRow
                icon={Car}
                label="Top Gate"
                state={commandLoading && pendingCommand?.kind === "gate" ? "opening" : topGateState}
                onActionClick={maintenanceActive ? undefined : commandForGate("Top Gate", topGateState, setPendingCommand, setCommandError)}
              />
            )}
            {garageDoorEntities.map((door) => (
              <GarageDoorRow
                key={door.entity_id}
                label={door.name || door.entity_id}
                state={commandLoading && pendingCommand?.kind === "garage_door" && pendingCommand.entity_id === door.entity_id ? inProgressState(pendingCommand.action) : door.state ?? "unknown"}
                onActionClick={maintenanceActive ? undefined : commandForGarageDoor(door, setPendingCommand, setCommandError)}
              />
            ))}
            <DoorRow label="Back Door" state={integrationStatus?.back_door_state ?? "unknown"} />
          </div>
          {maintenanceActive ? (
            <div className="maintenance-control-overlay" aria-hidden="true">
              <HardHat size={96} strokeWidth={1} />
            </div>
          ) : null}
          </div>
        </div>

        <div className="card presence-summary-card">
          <PanelHeader title="Presence Summary" action="View all" />
          <div className="presence-stats">
            <PresenceStat label="Inside Now" value={String(present)} trend="current" tone="green" />
            <PresenceStat label="Expected" value={String(expected)} trend="profiles" tone="blue" />
            <PresenceStat label="Exited Today" value={String(exitedToday)} trend="events" tone="gray" />
          </div>
          <div className="presence-bar" aria-label="Presence mix">
            <span className="residents" style={{ width: `${presenceSegmentWidth(present, presence.length)}%` }} />
            <span className="staff" style={{ width: `${presenceSegmentWidth(exited, presence.length)}%` }} />
            <span className="visitors" style={{ width: `${presenceSegmentWidth(unknown, presence.length)}%` }} />
          </div>
          <div className="presence-legend">
            <LegendDot className="residents" label="Present" value={String(present)} />
            <LegendDot className="staff" label="Exited" value={String(exited)} />
            <LegendDot className="visitors" label="Unknown" value={String(unknown)} />
          </div>
        </div>

        <div className="card recent-events-card">
          <PanelHeader title="Recent Events" action="View all" />
          <div className="event-feed">
            {displayEvents.length ? displayEvents.map((event) => {
              const Icon = event.icon;
              return (
                <div className="event-feed-row" key={event.id}>
                  <time>{event.time}</time>
                  <span className={`feed-line ${event.tone}`} />
                  <span className={`event-chip ${event.tone}`}>
                    <Icon size={18} />
                  </span>
                  <div>
                    <strong>{event.label}</strong>
                    <span>{event.subtitle}</span>
                  </div>
                  <EventStatusBadge event={event} />
                </div>
              );
            }) : <EmptyState icon={CalendarDays} label="No recent events" />}
          </div>
          <p className="card-footnote">Showing latest 5 events</p>
        </div>

        <div className="card anomaly-card">
          <PanelHeader title="Alerts" action="View all" onAction={() => navigateToView("alerts")} />
          <div className="anomaly-feed">
            {displayAnomalies.length ? displayAnomalies.map((item) => (
              <button
                className="anomaly-feed-row"
                key={item.id}
                onClick={() => navigateToView("alerts", { search: `?alert=${encodeURIComponent(item.id)}` })}
                type="button"
              >
                <span className={`anomaly-icon ${item.severity}`}>
                  <AlertTriangle size={20} />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.detail}</span>
                </div>
                <time>{item.time}</time>
              </button>
            )) : <EmptyState icon={CheckCircle2} label="No actionable alerts" />}
          </div>
          <p className="unresolved-count">{actionableAlerts.length} action needed</p>
        </div>

        <div className="card chart-card">
          <PanelHeader title="Daily Entries vs Exits" action="7 Days" actionKind="select" />
          <DailyEntriesChart events={events} />
        </div>

        <div className="card access-simulator-card span-2">
          <PanelHeader title="Access Simulator" />
          <div className="simulator-form">
            <label>
              <span>Select Credential</span>
              <select value={selectedPlate} onChange={(event) => setSimulatorPlate(event.target.value)} disabled={!vehicles.length}>
                {vehicles.length ? vehicles.map((vehicle) => (
                  <option value={vehicle.registration_number} key={vehicle.id}>Plate - {vehicle.registration_number}</option>
                )) : <option value="">No vehicles available</option>}
              </select>
            </label>
            <label>
              <span>Select Gate</span>
              <select defaultValue="main">
                <option value="main">Main Gate</option>
                <option value="service">Service Gate</option>
              </select>
            </label>
            <label>
              <span>Select Date & Time</span>
              <div className="date-input">
                <CalendarDays size={18} />
                <span>{formatSimulatorDate(new Date())}</span>
              </div>
            </label>
            <button className="primary-button simulate-primary" onClick={() => simulate(`/api/v1/simulation/arrival/${selectedPlate}`, refresh)} type="button" disabled={!selectedPlate}>
              <Play size={17} /> Simulate Access
            </button>
          </div>
          <div className="simulator-footer-line">
            <p className="muted-line">
              {vehicles.length ? "Run a synthetic access event for a registered plate." : "Add a vehicle before running synthetic access events."}
              {latestEvent ? ` Latest: ${latestEvent.registration_number} ${latestEvent.decision}.` : ""}
            </p>
            <button className="misread-link" onClick={() => simulate(`/api/v1/simulation/misread-sequence/${selectedPlate}`, refresh)} type="button" disabled={!selectedPlate}>
              <SlidersHorizontal size={14} /> Simulate misread sequence
            </button>
          </div>
        </div>
      </div>

      {pendingCommand ? (
        <GateConfirmModal
          action={pendingCommand.action}
          error={commandError}
          label={pendingCommand.label}
          loading={commandLoading}
          onCancel={() => {
            if (commandLoading) return;
            setPendingCommand(null);
            setCommandError("");
          }}
          onConfirm={runDashboardCommand}
        />
      ) : null}
      {maintenanceDisableOpen ? (
        <MaintenanceDisableModal
          error={maintenanceError}
          loading={maintenanceLoading}
          onCancel={() => {
            if (maintenanceLoading) return;
            setMaintenanceDisableOpen(false);
            setMaintenanceError("");
          }}
          onConfirm={disableMaintenanceMode}
        />
      ) : null}
    </section>
  );
}

function MaintenanceDisableModal({
  error,
  loading,
  onCancel,
  onConfirm
}: {
  error: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="maintenance-disable-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon maintenance">
              <Construction size={20} strokeWidth={1} />
            </span>
            <div>
              <h2 className="maintenance-disable-title" id="maintenance-disable-title">Disable Maintenance Mode</h2>
              <p>Allow automated actions to resume normal operation</p>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Cancel
          </button>
          <button className="primary-button" disabled={loading} onClick={onConfirm} type="button">
            <Check size={16} />
            {loading ? "Resuming..." : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

function GateConfirmModal({
  action,
  error,
  label,
  loading,
  onCancel,
  onConfirm
}: {
  action: DoorCommandAction;
  error: string;
  label: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const actionLabel = titleCase(action);
  const isGarage = label.toLowerCase().includes("garage");
  const Icon = isGarage
    ? Warehouse
    : action === "open" ? DoorOpen : DoorClosed;
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="gate-confirm-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon">
              <Icon size={20} />
            </span>
            <div>
              <h2 id="gate-confirm-title">{actionLabel} {label}?</h2>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Cancel
          </button>
          <button className="primary-button" disabled={loading} onClick={onConfirm} type="button">
            <Icon size={16} />
            {loading ? `${titleCase(inProgressState(action))}...` : `${actionLabel} ${label}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function PanelHeader({ title, action, actionKind, onAction }: { title: string; action?: string; actionKind?: "link" | "select"; onAction?: () => void }) {
  return (
    <div className="panel-header">
      <h2>{title}</h2>
      {action ? (
        actionKind === "select" ? (
          <button className="panel-select" type="button">
            {action}
            <ChevronDown size={14} />
          </button>
        ) : (
          <button className="panel-link" onClick={onAction} type="button">{action}</button>
        )
      ) : null}
    </div>
  );
}

function StatusMetric({ label, mobileLabel, value }: { label: string; mobileLabel?: string; value: string }) {
  return (
    <div>
      <span>
        <i />
        <span className="status-label status-label-desktop">{label}</span>
        <span className="status-label status-label-mobile">{mobileLabel ?? label}</span>
      </span>
      <strong>{value}</strong>
    </div>
  );
}

function GateRow({
  icon: Icon,
  label,
  state,
  onActionClick
}: {
  icon: React.ElementType;
  label: string;
  state: string;
  onActionClick?: () => void;
}) {
  const normalized = normalizeGateState(state);
  const display = gateStateDisplay(state);
  const hasAction = (normalized === "closed" || normalized === "open") && display.actionable && onActionClick;
  return (
    <div className="gate-row">
      <Icon size={18} />
      <strong>{label}</strong>
      {hasAction ? (
        <button className={`badge ${display.tone} badge-action`} onClick={onActionClick} type="button">
          {display.label}
        </button>
      ) : (
        <Badge tone={display.tone}>{display.label}</Badge>
      )}
    </div>
  );
}

function DoorRow({ label, state }: { label: string; state: string }) {
  const normalized = normalizeGateState(state);
  const Icon = normalized === "open" ? DoorOpen : DoorClosed;
  return <GateRow icon={Icon} label={label} state={state} />;
}

function GarageDoorRow({ label, state, onActionClick }: { label: string; state: string; onActionClick?: () => void }) {
  return <GateRow icon={Warehouse} label={label} state={state} onActionClick={onActionClick} />;
}

function commandForGate(
  label: string,
  state: string,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>
) {
  const normalized = normalizeGateState(state);
  if (normalized !== "closed") return undefined;
  return () => {
    setCommandError("");
    setPendingCommand({ kind: "gate", label, action: "open" });
  };
}

function commandForGarageDoor(
  door: HomeAssistantManagedCover,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>
) {
  const normalized = normalizeGateState(door.state ?? "unknown");
  if (!["open", "closed"].includes(normalized)) return undefined;
  const action = normalized === "open" ? "close" : "open";
  return () => {
    setCommandError("");
    setPendingCommand({ kind: "garage_door", entity_id: door.entity_id, label: door.name || door.entity_id, action });
  };
}

function activeManagedCovers(entities: HomeAssistantManagedCover[] | undefined) {
  return (entities ?? []).filter((entity) => entity.enabled !== false);
}

function inProgressState(action: DoorCommandAction) {
  return action === "open" ? "opening" : "closing";
}

function gateStateDisplay(state: string): { label: string; tone: BadgeTone; actionable: boolean } {
  const normalized = state.toLowerCase();
  if (normalized === "open") return { label: "Open", tone: "green", actionable: true };
  if (normalized === "opening") return { label: "Opening", tone: "amber", actionable: false };
  if (normalized === "closed") return { label: "Closed", tone: "gray", actionable: true };
  if (normalized === "closing") return { label: "Closing", tone: "amber", actionable: false };
  return { label: "Unknown", tone: "amber", actionable: false };
}

function normalizeGateState(state: string) {
  const normalized = state.toLowerCase();
  if (["open", "opening"].includes(normalized)) return "open";
  if (["closed", "closing"].includes(normalized)) return "closed";
  return "unknown";
}

function PresenceStat({ label, value, trend, tone }: { label: string; value: string; trend: string; tone: "green" | "blue" | "gray" }) {
  return (
    <div className="presence-stat">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
      <small>{trend}</small>
    </div>
  );
}

function presenceSegmentWidth(value: number, total: number) {
  if (!total || !value) return 0;
  return Math.max((value / total) * 100, 6);
}

function LegendDot({ className, label, value }: { className: string; label: string; value: string }) {
  return (
    <span>
      <i className={className} />
      {label}
      <strong>{value}</strong>
    </span>
  );
}

type DashboardEvent = {
  id: string;
  time: string;
  label: string;
  subtitle: string;
  status: "IN" | "OUT";
  statusTone: BadgeTone;
  statusIcon?: React.ElementType;
  statusLabel: string;
  tone: "green" | "blue" | "gray" | "amber";
  icon: React.ElementType;
};

function getDashboardEvents(events: AccessEvent[], vehicles: Vehicle[], people: Person[]): DashboardEvent[] {
  const peopleById = new Map(people.map((person) => [person.id, person]));
  const vehiclesByRegistration = new Map(vehicles.map((vehicle) => [vehicle.registration_number.toUpperCase(), vehicle]));

  return events.slice(0, 5).map((event) => {
    const vehicle = vehiclesByRegistration.get(event.registration_number.toUpperCase());
    const owner = vehicle?.person_id ? peopleById.get(vehicle.person_id) : undefined;
    const ownerFirstName = owner?.first_name || vehicle?.owner?.split(" ")[0] || "";
    const visitorName = visitorEventDisplayName(event);
    const isDenied = event.decision === "denied" || event.direction === "denied";

    return {
      id: event.id,
      time: formatTime(event.occurred_at),
      label: visitorName || ownerFirstName || "Unknown",
      subtitle: `${event.registration_number}  •  ${event.visitor_pass_id ? "Visitor Pass" : "LPR"}`,
      status: event.direction === "exit" ? "OUT" : "IN",
      statusTone: isDenied ? "amber" : event.direction === "entry" ? "green" : "gray",
      statusIcon: isDenied ? Lock : undefined,
      statusLabel: isDenied ? "Denied" : event.direction === "exit" ? "Out" : "In",
      tone: isDenied ? "amber" : event.direction === "entry" ? "green" : "blue",
      icon: event.direction === "exit" ? LogOut : isDenied ? AlertTriangle : Car
    };
  });
}

function visitorEventDisplayName(event: Pick<AccessEvent, "visitor_name">) {
  const name = (event.visitor_name || "").trim();
  if (!name) return "";
  const parts = name.split(":").map((part) => part.trim()).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : name;
}

function EventStatusBadge({ event }: { event: DashboardEvent }) {
  if (event.statusIcon) {
    const Icon = event.statusIcon;
    return (
      <Badge tone={event.statusTone}>
        <span className="event-status-icon" aria-label={event.statusLabel} title={event.statusLabel}>
          <Icon size={13} aria-hidden="true" />
        </span>
      </Badge>
    );
  }
  return <Badge tone={event.statusTone}>{event.status}</Badge>;
}

type DashboardAnomaly = {
  id: string;
  title: string;
  detail: string;
  time: string;
  severity: AlertSeverity;
};

function getDashboardAnomalies(anomalies: Anomaly[]): DashboardAnomaly[] {
  return anomalies.slice(0, 4).map((item) => ({
    id: item.id,
    title: titleCase(item.type),
    detail: item.message,
    time: formatTime(item.last_seen_at || item.created_at),
    severity: item.severity
  }));
}

function isActionableAlert(alert: Anomaly) {
  return alert.status === "open" && (alert.severity === "warning" || alert.severity === "critical");
}

function isBellAlert(alert: Anomaly) {
  return isActionableAlert(alert) && alert.type !== "unauthorized_plate";
}

function alertIdFromLocation() {
  return new URLSearchParams(window.location.search).get("alert") ?? "";
}

function alertMatchesFocus(alert: Anomaly, focusedAlertId: string) {
  return Boolean(focusedAlertId && (alert.id === focusedAlertId || alert.alert_ids.includes(focusedAlertId)));
}

function alertDomId(alertId: string) {
  return `alert-row-${alertId.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

function alertSeverityTone(severity: AlertSeverity): BadgeTone {
  if (severity === "critical") return "red";
  if (severity === "warning") return "amber";
  return "blue";
}

function alertSeverityLabel(severity: AlertSeverity) {
  if (severity === "info") return "Informational";
  return titleCase(severity);
}

function DailyEntriesChart({ events }: { events: AccessEvent[] }) {
  const days = lastSevenDayBuckets(events);
  const max = Math.max(...days.flatMap((item) => [item.entries, item.exits]), 1);

  return (
    <div className="daily-chart">
      <div className="chart-grid-lines" aria-hidden="true">
        <span>{max}</span>
        <span>{Math.ceil(max * 0.66)}</span>
        <span>{Math.ceil(max * 0.33)}</span>
        <span>0</span>
      </div>
      <div className="chart-bars">
        {days.map((item) => (
          <div className="chart-day" key={item.day}>
            <div className="chart-pair">
              <span className="entry" style={{ height: `${(item.entries / max) * 100}%` }} />
              <span className="exit" style={{ height: `${(item.exits / max) * 100}%` }} />
            </div>
            <small>{item.day}</small>
          </div>
        ))}
      </div>
      <div className="chart-legend">
        <LegendDot className="residents" label="Entries" value="" />
        <LegendDot className="exits" label="Exits" value="" />
      </div>
    </div>
  );
}

function lastSevenDayBuckets(events: AccessEvent[]) {
  const formatter = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date();
    date.setHours(0, 0, 0, 0);
    date.setDate(date.getDate() - (6 - index));
    const nextDate = new Date(date);
    nextDate.setDate(date.getDate() + 1);
    const dayEvents = events.filter((event) => {
      const occurred = new Date(event.occurred_at);
      return occurred >= date && occurred < nextDate;
    });
    return {
      day: formatter.format(date),
      entries: dayEvents.filter((event) => event.direction === "entry").length,
      exits: dayEvents.filter((event) => event.direction === "exit").length
    };
  });
}

function MetricCard({ icon: Icon, label, value, detail, tone }: { icon: React.ElementType; label: string; value: string; detail: string; tone: BadgeTone }) {
  return (
    <div className="card metric-card">
      <div className={`metric-icon ${tone}`}>
        <Icon size={20} />
      </div>
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      <span className="metric-detail">{detail}</span>
    </div>
  );
}

function CardHeader({ icon: Icon, title, action }: { icon: React.ElementType; title: string; action?: React.ReactNode }) {
  return (
    <div className="card-header">
      <div className="card-title">
        <Icon size={17} />
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}

function EventTimeline({ events }: { events: AccessEvent[] }) {
  return (
    <div className="timeline">
      {events.map((event) => (
        <div className="timeline-row" key={event.id}>
          <span className={`event-dot ${event.decision}`} />
          <div>
            <strong>{visitorEventDisplayName(event) || event.registration_number}</strong>
            <span>
              {event.visitor_pass_id ? `${event.registration_number} · ${event.direction} · Visitor Pass` : `${event.direction} · ${event.source}`}
            </span>
          </div>
          <Badge tone={event.decision === "granted" ? "green" : "red"}>{event.decision}</Badge>
          <time>{formatDate(event.occurred_at)}</time>
        </div>
      ))}
    </div>
  );
}

function PresenceList({ presence }: { presence: Presence[] }) {
  return (
    <div className="compact-list">
      {presence.map((item) => (
        <div className="compact-row" key={item.person_id}>
          <div className="avatar">{item.display_name.slice(0, 1)}</div>
          <div>
            <strong>{item.display_name}</strong>
            <span>{item.last_changed_at ? formatDate(item.last_changed_at) : "No recent movement"}</span>
          </div>
          <Badge tone={item.state === "present" ? "green" : "gray"}>{item.state}</Badge>
        </div>
      ))}
    </div>
  );
}

function AnomalyList({ anomalies }: { anomalies: Anomaly[] }) {
  if (!anomalies.length) return <EmptyState icon={CheckCircle2} label="No alerts" />;
  return (
    <div className="compact-list">
      {anomalies.map((item) => (
        <div className="compact-row anomaly-row" key={item.id}>
          <AlertTriangle size={18} />
          <div>
            <strong>{item.type.replaceAll("_", " ")}</strong>
            <span>{item.message}</span>
          </div>
          <Badge tone={alertSeverityTone(item.severity)}>{alertSeverityLabel(item.severity)}</Badge>
        </div>
      ))}
    </div>
  );
}

function RhythmChart({ events }: { events: AccessEvent[] }) {
  const buckets = ["Entry", "Exit", "Denied"];
  const values = [
    events.filter((event) => event.direction === "entry").length,
    events.filter((event) => event.direction === "exit").length,
    events.filter((event) => event.decision === "denied").length
  ];
  const max = Math.max(...values, 1);
  return (
    <div className="bar-chart">
      {buckets.map((bucket, index) => (
        <div className="bar-row" key={bucket}>
          <span>{bucket}</span>
          <div className="bar-track">
            <div className={`bar-fill fill-${index}`} style={{ width: `${(values[index] / max) * 100}%` }} />
          </div>
          <strong>{values[index]}</strong>
        </div>
      ))}
    </div>
  );
}

function GroupsView({
  groups,
  people,
  query,
  refresh
}: {
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedGroup, setSelectedGroup] = React.useState<Group | null>(null);
  const [error, setError] = React.useState("");
  const peopleByGroup = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const person of people) {
      if (person.group_id) counts.set(person.group_id, (counts.get(person.group_id) ?? 0) + 1);
    }
    return counts;
  }, [people]);
  const filtered = groups.filter((group) =>
    matches(group.name, query) ||
    matches(titleCase(group.category), query) ||
    matches(group.subtype ?? "", query)
  );

  const openCreate = () => {
    setSelectedGroup(null);
    setModalOpen(true);
  };

  const openEdit = (group: Group) => {
    setSelectedGroup(group);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedGroup(null);
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Directory</span>
          <h1>Groups</h1>
          <p>Create access groups for family, friends, visitors, and contractors.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> Add Group
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      <div className="card users-card groups-card">
        <PanelHeader title="Group Directory" action={`${filtered.length} groups`} actionKind="select" />
        {filtered.length ? (
          <div className="users-table groups-table">
            {filtered.map((group) => {
              const peopleCount = group.people_count ?? peopleByGroup.get(group.id) ?? 0;
              return (
                <article
                  className="user-row group-row group-row-button"
                  key={group.id}
                  onClick={() => openEdit(group)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      openEdit(group);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <span className={`group-mark ${group.category}`}>
                    <Users size={17} />
                  </span>
                  <div>
                    <strong>{group.name}</strong>
                    <span>{group.subtype || group.description || "General access group"}</span>
                  </div>
                  <Badge tone={groupCategoryTone(group.category)}>{titleCase(group.category)}</Badge>
                  <span className="member-count">{peopleCount} {peopleCount === 1 ? "person" : "people"}</span>
                </article>
              );
            })}
          </div>
        ) : (
          <EmptyState icon={Users} label="No groups match this view" />
        )}
      </div>

      {modalOpen ? (
        <GroupModal
          group={selectedGroup}
          members={selectedGroup ? people.filter((person) => person.group_id === selectedGroup.id) : []}
          mode={selectedGroup ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          setPageError={setError}
        />
      ) : null}
    </section>
  );
}

function GroupModal({
  group,
  members,
  mode,
  onClose,
  onSaved,
  setPageError
}: {
  group: Group | null;
  members: Person[];
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  setPageError: (message: string) => void;
}) {
  const [form, setForm] = React.useState({
    name: group?.name ?? "",
    category: group?.category ?? "family",
    subtype: group?.subtype ?? "",
    description: group?.description ?? ""
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const update = (field: keyof typeof form, value: string) => setForm((current) => ({ ...current, [field]: value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setPageError("");
    setSubmitting(true);
    try {
      const payload = {
        name: form.name,
        category: form.category,
        subtype: form.subtype || null,
        description: form.description || null
      };
      if (mode === "edit" && group) {
        await api.patch<Group>(`/api/v1/groups/${group.id}`, payload);
      } else {
        await api.post<Group>("/api/v1/groups", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save group";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card group-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Group" : "Add Group"}</h2>
            <p>{mode === "edit" ? "Update group details and review assigned members." : "Define a membership bucket for access schedules and directory profiles."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <label className="field">
          <span>Group name</span>
          <div className="field-control">
            <Users size={17} />
            <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
          </div>
        </label>
        <div className="field-grid">
          <label className="field">
            <span>Category</span>
            <select value={form.category} onChange={(event) => update("category", event.target.value)}>
              {groupCategoryOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Subtype</span>
            <div className="field-control">
              <CircleDot size={17} />
              <input value={form.subtype} onChange={(event) => update("subtype", event.target.value)} placeholder="Gardener, overnight guest..." />
            </div>
          </label>
        </div>
        <label className="field">
          <span>Description</span>
          <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
        </label>
        {mode === "edit" ? (
          <div className="group-members-panel">
            <div className="panel-header">
              <h2>Members</h2>
              <span className="member-count">{members.length} {members.length === 1 ? "person" : "people"}</span>
            </div>
            {members.length ? (
              <div className="group-member-list">
                {members.map((member) => (
                  <div className="group-member-row" key={member.id}>
                    <PersonAvatar person={member} />
                    <div>
                      <strong>{member.display_name}</strong>
                      <span>{member.vehicles.length ? member.vehicles.map((vehicle) => vehicle.registration_number).join(", ") : "No vehicles"}</span>
                    </div>
                    <Badge tone={member.is_active ? "green" : "gray"}>{member.is_active ? "Active" : "Inactive"}</Badge>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state compact">No members assigned</div>
            )}
          </div>
        ) : null}
        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <Plus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Group"}
          </button>
        </div>
      </form>
    </div>
  );
}

const scheduleDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const scheduleDayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const scheduleSlotCount = 48;
const scheduleMinutesPerSlot = 30;

function SchedulesView({
  schedules,
  query,
  refresh
}: {
  schedules: Schedule[];
  query: string;
  refresh: () => Promise<void>;
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedSchedule, setSelectedSchedule] = React.useState<Schedule | null>(null);
  const [error, setError] = React.useState("");
  const [policySaved, setPolicySaved] = React.useState("");
  const [policySaving, setPolicySaving] = React.useState(false);
  const accessSettings = useSettings("access");
  const defaultPolicy = String(accessSettings.values.schedule_default_policy ?? "allow").toLowerCase() === "deny" ? "deny" : "allow";
  const filtered = schedules.filter((schedule) =>
    matches(schedule.name, query) ||
    matches(schedule.description ?? "", query) ||
    matches(scheduleSummary(schedule.time_blocks), query)
  );

  React.useEffect(() => {
    if (!policySaved) return undefined;
    const timer = window.setTimeout(() => setPolicySaved(""), 5200);
    return () => window.clearTimeout(timer);
  }, [policySaved]);

  const openCreate = () => {
    setSelectedSchedule(null);
    setModalOpen(true);
  };

  const openEdit = (schedule: Schedule) => {
    setSelectedSchedule(schedule);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedSchedule(null);
  };

  const deleteSchedule = async (schedule: Schedule) => {
    if (!window.confirm(`Delete ${schedule.name}?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/schedules/${schedule.id}`);
      await refresh();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete schedule");
    }
  };

  const updateDefaultPolicy = async (policy: "allow" | "deny") => {
    if (policy === defaultPolicy || policySaving) return;
    setError("");
    setPolicySaved("");
    setPolicySaving(true);
    try {
      await accessSettings.save({ schedule_default_policy: policy });
      setPolicySaved("Default policy saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save default policy");
    } finally {
      setPolicySaving(false);
    }
  };

  return (
    <section className="view-stack schedules-page">
      <div className="users-hero schedules-hero card">
        <div className="schedules-hero-main">
          <div>
            <span className="eyebrow">Access Control</span>
            <h1>Schedules</h1>
            <p>Reusable weekly access templates for people, vehicles, gates, and garage doors.</p>
          </div>
          <button className="primary-button" onClick={openCreate} type="button">
            <Plus size={17} /> New Schedule
          </button>
        </div>
        <section className="schedule-policy-card" aria-labelledby="schedule-default-policy-title">
          <div className="schedule-policy-copy">
            <div className="schedule-card-icon">
              <ShieldCheck size={18} />
            </div>
            <div>
              <h2 id="schedule-default-policy-title">Default Policy</h2>
              <p>Used when a person, vehicle, gate, or garage door has no schedule assigned.</p>
            </div>
          </div>
          <div className="schedule-policy-actions" role="group" aria-label="No schedule default policy">
            <button
              aria-label="Always Allow"
              aria-pressed={defaultPolicy === "allow"}
              className={defaultPolicy === "allow" ? "schedule-policy-option active allow" : "schedule-policy-option allow"}
              disabled={accessSettings.loading || policySaving}
              onClick={() => updateDefaultPolicy("allow")}
              type="button"
            >
              <CheckCircle2 size={16} />
              <span className="policy-label-full">Always Allow</span>
              <span className="policy-label-short">Allow</span>
            </button>
            <button
              aria-label="Never Allow"
              aria-pressed={defaultPolicy === "deny"}
              className={defaultPolicy === "deny" ? "schedule-policy-option active deny" : "schedule-policy-option deny"}
              disabled={accessSettings.loading || policySaving}
              onClick={() => updateDefaultPolicy("deny")}
              type="button"
            >
              <Lock size={16} />
              <span className="policy-label-full">Never Allow</span>
              <span className="policy-label-short">Deny</span>
            </button>
          </div>
          <div className="schedule-policy-status">
            {policySaving ? (
              <Badge tone="gray">Saving</Badge>
            ) : policySaved ? (
              <span className="schedule-policy-saved-pill">
                <Badge tone="green">Saved</Badge>
              </span>
            ) : null}
          </div>
        </section>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {accessSettings.error ? <div className="auth-error inline-error">{accessSettings.error}</div> : null}

      <div className="schedule-card-grid">
        {filtered.length ? filtered.map((schedule) => (
          <article className="card schedule-card" key={schedule.id}>
            <button className="schedule-card-main" onClick={() => openEdit(schedule)} type="button">
              <div className="schedule-card-icon">
                <Clock3 size={18} />
              </div>
              <div className="schedule-card-copy">
                <strong>{schedule.name}</strong>
                <span>{schedule.description || scheduleSummary(schedule.time_blocks)}</span>
              </div>
              <Badge tone={scheduleHasBlocks(schedule.time_blocks) ? "green" : "amber"}>
                {scheduleSummary(schedule.time_blocks)}
              </Badge>
            </button>
            <div className="schedule-card-days" aria-hidden="true">
              {scheduleDays.map((day, index) => (
                <span
                  className={scheduleDayHasBlocks(schedule.time_blocks, index) ? "active" : ""}
                  key={day}
                >
                  {day.slice(0, 1)}
                </span>
              ))}
            </div>
            <div className="schedule-card-actions">
              <button className="secondary-button" onClick={() => openEdit(schedule)} type="button">
                <CalendarDays size={15} /> Edit
              </button>
              <button className="icon-button danger" onClick={() => deleteSchedule(schedule)} type="button" aria-label={`Delete ${schedule.name}`}>
                <Trash2 size={15} />
              </button>
            </div>
          </article>
        )) : (
          <div className="card schedule-empty-card">
            <EmptyState icon={Clock3} label="No schedules match this view" />
          </div>
        )}
      </div>

      {modalOpen ? (
        <ScheduleModal
          mode={selectedSchedule ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          schedule={selectedSchedule}
          setPageError={setError}
        />
      ) : null}
    </section>
  );
}

const visitorPassStatuses: VisitorPassStatus[] = ["active", "scheduled", "used", "expired", "cancelled"];
const defaultVisitorPassFilters = new Set<VisitorPassStatus>(["active", "scheduled"]);
const visitorPassWindowOptions = [30, 60, 90, 120, 180];

function PassesView({ query, realtime }: { query: string; realtime: RealtimeMessage[] }) {
  const [passes, setPasses] = React.useState<VisitorPass[]>([]);
  const [filters, setFilters] = React.useState<Set<VisitorPassStatus>>(() => new Set(defaultVisitorPassFilters));
  const [modalPass, setModalPass] = React.useState<VisitorPass | null>(null);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [cancellingId, setCancellingId] = React.useState<string | null>(null);

  const loadPasses = React.useCallback(async () => {
    const params = new URLSearchParams();
    if (filters.size && filters.size < visitorPassStatuses.length) {
      filters.forEach((status) => params.append("status", status));
    }
    if (query.trim()) params.set("q", query.trim());
    const suffix = params.toString() ? `?${params.toString()}` : "";
    setLoading(true);
    setError("");
    try {
      setPasses(await api.get<VisitorPass[]>(`/api/v1/visitor-passes${suffix}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Visitor Passes");
    } finally {
      setLoading(false);
    }
  }, [filters, query]);

  React.useEffect(() => {
    loadPasses().catch(() => undefined);
  }, [loadPasses]);

  React.useEffect(() => {
    const latest = realtime[0];
    if (!latest) return;
    if (isVisitorPassRealtimeEvent(latest)) {
      const livePass = visitorPassFromRealtime(latest);
      if (livePass) {
        setPasses((current) => [livePass, ...current.filter((item) => item.id !== livePass.id)]);
      }
      loadPasses().catch(() => undefined);
    } else if (latest.type === "access_event.finalized") {
      loadPasses().catch(() => undefined);
    }
  }, [realtime, loadPasses]);

  const openCreate = () => {
    setModalPass(null);
    setModalOpen(true);
  };

  const openEdit = (visitorPass: VisitorPass) => {
    setModalPass(visitorPass);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setModalPass(null);
  };

  const cancelPass = async (visitorPass: VisitorPass) => {
    if (!window.confirm(`Cancel Visitor Pass for ${visitorPass.visitor_name}?`)) return;
    setCancellingId(visitorPass.id);
    setError("");
    try {
      await api.post<VisitorPass>(`/api/v1/visitor-passes/${visitorPass.id}/cancel`, { reason: "Cancelled from dashboard" });
      await loadPasses();
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Unable to cancel Visitor Pass");
    } finally {
      setCancellingId(null);
    }
  };

  const visiblePasses = passes.filter((visitorPass) => visitorPassMatchesStatus(visitorPass, filters) && visitorPassMatches(visitorPass, query));
  const counts = React.useMemo(() => visitorPassStatuses.reduce<Record<VisitorPassStatus, number>>((acc, status) => {
    acc[status] = passes.filter((visitorPass) => visitorPass.status === status).length;
    return acc;
  }, { active: 0, scheduled: 0, used: 0, expired: 0, cancelled: 0 }), [passes]);

  return (
    <section className="view-stack passes-page">
      <div className="users-hero passes-hero card">
        <div>
          <span className="eyebrow">Anticipatory Access</span>
          <h1>Passes</h1>
          <p>One-shot visitor windows for unknown vehicles, with captured arrival, vehicle, and duration telemetry.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> Visitor Pass
        </button>
      </div>

      <div className="passes-toolbar card">
        <PassFilterBar counts={counts} filters={filters} onChange={setFilters} />
        <button className="secondary-button" onClick={() => loadPasses()} type="button">
          <RefreshCw size={15} /> Refresh
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      {loading ? (
        <div className="card passes-loading">
          <Loader2 className="spin" size={18} /> Loading Visitor Passes
        </div>
      ) : visiblePasses.length ? (
        <div className="visitor-pass-grid">
          {visiblePasses.map((visitorPass) => (
            <VisitorPassCard
              cancelling={cancellingId === visitorPass.id}
              key={visitorPass.id}
              onCancel={cancelPass}
              onEdit={openEdit}
              visitorPass={visitorPass}
            />
          ))}
        </div>
      ) : (
        <div className="card passes-empty-card">
          <EmptyState icon={ClipboardPaste} label="No Visitor Passes match this view" />
        </div>
      )}

      {modalOpen ? (
        <VisitorPassModal
          mode={modalPass ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await loadPasses();
            closeModal();
          }}
          visitorPass={modalPass}
        />
      ) : null}
    </section>
  );
}

function PassFilterBar({
  filters,
  counts,
  onChange
}: {
  filters: Set<VisitorPassStatus>;
  counts: Record<VisitorPassStatus, number>;
  onChange: (filters: Set<VisitorPassStatus>) => void;
}) {
  const allSelected = filters.size === visitorPassStatuses.length;
  const toggleStatus = (status: VisitorPassStatus) => {
    const next = new Set(filters);
    if (next.has(status)) {
      next.delete(status);
    } else {
      next.add(status);
    }
    onChange(next.size ? next : new Set(defaultVisitorPassFilters));
  };
  return (
    <div className="pass-filter-bar" role="group" aria-label="Visitor Pass status filters">
      <button className={allSelected ? "active" : ""} onClick={() => onChange(new Set(visitorPassStatuses))} type="button">
        All
      </button>
      {visitorPassStatuses.map((status) => (
        <button
          aria-pressed={filters.has(status)}
          className={filters.has(status) ? "active" : ""}
          key={status}
          onClick={() => toggleStatus(status)}
          type="button"
        >
          {titleCase(status)}
          <span>{counts[status]}</span>
        </button>
      ))}
    </div>
  );
}

function VisitorPassCard({
  visitorPass,
  cancelling,
  onEdit,
  onCancel
}: {
  visitorPass: VisitorPass;
  cancelling: boolean;
  onEdit: (visitorPass: VisitorPass) => void;
  onCancel: (visitorPass: VisitorPass) => void;
}) {
  const editable = visitorPass.status === "active" || visitorPass.status === "scheduled";
  const vehicleSummary = visitorPassVehicleSummary(visitorPass);
  const windowLabel = visitorPassWindowLabel(visitorPass);
  const sourceLabel = visitorPassSourceLabel(visitorPass.creation_source);
  return (
    <article className={`card visitor-pass-card ${visitorPass.status}`}>
      <div className="visitor-pass-card-head">
        <div className="visitor-pass-icon">
          <ClipboardPaste size={18} />
        </div>
        <div>
          <strong>{visitorPass.visitor_name}</strong>
          <span>{formatDate(visitorPass.expected_time)} · {windowLabel}</span>
        </div>
        <Badge tone={visitorPassStatusTone(visitorPass.status)}>{titleCase(visitorPass.status)}</Badge>
      </div>

      <div className="visitor-pass-window">
        <div>
          <Clock3 size={15} />
          <span>{formatDate(visitorPass.window_start)} to {formatDate(visitorPass.window_end)}</span>
        </div>
        <div>
          <GitBranch size={15} />
          <span>{sourceLabel}{visitorPass.created_by ? ` · ${visitorPass.created_by}` : ""}</span>
        </div>
      </div>

      {visitorPass.status === "used" ? (
        <section className="visitor-pass-telemetry">
          <div className="visitor-pass-vehicle">
            <Car size={17} />
            <div>
              <strong>{vehicleSummary || "Vehicle details pending"}</strong>
              <span>{visitorPass.arrival_time ? `Arrived ${formatDate(visitorPass.arrival_time)}` : "Arrival not recorded"}</span>
            </div>
          </div>
          <div className="visitor-pass-duration">
            <Badge tone={visitorPass.departure_time ? "green" : "amber"}>
              {visitorPass.duration_human || (visitorPass.departure_time ? "Duration pending" : "On site")}
            </Badge>
            {visitorPass.departure_time ? <span>Left {formatDate(visitorPass.departure_time)}</span> : <span>Departure pending</span>}
          </div>
        </section>
      ) : null}

      <div className="visitor-pass-actions">
        {editable ? (
          <>
            <button className="secondary-button" onClick={() => onEdit(visitorPass)} type="button">
              <Pencil size={15} /> Edit
            </button>
            <button className="secondary-button danger" disabled={cancelling} onClick={() => onCancel(visitorPass)} type="button">
              <X size={15} /> {cancelling ? "Cancelling..." : "Cancel"}
            </button>
          </>
        ) : (
          <span>{visitorPass.number_plate || "No plate linked"}</span>
        )}
      </div>
    </article>
  );
}

function VisitorPassModal({
  mode,
  visitorPass,
  onClose,
  onSaved
}: {
  mode: "create" | "edit";
  visitorPass: VisitorPass | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [visitorName, setVisitorName] = React.useState(visitorPass?.visitor_name ?? "");
  const [expectedTime, setExpectedTime] = React.useState(() => visitorPass ? new Date(visitorPass.expected_time) : nextVisitorPassDate());
  const [windowMinutes, setWindowMinutes] = React.useState(visitorPass?.window_minutes ?? 30);
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    const payload = {
      visitor_name: visitorName.trim(),
      expected_time: expectedTime.toISOString(),
      window_minutes: windowMinutes
    };
    try {
      if (mode === "edit" && visitorPass) {
        await api.patch<VisitorPass>(`/api/v1/visitor-passes/${visitorPass.id}`, payload);
      } else {
        await api.post<VisitorPass>("/api/v1/visitor-passes", payload);
      }
      await onSaved();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save Visitor Pass");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card visitor-pass-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Visitor Pass" : "New Visitor Pass"}</h2>
            <p>{formatDate(expectedTime.toISOString())} · +/- {windowMinutes} minutes</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}

        <label className="field">
          <span>Visitor name</span>
          <div className="field-control">
            <UserPlus size={17} />
            <input value={visitorName} onChange={(event) => setVisitorName(event.target.value)} required />
          </div>
        </label>

        <VisitorDateTimePicker value={expectedTime} onChange={setExpectedTime} />

        <div className="visitor-pass-window-select">
          <span>Time Window</span>
          <div>
            {visitorPassWindowOptions.map((minutes) => (
              <button
                aria-pressed={windowMinutes === minutes}
                className={windowMinutes === minutes ? "active" : ""}
                key={minutes}
                onClick={() => setWindowMinutes(minutes)}
                type="button"
              >
                +/- {minutes}m
              </button>
            ))}
          </div>
        </div>

        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            <Save size={16} />
            {submitting ? "Saving..." : mode === "edit" ? "Save Pass" : "Create Pass"}
          </button>
        </div>
      </form>
    </div>
  );
}

function VisitorDateTimePicker({ value, onChange }: { value: Date; onChange: (value: Date) => void }) {
  const [visibleMonth, setVisibleMonth] = React.useState(() => new Date(value.getFullYear(), value.getMonth(), 1));
  const days = visitorCalendarDays(visibleMonth);
  const selectedKey = visitorDateKey(value);
  const timeValue = `${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
  const timeOptions = visitorTimeOptions(timeValue);

  const setDay = (day: Date) => {
    const next = new Date(day);
    next.setHours(value.getHours(), value.getMinutes(), 0, 0);
    onChange(next);
  };

  const setTime = (time: string) => {
    const [hour, minute] = time.split(":").map(Number);
    const next = new Date(value);
    next.setHours(hour, minute, 0, 0);
    onChange(next);
  };

  return (
    <section className="visitor-date-picker">
      <div className="visitor-date-picker-head">
        <button aria-label="Previous month" className="icon-button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1))} type="button">
          <ChevronDown className="rotate-90" size={15} />
        </button>
        <strong>{visibleMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong>
        <button aria-label="Next month" className="icon-button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1))} type="button">
          <ChevronRight size={15} />
        </button>
      </div>
      <div className="visitor-calendar-grid">
        {scheduleDays.map((day) => <span key={day}>{day.slice(0, 2)}</span>)}
        {days.map((day) => (
          <button
            className={`${day.getMonth() === visibleMonth.getMonth() ? "" : "muted"} ${visitorDateKey(day) === selectedKey ? "active" : ""}`}
            key={day.toISOString()}
            onClick={() => setDay(day)}
            type="button"
          >
            {day.getDate()}
          </button>
        ))}
      </div>
      <label className="field">
        <span>Expected time</span>
        <select value={timeValue} onChange={(event) => setTime(event.target.value)}>
          {timeOptions.map((time) => (
            <option key={time} value={time}>{time}</option>
          ))}
        </select>
      </label>
    </section>
  );
}

function ScheduleModal({
  mode,
  onClose,
  onSaved,
  schedule,
  setPageError
}: {
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  schedule: Schedule | null;
  setPageError: (message: string) => void;
}) {
  const [form, setForm] = React.useState({
    name: schedule?.name ?? "",
    description: schedule?.description ?? "",
    time_blocks: normalizeScheduleBlocks(schedule?.time_blocks ?? emptyScheduleBlocks())
  });
  const [dependencies, setDependencies] = React.useState<ScheduleDependencies | null>(null);
  const [dependenciesLoading, setDependenciesLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  React.useEffect(() => {
    if (!schedule) {
      setDependencies(null);
      return;
    }
    setDependenciesLoading(true);
    api.get<ScheduleDependencies>(`/api/v1/schedules/${schedule.id}/dependencies`)
      .then(setDependencies)
      .catch(() => setDependencies(null))
      .finally(() => setDependenciesLoading(false));
  }, [schedule]);

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setPageError("");
    setSubmitting(true);
    const payload = {
      name: form.name,
      description: form.description || null,
      time_blocks: normalizeScheduleBlocks(form.time_blocks)
    };
    try {
      if (mode === "edit" && schedule) {
        await api.patch<Schedule>(`/api/v1/schedules/${schedule.id}`, payload);
      } else {
        await api.post<Schedule>("/api/v1/schedules", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save schedule";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card schedule-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Schedule" : "New Schedule"}</h2>
            <p>{scheduleSummary(form.time_blocks)}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}

        <div className="schedule-modal-grid">
          <div className="schedule-details-panel">
            <label className="field">
              <span>Schedule name</span>
              <div className="field-control">
                <Clock3 size={17} />
                <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
              </div>
            </label>
            <label className="field">
              <span>Description</span>
              <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
            </label>
            <ScheduleDependencyPanel dependencies={dependencies} loading={dependenciesLoading} />
          </div>

          <WeeklyScheduleGrid
            value={form.time_blocks}
            onChange={(timeBlocks) => update("time_blocks", timeBlocks)}
          />
        </div>

        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            <Save size={16} />
            {submitting ? "Saving..." : mode === "edit" ? "Save Schedule" : "Create Schedule"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ScheduleDependencyPanel({
  dependencies,
  loading
}: {
  dependencies: ScheduleDependencies | null;
  loading: boolean;
}) {
  const items = dependencies ? [
    ...dependencies.people.map((item) => ({ ...item, tone: "blue" as BadgeTone })),
    ...dependencies.vehicles.map((item) => ({ ...item, tone: "green" as BadgeTone })),
    ...dependencies.doors.map((item) => ({ ...item, tone: "amber" as BadgeTone }))
  ] : [];

  return (
    <section className="schedule-dependencies">
      <div className="panel-header">
        <h2>In Use By</h2>
        <Badge tone={items.length ? "blue" : "gray"}>{loading ? "loading" : String(items.length)}</Badge>
      </div>
      {loading ? (
        <div className="schedule-dependency-empty">Loading dependencies</div>
      ) : items.length ? (
        <div className="schedule-dependency-list">
          {items.map((item) => (
            <span className={`schedule-dependency-pill ${item.tone}`} key={`${item.kind}-${item.id}`}>
              {dependencyIcon(item.kind)}
              <span>{item.name}</span>
            </span>
          ))}
        </div>
      ) : (
        <div className="schedule-dependency-empty">No assignments</div>
      )}
    </section>
  );
}

function dependencyIcon(kind: string) {
  if (kind === "vehicle") return <Car size={13} />;
  if (kind === "gate" || kind === "garage_door") return <Warehouse size={13} />;
  return <UserRound size={13} />;
}

function WeeklyScheduleGrid({
  value,
  onChange
}: {
  value: ScheduleTimeBlocks;
  onChange: (timeBlocks: ScheduleTimeBlocks) => void;
}) {
  const [selectedSlots, setSelectedSlots] = React.useState<Set<string>>(() => scheduleBlocksToSlots(value));
  const calendarRef = React.useRef<HTMLDivElement | null>(null);
  const dragRef = React.useRef<ScheduleDragState>({
    active: false,
    targetSelected: false,
    anchorDay: 0,
    anchorSlot: 0,
    baseSlots: new Set()
  });
  const autoScrollRef = React.useRef<{ frame: number | null; clientX: number; clientY: number }>({
    frame: null,
    clientX: 0,
    clientY: 0
  });
  const [copiedBlock, setCopiedBlock] = React.useState<ScheduleCopiedBlock | null>(null);
  const [contextMenu, setContextMenu] = React.useState<ScheduleContextMenu | null>(null);

  React.useEffect(() => {
    setSelectedSlots(scheduleBlocksToSlots(value));
  }, [value]);

  const commitSlots = React.useCallback((nextSlots: Set<string>) => {
    setSelectedSlots(nextSlots);
    onChange(slotsToScheduleBlocks(nextSlots));
  }, [onChange]);

  const applyDragRange = React.useCallback((day: number, slot: number) => {
    const drag = dragRef.current;
    if (!drag.active) return;

    const next = new Set(drag.baseSlots);
    const startDay = Math.min(drag.anchorDay, day);
    const endDay = Math.max(drag.anchorDay, day);
    const startSlot = Math.min(drag.anchorSlot, slot);
    const endSlot = Math.max(drag.anchorSlot, slot);

    for (let rangeDay = startDay; rangeDay <= endDay; rangeDay += 1) {
      for (let rangeSlot = startSlot; rangeSlot <= endSlot; rangeSlot += 1) {
        const key = scheduleSlotKey(rangeDay, rangeSlot);
        if (drag.targetSelected) {
          next.add(key);
        } else {
          next.delete(key);
        }
      }
    }

    setSelectedSlots(next);
    onChange(slotsToScheduleBlocks(next));
  }, [onChange]);

  const applyBlockToDays = React.useCallback((days: number[], block: ScheduleCopiedBlock) => {
    setSelectedSlots((current) => {
      const next = new Set(current);
      for (const day of days) {
        for (let slot = block.startSlot; slot <= block.endSlot; slot += 1) {
          next.add(scheduleSlotKey(day, slot));
        }
      }
      onChange(slotsToScheduleBlocks(next));
      return next;
    });
    setContextMenu(null);
  }, [onChange]);

  const stopAutoScroll = React.useCallback(() => {
    if (autoScrollRef.current.frame !== null) {
      window.cancelAnimationFrame(autoScrollRef.current.frame);
      autoScrollRef.current.frame = null;
    }
  }, []);

  const runAutoScroll = React.useCallback(() => {
    const calendar = calendarRef.current;
    if (!dragRef.current.active || !calendar) {
      autoScrollRef.current.frame = null;
      return;
    }

    const { clientX, clientY } = autoScrollRef.current;
    const rect = calendar.getBoundingClientRect();
    const edgeSize = 56;
    const maxStep = 18;
    let top = 0;
    let left = 0;

    if (clientY < rect.top + edgeSize) {
      top = -Math.ceil(((rect.top + edgeSize - clientY) / edgeSize) * maxStep);
    } else if (clientY > rect.bottom - edgeSize) {
      top = Math.ceil(((clientY - (rect.bottom - edgeSize)) / edgeSize) * maxStep);
    }

    if (clientX < rect.left + edgeSize) {
      left = -Math.ceil(((rect.left + edgeSize - clientX) / edgeSize) * maxStep);
    } else if (clientX > rect.right - edgeSize) {
      left = Math.ceil(((clientX - (rect.right - edgeSize)) / edgeSize) * maxStep);
    }

    if (top !== 0 || left !== 0) {
      calendar.scrollBy({ top, left });
      const cell = scheduleCellFromPoint(clientX, clientY, calendar);
      if (cell) applyDragRange(cell.day, cell.slot);
    }

    autoScrollRef.current.frame = window.requestAnimationFrame(runAutoScroll);
  }, [applyDragRange]);

  const updateAutoScrollPointer = React.useCallback((clientX: number, clientY: number) => {
    autoScrollRef.current.clientX = clientX;
    autoScrollRef.current.clientY = clientY;
    if (autoScrollRef.current.frame === null) {
      autoScrollRef.current.frame = window.requestAnimationFrame(runAutoScroll);
    }
  }, [runAutoScroll]);

  React.useEffect(() => {
    const onPointerMove = (event: PointerEvent) => {
      if (!dragRef.current.active) return;
      updateAutoScrollPointer(event.clientX, event.clientY);
      const cell = scheduleCellFromPoint(event.clientX, event.clientY, calendarRef.current);
      if (!cell) return;
      applyDragRange(cell.day, cell.slot);
    };
    const onPointerUp = () => {
      dragRef.current.active = false;
      stopAutoScroll();
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      stopAutoScroll();
    };
  }, [applyDragRange, stopAutoScroll, updateAutoScrollPointer]);

  React.useEffect(() => {
    if (!contextMenu) return undefined;
    const closeMenu = () => setContextMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    window.addEventListener("pointerdown", closeMenu);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", closeMenu, true);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [contextMenu]);

  const startPaint = (day: number, slot: number, event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    setContextMenu(null);
    const targetSelected = !selectedSlots.has(scheduleSlotKey(day, slot));
    dragRef.current = {
      active: true,
      targetSelected,
      anchorDay: day,
      anchorSlot: slot,
      baseSlots: new Set(selectedSlots)
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    updateAutoScrollPointer(event.clientX, event.clientY);
    applyDragRange(day, slot);
  };

  const openCellMenu = (day: number, slot: number, event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    dragRef.current.active = false;
    stopAutoScroll();

    const point = scheduleContextMenuPoint(event.clientX, event.clientY);
    const key = scheduleSlotKey(day, slot);
    if (selectedSlots.has(key)) {
      const range = selectedSlotRange(selectedSlots, day, slot);
      if (range) {
        setContextMenu({ kind: "selected", day, range, ...point });
      }
      return;
    }

    setContextMenu({ kind: "empty", day, ...point });
  };

  const copyContextRange = () => {
    if (contextMenu?.kind !== "selected") return;
    setCopiedBlock(contextMenu.range);
    setContextMenu(null);
  };

  const replicateContextRange = (days: number[]) => {
    if (contextMenu?.kind !== "selected") return;
    applyBlockToDays(days, contextMenu.range);
  };

  const clearContextRange = () => {
    if (contextMenu?.kind !== "selected") return;
    const { day, range } = contextMenu;
    setSelectedSlots((current) => {
      const next = new Set(current);
      for (let slot = range.startSlot; slot <= range.endSlot; slot += 1) {
        next.delete(scheduleSlotKey(day, slot));
      }
      onChange(slotsToScheduleBlocks(next));
      return next;
    });
    setContextMenu(null);
  };

  const clearAllContextRanges = () => {
    commitSlots(new Set());
    setContextMenu(null);
  };

  const pasteCopiedBlock = () => {
    if (contextMenu?.kind !== "empty" || !copiedBlock) return;
    applyBlockToDays([contextMenu.day], copiedBlock);
  };

  const applyPreset = (preset: "clear" | "all" | "weekdays" | "mornings") => {
    const next = new Set<string>();
    if (preset === "all") {
      for (let day = 0; day < 7; day += 1) {
        for (let slot = 0; slot < scheduleSlotCount; slot += 1) next.add(scheduleSlotKey(day, slot));
      }
    }
    if (preset === "weekdays") {
      addSlotRange(next, [0, 1, 2, 3, 4], 9 * 60, 17 * 60);
    }
    if (preset === "mornings") {
      addSlotRange(next, [0, 1, 2, 3, 4], 7 * 60, 12 * 60);
    }
    commitSlots(next);
  };

  return (
    <section className="weekly-schedule-panel">
      <div className="weekly-schedule-toolbar">
        <div>
          <strong>Weekly Access</strong>
          <span>{scheduleSummary(slotsToScheduleBlocks(selectedSlots))}</span>
        </div>
        <div>
          <button className="secondary-button" onClick={() => applyPreset("weekdays")} type="button">Weekdays</button>
          <button className="secondary-button" onClick={() => applyPreset("mornings")} type="button">Mornings</button>
          <button className="secondary-button" onClick={() => applyPreset("all")} type="button">24/7</button>
          <button className="secondary-button" onClick={() => applyPreset("clear")} type="button">Clear</button>
        </div>
      </div>

      <div className="schedule-calendar" onDragStart={(event) => event.preventDefault()} ref={calendarRef}>
        <div className="schedule-calendar-head">
          <span />
          {scheduleDays.map((day) => <strong key={day}>{day}</strong>)}
        </div>
        <div className="schedule-calendar-body">
          <div className="schedule-time-axis" aria-hidden="true">
            {Array.from({ length: 24 }, (_, hour) => (
              <span key={hour} style={{ gridRow: `${hour * 2 + 1} / span 2` }}>{`${hour.toString().padStart(2, "0")}:00`}</span>
            ))}
          </div>
          {scheduleDays.map((day, dayIndex) => (
            <div className="schedule-day-column" key={day}>
              {Array.from({ length: scheduleSlotCount }, (_, slot) => {
                const key = scheduleSlotKey(dayIndex, slot);
                const selected = selectedSlots.has(key);
                const previousSelected = selectedSlots.has(scheduleSlotKey(dayIndex, slot - 1));
                const nextSelected = selectedSlots.has(scheduleSlotKey(dayIndex, slot + 1));
                const className = [
                  "schedule-cell",
                  selected ? "selected" : "",
                  selected && !previousSelected ? "selected-start" : "",
                  selected && !nextSelected ? "selected-end" : ""
                ].filter(Boolean).join(" ");
                return (
                  <button
                    aria-label={`${scheduleDayNames[dayIndex]} ${formatSlotLabel(slot)} ${selected ? "allowed" : "blocked"}`}
                    className={className}
                    data-day={dayIndex}
                    data-schedule-cell="true"
                    data-slot={slot}
                    key={key}
                    onContextMenu={(event) => openCellMenu(dayIndex, slot, event)}
                    onPointerDown={(event) => startPaint(dayIndex, slot, event)}
                    type="button"
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      {contextMenu ? (
        <div
          className="schedule-context-menu"
          onContextMenu={(event) => event.preventDefault()}
          onPointerDown={(event) => event.stopPropagation()}
          role="menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          {contextMenu.kind === "selected" ? (
            <>
              <div className="schedule-context-menu-label">
                <span>{scheduleDayNames[contextMenu.day]}</span>
                <strong>{formatScheduleBlockLabel(contextMenu.range)}</strong>
              </div>
              <button onClick={copyContextRange} role="menuitem" type="button">
                <Copy size={15} />
                Copy
              </button>
              <button onClick={() => replicateContextRange([0, 1, 2, 3, 4, 5, 6])} role="menuitem" type="button">
                <CalendarDays size={15} />
                Replicate All Week
              </button>
              <button onClick={() => replicateContextRange([0, 1, 2, 3, 4])} role="menuitem" type="button">
                <CalendarDays size={15} />
                Replicate Week Days Only
              </button>
              <div aria-hidden="true" className="schedule-context-menu-separator" />
              <button className="danger" onClick={clearContextRange} role="menuitem" type="button">
                <Trash2 size={15} />
                Clear Selected
              </button>
              <button className="danger" onClick={clearAllContextRanges} role="menuitem" type="button">
                <X size={15} />
                Clear All
              </button>
            </>
          ) : (
            <>
              <div className="schedule-context-menu-label">
                <span>{scheduleDayNames[contextMenu.day]}</span>
                <strong>{copiedBlock ? formatScheduleBlockLabel(copiedBlock) : "Nothing copied"}</strong>
              </div>
              <button disabled={!copiedBlock} onClick={pasteCopiedBlock} role="menuitem" type="button">
                <ClipboardPaste size={15} />
                Paste
              </button>
              <div aria-hidden="true" className="schedule-context-menu-separator" />
              <button className="danger" disabled={selectedSlots.size === 0} onClick={clearAllContextRanges} role="menuitem" type="button">
                <X size={15} />
                Clear All
              </button>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}

function scheduleCellFromPoint(
  clientX: number,
  clientY: number,
  calendar: HTMLDivElement | null
): ScheduleCellPoint | null {
  const element = document.elementFromPoint(clientX, clientY);
  const cell = element?.closest("[data-schedule-cell='true']") as HTMLElement | null;
  if (cell) {
    const day = Number(cell.dataset.day);
    const slot = Number(cell.dataset.slot);
    if (Number.isInteger(day) && Number.isInteger(slot)) return { day, slot };
  }

  if (!calendar) return null;

  const calendarRect = calendar.getBoundingClientRect();
  const edgeSlack = 72;
  if (
    clientX < calendarRect.left ||
    clientX > calendarRect.right ||
    clientY < calendarRect.top - edgeSlack ||
    clientY > calendarRect.bottom + edgeSlack
  ) {
    return null;
  }

  const body = calendar.querySelector<HTMLElement>(".schedule-calendar-body");
  if (!body) return null;

  const bodyRect = body.getBoundingClientRect();
  const axis = body.querySelector<HTMLElement>(".schedule-time-axis");
  const axisWidth = axis?.getBoundingClientRect().width ?? 56;
  const dayWidth = (bodyRect.width - axisWidth) / scheduleDays.length;
  const slotHeight = bodyRect.height / scheduleSlotCount;
  if (dayWidth <= 0 || slotHeight <= 0) return null;

  const rawDay = Math.floor((clientX - bodyRect.left - axisWidth) / dayWidth);
  const rawSlot = Math.floor((clientY - bodyRect.top) / slotHeight);
  const day = Math.max(0, Math.min(scheduleDays.length - 1, rawDay));
  const slot = Math.max(0, Math.min(scheduleSlotCount - 1, rawSlot));
  return { day, slot };
}

function selectedSlotRange(slots: Set<string>, day: number, slot: number): ScheduleCopiedBlock | null {
  if (!slots.has(scheduleSlotKey(day, slot))) return null;
  let startSlot = slot;
  let endSlot = slot;
  while (startSlot > 0 && slots.has(scheduleSlotKey(day, startSlot - 1))) startSlot -= 1;
  while (endSlot < scheduleSlotCount - 1 && slots.has(scheduleSlotKey(day, endSlot + 1))) endSlot += 1;
  return { startSlot, endSlot };
}

function scheduleContextMenuPoint(clientX: number, clientY: number) {
  const menuWidth = 244;
  const menuHeight = 292;
  return {
    x: Math.max(12, Math.min(clientX, window.innerWidth - menuWidth - 12)),
    y: Math.max(12, Math.min(clientY, window.innerHeight - menuHeight - 12))
  };
}

function formatScheduleBlockLabel(block: ScheduleCopiedBlock) {
  return `${formatScheduleMinute(block.startSlot * scheduleMinutesPerSlot)} - ${formatScheduleMinute((block.endSlot + 1) * scheduleMinutesPerSlot)}`;
}

function emptyScheduleBlocks(): ScheduleTimeBlocks {
  return Object.fromEntries(scheduleDays.map((_, index) => [String(index), []])) as ScheduleTimeBlocks;
}

function normalizeScheduleBlocks(blocks: ScheduleTimeBlocks): ScheduleTimeBlocks {
  return slotsToScheduleBlocks(scheduleBlocksToSlots(blocks));
}

function scheduleBlocksToSlots(blocks: ScheduleTimeBlocks): Set<string> {
  const slots = new Set<string>();
  for (let day = 0; day < 7; day += 1) {
    for (const block of blocks[String(day)] ?? []) {
      const start = parseScheduleTime(block.start);
      const end = parseScheduleTime(block.end);
      if (start == null || end == null || start >= end) continue;
      for (let minute = start; minute < end; minute += scheduleMinutesPerSlot) {
        const slot = Math.floor(minute / scheduleMinutesPerSlot);
        if (slot >= 0 && slot < scheduleSlotCount) slots.add(scheduleSlotKey(day, slot));
      }
    }
  }
  return slots;
}

function slotsToScheduleBlocks(slots: Set<string>): ScheduleTimeBlocks {
  const blocks = emptyScheduleBlocks();
  for (let day = 0; day < 7; day += 1) {
    const selected = Array.from({ length: scheduleSlotCount }, (_, slot) => slots.has(scheduleSlotKey(day, slot)));
    const intervals: ScheduleTimeBlock[] = [];
    let startSlot: number | null = null;
    for (let slot = 0; slot <= scheduleSlotCount; slot += 1) {
      const active = selected[slot] ?? false;
      if (active && startSlot === null) {
        startSlot = slot;
      }
      if ((!active || slot === scheduleSlotCount) && startSlot !== null) {
        intervals.push({
          start: formatScheduleMinute(startSlot * scheduleMinutesPerSlot),
          end: formatScheduleMinute(slot * scheduleMinutesPerSlot)
        });
        startSlot = null;
      }
    }
    blocks[String(day)] = intervals;
  }
  return blocks;
}

function addSlotRange(target: Set<string>, days: number[], startMinute: number, endMinute: number) {
  for (const day of days) {
    for (let minute = startMinute; minute < endMinute; minute += scheduleMinutesPerSlot) {
      target.add(scheduleSlotKey(day, minute / scheduleMinutesPerSlot));
    }
  }
}

function scheduleSlotKey(day: number, slot: number) {
  return `${day}:${slot}`;
}

function parseScheduleTime(value: string) {
  if (value === "24:00" || value === "23:59") return 24 * 60;
  const [hours, minutes] = value.split(":").map((part) => Number(part));
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  return hours * 60 + minutes;
}

function formatScheduleMinute(value: number) {
  if (value >= 24 * 60) return "24:00";
  return `${Math.floor(value / 60).toString().padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
}

function formatSlotLabel(slot: number) {
  return `${formatScheduleMinute(slot * scheduleMinutesPerSlot)}-${formatScheduleMinute((slot + 1) * scheduleMinutesPerSlot)}`;
}

function scheduleHasBlocks(blocks: ScheduleTimeBlocks) {
  return Object.values(blocks ?? {}).some((items) => items.length);
}

function scheduleDayHasBlocks(blocks: ScheduleTimeBlocks, day: number) {
  return Boolean(blocks?.[String(day)]?.length);
}

function scheduleSummary(blocks: ScheduleTimeBlocks) {
  const selected = scheduleBlocksToSlots(blocks);
  if (selected.size === 0) return "No allowed time";
  if (selected.size === scheduleSlotCount * 7) return "24/7";
  const hours = selected.size / 2;
  const days = Array.from({ length: 7 }, (_, day) =>
    Array.from({ length: scheduleSlotCount }, (_, slot) => selected.has(scheduleSlotKey(day, slot))).some(Boolean)
  ).filter(Boolean).length;
  return `${hours % 1 === 0 ? hours : hours.toFixed(1)}h across ${days} day${days === 1 ? "" : "s"}`;
}

function scheduleDefaultPolicyDisplay(value: unknown) {
  return String(value ?? "allow").trim().toLowerCase() === "deny" ? "Never Allow" : "Always Allow";
}

function useScheduleDefaultPolicyOptionLabel() {
  const accessSettings = useSettings("access");
  return `Default Policy - ${scheduleDefaultPolicyDisplay(accessSettings.values.schedule_default_policy)}`;
}

function PeopleView({
  garageDoors,
  groups,
  people,
  query,
  refresh,
  schedules,
  vehicles
}: {
  garageDoors: HomeAssistantManagedCover[];
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedPerson, setSelectedPerson] = React.useState<Person | null>(null);
  const [error, setError] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const availableGarageDoors = React.useMemo(() => activeManagedCovers(garageDoors), [garageDoors]);
  const filtered = people.filter((item) =>
    matches(item.display_name, query) ||
    matches(item.group ?? "", query) ||
    item.vehicles.some((vehicle) => matches(vehicle.registration_number, query)) ||
    (item.garage_door_entity_ids ?? []).some((entityId) => matches(garageDoors.find((door) => door.entity_id === entityId)?.name ?? entityId, query)) ||
    matches(item.home_assistant_mobile_app_notify_service ?? "", query)
  );
  const assignedVehicleIds = React.useMemo(() => new Set(people.flatMap((person) => person.vehicles.map((vehicle) => vehicle.id))), [people]);
  const garageDoorNameMap = React.useMemo(() => new Map(garageDoors.map((door) => [door.entity_id, door.name || door.entity_id])), [garageDoors]);

  const openCreate = () => {
    setSelectedPerson(null);
    setModalOpen(true);
  };

  const openEdit = (person: Person) => {
    setSelectedPerson(person);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedPerson(null);
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Directory</span>
          <h1>People</h1>
          <p>Manage profiles, access groups, and vehicle assignments.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <UserPlus size={17} /> Add Person
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      <div className="card users-card people-card">
        <PanelHeader title="Profile Roster" action={`${filtered.length} profiles`} actionKind="select" />
        {filtered.length ? (
          <div className="users-table people-table">
            {filtered.map((person) => (
              <article
                className="user-row person-row person-row-button"
                key={person.id}
                onClick={() => openEdit(person)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openEdit(person);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <PersonAvatar person={person} />
                <div>
                  <strong>{person.display_name}</strong>
                  <span>{person.category ? titleCase(person.category) : "No category"}{person.group ? ` • ${person.group}` : ""}</span>
                </div>
                <Badge tone={person.is_active ? "green" : "gray"}>{person.is_active ? "Active" : "Inactive"}</Badge>
                <div className="vehicle-chip-list">
                  {person.schedule ? <span className="vehicle-chip schedule-chip">{person.schedule}</span> : null}
                  {person.vehicles.length ? person.vehicles.map((vehicle) => (
                    <span className="vehicle-chip" key={vehicle.id}>{vehicle.registration_number}</span>
                  )) : <span className="muted-value">No vehicles</span>}
                  {(person.garage_door_entity_ids ?? []).map((entityId) => (
                    <span className="vehicle-chip garage-chip" key={entityId}>{garageDoorNameMap.get(entityId) ?? entityId}</span>
                  ))}
                  {person.home_assistant_mobile_app_notify_service ? <span className="vehicle-chip ha-chip">HA mobile</span> : null}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState icon={Users} label="No people match this view" />
        )}
      </div>

      {modalOpen ? (
        <PersonModal
          assignedVehicleIds={assignedVehicleIds}
          defaultPolicyOptionLabel={defaultPolicyOptionLabel}
          garageDoors={availableGarageDoors}
          groups={groups}
          mode={selectedPerson ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          person={selectedPerson}
          schedules={schedules}
          setPageError={setError}
          vehicles={vehicles}
        />
      ) : null}
    </section>
  );
}

function PersonModal({
  assignedVehicleIds,
  defaultPolicyOptionLabel,
  garageDoors,
  groups,
  mode,
  onClose,
  onSaved,
  person,
  schedules,
  setPageError,
  vehicles
}: {
  assignedVehicleIds: Set<string>;
  defaultPolicyOptionLabel: string;
  garageDoors: HomeAssistantManagedCover[];
  groups: Group[];
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  person: Person | null;
  schedules: Schedule[];
  setPageError: (message: string) => void;
  vehicles: Vehicle[];
}) {
  const [form, setForm] = React.useState({
    first_name: person?.first_name ?? "",
    last_name: person?.last_name ?? "",
    profile_photo_data_url: person?.profile_photo_data_url ?? "",
    group_id: person?.group_id ?? groups[0]?.id ?? "",
    schedule_id: person?.schedule_id ?? "",
    vehicle_ids: person?.vehicles.map((vehicle) => vehicle.id) ?? ([] as string[]),
    garage_door_entity_ids: person?.garage_door_entity_ids ?? ([] as string[]),
    home_assistant_mobile_app_notify_service: person?.home_assistant_mobile_app_notify_service ?? "",
    notes: person?.notes ?? "",
    is_active: person?.is_active ?? true
  });
  const [error, setError] = React.useState("");
  const [haDiscovery, setHaDiscovery] = React.useState<HomeAssistantDiscovery | null>(null);
  const [haDiscoveryError, setHaDiscoveryError] = React.useState("");
  const [haDiscoveryLoading, setHaDiscoveryLoading] = React.useState(false);
  const [haMobileSelectionTouched, setHaMobileSelectionTouched] = React.useState(Boolean(person?.home_assistant_mobile_app_notify_service));
  const [haSuggestion, setHaSuggestion] = React.useState<HomeAssistantPersonSuggestion>({});
  const [haTestFeedback, setHaTestFeedback] = React.useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [sendingHaTest, setSendingHaTest] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => setForm((current) => ({ ...current, [field]: value }));

    const uploadPhoto = async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Please choose an image file.");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setError("Profile images must be 8 MB or smaller.");
      return;
    }
    setError("");
    update("profile_photo_data_url", await fileToDataUrl(file));
  };

  const toggleVehicle = (vehicleId: string) => {
    update(
      "vehicle_ids",
      form.vehicle_ids.includes(vehicleId)
        ? form.vehicle_ids.filter((id) => id !== vehicleId)
        : [...form.vehicle_ids, vehicleId]
    );
  };

  const toggleGarageDoor = (entityId: string) => {
    update(
      "garage_door_entity_ids",
      form.garage_door_entity_ids.includes(entityId)
        ? form.garage_door_entity_ids.filter((id) => id !== entityId)
        : [...form.garage_door_entity_ids, entityId]
    );
  };

  const updateMobileNotifyService = (serviceId: string) => {
    setHaMobileSelectionTouched(true);
    setHaTestFeedback(null);
    update("home_assistant_mobile_app_notify_service", serviceId);
  };

  const sendHomeAssistantMobileTest = async () => {
    if (!form.home_assistant_mobile_app_notify_service) {
      setHaTestFeedback({ tone: "error", text: "Select a mobile app notification service first." });
      return;
    }
    const personName = `${form.first_name} ${form.last_name}`.trim() || person?.display_name || "this person";
    setSendingHaTest(true);
    setHaTestFeedback({ tone: "info", text: "Sending Home Assistant test notification." });
    try {
      await api.post("/api/v1/integrations/home-assistant/mobile-notifications/test", {
        service_name: form.home_assistant_mobile_app_notify_service,
        person_name: personName
      });
      setHaTestFeedback({ tone: "success", text: "Home Assistant accepted the test notification." });
    } catch (testError) {
      setHaTestFeedback({
        tone: "error",
        text: testError instanceof Error ? testError.message : "Unable to send Home Assistant test notification."
      });
    } finally {
      setSendingHaTest(false);
    }
  };

  React.useEffect(() => {
    let active = true;
    setHaDiscoveryLoading(true);
    setHaDiscoveryError("");
    api.get<HomeAssistantDiscovery>("/api/v1/integrations/home-assistant/entities")
      .then((discovery) => {
        if (!active) return;
        setHaDiscovery(discovery);
      })
      .catch((loadError) => {
        if (!active) return;
        setHaDiscoveryError(loadError instanceof Error ? loadError.message : "Unable to load Home Assistant entities.");
      })
      .finally(() => {
        if (active) setHaDiscoveryLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  React.useEffect(() => {
    if (!haDiscovery) return;
    const firstName = form.first_name.trim();
    const lastName = form.last_name.trim();
    if (!firstName || !lastName) {
      setHaSuggestion({});
      return;
    }

    const timeout = window.setTimeout(() => {
      const suggestion = suggestHomeAssistantPersonIntegrations(firstName, lastName, haDiscovery);
      setHaSuggestion(suggestion);
      setForm((current) => ({
        ...current,
        home_assistant_mobile_app_notify_service:
          !haMobileSelectionTouched && !current.home_assistant_mobile_app_notify_service && suggestion.mobile?.id
            ? suggestion.mobile.id
            : current.home_assistant_mobile_app_notify_service
      }));
    }, 700);

    return () => window.clearTimeout(timeout);
  }, [form.first_name, form.last_name, haDiscovery, haMobileSelectionTouched]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setPageError("");
    setSubmitting(true);
    const payload = {
      first_name: form.first_name,
      last_name: form.last_name,
      profile_photo_data_url: form.profile_photo_data_url || null,
      group_id: form.group_id || null,
      schedule_id: form.schedule_id || null,
      vehicle_ids: form.vehicle_ids,
      garage_door_entity_ids: form.garage_door_entity_ids,
      home_assistant_mobile_app_notify_service: form.home_assistant_mobile_app_notify_service || null,
      notes: form.notes || null,
      is_active: form.is_active
    };
    try {
      if (mode === "edit" && person) {
        await api.patch<Person>(`/api/v1/people/${person.id}`, payload);
      } else {
        await api.post<Person>("/api/v1/people", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save person";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const previewPerson: Person = {
    id: "preview",
    first_name: form.first_name,
    last_name: form.last_name,
    display_name: `${form.first_name} ${form.last_name}`.trim() || "New person",
    profile_photo_data_url: form.profile_photo_data_url || null,
    group_id: form.group_id || null,
    group: groups.find((group) => group.id === form.group_id)?.name ?? null,
    category: groups.find((group) => group.id === form.group_id)?.category ?? null,
    schedule_id: form.schedule_id || null,
    schedule: schedules.find((schedule) => schedule.id === form.schedule_id)?.name ?? null,
    is_active: form.is_active,
    notes: form.notes || null,
    garage_door_entity_ids: form.garage_door_entity_ids,
    home_assistant_mobile_app_notify_service: form.home_assistant_mobile_app_notify_service || null,
    vehicles: []
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card person-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Person" : "Add Person"}</h2>
            <p>{mode === "edit" ? "Update the profile, group, and vehicle assignments." : "Create a directory profile and assign registered vehicles."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="profile-upload-row">
          <PersonAvatar person={previewPerson} size="large" />
          <label className="upload-button">
            <Camera size={16} />
            <span>{form.profile_photo_data_url ? "Change photo" : "Upload profile picture"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {form.profile_photo_data_url ? (
            <button className="secondary-button" onClick={() => update("profile_photo_data_url", "")} type="button">
              Remove
            </button>
          ) : null}
        </div>
        <div className="field-grid">
          <label className="field">
            <span>First name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} autoComplete="given-name" required />
            </div>
          </label>
          <label className="field">
            <span>Last name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} autoComplete="family-name" required />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Group</span>
            <select value={form.group_id} onChange={(event) => update("group_id", event.target.value)}>
              <option value="">No group</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>{group.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Access Schedule</span>
            <select value={form.schedule_id} onChange={(event) => update("schedule_id", event.target.value)}>
              <option value="">{defaultPolicyOptionLabel}</option>
              {schedules.map((schedule) => (
                <option key={schedule.id} value={schedule.id}>{schedule.name}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Status</span>
            <select value={form.is_active ? "active" : "inactive"} onChange={(event) => update("is_active", event.target.value === "active")}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        <section className="person-ha-section">
          <div className="person-ha-section-title">
            <span className="ha-device-icon"><Home size={17} /></span>
            <div>
              <strong>Home Assistant</strong>
              <span>{haDiscoveryLoading ? "Loading discovered entities" : haDiscovery ? "Mobile notification link" : "Save credentials in API & Integrations to enable discovery"}</span>
            </div>
          </div>
          {haDiscoveryError ? <div className="auth-error inline-error">{haDiscoveryError}</div> : null}
          <div className="field-grid">
            <MobileAppNotifySelectField
              label="Mobile app notification"
              value={form.home_assistant_mobile_app_notify_service}
              services={haDiscovery?.mobile_app_notification_services ?? []}
              onChange={updateMobileNotifyService}
            />
          </div>
          <div className="person-ha-actions">
            <button
              className="secondary-button"
              disabled={sendingHaTest || !form.home_assistant_mobile_app_notify_service}
              onClick={sendHomeAssistantMobileTest}
              type="button"
            >
              <Send size={15} /> {sendingHaTest ? "Sending..." : "Send Test"}
            </button>
            <span>{form.home_assistant_mobile_app_notify_service || "No mobile app service selected"}</span>
          </div>
          {haTestFeedback ? (
            <div className={`person-ha-test-feedback ${haTestFeedback.tone}`}>{haTestFeedback.text}</div>
          ) : null}
          {haSuggestion.mobile ? (
            <div className="person-ha-suggestions">
              <span>Mobile match {Math.round(haSuggestion.mobile.confidence * 100)}%</span>
            </div>
          ) : null}
        </section>
        <label className="field">
          <span>Operational notes</span>
          <textarea value={form.notes} onChange={(event) => update("notes", event.target.value)} rows={3} />
        </label>
        <div className="field">
          <span>Vehicles</span>
          <div className="vehicle-picker">
            {vehicles.length ? vehicles.map((vehicle) => {
              const selected = form.vehicle_ids.includes(vehicle.id);
              const assigned = assignedVehicleIds.has(vehicle.id) && !selected;
              return (
                <label className={selected ? "vehicle-option selected" : "vehicle-option"} key={vehicle.id}>
                  <input checked={selected} onChange={() => toggleVehicle(vehicle.id)} type="checkbox" />
                  <span>
                    <strong>{vehicle.registration_number}</strong>
                    <small>{vehicle.description ?? ([vehicle.make, vehicle.model].filter(Boolean).join(" ") || "Registered vehicle")}</small>
                  </span>
                  {selected ? <Badge tone="blue">Selected</Badge> : assigned ? <Badge tone="amber">Assigned</Badge> : <Badge tone="gray">Available</Badge>}
                </label>
              );
            }) : <div className="empty-state compact">No vehicles available</div>}
          </div>
        </div>
        <div className="field">
          <span>Garage Doors</span>
          <div className="vehicle-picker garage-door-picker">
            {garageDoors.length ? garageDoors.map((door) => {
              const selected = form.garage_door_entity_ids.includes(door.entity_id);
              return (
                <label className={selected ? "vehicle-option garage-door-option selected" : "vehicle-option garage-door-option"} key={door.entity_id}>
                  <input checked={selected} onChange={() => toggleGarageDoor(door.entity_id)} type="checkbox" />
                  <span>
                    <strong>{door.name || door.entity_id}</strong>
                    <small>{door.entity_id}</small>
                  </span>
                  {selected ? <Badge tone="blue">Selected</Badge> : <Badge tone="gray">Available</Badge>}
                </label>
              );
            }) : <div className="empty-state compact">No garage doors configured</div>}
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <UserPlus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Person"}
          </button>
        </div>
      </form>
    </div>
  );
}

function VehiclesView({
  people,
  query,
  refresh,
  schedules,
  vehicles
}: {
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedVehicle, setSelectedVehicle] = React.useState<Vehicle | null>(null);
  const [error, setError] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const filtered = vehicles.filter((item) =>
    matches(item.registration_number, query) ||
    matches(item.owner ?? "", query) ||
    matches(item.make ?? "", query) ||
    matches(item.model ?? "", query) ||
    matches(item.color ?? "", query)
  );

  const openCreate = () => {
    setSelectedVehicle(null);
    setModalOpen(true);
  };

  const openEdit = (vehicle: Vehicle) => {
    setSelectedVehicle(vehicle);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedVehicle(null);
  };

  const deleteVehicle = async (vehicle: Vehicle) => {
    if (!window.confirm(`Delete ${vehicle.registration_number}?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/vehicles/${vehicle.id}`);
      await refresh();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete vehicle");
    }
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Directory</span>
          <h1>Vehicles</h1>
          <p>Manage registered vehicles, photos, plates, and assigned drivers.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> Add Vehicle
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      <div className="card users-card vehicles-card">
        {filtered.length ? (
          <div className="users-table vehicles-table">
            {filtered.map((vehicle) => (
              <article
                className="user-row vehicle-row vehicle-row-button"
                key={vehicle.id}
                onClick={() => openEdit(vehicle)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openEdit(vehicle);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <VehiclePhoto vehicle={vehicle} />
                <div className="vehicle-row-main">
                  <strong>{vehicle.registration_number}</strong>
                  <span>{vehicleTitle(vehicle)}</span>
                </div>
                <span className="vehicle-owner">{vehicle.owner ?? "Unassigned"}</span>
                <span className={vehicle.schedule ? "vehicle-chip schedule-chip" : "vehicle-chip inherit-chip"}>
                  {vehicle.schedule ?? "Inherit"}
                </span>
                <Badge tone={vehicle.is_active !== false ? "green" : "gray"}>{vehicle.is_active !== false ? "Active" : "Inactive"}</Badge>
                <button
                  className="icon-button danger"
                  onClick={(event) => {
                    event.stopPropagation();
                    deleteVehicle(vehicle).catch(() => undefined);
                  }}
                  type="button"
                  aria-label={`Delete ${vehicle.registration_number}`}
                >
                  <Trash2 size={16} />
                </button>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState icon={Car} label="No vehicles match this view" />
        )}
      </div>

      {modalOpen ? (
        <VehicleModal
          defaultPolicyOptionLabel={defaultPolicyOptionLabel}
          mode={selectedVehicle ? "edit" : "create"}
            onClose={closeModal}
            onSaved={async () => {
              await refresh();
              closeModal();
            }}
            people={people}
            refreshVehicles={refresh}
            schedules={schedules}
            setPageError={setError}
            vehicle={selectedVehicle}
          />
      ) : null}
    </section>
  );
}

function VehicleModal({
  defaultPolicyOptionLabel,
  mode,
  onClose,
  onSaved,
  people,
  refreshVehicles,
  schedules,
  setPageError,
  vehicle
}: {
  defaultPolicyOptionLabel: string;
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  people: Person[];
  refreshVehicles: () => Promise<void>;
  schedules: Schedule[];
  setPageError: (message: string) => void;
  vehicle: Vehicle | null;
}) {
  const [form, setForm] = React.useState({
    registration_number: vehicle?.registration_number ?? "",
    vehicle_photo_data_url: vehicle?.vehicle_photo_data_url ?? "",
    make: vehicle?.make ?? "",
    model: vehicle?.model ?? "",
    color: vehicle?.color ?? "",
    mot_status: vehicle?.mot_status ?? "",
    tax_status: vehicle?.tax_status ?? "",
    mot_expiry: vehicle?.mot_expiry ?? "",
    tax_expiry: vehicle?.tax_expiry ?? "",
    last_dvla_lookup_date: vehicle?.last_dvla_lookup_date ?? "",
    description: vehicle?.description ?? "",
    person_id: vehicle?.person_id ?? "",
    schedule_id: vehicle?.schedule_id ?? "",
    is_active: vehicle?.is_active ?? true
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [complianceRefreshing, setComplianceRefreshing] = React.useState(false);
  const [dvlaLookup, setDvlaLookup] = React.useState<{ status: "idle" | "loading" | "found" | "error"; message: string }>({
    status: "idle",
    message: ""
  });
  const lookupRequestRef = React.useRef(0);
  const lastLookupRegistrationRef = React.useRef("");
  const initialRegistrationRef = React.useRef(vehicle?.registration_number ?? "");

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => setForm((current) => ({ ...current, [field]: value }));

  React.useEffect(() => {
    const registrationNumber = normalizePlateInput(form.registration_number);
    const initialRegistration = normalizePlateInput(initialRegistrationRef.current);
    if (registrationNumber.length < 2 || (mode === "edit" && registrationNumber === initialRegistration)) {
      setDvlaLookup({ status: "idle", message: "" });
      return;
    }
    if (registrationNumber === lastLookupRegistrationRef.current) return;

    const requestId = lookupRequestRef.current + 1;
    lookupRequestRef.current = requestId;
    setDvlaLookup({ status: "loading", message: "Looking up DVLA vehicle details" });

    const timer = window.setTimeout(async () => {
      try {
          const result = await api.post<DvlaLookupResponse>("/api/v1/integrations/dvla/lookup", {
            registration_number: registrationNumber
          });
          if (lookupRequestRef.current !== requestId) return;
          lastLookupRegistrationRef.current = registrationNumber;
          const displayVehicle = result.display_vehicle ?? result.vehicle;
          const normalizedVehicle = result.normalized_vehicle;
          const make = normalizedVehicle?.make || (typeof displayVehicle.make === "string" ? displayVehicle.make : "");
          const model = typeof displayVehicle.model === "string" ? displayVehicle.model : "";
          const normalizedColor = normalizedVehicle?.colour ?? normalizedVehicle?.color;
          const color = normalizedColor || (typeof (displayVehicle.colour ?? displayVehicle.color) === "string" ? String(displayVehicle.colour ?? displayVehicle.color) : "");
          setForm((current) => ({
            ...current,
            registration_number: result.registration_number || current.registration_number,
            make: make || current.make,
            model: model || current.model,
            color: color || current.color,
            mot_status: normalizedVehicle?.mot_status ?? current.mot_status,
            tax_status: normalizedVehicle?.tax_status ?? current.tax_status,
            mot_expiry: normalizedVehicle?.mot_expiry ?? current.mot_expiry,
            tax_expiry: normalizedVehicle?.tax_expiry ?? current.tax_expiry,
            last_dvla_lookup_date: normalizedVehicle ? localDateKey() : current.last_dvla_lookup_date
          }));
        setDvlaLookup({ status: "found", message: "DVLA details applied" });
      } catch (lookupError) {
        if (lookupRequestRef.current !== requestId) return;
        const message = lookupError instanceof Error ? lookupError.message : "DVLA lookup failed";
        if (message.toLowerCase().includes("api key is not configured")) {
          lastLookupRegistrationRef.current = registrationNumber;
          setDvlaLookup({ status: "idle", message: "" });
          return;
        }
        setDvlaLookup({ status: "error", message });
      }
    }, 850);

    return () => window.clearTimeout(timer);
  }, [form.registration_number, mode]);

  const uploadPhoto = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Please choose an image file.");
      return;
    }
      if (file.size > 8 * 1024 * 1024) {
        setError("Vehicle images must be 8 MB or smaller.");
        return;
      }
      setError("");
      update("vehicle_photo_data_url", await fileToDataUrl(file));
    };

    const refreshCompliance = async () => {
      if (mode !== "edit" || !vehicle) return;
      setError("");
      setPageError("");
      setComplianceRefreshing(true);
      try {
        const refreshed = await api.post<Vehicle>(`/api/v1/vehicles/${vehicle.id}/dvla-refresh`);
        setForm((current) => ({
          ...current,
          make: refreshed.make ?? current.make,
          color: refreshed.color ?? current.color,
          mot_status: refreshed.mot_status ?? "",
          tax_status: refreshed.tax_status ?? "",
          mot_expiry: refreshed.mot_expiry ?? "",
          tax_expiry: refreshed.tax_expiry ?? "",
          last_dvla_lookup_date: refreshed.last_dvla_lookup_date ?? ""
        }));
        await refreshVehicles();
      } catch (lookupError) {
        const message = lookupError instanceof Error ? lookupError.message : "Unable to refresh DVLA compliance";
        setError(message);
        setPageError(message);
      } finally {
        setComplianceRefreshing(false);
      }
    };

    const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setPageError("");
    setSubmitting(true);
      const payload = {
        registration_number: form.registration_number,
        vehicle_photo_data_url: form.vehicle_photo_data_url || null,
        make: form.make || null,
        model: form.model || null,
        color: form.color || null,
        mot_status: form.mot_status || null,
        tax_status: form.tax_status || null,
        mot_expiry: form.mot_expiry || null,
        tax_expiry: form.tax_expiry || null,
        last_dvla_lookup_date: form.last_dvla_lookup_date || null,
        description: form.description || null,
        person_id: form.person_id || null,
      schedule_id: form.schedule_id || null,
      is_active: form.is_active
    };
    try {
      if (mode === "edit" && vehicle) {
        await api.patch<Vehicle>(`/api/v1/vehicles/${vehicle.id}`, payload);
      } else {
        await api.post<Vehicle>("/api/v1/vehicles", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save vehicle";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const previewVehicle: Vehicle = {
    id: vehicle?.id ?? "preview",
    registration_number: form.registration_number || "NEW",
    vehicle_photo_data_url: form.vehicle_photo_data_url || null,
    description: form.description || null,
    make: form.make || null,
    model: form.model || null,
    color: form.color || null,
      mot_status: form.mot_status || null,
      tax_status: form.tax_status || null,
      mot_expiry: form.mot_expiry || null,
      tax_expiry: form.tax_expiry || null,
      last_dvla_lookup_date: form.last_dvla_lookup_date || null,
    person_id: form.person_id || null,
    owner: people.find((person) => person.id === form.person_id)?.display_name ?? null,
    schedule_id: form.schedule_id || null,
    schedule: schedules.find((schedule) => schedule.id === form.schedule_id)?.name ?? null,
    is_active: form.is_active
  };
    const motStatus = form.mot_status || null;
    const taxStatus = form.tax_status || null;

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card vehicle-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Vehicle" : "Add Vehicle"}</h2>
            <p>{mode === "edit" ? "Update vehicle details and assignment." : "Register a vehicle and assign it to a person."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="vehicle-upload-row">
          <VehiclePhoto vehicle={previewVehicle} size="large" />
          <label className="upload-button">
            <Camera size={16} />
            <span>{form.vehicle_photo_data_url ? "Change photo" : "Upload vehicle photo"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {form.vehicle_photo_data_url ? (
            <button className="secondary-button" onClick={() => update("vehicle_photo_data_url", "")} type="button">
              Remove
            </button>
          ) : null}
        </div>
        <label className="field">
          <span>Vehicle Registration</span>
          <div className="field-control">
            <Car size={17} />
            <input value={form.registration_number} onChange={(event) => update("registration_number", event.target.value.toUpperCase())} required />
          </div>
          {dvlaLookup.status !== "idle" ? (
            <small className={`field-hint dvla-lookup-hint ${dvlaLookup.status}`}>
              {dvlaLookup.status === "loading" ? <span className="inline-spinner" aria-hidden="true" /> : null}
              {dvlaLookup.message}
            </small>
          ) : null}
        </label>
        <div className="field-grid">
          <label className="field">
            <span>Vehicle Make</span>
            <div className="field-control">
              <Car size={17} />
              <input value={form.make} onChange={(event) => update("make", event.target.value)} />
            </div>
          </label>
          <label className="field">
            <span>Vehicle Model</span>
            <div className="field-control">
              <Car size={17} />
              <input value={form.model} onChange={(event) => update("model", event.target.value)} />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Colour</span>
            <div className="field-control">
              <CircleDot size={17} />
              <input value={form.color} onChange={(event) => update("color", event.target.value)} />
            </div>
          </label>
          <label className="field">
            <span>Status</span>
            <select value={form.is_active ? "active" : "inactive"} onChange={(event) => update("is_active", event.target.value === "active")}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        <label className="field">
          <span>Friendly description</span>
          <div className="field-control">
            <Type size={17} />
            <input value={form.description} onChange={(event) => update("description", event.target.value)} />
          </div>
        </label>
        <label className="field">
          <span>Assigned person</span>
          <select value={form.person_id} onChange={(event) => update("person_id", event.target.value)}>
            <option value="">Unassigned</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>{person.display_name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Access Schedule</span>
          <select value={form.schedule_id} onChange={(event) => update("schedule_id", event.target.value)}>
            <option value="">{defaultPolicyOptionLabel}</option>
            {schedules.map((schedule) => (
              <option key={schedule.id} value={schedule.id}>{schedule.name}</option>
            ))}
          </select>
        </label>
          <div className="vehicle-compliance-card">
            <div className="vehicle-compliance-title">
              <ShieldCheck size={17} />
              <div>
                <strong>Compliance</strong>
                <span>{vehicleLastDvlaCheckLabel(form.last_dvla_lookup_date || null)}</span>
              </div>
              {mode === "edit" ? (
                <button
                  aria-label="Refresh DVLA compliance"
                  className="icon-button vehicle-compliance-refresh"
                  disabled={complianceRefreshing}
                  onClick={refreshCompliance}
                  title="Refresh DVLA compliance"
                  type="button"
                >
                  <RefreshCw className={complianceRefreshing ? "spin" : undefined} size={15} />
                </button>
              ) : null}
            </div>
            <div className="vehicle-compliance-grid">
              <div className="vehicle-compliance-row">
                <span className="vehicle-compliance-label">MOT</span>
                <Badge tone={motComplianceTone(motStatus)}>{motStatus || "Unknown"}</Badge>
                <span className="vehicle-compliance-expiry">{vehicleComplianceExpiryLabel(form.mot_expiry || null)}</span>
              </div>
              <div className="vehicle-compliance-row">
                <span className="vehicle-compliance-label">Tax</span>
                <Badge tone={taxComplianceTone(taxStatus)}>{taxStatus || "Unknown"}</Badge>
                <span className="vehicle-compliance-expiry">{vehicleComplianceExpiryLabel(form.tax_expiry || null)}</span>
              </div>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <Plus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Vehicle"}
          </button>
        </div>
      </form>
    </div>
  );
}

function motComplianceTone(status: string | null | undefined): BadgeTone {
  const normalized = String(status || "").trim().toLowerCase().replace(/_/g, " ");
  if (!normalized) return "gray";
  return normalized === "valid" || normalized === "not required" ? "green" : "red";
}

function taxComplianceTone(status: string | null | undefined): BadgeTone {
  const normalized = String(status || "").trim().toLowerCase();
  if (!normalized) return "gray";
  if (normalized === "taxed") return "green";
  if (normalized === "sorn") return "gray";
  return "red";
}

function vehicleComplianceExpiryLabel(value: string | null | undefined) {
  return value ? `Expires ${formatDateOnly(value)}` : "Expiry unavailable";
}

function vehicleLastDvlaCheckLabel(value: string | null | undefined) {
  if (!value) return "Not checked yet";
  return dateOnlyKey(value) === localDateKey() ? "Last checked with DVLA: Today" : `Last checked with DVLA: ${formatDateOnly(value)}`;
}

function TopChartsView({ query, realtime }: { query: string; realtime: RealtimeMessage[] }) {
  const [leaderboard, setLeaderboard] = React.useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [refreshing, setRefreshing] = React.useState(false);
  const [error, setError] = React.useState("");

  const load = React.useCallback(async () => {
    setRefreshing(true);
    setError("");
    try {
      setLeaderboard(await api.get<LeaderboardResponse>("/api/v1/leaderboard"));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Top Charts.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const latestRealtime = realtime[0];
  React.useEffect(() => {
    if (!latestRealtime) return;
    if (latestRealtime.type === "access_event.finalized" || latestRealtime.type === "leaderboard_overtake") {
      load().catch(() => undefined);
    }
  }, [latestRealtime?.created_at, latestRealtime?.type, load]);

  const knownRows = React.useMemo(
    () => (leaderboard?.known ?? []).filter((item) => leaderboardKnownMatches(item, query)),
    [leaderboard?.known, query]
  );
  const unknownRows = React.useMemo(
    () => (leaderboard?.unknown ?? []).filter((item) => leaderboardUnknownMatches(item, query)),
    [leaderboard?.unknown, query]
  );
  const knownReadCount = React.useMemo(
    () => knownRows.reduce((total, item) => total + item.read_count, 0),
    [knownRows]
  );
  const unknownReadCount = React.useMemo(
    () => unknownRows.reduce((total, item) => total + item.read_count, 0),
    [unknownRows]
  );

  return (
    <section className="view-stack top-charts-page">
      <Toolbar title="Top Charts" count={knownRows.length + unknownRows.length} icon={Trophy}>
        <button className="secondary-button" onClick={() => load().catch(() => undefined)} disabled={refreshing} type="button">
          <RefreshCcw size={15} /> {refreshing ? "Refreshing" : "Refresh"}
        </button>
      </Toolbar>

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? (
        <div className="loading-panel">Loading Top Charts</div>
      ) : (
        <div className="top-charts-grid">
          <section className="card top-charts-card top-charts-known-card">
            <div className="top-charts-card-header">
              <div>
                <span className="eyebrow">Known Plates</span>
                <h2>The VIP Lounge</h2>
                <p>Known plates battling for driveway supremacy.</p>
              </div>
              <Badge tone="green">{knownReadCount} Detectiions</Badge>
            </div>

            {knownRows.length ? (
              <div className="top-charts-list">
                {knownRows.map((entry) => (
                  <LeaderboardKnownRow entry={entry} key={`${entry.vehicle_id}-${entry.registration_number}`} />
                ))}
              </div>
            ) : (
              <EmptyState icon={Trophy} label="No VIP Detectiions yet" />
            )}
          </section>

          <section className="card top-charts-card top-charts-unknown-card">
            <div className="top-charts-card-header">
              <div>
                <span className="eyebrow">Unknown Plates</span>
                <h2>The Mystery Guests</h2>
                <p>Who are these people and why do they keep turning around in the driveway?</p>
              </div>
              <Badge tone="amber">{unknownReadCount} Detectiions</Badge>
            </div>

            {unknownRows.length ? (
              <div className="top-charts-list">
                {unknownRows.map((entry) => (
                  <LeaderboardUnknownRow entry={entry} key={entry.registration_number} />
                ))}
              </div>
            ) : (
              <EmptyState icon={Search} label="No mystery guests yet" />
            )}
          </section>
        </div>
      )}
    </section>
  );
}

function LeaderboardKnownRow({ entry }: { entry: LeaderboardKnownEntry }) {
  const firstName = entry.person.first_name || entry.first_name || entry.display_name.split(" ")[0] || "VIP";
  return (
    <article className="top-charts-row">
      <span className={rankBadgeClass(entry.rank)}>{entry.rank}</span>
      <LeaderboardAvatar imageUrl={entry.person.profile_photo_data_url} name={entry.person.display_name || firstName} />
      <div className="top-charts-row-main">
        <strong>{firstName}</strong>
        <span>{entry.vehicle_name || entry.vehicle.display_name || "Vehicle details pending"}</span>
        <small>{entry.registration_number}</small>
      </div>
      <div className="top-charts-read-count">
        <strong>{entry.read_count}</strong>
        <span>{entry.read_count === 1 ? "Detectiion" : "Detectiions"}</span>
      </div>
    </article>
  );
}

function LeaderboardUnknownRow({ entry }: { entry: LeaderboardUnknownEntry }) {
  const label = entry.dvla.label || "DVLA details unavailable";
  const showStatus = entry.dvla.status && entry.dvla.status !== "ok";
  return (
    <article className="top-charts-row">
      <span className={rankBadgeClass(entry.rank)}>{entry.rank}</span>
      <div className="top-charts-plate-avatar" aria-hidden="true">
        <Search size={17} />
      </div>
      <div className="top-charts-row-main">
        <strong>{entry.registration_number}</strong>
        <span>{label}</span>
        <small>{mysteryGuestQuip(entry.rank)}</small>
      </div>
      <div className="top-charts-read-count">
        {showStatus ? <Badge tone={leaderboardDvlaTone(entry.dvla.status)}>{leaderboardDvlaLabel(entry.dvla.status)}</Badge> : null}
        <strong>{entry.read_count}</strong>
        <span>{entry.read_count === 1 ? "Detectiion" : "Detectiions"}</span>
      </div>
    </article>
  );
}

function LeaderboardAvatar({ imageUrl, name }: { imageUrl: string | null; name: string }) {
  return (
    <span className="top-charts-avatar" aria-label={name}>
      {imageUrl ? <img alt="" src={imageUrl} /> : initials(name).toUpperCase()}
    </span>
  );
}

function leaderboardKnownMatches(entry: LeaderboardKnownEntry, query: string) {
  return (
    matches(entry.registration_number, query) ||
    matches(entry.display_name, query) ||
    matches(entry.person.display_name, query) ||
    matches(entry.vehicle_name, query)
  );
}

function leaderboardUnknownMatches(entry: LeaderboardUnknownEntry, query: string) {
  return (
    matches(entry.registration_number, query) ||
    matches(entry.dvla.label, query) ||
    matches(String(entry.dvla.error ?? ""), query)
  );
}

function rankBadgeClass(rank: number) {
  if (rank === 1) return "rank-badge rank-badge-gold";
  if (rank === 2) return "rank-badge rank-badge-silver";
  if (rank === 3) return "rank-badge rank-badge-bronze";
  return "rank-badge";
}

function leaderboardDvlaTone(status: string): BadgeTone {
  if (status === "unconfigured") return "gray";
  if (status === "failed") return "amber";
  return "gray";
}

function leaderboardDvlaLabel(status: string) {
  if (status === "unconfigured") return "DVLA off";
  if (status === "failed") return "DVLA failed";
  return titleCase(status);
}

function mysteryGuestQuip(rank: number) {
  if (rank === 1) return "Chief driveway plot twist";
  if (rank === 2) return "Strong encore energy";
  if (rank === 3) return "Podium-level mystery";
  return "Still under investigation";
}

function EventsView({ events, query }: { events: AccessEvent[]; query: string }) {
  const filtered = events.filter(
    (item) => matches(item.registration_number, query) || matches(item.source, query) || matches(item.visitor_name || "", query)
  );
  return (
    <section className="view-stack">
      <Toolbar title="Timeline" count={filtered.length} icon={Clock3} />
      <div className="table-card">
        <table>
          <thead>
            <tr>
              <th>Plate</th>
              <th>Direction</th>
              <th>Decision</th>
              <th>Confidence</th>
              <th>When</th>
              <th>Alerts</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((event) => (
              <tr key={event.id}>
                <td>
                  <strong>{event.registration_number}</strong>
                  {event.visitor_name ? <span className="table-muted-line">{visitorEventDisplayName(event)}</span> : null}
                </td>
                <td>{event.direction}</td>
                <td><Badge tone={event.decision === "granted" ? "green" : "red"}>{event.decision}</Badge></td>
                <td>{Math.round(event.confidence * 100)}%</td>
                <td>{formatDate(event.occurred_at)}</td>
                <td>{event.anomaly_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type AlertActionTarget = {
  alert: Anomaly;
  action: "resolve" | "reopen";
};

function AlertsView({ refreshDashboard }: { refreshDashboard: () => Promise<void> }) {
  const [alerts, setAlerts] = React.useState<Anomaly[]>([]);
  const [statusFilter, setStatusFilter] = React.useState<"open" | "resolved" | "all">("open");
  const [severityFilter, setSeverityFilter] = React.useState<"all" | AlertSeverity>("all");
  const [typeFilter, setTypeFilter] = React.useState("all");
  const [query, setQuery] = React.useState("");
  const [focusedAlertId, setFocusedAlertId] = React.useState(() => alertIdFromLocation());
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [actionTarget, setActionTarget] = React.useState<AlertActionTarget | null>(null);
  const [resolutionNote, setResolutionNote] = React.useState("");
  const [actionLoading, setActionLoading] = React.useState(false);

  const loadAlerts = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ status: statusFilter, limit: "200" });
      if (severityFilter !== "all") params.set("severity", severityFilter);
      if (typeFilter !== "all") params.set("type", typeFilter);
      if (query.trim()) params.set("q", query.trim());
      setAlerts(await api.get<Anomaly[]>(`/api/v1/alerts?${params}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load alerts");
    } finally {
      setLoading(false);
    }
  }, [query, severityFilter, statusFilter, typeFilter]);

  React.useEffect(() => {
    loadAlerts().catch(() => undefined);
  }, [loadAlerts]);

  React.useEffect(() => {
    const updateFocusedAlert = () => setFocusedAlertId(alertIdFromLocation());
    window.addEventListener("popstate", updateFocusedAlert);
    return () => window.removeEventListener("popstate", updateFocusedAlert);
  }, []);

  React.useEffect(() => {
    if (!focusedAlertId || loading) return;
    const target = alerts.find((alert) => alertMatchesFocus(alert, focusedAlertId));
    if (!target) return;
    window.requestAnimationFrame(() => {
      const node = document.getElementById(alertDomId(target.id));
      node?.scrollIntoView({ behavior: "smooth", block: "center" });
      node?.focus({ preventScroll: true });
    });
  }, [alerts, focusedAlertId, loading]);

  const actOnAlert = async (target: AlertActionTarget, note?: string) => {
    setActionLoading(true);
    setError("");
    try {
      await api.patch("/api/v1/alerts/action", {
        alert_ids: target.alert.alert_ids,
        action: target.action,
        note: note ?? null
      });
      setActionTarget(null);
      setResolutionNote("");
      await Promise.all([loadAlerts(), refreshDashboard()]);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Unable to update alert");
    } finally {
      setActionLoading(false);
    }
  };

  const openCount = alerts.filter((alert) => alert.status === "open").length;
  const actionableCount = alerts.filter(isActionableAlert).length;

  return (
    <section className="view-stack alerts-page">
      <Toolbar title="Alerts" count={alerts.length} icon={Bell}>
        <button className="secondary-button" onClick={() => loadAlerts().catch(() => undefined)} type="button">
          <RefreshCcw size={15} /> Refresh
        </button>
      </Toolbar>

      <div className="alerts-summary-grid">
        <MetricCard icon={AlertTriangle} label="Action Needed" value={String(actionableCount)} detail="warning and critical" tone={actionableCount ? "amber" : "gray"} />
        <MetricCard icon={Bell} label="Open Alerts" value={String(openCount)} detail="including informational" tone={openCount ? "blue" : "green"} />
        <MetricCard icon={CheckCircle2} label="Resolved View" value={statusFilter === "resolved" ? String(alerts.length) : "available"} detail="audit trail retained" tone="green" />
      </div>

      <div className="alerts-controls">
        <div className="alert-status-tabs" role="tablist" aria-label="Alert status">
          {(["open", "resolved", "all"] as const).map((value) => (
            <button className={statusFilter === value ? "active" : ""} key={value} onClick={() => setStatusFilter(value)} type="button">
              {titleCase(value)}
            </button>
          ))}
        </div>
        <label className="search alerts-search">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search plate, message, or context..." />
        </label>
        <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value as typeof severityFilter)} aria-label="Filter by severity">
          <option value="all">All severities</option>
          <option value="critical">Critical</option>
          <option value="warning">Warning</option>
          <option value="info">Informational</option>
        </select>
        <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)} aria-label="Filter by alert type">
          <option value="all">All types</option>
          <option value="unauthorized_plate">Unknown plate</option>
          <option value="outside_schedule">Outside schedule</option>
          <option value="duplicate_entry">Duplicate entry</option>
          <option value="duplicate_exit">Duplicate exit</option>
        </select>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? (
        <div className="loading-panel">Loading alerts</div>
      ) : alerts.length ? (
        <div className="alerts-list">
          {alerts.map((alert) => (
            <AlertReviewRow
              alert={alert}
              focused={alertMatchesFocus(alert, focusedAlertId)}
              key={alert.id}
              onReopen={() => actOnAlert({ alert, action: "reopen" })}
              onResolve={() => {
                setResolutionNote("");
                setActionTarget({ alert, action: "resolve" });
              }}
            />
          ))}
        </div>
      ) : (
        <EmptyState icon={CheckCircle2} label="No alerts match this view" />
      )}

      {actionTarget?.action === "resolve" ? (
        <div className="modal-backdrop" role="presentation">
          <form
            className="modal-card alert-resolution-modal"
            onSubmit={(event) => {
              event.preventDefault();
              actOnAlert(actionTarget, resolutionNote).catch(() => undefined);
            }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="alert-resolution-title"
          >
            <div className="modal-header">
              <h2 id="alert-resolution-title">Resolve alert?</h2>
              <p>{actionTarget.alert.grouped ? `${actionTarget.alert.count} grouped alert records` : titleCase(actionTarget.alert.type)}</p>
            </div>
            <label className="field">
              <span>Resolution note</span>
              <textarea value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} placeholder="Optional note for the audit trail" rows={4} />
            </label>
            <div className="modal-actions">
              <button className="secondary-button" disabled={actionLoading} onClick={() => setActionTarget(null)} type="button">
                Cancel
              </button>
              <button className="primary-button" disabled={actionLoading} type="submit">
                <Check size={16} />
                {actionLoading ? "Resolving..." : "Resolve Alert"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}

function AlertReviewRow({
  alert,
  focused,
  onResolve,
  onReopen
}: {
  alert: Anomaly;
  focused: boolean;
  onResolve: () => void;
  onReopen: () => void;
}) {
  const isResolved = alert.status === "resolved";
  const isUnknownPlate = alert.type === "unauthorized_plate";
  const title = isUnknownPlate
    ? alert.registration_number || "Unknown registration"
    : titleCase(alert.type);
  const message = isUnknownPlate ? "Unauthorised Plate, Access Denied" : alert.message;
  const showReadCount = alert.grouped && alert.count > 1;
  return (
    <article
      className={`alert-review-row ${alert.status}${focused ? " focused" : ""}`}
      id={alertDomId(alert.id)}
      tabIndex={focused ? -1 : undefined}
    >
      <span className={`alert-review-icon ${alert.severity}`}>
        {isResolved ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
      </span>
      <div className="alert-review-main">
        <div className={alert.snapshot_url ? "alert-review-content has-snapshot" : "alert-review-content"}>
          {alert.snapshot_url ? (
            <img
              alt={`${title} snapshot from camera.gate`}
              className="alert-review-snapshot"
              loading="lazy"
              src={alert.snapshot_url}
            />
          ) : null}
          <div className="alert-review-copy">
            <strong>{title}</strong>
            <span>{message}</span>
          </div>
        </div>
        <div className="alert-review-meta">
          <span>First Seen: <strong>{formatDate(alert.first_seen_at || alert.created_at)}</strong></span>
          <span>Last Seen: <strong>{formatDate(alert.last_seen_at || alert.created_at)}</strong></span>
        </div>
        {isResolved ? (
          <div className="alert-resolution-detail">
            <span>Resolved {alert.resolved_at ? formatDate(alert.resolved_at) : ""}{alert.resolved_by ? ` by ${alert.resolved_by.display_name}` : ""}</span>
            {alert.resolution_note ? <p>{alert.resolution_note}</p> : null}
          </div>
        ) : null}
      </div>
      <div className="alert-review-side">
        <div className="alert-review-badges">
          <Badge tone={alertSeverityTone(alert.severity)}>{alertSeverityLabel(alert.severity)}</Badge>
          {showReadCount ? <Badge tone="gray">{alert.count} reads</Badge> : null}
          <Badge tone={isResolved ? "green" : "blue"}>{isResolved ? "Resolved" : "Open"}</Badge>
        </div>
        <div className="alert-review-actions">
          {isResolved ? (
            <button className="secondary-button" onClick={onReopen} type="button">Reopen</button>
          ) : (
            <button className="primary-button" onClick={onResolve} type="button">
              <Check size={15} /> Resolve
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

function ReportsView({ events, presence }: { events: AccessEvent[]; presence: Presence[] }) {
  return (
    <section className="dashboard-grid reports-grid">
      <MetricCard icon={FileText} label="Audit Events" value={String(events.length)} detail="latest window" tone="blue" />
      <MetricCard icon={UserRound} label="On Site" value={String(presence.filter((item) => item.state === "present").length)} detail="current occupancy" tone="green" />
      <MetricCard icon={AlertTriangle} label="Denied" value={String(events.filter((item) => item.decision === "denied").length)} detail="access attempts" tone="amber" />
      <div className="card span-3">
        <CardHeader icon={BarChart3} title="Duration Audit" action={<Badge tone="gray">Live data</Badge>} />
        <RhythmChart events={events} />
      </div>
    </section>
  );
}

function IntegrationsView({ people, realtime, schedules, status }: { people: Person[]; realtime: RealtimeMessage[]; schedules: Schedule[]; status: IntegrationStatus | null }) {
  const { values, loading, save, reload } = useSettings();
  const [pageTab, setPageTab] = React.useState<IntegrationsPageTab>("integrations");
  const [active, setActive] = React.useState<IntegrationDefinition | null>(null);
  const [activeTab, setActiveTab] = React.useState<ProtectIntegrationTab>("general");
  const [activeDependency, setActiveDependency] = React.useState<DependencyPackage | null>(null);
  const [llmProviderSaving, setLlmProviderSaving] = React.useState(false);
  const [protectStatus, setProtectStatus] = React.useState<UnifiProtectStatus | null>(null);
  const [protectCameras, setProtectCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [protectSnapshotRefreshToken, setProtectSnapshotRefreshToken] = React.useState(0);
  const [protectUpdateStatus, setProtectUpdateStatus] = React.useState<UnifiProtectUpdateStatus | null>(null);
  const [protectLoading, setProtectLoading] = React.useState(false);
  const [protectError, setProtectError] = React.useState("");
  const [icloudPayload, setIcloudPayload] = React.useState<ICloudCalendarPayload>({ accounts: [], recent_sync_runs: [] });
  const [icloudLoading, setIcloudLoading] = React.useState(false);
  const [icloudError, setIcloudError] = React.useState("");
  const [discordStatus, setDiscordStatus] = React.useState<DiscordStatus | null>(null);
  const [discordChannels, setDiscordChannels] = React.useState<DiscordChannel[]>([]);
  const [discordIdentities, setDiscordIdentities] = React.useState<DiscordIdentity[]>([]);
  const [discordLoading, setDiscordLoading] = React.useState(false);
  const [discordError, setDiscordError] = React.useState("");
  const [dependencyPackages, setDependencyPackages] = React.useState<DependencyPackage[]>([]);
  const [dependencyStorage, setDependencyStorage] = React.useState<DependencyStorageStatus | null>(null);
  const [dependencyLoading, setDependencyLoading] = React.useState(false);
  const [dependencyError, setDependencyError] = React.useState("");
  const processedIcloudRealtimeRef = React.useRef(new Set<string>());
  const loadProtect = React.useCallback(async (forceRefresh = false) => {
    setProtectLoading(true);
    setProtectError("");
    try {
      const refreshSuffix = forceRefresh ? "?refresh=true" : "";
      const nextStatus = await api.get<UnifiProtectStatus>("/api/v1/integrations/unifi-protect/status");
      setProtectStatus(nextStatus);
      if (nextStatus.configured) {
        const result = await api.get<{ cameras: UnifiProtectCamera[] }>(`/api/v1/integrations/unifi-protect/cameras${refreshSuffix}`);
        setProtectCameras(result.cameras);
        setProtectSnapshotRefreshToken(Date.now());
      } else {
        setProtectCameras([]);
      }
    } catch (error) {
      setProtectError(error instanceof Error ? error.message : "Unable to load UniFi Protect cameras.");
    } finally {
      setProtectLoading(false);
    }
  }, []);
  const loadProtectUpdateStatus = React.useCallback(async () => {
    try {
      setProtectUpdateStatus(await api.get<UnifiProtectUpdateStatus>("/api/v1/integrations/unifi-protect/update/status"));
    } catch {
      setProtectUpdateStatus(null);
    }
  }, []);
  const loadICloudCalendar = React.useCallback(async () => {
    setIcloudLoading(true);
    setIcloudError("");
    try {
      setIcloudPayload(await api.get<ICloudCalendarPayload>("/api/v1/integrations/icloud-calendar/accounts"));
    } catch (error) {
      setIcloudError(error instanceof Error ? error.message : "Unable to load iCloud Calendar accounts.");
    } finally {
      setIcloudLoading(false);
    }
  }, []);

  React.useEffect(() => {
    for (const message of realtime.slice(0, 20).reverse()) {
      const key = `${message.type}-${message.created_at ?? ""}`;
      if (processedIcloudRealtimeRef.current.has(key)) continue;
      if (message.type !== "icloud_calendar.accounts_changed" && message.type !== "icloud_calendar.sync_completed") continue;
      processedIcloudRealtimeRef.current.add(key);
      if (message.type === "icloud_calendar.accounts_changed" && Array.isArray(message.payload.accounts)) {
        setIcloudPayload((current) => ({ ...current, accounts: message.payload.accounts as ICloudCalendarAccount[] }));
      }
      if (message.type === "icloud_calendar.sync_completed" && isRecord(message.payload.sync)) {
        const run = message.payload.sync as ICloudCalendarSyncRun;
        setIcloudPayload((current) => ({
          ...current,
          recent_sync_runs: [run, ...current.recent_sync_runs.filter((item) => item.id !== run.id)].slice(0, 5)
        }));
      }
    }
  }, [realtime]);
  const loadDiscord = React.useCallback(async () => {
    setDiscordLoading(true);
    setDiscordError("");
    try {
      const [statusResult, channelResult, identityResult] = await Promise.all([
        api.get<DiscordStatus>("/api/v1/integrations/discord/status"),
        api.get<{ channels: DiscordChannel[] }>("/api/v1/integrations/discord/channels"),
        api.get<{ identities: DiscordIdentity[] }>("/api/v1/integrations/discord/identities")
      ]);
      setDiscordStatus(statusResult);
      setDiscordChannels(channelResult.channels);
      setDiscordIdentities(identityResult.identities);
    } catch (error) {
      setDiscordError(error instanceof Error ? error.message : "Unable to load Discord integration.");
    } finally {
      setDiscordLoading(false);
    }
  }, []);
  const loadDependencyUpdates = React.useCallback(async () => {
    setDependencyLoading(true);
    setDependencyError("");
    try {
      const [packagesResult, storageResult] = await Promise.all([
        api.get<{ packages: DependencyPackage[] }>("/api/v1/dependency-updates/packages"),
        api.get<DependencyStorageStatus>("/api/v1/dependency-updates/storage/status")
      ]);
      setDependencyPackages(packagesResult.packages);
      setDependencyStorage(storageResult);
    } catch (error) {
      setDependencyError(error instanceof Error ? error.message : "Unable to load dependency updates.");
    } finally {
      setDependencyLoading(false);
    }
  }, []);
  const reloadSettingsAndProtect = React.useCallback(async () => {
    await reload();
    await loadProtect(true);
    await loadProtectUpdateStatus();
    await loadICloudCalendar();
    await loadDiscord();
    await loadDependencyUpdates();
  }, [loadDependencyUpdates, loadDiscord, loadICloudCalendar, loadProtect, loadProtectUpdateStatus, reload]);

  React.useEffect(() => {
    loadProtect(false).catch(() => undefined);
    loadProtectUpdateStatus().catch(() => undefined);
    loadICloudCalendar().catch(() => undefined);
    loadDiscord().catch(() => undefined);
    loadDependencyUpdates().catch(() => undefined);
  }, [loadDependencyUpdates, loadDiscord, loadICloudCalendar, loadProtect, loadProtectUpdateStatus]);

  const actionableDependencyUpdateCount = dependencyPackages.filter(dependencyIsActionableUpdate).length;
  const tiles = integrationDefinitions(status, values, protectStatus, protectUpdateStatus, icloudPayload.accounts, icloudError, discordStatus, discordError, dependencyPackages);
  const groupedTiles = integrationCategories
    .map((category) => ({
      ...category,
      tiles: tiles.filter((tile) => tile.category === category.key)
    }))
    .filter((category) => category.tiles.length);
  return (
    <section className="view-stack integrations-page">
      <Toolbar title="API & Integrations" count={tiles.length} icon={PlugZap} />
      <div className="integration-page-tabs" role="tablist" aria-label="API and integrations sections">
        <button className={pageTab === "integrations" ? "integration-page-tab active" : "integration-page-tab"} onClick={() => setPageTab("integrations")} type="button">
          <PlugZap size={16} /> Integrations
        </button>
        <button className={pageTab === "updates" ? "integration-page-tab active" : "integration-page-tab"} onClick={() => setPageTab("updates")} type="button">
          <RefreshCcw size={16} /> Updates
          {actionableDependencyUpdateCount ? <Badge tone="amber">{actionableDependencyUpdateCount}</Badge> : null}
        </button>
      </div>
      {pageTab === "updates" ? (
        <DependencyUpdatesHub
          loading={dependencyLoading}
          packages={dependencyPackages}
          storage={dependencyStorage}
          error={dependencyError}
          onChanged={loadDependencyUpdates}
          onInspect={setActiveDependency}
        />
      ) : (
      <div className="integration-category-stack">
        {groupedTiles.map((category) => (
          <section className="integration-category" key={category.key}>
            <div className="integration-category-header">
              <div className="integration-category-title">
                <strong>{category.label}</strong>
                <span>{category.description}</span>
              </div>
              <div className="integration-category-actions">
                {category.key === "ai" ? (
                  <LlmProviderSelector
                    saving={llmProviderSaving || loading}
                    values={values}
                    onChange={async (provider) => {
                      setLlmProviderSaving(true);
                      try {
                        await save({ llm_provider: provider });
                      } finally {
                        setLlmProviderSaving(false);
                      }
                    }}
                  />
                ) : null}
                <Badge tone="gray">{category.tiles.length}</Badge>
              </div>
            </div>
            <div className="integration-tile-grid">
              {category.tiles.map((tile) => {
                const Icon = tile.icon;
                return (
                  <article className="card integration-tile" key={tile.key}>
                    <button
                      className="integration-tile-main"
                      onClick={() => {
                        setActive(tile);
                        setActiveTab(tile.updateAvailable ? "updates" : "general");
                      }}
                      type="button"
                    >
                      <span className="integration-icon"><Icon size={22} /></span>
                      <div>
                        <strong>{tile.title}</strong>
                        <span>{tile.description}</span>
                        {tile.notificationChannels?.length ? (
                          <span className="integration-notification-link">
                            <Bell size={13} /> Available to Notifications: {tile.notificationChannels.map((channel) => notificationChannelMeta[channel].label).join(", ")}
                          </span>
                        ) : null}
                      </div>
                      <Badge tone={tile.statusTone}>{tile.statusLabel}</Badge>
                      {tile.updateAvailable ? <Badge tone="amber">Update Available</Badge> : null}
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      )}
      {active ? (
        <IntegrationModal
          definition={active}
          initialTab={activeTab}
          dependencyPackages={dependenciesForIntegration(active, dependencyPackages)}
          dependencyStorage={dependencyStorage}
          loading={loading}
          protectCameras={protectCameras}
          protectError={protectError || protectStatus?.last_error || ""}
          protectLoading={protectLoading}
          protectStatus={protectStatus}
          protectUpdateStatus={protectUpdateStatus}
          icloudError={icloudError}
          icloudLoading={icloudLoading}
          icloudPayload={icloudPayload}
          discordChannels={discordChannels}
          discordError={discordError}
          discordIdentities={discordIdentities}
          discordLoading={discordLoading}
          discordStatus={discordStatus}
          people={people}
          schedules={schedules}
          values={values}
          onClose={() => setActive(null)}
          onICloudChanged={loadICloudCalendar}
          onDiscordChanged={loadDiscord}
          onProtectUpdateChanged={async () => {
            await loadProtectUpdateStatus();
            await loadProtect(true);
            await loadDependencyUpdates();
          }}
          onProtectRefresh={() => loadProtect(true)}
          onSettingsChanged={reloadSettingsAndProtect}
          onSaved={async (updates) => {
            await save(updates);
            await loadProtect(true);
            await loadDependencyUpdates();
            setActive(null);
          }}
        />
      ) : null}
      {activeDependency ? (
        <DependencyUpdateModal
          dependency={activeDependency}
          storage={dependencyStorage}
          onClose={() => setActiveDependency(null)}
          onChanged={loadDependencyUpdates}
        />
      ) : null}
      <UnifiProtectCameraSection
        cameras={protectCameras}
        error={protectError || protectStatus?.last_error || ""}
        loading={protectLoading}
        onRefresh={() => loadProtect(true)}
        refreshToken={protectSnapshotRefreshToken}
        status={protectStatus}
      />
    </section>
  );
}

const llmProviderDefinitions = [
  { key: "local", label: "Local fallback" },
  { key: "openai", label: "OpenAI" },
  { key: "gemini", label: "Gemini" },
  { key: "anthropic", label: "Claude" },
  { key: "ollama", label: "Ollama" }
] as const;

type LlmProviderKey = typeof llmProviderDefinitions[number]["key"];

function normalizeLlmProvider(value: unknown): LlmProviderKey {
  const provider = String(value || "local").toLowerCase();
  if (provider === "claude") return "anthropic";
  return llmProviderDefinitions.some((option) => option.key === provider) ? provider as LlmProviderKey : "local";
}

function isLlmProviderConfigured(key: LlmProviderKey, values: SettingsMap): boolean {
  if (key === "local") return true;
  if (key === "openai") return Boolean(values.openai_api_key);
  if (key === "gemini") return Boolean(values.gemini_api_key);
  if (key === "anthropic") return Boolean(values.anthropic_api_key);
  if (key === "ollama") return Boolean(values.ollama_base_url);
  return false;
}

function LlmProviderSelector({
  saving,
  values,
  onChange
}: {
  saving: boolean;
  values: SettingsMap;
  onChange: (provider: LlmProviderKey) => Promise<void>;
}) {
  const activeProvider = normalizeLlmProvider(values.llm_provider);
  return (
    <div className="llm-provider-selector">
      <Bot size={15} />
      <label className="llm-provider-select">
        <span>System LLM</span>
        <select
          disabled={saving}
          value={activeProvider}
          onChange={(event) => onChange(event.target.value as LlmProviderKey)}
        >
          {llmProviderDefinitions.map((provider) => {
            const configured = isLlmProviderConfigured(provider.key, values);
            return (
              <option disabled={!configured && provider.key !== activeProvider} key={provider.key} value={provider.key}>
                {provider.label}{configured ? "" : " (not configured)"}
              </option>
            );
          })}
        </select>
      </label>
    </div>
  );
}

type IntegrationDefinition = {
  key: string;
  title: string;
  description: string;
  category: "access" | "notifications" | "data" | "ai";
  icon: React.ElementType;
  fields: SettingFieldDefinition[];
  statusLabel: string;
  statusTone: BadgeTone;
  notificationChannels?: NotificationChannelId[];
  oauth?: boolean;
  updateAvailable?: boolean;
};

type IntegrationFeedback = {
  tone: "progress" | "success" | "error" | "info";
  title: string;
  detail: string;
  activeStep?: number;
};

const integrationCategories: Array<{
  key: IntegrationDefinition["category"];
  label: string;
  description: string;
}> = [
  {
    key: "access",
    label: "Access Control",
    description: "Physical site controls and sensor integrations."
  },
  {
    key: "notifications",
    label: "Notification Providers",
    description: "Destinations made available to the notification rules engine."
  },
  {
    key: "data",
    label: "Data & Intelligence",
    description: "Vehicle data, cameras, and operational enrichment."
  },
  {
    key: "ai",
    label: "AI Providers",
    description: "LLM providers used by chat, summaries, and analysis."
  }
];

function dependenciesForIntegration(definition: IntegrationDefinition, dependencies: DependencyPackage[]) {
  return dependenciesForIntegrationKey(definition.key, dependencies);
}

function dependenciesForIntegrationKey(key: string, dependencies: DependencyPackage[]) {
  const labels: Record<string, string[]> = {
    home_assistant: ["home assistant", "home-assistant"],
    icloud_calendar: ["icloud", "pyicloud"],
    apprise: ["apprise", "notifications"],
    discord: ["discord", "discord.py", "discord messaging"],
    dvla: ["dvla"],
    unifi_protect: ["unifi", "uiprotect"],
    openai: ["openai"],
    gemini: ["gemini"],
    anthropic: ["anthropic", "claude"],
    ollama: ["ollama"]
  };
  const needles = labels[key] ?? [key.replace(/_/g, " ")];
  return dependencies.filter((dependency) => {
    const haystack = [
      dependency.package_name,
      dependency.normalized_name,
      dependency.dependant_area,
      dependency.manifest_path
    ].join(" ").toLowerCase();
    return needles.some((needle) => haystack.includes(needle.toLowerCase()));
  });
}

function integrationDefinitions(
  status: IntegrationStatus | null,
  values: SettingsMap,
  protectStatus: UnifiProtectStatus | null,
  protectUpdateStatus: UnifiProtectUpdateStatus | null,
  icloudAccounts: ICloudCalendarAccount[],
  icloudError: string,
  discordStatus: DiscordStatus | null,
  discordError: string,
  dependencies: DependencyPackage[] = []
): IntegrationDefinition[] {
  const activeProvider = normalizeLlmProvider(values.llm_provider);
  const providerStatus = (key: string, secretKey?: string): Pick<IntegrationDefinition, "statusLabel" | "statusTone"> => {
    if (activeProvider === key) return { statusLabel: "Active", statusTone: "green" };
    if (secretKey && values[secretKey]) return { statusLabel: "Configured", statusTone: "blue" };
    if (key === "ollama" && values.ollama_base_url) return { statusLabel: "Configured", statusTone: "blue" };
    return { statusLabel: "Not Configured", statusTone: "gray" };
  };

  const hasDependencyUpdate = (key: string) => dependenciesForIntegrationKey(key, dependencies).some(dependencyIsActionableUpdate);
  const protectUpdateAvailable = Boolean(protectStatus?.connected && protectUpdateStatus?.update_available) || hasDependencyUpdate("unifi_protect");
  const activeIcloudAccounts = icloudAccounts.filter((account) => account.is_active);
  const icloudNeedsAttention = activeIcloudAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));

  return [
    {
      key: "home_assistant",
      title: "Home Assistant",
      description: "Gate control, mobile app notifications, TTS announcements, and state sync.",
      category: "access",
      icon: Home,
      statusLabel: status?.configured ? "Connected" : "Not Configured",
      statusTone: status?.configured ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("home_assistant"),
      notificationChannels: ["mobile", "voice"],
      fields: [
        { key: "home_assistant_url", label: "URL" },
        { key: "home_assistant_token", label: "Long-lived token", type: "password" },
        { key: "home_assistant_gate_entities", label: "Gate entities" },
        { key: "home_assistant_gate_open_service", label: "Cover open service" },
        { key: "home_assistant_garage_door_entities", label: "Garage doors" },
        { key: "home_assistant_tts_service", label: "TTS service" },
        { key: "home_assistant_default_media_player", label: "Default media player" }
      ]
    },
    {
      key: "icloud_calendar",
      title: "iCloud Calendar",
      description: "Create Visitor Passes from calendar events marked Open Gate.",
      category: "access",
      icon: CalendarDays,
      statusLabel: icloudError
        ? "Error"
        : icloudNeedsAttention
          ? "Needs Attention"
          : activeIcloudAccounts.length
            ? `${activeIcloudAccounts.length} Connected`
            : "Not Configured",
      statusTone: icloudError ? "red" : icloudNeedsAttention ? "amber" : activeIcloudAccounts.length ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("icloud_calendar"),
      fields: []
    },
    {
      key: "apprise",
      title: "Apprise",
      description: "Mobile and push notification fan-out.",
      category: "notifications",
      icon: Bell,
      statusLabel: values.apprise_urls ? "Configured" : "Not Configured",
      statusTone: values.apprise_urls ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("apprise"),
      notificationChannels: ["mobile"],
      fields: [{
        key: "apprise_urls",
        label: "Apprise URLs",
        type: "textarea",
        href: "https://github.com/caronc/apprise/wiki",
        help: "For Pushover use pover://USER_KEY@APP_TOKEN. The app also accepts pushover://USER_KEY/APP_TOKEN and normalizes it."
      }]
    },
    {
      key: "discord",
      title: "Discord",
      description: "Bidirectional Alfred chat and Discord notification channels.",
      category: "notifications",
      icon: MessageCircle,
      statusLabel: discordError
        ? "Error"
        : discordStatus?.connected
          ? "Connected"
          : discordStatus?.configured || values.discord_bot_token
            ? "Configured"
            : "Not Configured",
      statusTone: discordError ? "red" : discordStatus?.connected ? "green" : discordStatus?.configured || values.discord_bot_token ? "blue" : "gray",
      updateAvailable: hasDependencyUpdate("discord"),
      notificationChannels: ["discord"],
      fields: [
        { key: "discord_bot_token", label: "Bot token", type: "password" },
        { key: "discord_guild_allowlist", label: "Guild allowlist", type: "textarea", help: "One Discord server ID per line." },
        { key: "discord_channel_allowlist", label: "Channel allowlist", type: "textarea", help: "One channel ID per line. Empty denies guild-channel messages." },
        { key: "discord_user_allowlist", label: "User allowlist", type: "textarea", help: "One Discord user ID per line." },
        { key: "discord_role_allowlist", label: "Role allowlist", type: "textarea", help: "One Discord role ID per line." },
        { key: "discord_admin_role_ids", label: "Admin role IDs", type: "textarea", help: "Members with these roles can resolve Alfred confirmations." },
        { key: "discord_default_notification_channel_id", label: "Default notification channel" },
        { key: "discord_allow_direct_messages", label: "Allow direct messages", type: "select", options: ["false", "true"] },
        { key: "discord_require_mention", label: "Require mention", type: "select", options: ["true", "false"] }
      ]
    },
    {
      key: "dvla",
      title: "DVLA Lookup",
      description: "Vehicle Enquiry Service API plate lookups.",
      category: "data",
      icon: Search,
      statusLabel: values.dvla_api_key ? "Configured" : "Not Configured",
      statusTone: values.dvla_api_key ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("dvla"),
      fields: [
        {
          key: "dvla_api_key",
          label: "DVLA API Key",
          type: "password",
          href: "https://developer-portal.driver-vehicle-licensing.api.gov.uk/apis/vehicle-enquiry-service/vehicle-enquiry-service-description.html"
        },
        {
          key: "dvla_vehicle_enquiry_url",
          label: "Vehicle enquiry URL",
          help: "Production endpoint for the DVLA Vehicle Enquiry Service API."
        },
        {
          key: "dvla_test_registration_number",
          label: "Test VRN",
          help: "Used only when this modal tests the DVLA connection."
        },
        { key: "dvla_timeout_seconds", label: "Timeout seconds", type: "number", min: 1, step: 1 }
      ]
    },
    {
      key: "unifi_protect",
      title: "UniFi Protect",
      description: "Camera snapshots, detection events, and AI image analysis.",
      category: "data",
      icon: Camera,
      statusLabel: protectUpdateAvailable ? `Update ${protectUpdateStatus?.latest_version}` : protectStatus?.connected ? "Connected" : protectStatus?.configured || values.unifi_protect_host ? "Configured" : "Not Configured",
      statusTone: protectUpdateAvailable ? "amber" : protectStatus?.connected ? "green" : protectStatus?.configured || values.unifi_protect_host ? "blue" : "gray",
      updateAvailable: protectUpdateAvailable,
      fields: [
        { key: "unifi_protect_host", label: "Console host" },
        { key: "unifi_protect_port", label: "HTTPS port", type: "number", min: 1, max: 65535, step: 1 },
        { key: "unifi_protect_username", label: "Local username", type: "password" },
        { key: "unifi_protect_password", label: "Local password", type: "password" },
        {
          key: "unifi_protect_api_key",
          label: "Integration API key",
          type: "password",
          href: "https://uiprotect.readthedocs.io"
        },
        { key: "unifi_protect_verify_ssl", label: "Verify TLS", type: "select", options: ["false", "true"] },
        { key: "unifi_protect_snapshot_width", label: "Snapshot width", type: "number", min: 160, max: 4096, step: 1 },
        { key: "unifi_protect_snapshot_height", label: "Snapshot height", type: "number", min: 90, max: 2160, step: 1 }
      ]
    },
    {
      key: "openai",
      title: "OpenAI",
      description: "Responses API provider for tool-capable chat.",
      category: "ai",
      icon: Bot,
      ...providerStatus("openai", "openai_api_key"),
      oauth: true,
      fields: [
        { key: "openai_api_key", label: "API key", type: "password", href: "https://platform.openai.com/api-keys" },
        { key: "openai_model", label: "Model" },
        { key: "openai_base_url", label: "Base URL" }
      ]
    },
    {
      key: "gemini",
      title: "Gemini",
      description: "Google Gemini provider.",
      category: "ai",
      icon: CircleDot,
      ...providerStatus("gemini", "gemini_api_key"),
      oauth: true,
      fields: [
        { key: "gemini_api_key", label: "API key", type: "password", href: "https://aistudio.google.com/app/apikey" },
        { key: "gemini_model", label: "Model" },
        { key: "gemini_base_url", label: "Base URL" }
      ]
    },
    {
      key: "anthropic",
      title: "Anthropic",
      description: "Claude provider.",
      category: "ai",
      icon: MessageCircle,
      ...providerStatus("anthropic", "anthropic_api_key"),
      fields: [
        { key: "anthropic_api_key", label: "API key", type: "password", href: "https://console.anthropic.com/settings/keys" },
        { key: "anthropic_model", label: "Model" },
        { key: "anthropic_base_url", label: "Base URL" }
      ]
    },
    {
      key: "ollama",
      title: "Ollama",
      description: "Local model endpoint.",
      category: "ai",
      icon: Database,
      ...providerStatus("ollama"),
      fields: [
        { key: "ollama_model", label: "Model" },
        { key: "ollama_base_url", label: "Base URL" }
      ]
    }
  ];
}

function UnifiProtectCameraSection({
  cameras,
  error,
  loading,
  onRefresh,
  refreshToken,
  status
}: {
  cameras: UnifiProtectCamera[];
  error: string;
  loading: boolean;
  onRefresh: () => Promise<void>;
  refreshToken: number;
  status: UnifiProtectStatus | null;
}) {
  const [snapshotNonce, setSnapshotNonce] = React.useState<Record<string, number>>({});
  const [eventsByCamera, setEventsByCamera] = React.useState<Record<string, UnifiProtectEvent[]>>({});
  const [eventsLoading, setEventsLoading] = React.useState<Record<string, boolean>>({});
  const [analysisDrafts, setAnalysisDrafts] = React.useState<Record<string, string>>({});
  const [analysisByCamera, setAnalysisByCamera] = React.useState<Record<string, UnifiProtectAnalysis | string>>({});
  const [analysisLoading, setAnalysisLoading] = React.useState<Record<string, boolean>>({});

  const refreshSnapshot = (cameraId: string) => {
    setSnapshotNonce((current) => ({ ...current, [cameraId]: Date.now() }));
  };

  const loadEvents = async (cameraId: string) => {
    setEventsLoading((current) => ({ ...current, [cameraId]: true }));
    try {
      const result = await api.get<{ events: UnifiProtectEvent[] }>(`/api/v1/integrations/unifi-protect/events?camera_id=${encodeURIComponent(cameraId)}&limit=5`);
      setEventsByCamera((current) => ({ ...current, [cameraId]: result.events }));
    } catch (loadError) {
      setAnalysisByCamera((current) => ({
        ...current,
        [cameraId]: loadError instanceof Error ? loadError.message : "Unable to load recent camera events."
      }));
    } finally {
      setEventsLoading((current) => ({ ...current, [cameraId]: false }));
    }
  };

  const analyzeSnapshot = async (camera: UnifiProtectCamera) => {
    const prompt = analysisDrafts[camera.id]?.trim() || "Describe what is visible in this access-control camera snapshot. Call out people, vehicles, animals, packages, and anything unusual.";
    setAnalysisLoading((current) => ({ ...current, [camera.id]: true }));
    setAnalysisByCamera((current) => ({ ...current, [camera.id]: "" }));
    try {
      const result = await api.post<UnifiProtectAnalysis>(`/api/v1/integrations/unifi-protect/cameras/${encodeURIComponent(camera.id)}/analyze`, { prompt });
      setAnalysisByCamera((current) => ({ ...current, [camera.id]: result }));
    } catch (analysisError) {
      setAnalysisByCamera((current) => ({
        ...current,
        [camera.id]: analysisError instanceof Error ? analysisError.message : "Camera analysis failed."
      }));
    } finally {
      setAnalysisLoading((current) => ({ ...current, [camera.id]: false }));
    }
  };

  const configured = status?.configured ?? false;
  const connected = status?.connected ?? false;

  return (
    <section className="protect-section">
      <div className="protect-section-header">
        <div className="card-title">
          <Camera size={18} />
          <h2>UniFi Protect Cameras</h2>
        </div>
        <div className="protect-section-actions">
          <Badge tone={connected ? "green" : configured ? "blue" : "gray"}>{connected ? "Connected" : configured ? "Configured" : "Not Configured"}</Badge>
          <button className="secondary-button" onClick={onRefresh} disabled={loading} type="button">
            <RefreshCcw size={15} /> {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {!configured ? (
        <div className="empty-state">Configure UniFi Protect to load cameras</div>
      ) : loading && !cameras.length ? (
        <div className="empty-state">Loading cameras</div>
      ) : cameras.length ? (
        <div className="protect-camera-grid">
          {cameras.map((camera) => {
            const events = eventsByCamera[camera.id] ?? [];
            const analysis = analysisByCamera[camera.id];
            const detectionLabels = camera.detections.active.length ? camera.detections.active : camera.is_motion_detected ? ["motion"] : [];
            const snapshotUrl = `${camera.snapshot_url}?width=640&height=360&_=${snapshotNonce[camera.id] ?? refreshToken}`;
            return (
              <article className="protect-camera-card" key={camera.id}>
                <div className="protect-camera-media">
                  <img alt="" src={snapshotUrl} />
                  <div className="protect-camera-badges">
                    <Badge tone={camera.is_video_ready ? "green" : "amber"}>{camera.is_video_ready ? "Video Ready" : "Video Pending"}</Badge>
                    {camera.is_recording ? <Badge tone="blue">Recording</Badge> : null}
                  </div>
                </div>
                <div className="protect-camera-body">
                  <div className="protect-camera-title">
                    <div>
                      <strong>{camera.name}</strong>
                      <span>{camera.model || "UniFi Protect camera"} · {camera.state || "unknown"}</span>
                    </div>
                    <button className="icon-button" onClick={() => refreshSnapshot(camera.id)} type="button" aria-label={`Refresh ${camera.name} snapshot`}>
                      <RefreshCcw size={15} />
                    </button>
                  </div>

                  <div className="protect-detection-row">
                    {detectionLabels.length ? detectionLabels.map((label) => (
                      <Badge tone={label === "motion" ? "amber" : "blue"} key={label}>{titleCase(label)}</Badge>
                    )) : <Badge tone="gray">Clear</Badge>}
                    {camera.feature_flags.has_mic ? <Badge tone="gray">Mic</Badge> : null}
                    {camera.feature_flags.has_package_camera ? <Badge tone="gray">Package Cam</Badge> : null}
                  </div>

                  <div className="protect-channel-row">
                    {camera.channels.slice(0, 3).map((channel) => (
                      <span key={channel.id}>
                        {channel.width ?? "-"}x{channel.height ?? "-"} {channel.fps ? `${channel.fps}fps` : ""}
                      </span>
                    ))}
                  </div>

                  <div className="protect-camera-actions">
                    <button className="secondary-button" onClick={() => loadEvents(camera.id)} disabled={eventsLoading[camera.id]} type="button">
                      <Play size={15} /> {eventsLoading[camera.id] ? "Loading..." : "Recent Events"}
                    </button>
                  </div>

                  {events.length ? (
                    <div className="protect-event-list">
                      {events.map((event) => (
                        <div className="protect-event-row" key={event.id}>
                          <img alt="" src={`${event.thumbnail_url}?width=96&height=54`} />
                          <div>
                            <strong>{titleCase(event.type)}</strong>
                            <span>{event.start ? formatDate(event.start) : "Time pending"} · {event.smart_detect_types.map(titleCase).join(", ") || "motion"}</span>
                          </div>
                          {event.video_url ? <a className="icon-button" href={event.video_url} target="_blank" rel="noreferrer" aria-label="Open event clip"><Play size={14} /></a> : null}
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="protect-analysis-box">
                    <input
                      value={analysisDrafts[camera.id] ?? ""}
                      onChange={(event) => setAnalysisDrafts((current) => ({ ...current, [camera.id]: event.target.value }))}
                      placeholder="Ask what to inspect"
                    />
                    <button className="primary-button" onClick={() => analyzeSnapshot(camera)} disabled={analysisLoading[camera.id]} type="button">
                      <Bot size={15} /> {analysisLoading[camera.id] ? "Analyzing..." : "Analyze"}
                    </button>
                  </div>
                  {analysis ? (
                    <div className={typeof analysis === "string" ? "protect-analysis-result error" : "protect-analysis-result"}>
                      {typeof analysis === "string" ? analysis : analysis.text}
                    </div>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty-state">No Protect cameras returned</div>
      )}
    </section>
  );
}

type ProtectExposeRow = {
  name: string;
  value: string;
};

function UnifiProtectExposesPanel({
  cameras,
  error,
  loading,
  onRefresh,
  status
}: {
  cameras: UnifiProtectCamera[];
  error: string;
  loading: boolean;
  onRefresh: () => Promise<void>;
  status: UnifiProtectStatus | null;
}) {
  const rows = buildProtectExposeRows(status, cameras);
  return (
    <div className="protect-exposes-panel">
      <div className="protect-exposes-header">
        <div>
          <strong>Exposed entities</strong>
          <span>Current values from UniFi Protect discovery and camera state.</span>
        </div>
        <button className="secondary-button" onClick={onRefresh} disabled={loading} type="button">
          <RefreshCcw size={15} /> {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {!status?.configured ? (
        <div className="empty-state">Configure UniFi Protect to see exposed entities</div>
      ) : (
        <div className="protect-exposes-grid">
          <ProtectExposeTable title="Console" rows={rows.console} defaultOpen />
          <ProtectExposeTable title="Cameras" rows={rows.cameras} defaultOpen />
          <ProtectExposeTable title="Sensors" rows={rows.sensors} defaultOpen />
          <ProtectExposeTable title="Detections" rows={rows.detections} defaultOpen />
          <ProtectExposeTable title="Channels" rows={rows.channels} />
        </div>
      )}
    </div>
  );
}

function ProtectExposeTable({
  defaultOpen = false,
  rows,
  title
}: {
  defaultOpen?: boolean;
  rows: ProtectExposeRow[];
  title: string;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <section className="protect-expose-table-card">
      <button className="protect-expose-table-toggle" onClick={() => setOpen((current) => !current)} type="button" aria-expanded={open}>
        <div>
          <strong>{title}</strong>
          <span>{rows.length} item{rows.length === 1 ? "" : "s"}</span>
        </div>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </button>
      {open ? (
        rows.length ? (
          <table className="protect-expose-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Current value</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${title}-${row.name}`}>
                  <td>{row.name}</td>
                  <td>{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state compact">No {title.toLowerCase()} exposed yet</div>
        )
      ) : null}
    </section>
  );
}

function buildProtectExposeRows(status: UnifiProtectStatus | null, cameras: UnifiProtectCamera[]) {
  const consoleRows: ProtectExposeRow[] = [
    { name: "Connection", value: status?.connected ? "Connected" : status?.configured ? "Configured" : "Not configured" },
    { name: "Console", value: status?.host ? `${status.host}:${status.port}` : "Not configured" },
    { name: "TLS verification", value: formatExposeValue(status?.verify_ssl) },
    { name: "Camera count", value: String(status?.camera_count ?? cameras.length) },
    { name: "Snapshot dimensions", value: status ? `${status.snapshot_width}x${status.snapshot_height}` : "Unknown" }
  ];

  const cameraRows = cameras.map((camera) => ({
    name: camera.name,
    value: [
      camera.state || "unknown",
      camera.is_video_ready ? "video ready" : "video pending",
      camera.is_recording ? "recording" : "not recording"
    ].join(" · ")
  }));

  const sensorRows = cameras.flatMap((camera) => [
    { name: `${camera.name} motion`, value: formatExposeValue(camera.is_motion_detected) },
    { name: `${camera.name} smart detection`, value: formatExposeValue(camera.is_smart_detected) },
    { name: `${camera.name} recording enabled`, value: formatExposeValue(camera.is_recording_enabled) },
    { name: `${camera.name} microphone`, value: formatExposeValue(camera.feature_flags.has_mic) },
    { name: `${camera.name} package camera`, value: formatExposeValue(camera.feature_flags.has_package_camera) }
  ]);

  const detectionRows = cameras.flatMap((camera) => [
    { name: `${camera.name} active detections`, value: camera.detections.active.length ? camera.detections.active.map(titleCase).join(", ") : "Clear" },
    { name: `${camera.name} supported smart detections`, value: camera.feature_flags.smart_detect_types.length ? camera.feature_flags.smart_detect_types.map(titleCase).join(", ") : "None" },
    { name: `${camera.name} supported audio detections`, value: camera.feature_flags.smart_detect_audio_types.length ? camera.feature_flags.smart_detect_audio_types.map(titleCase).join(", ") : "None" },
    { name: `${camera.name} last motion`, value: camera.last_motion_at ? formatDate(camera.last_motion_at) : "None" },
    { name: `${camera.name} last smart detection`, value: camera.last_smart_detect_at ? formatDate(camera.last_smart_detect_at) : "None" }
  ]);

  const channelRows = cameras.flatMap((camera) => camera.channels.map((channel) => ({
    name: `${camera.name} · ${channel.name || channel.id}`,
    value: [
      channel.width && channel.height ? `${channel.width}x${channel.height}` : "resolution unknown",
      channel.fps ? `${channel.fps}fps` : null,
      channel.bitrate ? `${channel.bitrate}kbps` : null,
      channel.is_rtsp_enabled ? "RTSP enabled" : "RTSP disabled",
      channel.is_package ? "package channel" : null
    ].filter(Boolean).join(" · ")
  })));

  return {
    console: consoleRows,
    cameras: cameraRows,
    sensors: sensorRows,
    detections: detectionRows,
    channels: channelRows
  };
}

function formatExposeValue(value: boolean | string | number | null | undefined) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined || value === "") return "Unknown";
  return String(value);
}

type ProtectUpdateConfirmAction =
  | { kind: "apply" }
  | { kind: "restore"; backup: UnifiProtectBackup }
  | { kind: "delete"; backup: UnifiProtectBackup };

function dependencyUpdateTone(dependency: DependencyPackage): BadgeTone {
  if (dependency.update_available) return "amber";
  if (dependency.last_checked_at) return "green";
  return "gray";
}

function dependencyUpdateLabel(dependency: DependencyPackage): string {
  if (dependency.update_available && !dependencyCanApply(dependency)) return "Transitive Update";
  if (dependency.update_available) return "Update Available";
  if (dependency.last_checked_at) return "Current";
  return "Unchecked";
}

function dependencyCanApply(dependency: DependencyPackage): boolean {
  return dependency.ecosystem === "docker_image" || dependency.is_direct;
}

function dependencyIsActionableUpdate(dependency: DependencyPackage): boolean {
  return dependency.update_available && dependencyCanApply(dependency);
}

function dependencyJobProgress(job: DependencyJob | null, events: DependencyJobEvent[]) {
  const phase = job?.phase || [...events].reverse().find((event) => event.phase)?.phase || "starting";
  const status = job?.status || "queued";
  const phaseProgress: Record<string, number> = {
    queued: 3,
    starting: 8,
    backup: 22,
    validate_backup: 22,
    apply: 55,
    restore_files: 55,
    verify: 82,
    rollback: 92,
    completed: 100,
    failed: 100
  };
  const labelMap: Record<string, string> = {
    queued: "Queued",
    starting: "Starting",
    backup: "Creating offline backup",
    validate_backup: "Validating backup",
    apply: "Applying update",
    restore_files: "Restoring files",
    verify: "Verifying",
    rollback: "Rolling back",
    completed: "Completed",
    failed: "Failed"
  };
  return {
    percent: status === "completed" ? 100 : status === "failed" ? 100 : phaseProgress[phase] ?? 10,
    label: labelMap[phase] || titleCase(phase),
    phase,
    status
  };
}

function formatDependencyLogTime(value?: string) {
  if (!value) return "now";
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function dependencyLogTypeLabel(value: string) {
  if (value === "stdout") return "log";
  if (value === "connection.ready") return "ready";
  return value.replaceAll("_", " ");
}

function dependencyJobDiagnosis(job: DependencyJob | null): DependencyFailureDiagnosis | null {
  const value = job?.result?.diagnosis;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  return {
    category: stringPayload(payload.category) || "unknown",
    title: stringPayload(payload.title) || "Update job failed",
    summary: stringPayload(payload.summary) || "The update did not complete.",
    safe_state: stringPayload(payload.safe_state) || "IACS stopped before promoting unverified runtime changes.",
    retry_recommendation: stringPayload(payload.retry_recommendation) || "Review the logs and retry when the blocker is resolved.",
    actions: arrayOfStrings(payload.actions),
    affected_packages: arrayOfStrings(payload.affected_packages),
    command: stringPayload(payload.command),
    technical_detail: stringPayload(payload.technical_detail)
  };
}

function dependencyRollbackSummary(job: DependencyJob | null): string {
  const rollback = job?.result?.rollback;
  if (!rollback || typeof rollback !== "object" || Array.isArray(rollback)) {
    return "No live manifests were promoted.";
  }
  const payload = rollback as Record<string, unknown>;
  if (payload.restored === true) return "Offline backup restored; live manifests are back to their pre-update state.";
  if (payload.attempted === true) return "Rollback was attempted. Review the job logs to confirm the restore result.";
  return "No live manifests were promoted.";
}

function arrayOfStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function verificationStepDetails(step: string) {
  const clean = step.trim();
  const explicit = clean.match(/^\[(automated|operator|manual|iacs)\]\s*(.+)$/i);
  if (explicit) {
    const type = explicit[1].toLowerCase();
    return {
      label: type === "operator" || type === "manual" ? "Operator" : "IACS job",
      text: explicit[2],
      tone: type === "operator" || type === "manual" ? "amber" as BadgeTone : "blue" as BadgeTone
    };
  }
  const automated = /\b(npm run build|frontend build|compile|pytest|unit test|health|typecheck|lint)\b/i.test(clean);
  return {
    label: automated ? "IACS job" : "Operator",
    text: clean,
    tone: automated ? "blue" as BadgeTone : "amber" as BadgeTone
  };
}

function parseSuggestedDiff(diff: string) {
  const files: Array<{ file: string; added: number; removed: number; lines: string[] }> = [];
  let current: { file: string; added: number; removed: number; lines: string[] } | null = null;
  for (const line of diff.split(/\r?\n/)) {
    const fileMatch = line.match(/^\*\*\* (?:Update|Add|Delete) File:\s+(.+)$/);
    if (fileMatch) {
      current = { file: fileMatch[1], added: 0, removed: 0, lines: [line] };
      files.push(current);
      continue;
    }
    if (!current) {
      current = { file: "Suggested patch", added: 0, removed: 0, lines: [] };
      files.push(current);
    }
    current.lines.push(line);
    if (line.startsWith("+") && !line.startsWith("+++")) current.added += 1;
    if (line.startsWith("-") && !line.startsWith("---")) current.removed += 1;
  }
  return files;
}

function DependencyUpdatesHub({
  packages,
  storage,
  loading,
  error,
  onChanged,
  onInspect
}: {
  packages: DependencyPackage[];
  storage: DependencyStorageStatus | null;
  loading: boolean;
  error: string;
  onChanged: () => Promise<void>;
  onInspect: (dependency: DependencyPackage) => void;
}) {
  const [checkingAll, setCheckingAll] = React.useState(false);
  const [checkSummary, setCheckSummary] = React.useState<DependencyCheckAllResult | null>(null);
  const [checkError, setCheckError] = React.useState("");
  const [showAll, setShowAll] = React.useState(false);
  const updateRows = React.useMemo(() => packages.filter(dependencyIsActionableUpdate), [packages]);
  const transitiveUpdateCount = packages.filter((dependency) => dependency.update_available && !dependencyCanApply(dependency)).length;
  const rows = showAll ? packages : updateRows;
  const checkedCount = packages.filter((dependency) => dependency.last_checked_at).length;
  const directCount = packages.filter((dependency) => dependency.is_direct).length;
  const sync = async () => {
    await api.post("/api/v1/dependency-updates/sync", {});
    await onChanged();
  };
  const checkAll = async () => {
    setCheckingAll(true);
    setCheckSummary(null);
    setCheckError("");
    try {
      const result = await api.post<DependencyCheckAllResult>("/api/v1/dependency-updates/check", { direct_only: false });
      setCheckSummary(result);
      await onChanged();
      setShowAll(false);
    } catch (nextError) {
      setCheckError(nextError instanceof Error ? nextError.message : "Unable to check dependencies.");
    } finally {
      setCheckingAll(false);
    }
  };
  return (
    <div className="dependency-updates-page">
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {checkError ? <div className="auth-error inline-error">{checkError}</div> : null}
      <DependencyStoragePanel storage={storage} onChanged={onChanged} />
      <section className="card dependency-update-table-card">
        <div className="dependency-update-table-head">
          <div>
            <h2>{showAll ? "Enrolled Dependencies" : "Available Updates"}</h2>
            <p>
              {showAll
                ? "All auto-enrolled external packages, including dependencies that are current or not checked yet."
                : updateRows.length
                  ? "Direct packages and images with newer versions that can be applied from IACS."
                  : checkedCount
                    ? "No actionable updates are currently known."
                    : "Run Check All to compare enrolled packages with their registries."}
            </p>
          </div>
          <div className="dependency-update-actions">
            <button className="primary-button" onClick={checkAll} disabled={loading || checkingAll} type="button">
              {checkingAll ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />} Check All
            </button>
            <button className="secondary-button" onClick={sync} disabled={loading} type="button">
              <RefreshCcw size={15} /> Sync Enrollment
            </button>
            <button className="secondary-button" onClick={onChanged} disabled={loading} type="button">
              <RefreshCw size={15} /> Refresh
            </button>
            <button className={showAll ? "secondary-button active" : "secondary-button"} onClick={() => setShowAll((value) => !value)} disabled={loading} type="button">
              {showAll ? "Show Updates" : "Show All"}
            </button>
          </div>
        </div>
        <div className="dependency-update-metrics">
          <div><span>Actionable</span><strong>{updateRows.length}</strong></div>
          <div><span>Enrolled</span><strong>{packages.length}</strong></div>
          <div><span>Direct</span><strong>{directCount}</strong></div>
          <div><span>Checked</span><strong>{checkedCount}</strong></div>
          <div><span>Transitive</span><strong>{transitiveUpdateCount}</strong></div>
        </div>
        {checkSummary ? (
          <div className={checkSummary.failed ? "dependency-check-summary warning" : "dependency-check-summary"}>
            Checked {checkSummary.checked} packages and found {checkSummary.updates} registry updates.
            {updateRows.length ? ` ${updateRows.length} can be applied directly from this hub.` : ""}
            {transitiveUpdateCount ? ` ${transitiveUpdateCount} transitive lockfile updates are available under Show All.` : ""}
            {checkSummary.failed ? ` ${checkSummary.failed} checks failed; see Updates & Rollbacks logs for details.` : ""}
          </div>
        ) : null}
        {loading || checkingAll ? <div className="loading-panel">{checkingAll ? "Checking every enrolled dependency" : "Loading dependency updates"}</div> : null}
        {!loading && !checkingAll && rows.length ? (
          <div className="dependency-update-table">
            <div className="dependency-update-row header">
              <span>Package</span>
              <span>Dependant</span>
              <span>Current</span>
              <span>New</span>
              <span>{showAll ? "Update" : "Risk"}</span>
              <span />
            </div>
            {rows.map((dependency) => (
              <div className="dependency-update-row" key={dependency.id}>
                <div>
                  <strong>{dependency.package_name}</strong>
                  <small>{dependency.ecosystem} · {dependency.is_direct ? "direct" : "transitive"}</small>
                </div>
                <span>{dependency.dependant_area}</span>
                <code>{dependency.current_version || "unknown"}</code>
                <code>{dependency.latest_version || "unchecked"}</code>
                <Badge tone={showAll ? dependencyUpdateTone(dependency) : riskTone(dependency.risk_status)}>
                  {showAll ? dependencyUpdateLabel(dependency) : titleCase(String(dependency.risk_status || "unknown"))}
                </Badge>
                <button className="secondary-button" onClick={() => onInspect(dependency)} type="button">
                  {dependency.update_available && dependencyCanApply(dependency) ? "Inspect/Update" : "Inspect"}
                </button>
              </div>
            ))}
          </div>
        ) : null}
        {!loading && !checkingAll && !rows.length ? (
          <div className="dependency-empty-state">
            <EmptyState icon={RefreshCcw} label={packages.length ? "No actionable updates." : "No dependencies enrolled yet."} />
            {packages.length ? <p>{checkedCount ? transitiveUpdateCount ? `${transitiveUpdateCount} transitive update${transitiveUpdateCount === 1 ? "" : "s"} can be inspected in Show All, but should move through their direct parent dependency.` : "Everything currently checked is up to date." : "Run Check All to populate this hub."}</p> : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

function DependencyUpdatePanel({
  packages,
  storage,
  onChanged
}: {
  packages: DependencyPackage[];
  storage: DependencyStorageStatus | null;
  onChanged: () => Promise<void>;
}) {
  const [selected, setSelected] = React.useState<DependencyPackage | null>(packages.find(dependencyIsActionableUpdate) ?? packages.find((dependency) => dependency.update_available) ?? packages[0] ?? null);
  React.useEffect(() => {
    setSelected((current) => current && packages.some((dependency) => dependency.id === current.id)
      ? packages.find((dependency) => dependency.id === current.id) ?? current
      : packages.find(dependencyIsActionableUpdate) ?? packages.find((dependency) => dependency.update_available) ?? packages[0] ?? null);
  }, [packages]);
  if (!packages.length) return <div className="empty-state">No enrolled dependencies are linked to this integration yet</div>;
  return (
    <div className="dependency-integration-panel">
      <div className="dependency-package-list">
        {packages.map((dependency) => (
          <button className={selected?.id === dependency.id ? "dependency-package-button active" : "dependency-package-button"} key={dependency.id} onClick={() => setSelected(dependency)} type="button">
            <span>
              <strong>{dependency.package_name}</strong>
              <small>{dependency.current_version || "unknown"}{" -> "}{dependency.latest_version || "unchecked"}</small>
            </span>
            <Badge tone={dependencyUpdateTone(dependency)}>{dependencyUpdateLabel(dependency)}</Badge>
          </button>
        ))}
      </div>
      {selected ? (
        <DependencyUpdateDeepDive dependency={selected} embedded storage={storage} onChanged={onChanged} />
      ) : null}
    </div>
  );
}

function DependencyUpdateModal({
  dependency,
  storage,
  onClose,
  onChanged
}: {
  dependency: DependencyPackage;
  storage: DependencyStorageStatus | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card dependency-update-modal" role="dialog" aria-modal="true" aria-labelledby="dependency-update-title">
        <div className="modal-header">
          <div>
            <h2 id="dependency-update-title">{dependency.package_name}</h2>
            <p>{dependency.dependant_area} · {dependency.ecosystem}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close"><X size={16} /></button>
        </div>
        <DependencyUpdateDeepDive dependency={dependency} storage={storage} onChanged={onChanged} />
      </div>
    </div>
  );
}

function DependencyUpdateDeepDive({
  dependency,
  storage,
  embedded = false,
  onChanged
}: {
  dependency: DependencyPackage;
  storage: DependencyStorageStatus | null;
  embedded?: boolean;
  onChanged: () => Promise<void>;
}) {
  const [current, setCurrent] = React.useState(dependency);
  const [analysis, setAnalysis] = React.useState<DependencyAnalysis | null>(dependency.latest_analysis);
  const [backups, setBackups] = React.useState<DependencyBackup[]>([]);
  const [job, setJob] = React.useState<DependencyJob | null>(null);
  const [jobEvents, setJobEvents] = React.useState<DependencyJobEvent[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [confirmAction, setConfirmAction] = React.useState<DependencyConfirmAction | null>(null);
  const jobSocketRef = React.useRef<WebSocket | null>(null);

  const loadBackups = React.useCallback(async () => {
    const result = await api.get<{ backups: DependencyBackup[] }>(`/api/v1/dependency-updates/packages/${dependency.id}/backups`);
    setBackups(result.backups);
  }, [dependency.id]);

  const loadCurrentDependency = React.useCallback(async () => {
    const result = await api.get<{ packages: DependencyPackage[] }>("/api/v1/dependency-updates/packages");
    const next = result.packages.find((candidate) => candidate.id === dependency.id);
    if (!next) return null;
    setCurrent(next);
    setAnalysis(next.latest_analysis);
    return next;
  }, [dependency.id]);

  React.useEffect(() => {
    setCurrent(dependency);
    setAnalysis(dependency.latest_analysis);
    loadBackups().catch(() => undefined);
  }, [dependency, loadBackups]);

  const check = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await api.post<DependencyPackage>(`/api/v1/dependency-updates/packages/${dependency.id}/check`, {});
      setCurrent(next);
      setAnalysis(next.latest_analysis);
      await onChanged();
    } catch (checkError) {
      setError(checkError instanceof Error ? checkError.message : "Unable to check for updates.");
    } finally {
      setLoading(false);
    }
  };

  const analyze = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await api.post<DependencyAnalysis>(`/api/v1/dependency-updates/packages/${dependency.id}/analyze`, {
        target_version: current.latest_version || undefined
      });
      setAnalysis(next);
      await onChanged();
    } catch (analysisError) {
      setError(analysisError instanceof Error ? analysisError.message : "Unable to analyze this update.");
    } finally {
      setLoading(false);
    }
  };

  const closeJobSocket = React.useCallback((socket?: WebSocket) => {
    const target = socket ?? jobSocketRef.current;
    if (!target) return;
    target.onmessage = null;
    target.onerror = null;
    target.onclose = null;
    if (jobSocketRef.current === target) jobSocketRef.current = null;
    if (target.readyState === WebSocket.CONNECTING || target.readyState === WebSocket.OPEN) {
      target.close();
    }
  }, []);

  React.useEffect(() => () => closeJobSocket(), [closeJobSocket]);

  const openJobSocket = React.useCallback((jobId: string) => {
    closeJobSocket();
    const socket = new WebSocket(wsUrl(`/api/v1/dependency-updates/jobs/${jobId}/ws`));
    jobSocketRef.current = socket;
    socket.onmessage = (event) => {
      let parsed: DependencyJobEvent;
      try {
        parsed = JSON.parse(event.data) as DependencyJobEvent;
      } catch {
        return;
      }
      const next = compactDependencyJobEvent(parsed);
      if (next.type === "connection.ready") return;
      setJobEvents((events) => [...events, next].slice(-DEPENDENCY_JOB_EVENT_LIMIT));
      if (next.phase) {
        setJob((currentJob) => currentJob ? { ...currentJob, phase: next.phase || currentJob.phase } : currentJob);
      }
      if (next.type === "completed" || next.type === "failed") {
        setJob((currentJob) => currentJob ? {
          ...currentJob,
          status: next.type === "completed" ? "completed" : "failed",
          phase: next.phase || currentJob.phase,
          error: next.type === "failed" ? next.message || currentJob.error : currentJob.error,
          result: next.result || currentJob.result
        } : currentJob);
        if (next.type === "completed") {
          setCurrent((dependencyState) => ({
            ...dependencyState,
            current_version: dependencyState.latest_version || dependencyState.current_version,
            update_available: false,
            risk_status: "safe"
          }));
        }
        closeJobSocket(socket);
        Promise.all([
          api.get<DependencyJob>(`/api/v1/dependency-updates/jobs/${jobId}`).then(setJob),
          onChanged(),
          loadCurrentDependency(),
          loadBackups()
        ]).catch(() => undefined);
      }
    };
    socket.onerror = () => closeJobSocket(socket);
    socket.onclose = () => {
      if (jobSocketRef.current === socket) jobSocketRef.current = null;
    };
  }, [closeJobSocket, loadBackups, loadCurrentDependency, onChanged]);

  const startApplyUpdate = async () => {
    setLoading(true);
    setError("");
    setJobEvents([]);
    try {
      const nextJob = await api.post<DependencyJob>(`/api/v1/dependency-updates/packages/${dependency.id}/apply`, {
        target_version: current.latest_version || undefined,
        confirmed: true
      });
      setJob(nextJob);
      openJobSocket(nextJob.id);
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "Unable to start update job.");
    } finally {
      setLoading(false);
    }
  };

  const startRestoreBackup = async (backup: DependencyBackup) => {
    setLoading(true);
    setError("");
    setJobEvents([]);
    try {
      const nextJob = await api.post<DependencyJob>(`/api/v1/dependency-updates/backups/${backup.id}/restore`, { confirmed: true });
      setJob(nextJob);
      openJobSocket(nextJob.id);
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Unable to start restore job.");
    } finally {
      setLoading(false);
    }
  };

  const confirmSelectedAction = async () => {
    const action = confirmAction;
    if (!action) return;
    setConfirmAction(null);
    if (action.kind === "apply") {
      await startApplyUpdate();
    } else {
      await startRestoreBackup(action.backup);
    }
  };

  const updateActionAvailable = Boolean(current.update_available && current.latest_version);
  const checked = Boolean(current.last_checked_at);
  const analysisMatchesTarget = Boolean(analysis && current.latest_version && analysis.target_version === current.latest_version);
  const analysisRequired = updateActionAvailable && !analysisMatchesTarget;
  const breakingBlocked = updateActionAvailable && analysisMatchesTarget && String(analysis?.verdict || "").toLowerCase() === "breaking";
  const applyActionAvailable = updateActionAvailable && dependencyCanApply(current) && !analysisRequired && !breakingBlocked;
  const applyActionTitle = applyActionAvailable
    ? "Apply this update"
    : breakingBlocked
      ? "Breaking updates are blocked until the migration is resolved and analysis is re-run"
      : analysisRequired
        ? "Analyze this target version before applying"
        : updateActionAvailable
          ? "Transitive packages must be updated through their direct dependency"
          : checked
            ? "No update is available to apply"
            : "Check this dependency first";
  const jobActive = job?.status === "queued" || job?.status === "running";
  const jobCompleted = job?.status === "completed";
  const hasExecution = Boolean(job || jobEvents.length);

  return (
    <div className={embedded ? "dependency-update-deep-dive embedded" : "dependency-update-deep-dive"}>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <div className="dependency-version-strip">
        <div><span>Current</span><strong>{current.current_version || "unknown"}</strong></div>
        <div><span>Latest</span><strong>{current.latest_version || "unchecked"}</strong></div>
        <div><span>Storage</span><strong>{storage?.config_status || "unknown"}</strong></div>
        <Badge tone={dependencyUpdateTone(current)}>{dependencyUpdateLabel(current)}</Badge>
      </div>
      <div className="dependency-update-actions">
        <button className="secondary-button" onClick={check} disabled={loading} type="button">
          <RefreshCcw size={15} /> Check
        </button>
        {jobCompleted ? (
          <button className="secondary-button" disabled type="button">
            <CheckCircle2 size={15} /> Update Complete
          </button>
        ) : (
          <>
            <button className="primary-button" onClick={analyze} disabled={loading || jobActive || !updateActionAvailable} title={updateActionAvailable ? "Analyze this update" : checked ? "No update is available to analyze" : "Check this dependency first"} type="button">
              <Bot size={15} /> Analyze
            </button>
            <button
              className="primary-button"
              onClick={() => setConfirmAction({ kind: "apply" })}
              disabled={loading || jobActive || !applyActionAvailable}
              title={applyActionTitle}
              type="button"
            >
              <Play size={15} /> Proceed with Update
            </button>
          </>
        )}
      </div>
      {updateActionAvailable && !dependencyCanApply(current) ? (
        <div className="dependency-check-summary warning">This package is transitive. Review the analysis here, then update the owning direct dependency or lockfile.</div>
      ) : null}
      {updateActionAvailable && dependencyCanApply(current) && analysisRequired ? (
        <div className="dependency-check-summary warning">Run analysis for {current.latest_version} before applying so IACS can review changelog risk against local usage.</div>
      ) : null}
      {breakingBlocked ? (
        <div className="dependency-check-summary danger">IACS blocked this update because the latest analysis marked it Breaking. Resolve the proposed migration, run the build checks, then re-run analysis before applying.</div>
      ) : null}
      {hasExecution ? (
        <DependencyLiveExecution
          events={jobEvents}
          job={job}
          onRetry={() => setConfirmAction({ kind: "apply" })}
          retryDisabled={loading || !applyActionAvailable || jobActive}
        />
      ) : (
        <>
          <section className="dependency-analysis-panel">
            <div className="dependency-panel-title">
              <strong>LLM Analysis</strong>
              {analysis ? <Badge tone={riskTone(analysis.verdict)}>{titleCase(String(analysis.verdict))}</Badge> : <Badge tone="gray">Not Analyzed</Badge>}
            </div>
            {analysis ? (
              <DependencyAnalysisReview analysis={analysis} />
            ) : (
              <div className="empty-state">Run analysis to review changelog risk and local code usage.</div>
            )}
          </section>
          {analysis?.suggested_diff ? <DependencySuggestedFixes diff={analysis.suggested_diff} /> : null}
        </>
      )}
      <section className="dependency-backup-panel">
        <div className="dependency-panel-title">
          <strong>Backup History</strong>
          <Badge tone="gray">{backups.length}</Badge>
        </div>
        {backups.length ? backups.map((backup) => (
          <div className="dependency-backup-row" key={backup.id}>
            <div>
              <strong>{backup.version || "unknown"} · {backup.reason}</strong>
              <span>{formatDate(backup.created_at)} · {formatFileSize(backup.size_bytes)}</span>
            </div>
            <button className="secondary-button" onClick={() => setConfirmAction({ kind: "restore", backup })} disabled={loading || jobActive} type="button">
              <ShieldCheck size={15} /> Restore
            </button>
          </div>
        )) : <div className="empty-state">No backups have been created for this package.</div>}
      </section>
      {confirmAction ? (
        <DependencyUpdateConfirmModal
          action={confirmAction}
          dependency={current}
          loading={loading}
          onCancel={() => setConfirmAction(null)}
          onConfirm={confirmSelectedAction}
        />
      ) : null}
    </div>
  );
}

function DependencyAnalysisReview({ analysis }: { analysis: DependencyAnalysis }) {
  const summaryLines = analysis.summary_markdown.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return (
    <div className="dependency-analysis-review">
      <section className="dependency-analysis-card">
        <h4>Summary</h4>
        <div className="dependency-analysis-lines">
          {summaryLines.map((line, index) => (
            <ProtectAnalysisLine line={line} key={`${analysis.id}-summary-${index}`} />
          ))}
        </div>
      </section>
      <section className="dependency-analysis-card">
        <div className="dependency-verification-title">
          <h4>Verification Plan</h4>
          <Badge tone="blue">Guided</Badge>
        </div>
        <p>IACS runs install, build, and health checks during Live Execution. These LLM-generated steps are the remaining checks to confirm the affected feature still behaves correctly.</p>
        <div className="dependency-verification-list">
          {analysis.verification_steps.length ? analysis.verification_steps.map((step, index) => {
            const details = verificationStepDetails(step);
            return (
              <div className="dependency-verification-step" key={`${analysis.id}-verify-${index}`}>
                <Badge tone={details.tone}>{details.label}</Badge>
                <span>{renderInlineMarkdown(details.text)}</span>
              </div>
            );
          }) : (
            <span className="dependency-muted">No extra verification steps were suggested.</span>
          )}
        </div>
      </section>
    </div>
  );
}

function DependencySuggestedFixes({ diff }: { diff: string }) {
  const files = React.useMemo(() => parseSuggestedDiff(diff), [diff]);
  return (
    <section className="dependency-fix-panel">
      <div className="dependency-panel-title">
        <div>
          <strong>Proposed Fixes</strong>
          <p>LLM-generated patch guidance. IACS applies package-manager changes automatically when you proceed.</p>
        </div>
        <Badge tone="gray">Draft</Badge>
      </div>
      <div className="dependency-fix-files">
        {files.map((file) => (
          <details className="dependency-fix-file" key={file.file}>
            <summary>
              <span>
                <strong>{file.file}</strong>
                <small>{file.added} added · {file.removed} removed</small>
              </span>
              <span className="dependency-fix-toggle">Show patch</span>
            </summary>
            <pre>{file.lines.join("\n")}</pre>
          </details>
        ))}
      </div>
    </section>
  );
}

function DependencyLiveExecution({
  events,
  job,
  onRetry,
  retryDisabled
}: {
  events: DependencyJobEvent[];
  job: DependencyJob | null;
  onRetry: () => void;
  retryDisabled: boolean;
}) {
  const progress = dependencyJobProgress(job, events);
  const failed = progress.status === "failed";
  const completed = progress.status === "completed";
  const diagnosis = dependencyJobDiagnosis(job);
  const rollbackSummary = dependencyRollbackSummary(job);
  const terminalRef = React.useRef<HTMLDivElement | null>(null);
  const latestEvent = events[events.length - 1];
  const latestEventKey = latestEvent
    ? `${latestEvent.created_at || ""}:${latestEvent.type}:${latestEvent.phase || ""}:${latestEvent.message || ""}`
    : "empty";

  React.useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return undefined;
    const frame = window.requestAnimationFrame(() => {
      terminal.scrollTop = terminal.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [latestEventKey, progress.status]);

  return (
    <section className="dependency-terminal-panel">
      <div className="dependency-panel-title">
        <div>
          <strong>Live Execution</strong>
          <p>{progress.label}</p>
        </div>
        {job ? <Badge tone={failed ? "red" : completed ? "green" : "blue"}>{titleCase(job.status)}</Badge> : null}
      </div>
      <div className={failed ? "dependency-progress failed" : completed ? "dependency-progress completed" : "dependency-progress"}>
        <div className="dependency-progress-track" aria-label={`Update progress ${progress.percent}%`}>
          <span style={{ width: `${progress.percent}%` }} />
        </div>
        <div className="dependency-progress-meta">
          <span>{progress.percent}%</span>
          <span>{progress.label}</span>
        </div>
      </div>
      {failed ? (
        <div className="dependency-job-resolution error">
          <strong>{diagnosis?.title || "Update did not complete."}</strong>
          <p>{diagnosis?.summary || "IACS could not complete this update."}</p>
          <p>{rollbackSummary}</p>
          {diagnosis?.affected_packages.length ? (
            <div className="dependency-recovery-pills">
              {diagnosis.affected_packages.map((name) => <Badge tone="amber" key={name}>{name}</Badge>)}
            </div>
          ) : null}
          {diagnosis?.actions.length ? (
            <div className="dependency-recovery-list">
              {diagnosis.actions.map((action, index) => (
                <div key={`${diagnosis.category}-action-${index}`}>
                  <CheckCircle2 size={14} />
                  <span>{action}</span>
                </div>
              ))}
            </div>
          ) : null}
          {diagnosis?.retry_recommendation ? <p>{diagnosis.retry_recommendation}</p> : null}
          {diagnosis?.command ? <code className="dependency-failed-command">{diagnosis.command}</code> : null}
          <button className="secondary-button" onClick={onRetry} disabled={retryDisabled} type="button">
            <RefreshCcw size={15} /> Retry Update
          </button>
        </div>
      ) : null}
      <div className="log-console dependency-terminal" ref={terminalRef}>
        {events.length ? events.map((event, index) => (
          <div className="log-line" key={`${event.created_at}-${event.type}-${index}`}>
            <time>{formatDependencyLogTime(event.created_at)}</time>
            <strong>{dependencyLogTypeLabel(event.type)}</strong>
            <code>{event.message || event.phase || ""}</code>
          </div>
        )) : (
          <div className="log-line">
            <time>now</time>
            <strong>queued</strong>
            <code>Waiting for job output...</code>
          </div>
        )}
      </div>
    </section>
  );
}

function DependencyUpdateConfirmModal({
  action,
  dependency,
  loading,
  onCancel,
  onConfirm
}: {
  action: DependencyConfirmAction;
  dependency: DependencyPackage;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const isApply = action.kind === "apply";
  return (
    <div className="modal-backdrop nested-modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="dependency-update-confirm-title">
        <div className="gate-confirm-title">
          <span className="gate-confirm-icon">
            {isApply ? <Play size={19} /> : <ShieldCheck size={19} />}
          </span>
          <div>
            <h2 id="dependency-update-confirm-title">{isApply ? "Proceed with dependency update?" : "Restore dependency backup?"}</h2>
            <p>
              {isApply
                ? `${dependency.package_name} will update from ${dependency.current_version || "unknown"} to ${dependency.latest_version || "the selected version"}. IACS will create an offline backup first, stream progress, verify the build, and roll back automatically if the update cannot be completed.`
                : `Restore backup ${action.backup.id}. IACS will validate the archive checksum, restore manifests, and run verification afterwards.`}
            </p>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading} type="button">Cancel</button>
          <button className="primary-button" onClick={onConfirm} disabled={loading} type="button">
            {isApply ? <Play size={15} /> : <ShieldCheck size={15} />}
            {loading ? "Starting..." : isApply ? "Start Update" : "Restore Backup"}
          </button>
        </div>
      </div>
    </div>
  );
}

function DependencyStoragePanel({ storage, onChanged }: { storage: DependencyStorageStatus | null; onChanged: () => Promise<void> }) {
  const [mode, setMode] = React.useState(storage?.mode || "local");
  const [source, setSource] = React.useState(storage?.mount_source || "");
  const [options, setOptions] = React.useState(storage?.mount_options || "");
  const [minFree, setMinFree] = React.useState(String(storage?.min_free_bytes ?? 1073741824));
  const [retentionDays, setRetentionDays] = React.useState(String(storage?.retention_days || ""));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    setMode(storage?.mode || "local");
    setSource(storage?.mount_source || "");
    setOptions(storage?.mount_options || "");
    setMinFree(String(storage?.min_free_bytes ?? 1073741824));
    setRetentionDays(String(storage?.retention_days || ""));
  }, [storage]);

  const saveStorage = async () => {
    setSaving(true);
    setError("");
    try {
      await api.post("/api/v1/dependency-updates/storage/config", {
        mode,
        mount_source: source,
        mount_options: options,
        retention_days: retentionDays,
        min_free_bytes: Number(minFree) || 0
      });
      await onChanged();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save storage configuration.");
    } finally {
      setSaving(false);
    }
  };

  const validate = async () => {
    setSaving(true);
    setError("");
    try {
      await api.post("/api/v1/dependency-updates/storage/validate", {});
      await onChanged();
    } catch (validateError) {
      setError(validateError instanceof Error ? validateError.message : "Unable to validate storage.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card dependency-storage-panel">
      <div className="dependency-storage-summary">
        <div>
          <h2>Backup Storage</h2>
          <p>{storage?.detail || "Configure where offline update backups are stored."}</p>
        </div>
        <Badge tone={storage?.config_status === "pending_reboot" ? "amber" : storage?.ok ? "green" : "red"}>
          {storage?.config_status === "pending_reboot" ? "Reboot Required" : storage?.ok ? "Ready" : "Needs Attention"}
        </Badge>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <div className="dependency-storage-grid">
        <label className="field">
          <span>Mode</span>
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            <option value="local">Local bind mount</option>
            <option value="nfs">Host-mounted NFS path</option>
            <option value="samba">Host-mounted Samba/CIFS path</option>
          </select>
        </label>
        <label className="field">
          <span>Mount source</span>
          <input value={source} onChange={(event) => setSource(event.target.value)} placeholder={mode === "local" ? "./data/backend/dependency-update-backups" : mode === "nfs" ? "/mnt/iacs-update-backups" : "/mnt/iacs-update-backups"} disabled={mode === "local"} />
        </label>
        <label className="field">
          <span>Mount options</span>
          <input value={options} onChange={(event) => setOptions(event.target.value)} placeholder={mode === "local" ? "not used for local mode" : mode === "samba" ? "username=iacs,password=...,vers=3.0,rw" : "addr=nas.local,rw"} disabled={mode === "local"} />
        </label>
        <label className="field">
          <span>Minimum free bytes</span>
          <input value={minFree} onChange={(event) => setMinFree(event.target.value)} inputMode="numeric" />
        </label>
        <label className="field">
          <span>Retention days</span>
          <input value={retentionDays} onChange={(event) => setRetentionDays(event.target.value)} inputMode="numeric" placeholder="optional" />
        </label>
      </div>
      <div className="dependency-storage-meta">
        <span>Active root: <code>{storage?.backup_root || "/app/update-backups"}</code></span>
        <span>Free: <strong>{formatFileSize(storage?.free_bytes ?? 0)}</strong></span>
      </div>
      <div className="dependency-update-actions">
        <button className="secondary-button" onClick={validate} disabled={saving} type="button">
          <CheckCircle2 size={15} /> Validate
        </button>
        <button className="primary-button" onClick={saveStorage} disabled={saving} type="button">
          <Save size={15} /> Save Storage Config
        </button>
      </div>
      <p className="dependency-storage-note">Changing NFS/Samba storage writes a generated Compose override and requires a host reboot or full Compose recreation before the mount changes.</p>
    </section>
  );
}

function riskTone(value: string | null | undefined): BadgeTone {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "breaking" || normalized === "red" || normalized === "error") return "red";
  if (normalized === "warning" || normalized === "unknown" || normalized === "amber") return "amber";
  if (normalized === "safe" || normalized === "green") return "green";
  return "gray";
}

function UnifiProtectUpdatesPanel({
  status,
  onChanged
}: {
  status: UnifiProtectUpdateStatus | null;
  onChanged: () => Promise<void>;
}) {
  const [updateStatus, setUpdateStatus] = React.useState<UnifiProtectUpdateStatus | null>(status);
  const [targetVersion, setTargetVersion] = React.useState(status?.latest_version ?? "");
  const [analysis, setAnalysis] = React.useState<UnifiProtectUpdateAnalysis | null>(null);
  const [backups, setBackups] = React.useState<UnifiProtectBackup[]>([]);
  const [result, setResult] = React.useState<UnifiProtectUpdateApplyResult | null>(null);
  const [confirmAction, setConfirmAction] = React.useState<ProtectUpdateConfirmAction | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const loadUpdateData = React.useCallback(async () => {
    setError("");
    try {
      const [nextStatus, backupResult] = await Promise.all([
        api.get<UnifiProtectUpdateStatus>("/api/v1/integrations/unifi-protect/update/status"),
        api.get<{ backups: UnifiProtectBackup[] }>("/api/v1/integrations/unifi-protect/backups")
      ]);
      setUpdateStatus(nextStatus);
      setTargetVersion((current) => current || nextStatus.latest_version);
      setBackups(backupResult.backups);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load UniFi Protect update data.");
    }
  }, []);

  React.useEffect(() => {
    loadUpdateData().catch(() => undefined);
  }, [loadUpdateData]);

  const analyze = async () => {
    setLoading(true);
    setError("");
    setAnalysis(null);
    try {
      setAnalysis(await api.post<UnifiProtectUpdateAnalysis>("/api/v1/integrations/unifi-protect/update/analyze", {
        target_version: targetVersion || undefined
      }));
    } catch (analysisError) {
      setError(analysisError instanceof Error ? analysisError.message : "Unable to analyze the update.");
    } finally {
      setLoading(false);
    }
  };

  const createBackup = async () => {
    setLoading(true);
    setError("");
    try {
      const backup = await api.post<UnifiProtectBackup>("/api/v1/integrations/unifi-protect/backups", {});
      setBackups((current) => [backup, ...current]);
    } catch (backupError) {
      setError(backupError instanceof Error ? backupError.message : "Unable to create backup.");
    } finally {
      setLoading(false);
    }
  };

  const applyUpdate = async () => {
    if (!analysis) {
      setError("Analyze the release notes before applying the update.");
      return;
    }
    setConfirmAction({ kind: "apply" });
  };

  const runApplyUpdate = async () => {
    if (!analysis) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const applied = await api.post<UnifiProtectUpdateApplyResult>("/api/v1/integrations/unifi-protect/update/apply", {
        target_version: analysis.target_version,
        confirmed: true
      });
      setResult(applied);
      await loadUpdateData();
      await onChanged();
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "Unable to apply the update.");
      await loadUpdateData();
    } finally {
      setLoading(false);
    }
  };

  const restore = async (backup: UnifiProtectBackup) => {
    setConfirmAction({ kind: "restore", backup });
  };

  const deleteBackup = async (backup: UnifiProtectBackup) => {
    setConfirmAction({ kind: "delete", backup });
  };

  const runRestore = async (backup: UnifiProtectBackup) => {
    setLoading(true);
    setError("");
    try {
      await api.post(`/api/v1/integrations/unifi-protect/backups/${encodeURIComponent(backup.id)}/restore`, {});
      await loadUpdateData();
      await onChanged();
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Unable to restore backup.");
    } finally {
      setLoading(false);
    }
  };

  const runDeleteBackup = async (backup: UnifiProtectBackup) => {
    setLoading(true);
    setError("");
    try {
      await api.delete(`/api/v1/integrations/unifi-protect/backups/${encodeURIComponent(backup.id)}`);
      setBackups((current) => current.filter((item) => item.id !== backup.id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete backup.");
    } finally {
      setLoading(false);
    }
  };

  const currentVersion = updateStatus?.current_version ?? status?.current_version ?? "unknown";
  const latestVersion = updateStatus?.latest_version ?? status?.latest_version ?? "unknown";
  const updateAvailable = Boolean(updateStatus?.update_available);
  const updateApplied = Boolean(result?.ok);

  return (
    <div className="protect-update-panel">
        {error ? <div className="auth-error inline-error">{error}</div> : null}

        <div className="protect-update-summary">
          <div>
            <span>Current</span>
            <strong>{currentVersion}</strong>
          </div>
          <div>
            <span>Latest</span>
            <strong>{latestVersion}</strong>
          </div>
          <Badge tone={updateAvailable ? "amber" : "green"}>{updateAvailable ? "Update Available" : "Up To Date"}</Badge>
        </div>

        <div className="protect-update-actions">
          <label className="field protect-version-field">
            <span>Target version</span>
            <input value={targetVersion} onChange={(event) => setTargetVersion(event.target.value)} placeholder={latestVersion} />
          </label>
          <button className="secondary-button" onClick={createBackup} disabled={loading} type="button">
            <Download size={15} /> Backup
          </button>
        </div>

        <div className="protect-review-cta">
          <button className="primary-button" onClick={analyze} disabled={loading} type="button">
            <Bot size={15} /> {loading && !analysis ? "Reviewing..." : "Review Changes to Verify Compatibility"}
          </button>
        </div>

        {analysis ? (
          <section className="protect-update-analysis">
            <div className="protect-update-analysis-head">
              <div>
                <strong>AI Review</strong>
                <span>{analysis.provider} · {analysis.current_version} to {analysis.target_version}</span>
              </div>
              {analysis.release_notes.html_url ? <a href={analysis.release_notes.html_url} target="_blank" rel="noreferrer">Release notes</a> : null}
            </div>
            <ProtectAnalysisReview analysis={analysis.analysis} />
            <button className={updateApplied ? "secondary-button full" : "primary-button full"} onClick={applyUpdate} disabled={loading || updateApplied} type="button">
              {updateApplied ? <CheckCircle2 size={15} /> : <RefreshCcw size={15} />}
              {updateApplied ? "Update Complete" : loading ? "Applying..." : "Apply Update & Verify"}
            </button>
          </section>
        ) : (
          <div className="empty-state">Run analysis before applying a UniFi Protect package update</div>
        )}

        {result ? (
          <div className="protect-update-result">
            <CheckCircle2 size={17} />
            <div>
              <strong>Updated to {result.current_version}</strong>
              <span>{result.verification.camera_count ?? 0} cameras verified, sample snapshot {result.verification.snapshot_bytes ?? 0} bytes. Backup {result.backup.id} was created first.</span>
            </div>
          </div>
        ) : null}

        <section className="protect-backup-panel">
          <div className="protect-backup-title">
            <strong>Backups</strong>
            <span>Encrypted integration settings and package state.</span>
          </div>
          {backups.length ? backups.map((backup) => (
            <div className="protect-backup-row" key={backup.id}>
              <div>
                <strong>{backup.reason}</strong>
                <span>{formatDate(backup.created_at)} · package {backup.package_version} · {backup.settings_count} settings</span>
              </div>
              <a className="icon-button" href={backup.download_url} aria-label={`Download backup ${backup.id}`}>
                <Download size={14} />
              </a>
              <button className="icon-button danger" onClick={() => deleteBackup(backup)} disabled={loading} type="button" aria-label={`Delete backup ${backup.id}`}>
                <Trash2 size={14} />
              </button>
              <button className="secondary-button" onClick={() => restore(backup)} disabled={loading} type="button">
                Restore
              </button>
            </div>
          )) : (
            <div className="empty-state">No UniFi Protect backups yet</div>
          )}
        </section>

        {confirmAction ? (
          <ProtectUpdateConfirmModal
            action={confirmAction}
            loading={loading}
            onCancel={() => setConfirmAction(null)}
            onConfirm={async () => {
              const action = confirmAction;
              setConfirmAction(null);
              if (action.kind === "apply") {
                await runApplyUpdate();
              } else if (action.kind === "restore") {
                await runRestore(action.backup);
              } else {
                await runDeleteBackup(action.backup);
              }
            }}
          />
        ) : null}
    </div>
  );
}

function ProtectAnalysisReview({ analysis }: { analysis: string }) {
  const sections = parseProtectAnalysisSections(analysis);
  const riskSection = findAnalysisSection(sections, "risk level");
  const recommendationSection = findAnalysisSection(sections, "recommendation");
  const risk = firstMeaningfulAnalysisLine(riskSection?.body) || "Review";
  const recommendation = firstMeaningfulAnalysisLine(recommendationSection?.body) || "Review the notes before applying.";
  const riskTone = analysisTone(risk);
  const recommendationTone = analysisTone(recommendation);
  const detailSections = sections.filter((section) => !["risk level", "recommendation"].includes(section.title.toLowerCase()));

  return (
    <div className="protect-analysis-review">
      <div className="protect-analysis-summary">
        <div className={`protect-analysis-callout ${riskTone}`}>
          <span>Risk Level</span>
          <strong>{risk}</strong>
        </div>
        <div className={`protect-analysis-callout ${recommendationTone}`}>
          <span>Recommendation</span>
          <strong>{recommendation}</strong>
        </div>
      </div>
      <div className="protect-analysis-sections">
        {detailSections.length ? detailSections.map((section) => (
          <section className="protect-analysis-section" key={section.title}>
            <h4>{section.title}</h4>
            <div className="protect-analysis-lines">
              {section.body.map((line, index) => (
                <ProtectAnalysisLine line={line} key={`${section.title}-${index}`} />
              ))}
            </div>
          </section>
        )) : (
          <section className="protect-analysis-section">
            <h4>Review</h4>
            <div className="protect-analysis-lines">
              {analysis.split(/\r?\n/).map((line, index) => <ProtectAnalysisLine line={line} key={index} />)}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function ProtectAnalysisLine({ line }: { line: string }) {
  if (!line.trim()) return null;
  const leadingSpaces = line.match(/^\s*/)?.[0].length ?? 0;
  const cleanLine = line.trim().replace(/^[-*]\s+/, "");
  const isBullet = /^\s*[-*]\s+/.test(line);
  return (
    <div className={isBullet ? "protect-analysis-line bullet" : "protect-analysis-line"} style={isBullet ? { "--analysis-indent": String(Math.min(leadingSpaces / 2, 3)) } as React.CSSProperties : undefined}>
      {isBullet ? <span className="analysis-dot" aria-hidden="true" /> : null}
      <span>{renderInlineMarkdown(cleanLine)}</span>
    </div>
  );
}

type ProtectAnalysisSection = {
  title: string;
  body: string[];
};

function parseProtectAnalysisSections(markdown: string): ProtectAnalysisSection[] {
  const sections: ProtectAnalysisSection[] = [];
  let current: ProtectAnalysisSection | null = null;
  for (const line of markdown.split(/\r?\n/)) {
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    if (heading) {
      current = { title: cleanInlineMarkdown(heading[1]), body: [] };
      sections.push(current);
      continue;
    }
    if (!current) {
      current = { title: "Review", body: [] };
      sections.push(current);
    }
    current.body.push(line);
  }
  return sections.filter((section) => section.title || section.body.some((line) => line.trim()));
}

function findAnalysisSection(sections: ProtectAnalysisSection[], title: string) {
  return sections.find((section) => section.title.toLowerCase().includes(title));
}

function firstMeaningfulAnalysisLine(lines: string[] | undefined) {
  return cleanInlineMarkdown(lines?.find((line) => line.trim()) ?? "");
}

function cleanInlineMarkdown(value: string) {
  return value.replace(/^[-*]\s+/, "").replace(/\*\*/g, "").replace(/`/g, "").trim();
}

function analysisTone(value: string): "green" | "amber" | "red" | "blue" {
  const normalized = value.toLowerCase();
  if (normalized.includes("no-go") || normalized.includes("no go") || normalized.includes("high") || normalized.includes("critical")) return "red";
  if (normalized.includes("medium") || normalized.includes("caution") || normalized.includes("manual")) return "amber";
  if (normalized.includes("go") || normalized.includes("low")) return "green";
  return "blue";
}

function renderInlineMarkdown(value: string) {
  const parts = value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}

function ProtectUpdateConfirmModal({
  action,
  loading,
  onCancel,
  onConfirm
}: {
  action: ProtectUpdateConfirmAction;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const isApply = action.kind === "apply";
  const isDelete = action.kind === "delete";
  return (
    <div className="modal-backdrop nested-modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="protect-update-confirm-title">
        <div className="gate-confirm-title">
          <span className="gate-confirm-icon">
            {isApply ? <RefreshCcw size={19} /> : isDelete ? <Trash2 size={19} /> : <ShieldCheck size={19} />}
          </span>
          <div>
            <h2 id="protect-update-confirm-title">
              {isApply ? "Apply UniFi Protect update?" : isDelete ? "Delete UniFi Protect backup?" : "Restore UniFi Protect backup?"}
            </h2>
            <p>
              {isApply
                ? "A backup will be created first, then the package update will be applied and cameras verified."
                : isDelete
                  ? `Permanently delete backup ${action.backup.id}. This cannot be restored later.`
                  : `Restore backup ${action.backup.id} and verify the integration afterwards.`}
            </p>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading} type="button">Cancel</button>
          <button className={isDelete ? "danger-button" : "primary-button"} onClick={onConfirm} disabled={loading} type="button">
            {isApply ? <RefreshCcw size={15} /> : isDelete ? <Trash2 size={15} /> : <ShieldCheck size={15} />}
            {loading ? "Working..." : isApply ? "Apply Update" : isDelete ? "Delete Backup" : "Restore Backup"}
          </button>
        </div>
      </div>
    </div>
  );
}

function IntegrationModal({
  definition,
  initialTab,
  values,
  loading,
  protectCameras,
  protectError,
  protectLoading,
  protectStatus,
  protectUpdateStatus,
  dependencyPackages,
  dependencyStorage,
  icloudError,
  icloudLoading,
  icloudPayload,
  discordChannels,
  discordError,
  discordIdentities,
  discordLoading,
  discordStatus,
  people,
  schedules,
  onClose,
  onDiscordChanged,
  onICloudChanged,
  onProtectUpdateChanged,
  onProtectRefresh,
  onSettingsChanged,
  onSaved
}: {
  definition: IntegrationDefinition;
  initialTab: ProtectIntegrationTab;
  values: SettingsMap;
  loading: boolean;
  protectCameras?: UnifiProtectCamera[];
  protectError?: string;
  protectLoading?: boolean;
  protectStatus?: UnifiProtectStatus | null;
  protectUpdateStatus?: UnifiProtectUpdateStatus | null;
  dependencyPackages: DependencyPackage[];
  dependencyStorage: DependencyStorageStatus | null;
  icloudError?: string;
  icloudLoading?: boolean;
  icloudPayload?: ICloudCalendarPayload;
  discordChannels?: DiscordChannel[];
  discordError?: string;
  discordIdentities?: DiscordIdentity[];
  discordLoading?: boolean;
  discordStatus?: DiscordStatus | null;
  people: Person[];
  schedules: Schedule[];
  onClose: () => void;
  onDiscordChanged?: () => Promise<void>;
  onICloudChanged?: () => Promise<void>;
  onProtectUpdateChanged?: () => Promise<void>;
  onProtectRefresh?: () => Promise<void>;
  onSettingsChanged: () => Promise<void>;
  onSaved: (updates: Record<string, unknown>) => Promise<void>;
}) {
  const [activeTab, setActiveTab] = React.useState<ProtectIntegrationTab>(initialTab);
  const [form, setForm] = React.useState<Record<string, string>>(() => integrationInitialValues(definition, values));
  const [testing, setTesting] = React.useState(false);
  const [sendingTest, setSendingTest] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const [haDiscovery, setHaDiscovery] = React.useState<HomeAssistantDiscovery | null>(null);
  const [haDiscoveryError, setHaDiscoveryError] = React.useState("");
  const [haDiscoveryLoading, setHaDiscoveryLoading] = React.useState(false);
  const [appriseUrls, setAppriseUrls] = React.useState<AppriseUrlSummary[]>([]);
  const [appriseLoading, setAppriseLoading] = React.useState(false);
  const isHomeAssistant = definition.key === "home_assistant";
  const isApprise = definition.key === "apprise";
  const isUnifiProtect = definition.key === "unifi_protect";
  const isICloudCalendar = definition.key === "icloud_calendar";
  const isDiscord = definition.key === "discord";
  const hasDependencyUpdates = dependencyPackages.length > 0;

  React.useEffect(() => {
    setForm(integrationInitialValues(definition, values));
    setActiveTab(initialTab);
    setFeedback(null);
    setHaDiscovery(null);
    setHaDiscoveryError("");
    setAppriseUrls([]);
  }, [definition.key, initialTab]);

  const update = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }));

  const loadHomeAssistantDiscovery = React.useCallback(async () => {
    if (!isHomeAssistant) return;
    setHaDiscoveryLoading(true);
    setHaDiscoveryError("");
    try {
      const discovery = await api.get<HomeAssistantDiscovery>("/api/v1/integrations/home-assistant/entities");
      setHaDiscovery(discovery);
    } catch (error) {
      setHaDiscoveryError(error instanceof Error ? error.message : "Unable to load Home Assistant entities.");
    } finally {
      setHaDiscoveryLoading(false);
    }
  }, [isHomeAssistant]);

  React.useEffect(() => {
    if (isHomeAssistant) {
      loadHomeAssistantDiscovery().catch(() => undefined);
    }
  }, [isHomeAssistant, loadHomeAssistantDiscovery]);

  const loadAppriseUrls = React.useCallback(async () => {
    if (!isApprise) return;
    setAppriseLoading(true);
    try {
      const result = await api.get<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls");
      setAppriseUrls(result.urls);
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Unable to load Apprise URLs",
        detail: error instanceof Error ? error.message : "Unable to load Apprise URLs."
      });
    } finally {
      setAppriseLoading(false);
    }
  }, [isApprise]);

  React.useEffect(() => {
    if (isApprise) {
      loadAppriseUrls().catch(() => undefined);
    }
  }, [isApprise, loadAppriseUrls]);

  const testConnection = async () => {
    setTesting(true);
    setFeedback({
      tone: "progress",
      title: "Testing connection",
      detail: "Preparing integration settings.",
      activeStep: 0
    });
    try {
      await sleep(180);
      setFeedback({
        tone: "progress",
        title: "Testing connection",
        detail: `Contacting ${definition.title}.`,
        activeStep: 1
      });
      const request = api.post<{ ok: boolean; message: string }>("/api/v1/settings/test", {
        integration: definition.key,
        values: coerceSettingsPayload(form)
      });
      await sleep(260);
      setFeedback({
        tone: "progress",
        title: "Testing connection",
        detail: "Validating the response.",
        activeStep: 2
      });
      const result = await request;
      if (!result.ok) throw new Error(result.message);
      setFeedback({
        tone: "success",
        title: "Connection verified",
        detail: result.message
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Connection failed",
        detail: error instanceof Error ? error.message : "Connection test failed."
      });
    } finally {
      setTesting(false);
    }
  };

  const sendTestNotification = async () => {
    setSendingTest(true);
    setFeedback({
      tone: "progress",
      title: "Sending test notification",
      detail: "Composing a test message.",
      activeStep: 0
    });
    try {
      await sleep(180);
      setFeedback({
        tone: "progress",
        title: "Sending test notification",
        detail: `Delivering through ${isDiscord ? "Discord" : "Apprise"}.`,
        activeStep: 1
      });
      if (isDiscord) {
        await api.post("/api/v1/integrations/discord/test", {
          channel_id: form.discord_default_notification_channel_id || undefined,
          message: "This is a test Discord notification from API & Integrations."
        });
      } else {
        await api.post("/api/v1/integrations/notifications/test", {
          subject: "IACS test notification",
          severity: "info",
          message: "This is a test notification from API & Integrations."
        });
      }
      setFeedback({
        tone: "success",
        title: "Test notification sent",
        detail: `${isDiscord ? "Discord" : "Apprise"} accepted the notification request.`
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Notification failed",
        detail: error instanceof Error ? error.message : "Unable to send test notification."
      });
    } finally {
      setSendingTest(false);
    }
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setFeedback(null);
    try {
      await onSaved(coerceSettingsPayload(form));
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Unable to save settings",
        detail: error instanceof Error ? error.message : "Unable to save settings."
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card integration-modal">
        <div className="modal-header">
          <div>
            <h2>{definition.title}</h2>
            <p>{loading ? "Loading settings..." : definition.description}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close"><X size={16} /></button>
        </div>
        {isUnifiProtect || hasDependencyUpdates ? (
          <div className="integration-modal-tabs" role="tablist" aria-label={`${definition.title} settings sections`}>
            <button
              aria-selected={activeTab === "general"}
              className={activeTab === "general" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("general")}
              role="tab"
              type="button"
            >
              <Settings size={15} /> General
            </button>
            <button
              aria-selected={activeTab === "updates"}
              className={activeTab === "updates" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("updates")}
              role="tab"
              type="button"
            >
              <RefreshCcw size={15} /> Updates
              {dependencyPackages.some(dependencyIsActionableUpdate) ? <Badge tone="amber">{dependencyPackages.filter(dependencyIsActionableUpdate).length}</Badge> : null}
            </button>
            {isUnifiProtect ? (
            <button
              aria-selected={activeTab === "exposes"}
              className={activeTab === "exposes" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("exposes")}
              role="tab"
              type="button"
            >
              <Activity size={15} /> Exposes
            </button>
            ) : null}
          </div>
        ) : null}
        {activeTab === "updates" ? (
          dependencyPackages.length ? (
            <DependencyUpdatePanel
              packages={dependencyPackages}
              storage={dependencyStorage}
              onChanged={onProtectUpdateChanged ?? onSettingsChanged}
            />
          ) : isUnifiProtect ? (
            <UnifiProtectUpdatesPanel
              status={protectUpdateStatus ?? null}
              onChanged={onProtectUpdateChanged ?? onSettingsChanged}
            />
          ) : (
            <div className="empty-state">No enrolled dependencies are linked to this integration yet</div>
          )
        ) : isICloudCalendar ? (
          <ICloudCalendarModal
            error={icloudError ?? ""}
            loading={Boolean(icloudLoading)}
            payload={icloudPayload ?? { accounts: [], recent_sync_runs: [] }}
            onChanged={onICloudChanged ?? onSettingsChanged}
          />
        ) : isUnifiProtect && activeTab === "exposes" ? (
          <UnifiProtectExposesPanel
            cameras={protectCameras ?? []}
            error={protectError ?? ""}
            loading={Boolean(protectLoading)}
            onRefresh={onProtectRefresh ?? onSettingsChanged}
            status={protectStatus ?? null}
          />
        ) : (
          <form className="integration-settings-form" onSubmit={save}>
        {definition.oauth ? (
          <button className="secondary-button full" onClick={() => setFeedback({
            tone: "info",
            title: "OAuth is not active yet",
            detail: "Use an API key for this integration in the current build."
          })} type="button">
            <LogIn size={16} /> Login to {definition.title}
          </button>
        ) : null}
        {isHomeAssistant ? (
          <HomeAssistantSettingsFields
            discovery={haDiscovery}
            discoveryError={haDiscoveryError}
            discoveryLoading={haDiscoveryLoading}
            form={form}
            onChange={update}
            onReload={loadHomeAssistantDiscovery}
            schedules={schedules}
          />
        ) : isApprise ? (
          <AppriseSettingsFields
            loading={appriseLoading}
            urls={appriseUrls}
            onChanged={async (urls) => {
              setAppriseUrls(urls);
              await onSettingsChanged();
            }}
            onError={(error) => setFeedback({
              tone: "error",
              title: "Apprise URL update failed",
              detail: error
            })}
          />
        ) : isDiscord ? (
          <DiscordSettingsFields
            channels={discordChannels ?? []}
            error={discordError ?? ""}
            fields={definition.fields}
            form={form}
            identities={discordIdentities ?? []}
            isConfiguredSecret={(key) => secretSettingKeys.has(key) && Boolean(values[key])}
            loading={Boolean(discordLoading)}
            onChange={update}
            onIdentityChanged={onDiscordChanged ?? onSettingsChanged}
            people={people}
            status={discordStatus ?? null}
          />
        ) : (
          <div className="settings-form-grid">
            {definition.fields.map((field) => (
              <SettingField
                field={field}
                key={field.key}
                isConfiguredSecret={secretSettingKeys.has(field.key) && Boolean(values[field.key])}
                value={form[field.key] ?? ""}
                onChange={(value) => update(field.key, value)}
              />
            ))}
          </div>
        )}
        {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}
        <div className="modal-actions">
          {isApprise || isDiscord ? (
            <button className="secondary-button" onClick={sendTestNotification} disabled={sendingTest} type="button">
              <Send size={15} /> {sendingTest ? "Sending..." : "Send Test"}
            </button>
          ) : null}
          <button className="secondary-button" onClick={testConnection} disabled={testing} type="button">
            {testing ? "Testing..." : "Test Connection"}
          </button>
          {isApprise ? (
            <button className="primary-button" onClick={onClose} type="button">Done</button>
          ) : (
            <button className="primary-button" disabled={saving} type="submit">
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </div>
          </form>
        )}
      </div>
    </div>
  );
}

function ICloudCalendarModal({
  payload,
  loading,
  error,
  onChanged
}: {
  payload: ICloudCalendarPayload;
  loading: boolean;
  error: string;
  onChanged: () => Promise<void>;
}) {
  const [adding, setAdding] = React.useState(false);
  const [step, setStep] = React.useState<"credentials" | "verify">("credentials");
  const [appleId, setAppleId] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [code, setCode] = React.useState("");
  const [handshakeId, setHandshakeId] = React.useState("");
  const [handshakeAppleId, setHandshakeAppleId] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [syncing, setSyncing] = React.useState(false);
  const [removingId, setRemovingId] = React.useState<string | null>(null);
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const activeAccounts = payload.accounts.filter((account) => account.is_active);
  const latestRun = payload.recent_sync_runs[0] ?? null;
  const hasAttention = activeAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));

  const resetAddFlow = () => {
    setAdding(false);
    setStep("credentials");
    setAppleId("");
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
  };

  const startAuth = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await api.post<ICloudAuthStartResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/start", {
        apple_id: appleId.trim(),
        password
      });
      setPassword("");
      if (result.status === "requires_2fa" && result.handshake_id) {
        setHandshakeId(result.handshake_id);
        setHandshakeAppleId(result.apple_id || appleId.trim());
        setStep("verify");
        setFeedback({
          tone: "info",
          title: "Verification code required",
          detail: result.detail || "Enter the six-digit Apple verification code to finish connecting this account."
        });
      } else {
        resetAddFlow();
        await onChanged();
        setFeedback({
          tone: "success",
          title: "iCloud Calendar connected",
          detail: `${result.account?.display_name || appleId.trim()} is ready for calendar sync.`
        });
      }
    } catch (authError) {
      setFeedback({
        tone: "error",
        title: "Unable to connect iCloud Calendar",
        detail: authError instanceof Error ? authError.message : "Unable to connect iCloud Calendar."
      });
    } finally {
      setSubmitting(false);
    }
  };

  const verifyCode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!handshakeId) return;
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await api.post<ICloudAuthVerifyResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/verify", {
        handshake_id: handshakeId,
        code: code.trim()
      });
      resetAddFlow();
      await onChanged();
      setFeedback({
        tone: "success",
        title: "iCloud Calendar connected",
        detail: `${result.account.display_name} is ready for calendar sync.`
      });
    } catch (verifyError) {
      setFeedback({
        tone: "error",
        title: "Verification failed",
        detail: verifyError instanceof Error ? verifyError.message : "Unable to verify that code."
      });
    } finally {
      setSubmitting(false);
    }
  };

  const syncNow = async () => {
    setSyncing(true);
    setFeedback({
      tone: "progress",
      title: "Syncing calendars",
      detail: "Scanning connected accounts for Open Gate events.",
      activeStep: 1
    });
    try {
      const run = await api.post<ICloudCalendarSyncRun>("/api/v1/integrations/icloud-calendar/sync");
      await onChanged();
      setFeedback({
        tone: run.status === "ok" ? "success" : "info",
        title: run.status === "ok" ? "Calendar sync complete" : "Calendar sync complete with notes",
        detail: icloudSyncRunSummary(run)
      });
    } catch (syncError) {
      setFeedback({
        tone: "error",
        title: "Calendar sync failed",
        detail: syncError instanceof Error ? syncError.message : "Unable to sync iCloud Calendars."
      });
    } finally {
      setSyncing(false);
    }
  };

  const removeAccount = async (account: ICloudCalendarAccount) => {
    if (!window.confirm(`Remove iCloud Calendar account ${account.display_name}? Future unused calendar passes from this account will be cancelled.`)) return;
    setRemovingId(account.id);
    setFeedback(null);
    try {
      await api.delete<ICloudCalendarAccount>(`/api/v1/integrations/icloud-calendar/accounts/${account.id}`);
      await onChanged();
      setFeedback({
        tone: "success",
        title: "Account removed",
        detail: `${account.display_name} is no longer connected.`
      });
    } catch (removeError) {
      setFeedback({
        tone: "error",
        title: "Unable to remove account",
        detail: removeError instanceof Error ? removeError.message : "Unable to remove that iCloud Calendar account."
      });
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <div className="icloud-calendar-panel">
      <section className="icloud-overview">
        <div className="icloud-overview-icon">
          <CalendarDays size={20} />
        </div>
        <div className="icloud-overview-copy">
          <strong>Automated Visitor Passes</strong>
          <span>Events with Open Gate in their notes create or update Visitor Passes for the next 14 days.</span>
        </div>
        <Badge tone={error ? "red" : hasAttention ? "amber" : activeAccounts.length ? "green" : "gray"}>
          {error ? "Error" : hasAttention ? "Needs Attention" : activeAccounts.length ? `${activeAccounts.length} Connected` : "Not Configured"}
        </Badge>
      </section>

      <div className="icloud-actions">
        <button className="primary-button" onClick={() => setAdding((current) => !current)} disabled={submitting || syncing} type="button">
          <Plus size={15} /> {adding ? "Close Add Account" : "Add Account"}
        </button>
        <button className="secondary-button" onClick={syncNow} disabled={loading || syncing || !activeAccounts.length} type="button">
          {syncing ? <Loader2 className="spin" size={15} /> : <RefreshCcw size={15} />}
          {syncing ? "Syncing..." : "Sync Calendars Now"}
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}

      {adding ? (
        step === "credentials" ? (
          <form className="icloud-auth-panel" onSubmit={startAuth}>
            <div className="icloud-auth-heading">
              <Key size={17} />
              <div>
                <strong>Add iCloud account</strong>
                <span>Enter the Apple ID details once; only the trusted session is stored.</span>
              </div>
            </div>
            <div className="icloud-auth-grid">
              <label className="field">
                <span>Apple ID</span>
                <div className="field-control">
                  <UserRound size={15} />
                  <input
                    autoComplete="username"
                    autoFocus
                    inputMode="email"
                    onChange={(event) => setAppleId(event.target.value)}
                    placeholder="name@example.com"
                    type="email"
                    value={appleId}
                  />
                </div>
              </label>
              <label className="field">
                <span>Password</span>
                <div className="field-control">
                  <Lock size={15} />
                  <input
                    autoComplete="current-password"
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="App-specific or account password"
                    type="password"
                    value={password}
                  />
                </div>
              </label>
            </div>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={resetAddFlow} disabled={submitting} type="button">Cancel</button>
              <button className="primary-button" disabled={submitting || !appleId.trim() || !password} type="submit">
                {submitting ? "Connecting..." : "Connect"}
              </button>
            </div>
          </form>
        ) : (
          <form className="icloud-auth-panel" onSubmit={verifyCode}>
            <div className="icloud-auth-heading">
              <ShieldCheck size={17} />
              <div>
                <strong>Enter verification code</strong>
                <span>{handshakeAppleId || "Apple"} is waiting for the six-digit code.</span>
              </div>
            </div>
            <label className="field icloud-code-field">
              <span>Verification code</span>
              <div className="field-control">
                <ShieldCheck size={15} />
                <input
                  autoComplete="one-time-code"
                  autoFocus
                  inputMode="numeric"
                  maxLength={6}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  pattern="[0-9]{6}"
                  placeholder="123456"
                  type="text"
                  value={code}
                />
              </div>
            </label>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={resetAddFlow} disabled={submitting} type="button">Cancel</button>
              <button className="primary-button" disabled={submitting || code.length !== 6} type="submit">
                {submitting ? "Verifying..." : "Verify and Connect"}
              </button>
            </div>
          </form>
        )
      ) : null}

      <section className="icloud-section">
        <div className="icloud-section-heading">
          <strong>Connected Accounts</strong>
          <span>{loading ? "Refreshing accounts" : `${activeAccounts.length} active`}</span>
        </div>
        <div className="icloud-account-list">
          {activeAccounts.length ? (
            activeAccounts.map((account) => (
              <article className="icloud-account-card" key={account.id}>
                <div className="icloud-account-main">
                  <span className="icloud-account-icon"><CalendarDays size={16} /></span>
                  <div>
                    <strong>{account.display_name}</strong>
                    <span>{account.apple_id}</span>
                  </div>
                </div>
                <div className="icloud-account-status">
                  <Badge tone={icloudAccountStatusTone(account.status)}>{icloudAccountStatusLabel(account.status)}</Badge>
                  <span>{account.last_sync_at ? `Last sync ${formatDate(account.last_sync_at)}` : "Not synced yet"}</span>
                  {account.last_error ? <small>{account.last_error}</small> : null}
                </div>
                <button
                  aria-label={`Remove ${account.display_name}`}
                  className="icon-button danger"
                  disabled={removingId === account.id}
                  onClick={() => removeAccount(account)}
                  type="button"
                >
                  {removingId === account.id ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
                </button>
              </article>
            ))
          ) : (
            <div className="icloud-empty">No iCloud Calendar accounts connected</div>
          )}
        </div>
      </section>

      <section className="icloud-section">
        <div className="icloud-section-heading">
          <strong>Recent Sync</strong>
          <span>{latestRun ? formatOptionalDate(latestRun.started_at || latestRun.finished_at) : "No syncs yet"}</span>
        </div>
        {latestRun ? (
          <div className="icloud-sync-summary">
            <div>
              <Badge tone={latestRun.status === "ok" ? "green" : latestRun.status === "error" ? "red" : "amber"}>{titleCase(latestRun.status)}</Badge>
              <span>{icloudSyncRunSummary(latestRun)}</span>
            </div>
            {latestRun.error ? <small>{latestRun.error}</small> : null}
          </div>
        ) : (
          <div className="icloud-empty">Run a manual sync after connecting an account</div>
        )}
      </section>
    </div>
  );
}

function icloudAccountStatusLabel(status: string) {
  if (status === "requires_reauth") return "Reconnect";
  if (status === "connected") return "Connected";
  if (status === "error") return "Error";
  if (status === "removed") return "Removed";
  return titleCase(status || "unknown");
}

function icloudAccountStatusTone(status: string): BadgeTone {
  if (status === "connected") return "green";
  if (status === "requires_reauth") return "amber";
  if (status === "error") return "red";
  return "gray";
}

function icloudSyncRunSummary(run: ICloudCalendarSyncRun) {
  const changes = [
    `${run.events_matched} matched`,
    `${run.passes_created} created`,
    `${run.passes_updated} updated`,
    `${run.passes_cancelled} cancelled`,
    `${run.passes_skipped} skipped`
  ];
  return `${run.account_count} account${run.account_count === 1 ? "" : "s"} scanned, ${run.events_scanned} event${run.events_scanned === 1 ? "" : "s"} read, ${changes.join(", ")}.`;
}

function formatOptionalDate(value: string | null | undefined) {
  return value ? formatDate(value) : "Pending";
}

function IntegrationFeedbackPanel({ feedback }: { feedback: IntegrationFeedback }) {
  const steps = ["Prepare", "Connect", "Validate"];
  const Icon = feedback.tone === "success" ? CheckCircle2 : feedback.tone === "error" ? AlertTriangle : Activity;
  return (
    <div className={`integration-feedback ${feedback.tone}`}>
      <div className="feedback-icon">
        <Icon size={18} />
      </div>
      <div className="feedback-copy">
        <strong>{feedback.title}</strong>
        <span>{feedback.detail}</span>
        {feedback.tone === "progress" ? (
          <div className="feedback-steps" aria-label="Connection test progress">
            {steps.map((step, index) => (
              <span
                className={index <= (feedback.activeStep ?? 0) ? "active" : ""}
                key={step}
              >
                {index < (feedback.activeStep ?? 0) ? <Check size={11} /> : null}
                {step}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function AppriseSettingsFields({
  loading,
  urls,
  onChanged,
  onError
}: {
  loading: boolean;
  urls: AppriseUrlSummary[];
  onChanged: (urls: AppriseUrlSummary[]) => Promise<void>;
  onError: (error: string) => void;
}) {
  const [adding, setAdding] = React.useState(false);
  const [newUrl, setNewUrl] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const reload = async () => {
    const result = await api.get<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls");
    await onChanged(result.urls);
  };

  const addUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!newUrl.trim()) return;
    setSubmitting(true);
    try {
      const result = await api.post<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls", { url: newUrl.trim() });
      setNewUrl("");
      setAdding(false);
      await onChanged(result.urls);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to add Apprise URL.");
    } finally {
      setSubmitting(false);
    }
  };

  const removeUrl = async (url: AppriseUrlSummary) => {
    setSubmitting(true);
    try {
      await api.delete(`/api/v1/integrations/apprise/urls/${url.index}`);
      await reload();
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to remove Apprise URL.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="apprise-manager">
      <div className="apprise-manager-header">
        <div>
          <strong>Notification URLs</strong>
          <span>Add one destination per service. Secrets stay encrypted; only safe previews are shown here.</span>
        </div>
        <button className="primary-button" onClick={() => setAdding((current) => !current)} type="button">
          <Plus size={15} /> Add New Apprise URL
        </button>
      </div>

      {adding ? (
        <form className="apprise-add-row" onSubmit={addUrl}>
          <label className="field">
            <span>Apprise URL</span>
            <div className="field-control">
              <Bell size={16} />
              <input
                autoFocus
                value={newUrl}
                onChange={(event) => setNewUrl(event.target.value)}
                placeholder="pover://USER_KEY@APP_TOKEN"
              />
            </div>
            <small className="field-hint">For Pushover use `pover://USER_KEY@APP_TOKEN`. `pushover://USER_KEY/APP_TOKEN` is accepted too.</small>
          </label>
          <div className="apprise-add-actions">
            <button className="secondary-button" onClick={() => setAdding(false)} type="button">Cancel</button>
            <button className="primary-button" disabled={submitting || !newUrl.trim()} type="submit">
              {submitting ? "Adding..." : "Add URL"}
            </button>
          </div>
        </form>
      ) : null}

      <div className="apprise-url-table">
        <div className="apprise-url-head">
          <span>Type</span>
          <span>API & Key Preview</span>
          <span />
        </div>
        {loading ? (
          <div className="apprise-empty">Loading saved URLs</div>
        ) : urls.length ? (
          urls.map((url) => (
            <div className="apprise-url-row" key={`${url.scheme}-${url.index}`}>
              <div>
                <Badge tone={url.type === "Pushover" ? "blue" : "gray"}>{url.type}</Badge>
              </div>
              <div>
                <strong>{url.preview}</strong>
                <span>{url.scheme}</span>
              </div>
              <button className="icon-button danger" onClick={() => removeUrl(url)} disabled={submitting} type="button" aria-label={`Remove ${url.type} URL`}>
                <Trash2 size={15} />
              </button>
            </div>
          ))
        ) : (
          <div className="apprise-empty">No notification URLs configured</div>
        )}
      </div>
    </div>
  );
}

function DiscordSettingsFields({
  channels,
  error,
  fields,
  form,
  identities,
  isConfiguredSecret,
  loading,
  onChange,
  onIdentityChanged,
  people,
  status
}: {
  channels: DiscordChannel[];
  error: string;
  fields: SettingFieldDefinition[];
  form: Record<string, string>;
  identities: DiscordIdentity[];
  isConfiguredSecret: (key: string) => boolean;
  loading: boolean;
  onChange: (key: string, value: string) => void;
  onIdentityChanged: () => Promise<void>;
  people: Person[];
  status: DiscordStatus | null;
}) {
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [savingIdentityId, setSavingIdentityId] = React.useState<string | null>(null);
  const [identityError, setIdentityError] = React.useState("");

  React.useEffect(() => {
    api.get<UserAccount[]>("/api/v1/users").then(setUsers).catch(() => setUsers([]));
  }, []);

  const linkIdentity = async (identity: DiscordIdentity, field: "user_id" | "person_id", value: string) => {
    setSavingIdentityId(identity.id);
    setIdentityError("");
    try {
      await api.patch<DiscordIdentity>(`/api/v1/integrations/discord/identities/${identity.id}`, {
        user_id: field === "user_id" ? value || null : identity.user_id,
        person_id: field === "person_id" ? value || null : identity.person_id
      });
      await onIdentityChanged();
    } catch (error) {
      setIdentityError(error instanceof Error ? error.message : "Unable to update Discord identity.");
    } finally {
      setSavingIdentityId(null);
    }
  };

  return (
    <div className="discord-settings">
      <section className="discord-overview">
        <div className="discord-overview-main">
          <span className="discord-overview-icon"><MessageCircle size={18} /></span>
          <div>
            <strong>{status?.connected ? "Bot connected" : status?.configured ? "Bot configured" : "Bot not configured"}</strong>
            <span>{status?.connected ? `${status.guild_count} guilds, ${status.channel_count} channels` : status?.last_error || error || "Save a bot token and allowlists to start Alfred on Discord."}</span>
          </div>
        </div>
        <Badge tone={error ? "red" : status?.connected ? "green" : status?.configured ? "blue" : "gray"}>
          {error ? "Error" : status?.connected ? "Connected" : status?.configured ? "Configured" : "Not Configured"}
        </Badge>
      </section>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      <div className="settings-form-grid">
        {fields.map((field) => (
          <SettingField
            field={field}
            key={field.key}
            isConfiguredSecret={isConfiguredSecret(field.key)}
            value={form[field.key] ?? ""}
            onChange={(value) => onChange(field.key, value)}
          />
        ))}
      </div>

      <section className="discord-section">
        <div className="icloud-section-heading">
          <strong>Notification Channels</strong>
          <span>{loading ? "Refreshing" : `${channels.length} available`}</span>
        </div>
        <div className="discord-channel-list">
          {channels.length ? channels.map((channel) => (
            <button
              className="discord-channel-row"
              key={channel.id}
              onClick={() => onChange("discord_default_notification_channel_id", channel.id)}
              type="button"
            >
              <span><MessageCircle size={14} /> {channel.label || channel.name}</span>
              <Badge tone={form.discord_default_notification_channel_id === channel.id ? "green" : "gray"}>
                {form.discord_default_notification_channel_id === channel.id ? "Default" : channel.id}
              </Badge>
            </button>
          )) : (
            <div className="icloud-empty">No channels discovered yet. Save the bot token and allowlists first.</div>
          )}
        </div>
      </section>

      <section className="discord-section">
        <div className="icloud-section-heading">
          <strong>Discord Identities</strong>
          <span>{loading ? "Refreshing" : `${identities.length} seen`}</span>
        </div>
        {identityError ? <div className="auth-error inline-error">{identityError}</div> : null}
        <div className="discord-identity-list">
          {identities.length ? identities.map((identity) => (
            <article className="discord-identity-row" key={identity.id}>
              <div>
                <strong>{identity.provider_display_name}</strong>
                <span>{identity.provider_user_id}{identity.last_seen_at ? ` · ${formatDate(identity.last_seen_at)}` : ""}</span>
              </div>
              <select
                disabled={savingIdentityId === identity.id}
                onChange={(event) => linkIdentity(identity, "user_id", event.target.value)}
                value={identity.user_id ?? ""}
              >
                <option value="">No IACS user</option>
                {users.map((user) => <option key={user.id} value={user.id}>{user.full_name || user.username}</option>)}
              </select>
              <select
                disabled={savingIdentityId === identity.id}
                onChange={(event) => linkIdentity(identity, "person_id", event.target.value)}
                value={identity.person_id ?? ""}
              >
                <option value="">No person</option>
                {people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}
              </select>
            </article>
          )) : (
            <div className="icloud-empty">No Discord users have messaged Alfred yet</div>
          )}
        </div>
      </section>
    </div>
  );
}

function HomeAssistantSettingsFields({
  discovery,
  discoveryError,
  discoveryLoading,
  form,
  onChange,
  onReload,
  schedules
}: {
  discovery: HomeAssistantDiscovery | null;
  discoveryError: string;
  discoveryLoading: boolean;
  form: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onReload: () => Promise<void>;
  schedules: Schedule[];
}) {
  type HomeAssistantTab = "setup" | "gates" | "garages";
  const [activeTab, setActiveTab] = React.useState<HomeAssistantTab>("setup");
  const gateEntities = parseManagedCovers(form.home_assistant_gate_entities);
  const garageDoorEntities = parseManagedCovers(form.home_assistant_garage_door_entities);
  const tabs: Array<{ key: HomeAssistantTab; label: string; meta: string; icon: React.ElementType }> = [
    { key: "setup", label: "Setup", meta: discovery ? "Discovery ready" : "Credentials", icon: Home },
    { key: "gates", label: "Gates", meta: `${gateEntities.length} configured`, icon: DoorOpen },
    { key: "garages", label: "Garage doors", meta: `${garageDoorEntities.length} configured`, icon: Warehouse }
  ];

  const updateGateEntities = (entities: HomeAssistantManagedCover[]) => {
    onChange("home_assistant_gate_entities", JSON.stringify(normalizeManagedCoversForSave(entities), null, 2));
  };

  const updateGarageDoorEntities = (entities: HomeAssistantManagedCover[]) => {
    onChange("home_assistant_garage_door_entities", JSON.stringify(normalizeManagedCoversForSave(entities), null, 2));
  };

  const autoDetectGateEntities = () => {
    const suggestions = discovery?.gate_suggestions?.length
      ? discovery.gate_suggestions
      : (discovery?.cover_entities ?? []).filter(isGateCandidate).map(managedCoverFromEntity);
    updateGateEntities(mergeManagedCovers(gateEntities, suggestions));
  };

  const autoDetectGarageDoors = () => {
    const suggestions = discovery?.garage_door_suggestions?.length
      ? discovery.garage_door_suggestions
      : (discovery?.cover_entities ?? []).filter(isGarageDoorCandidate).map(managedCoverFromEntity);
    updateGarageDoorEntities(mergeManagedCovers(garageDoorEntities, suggestions));
  };

  return (
    <div className="ha-config-shell">
      <div className="ha-tabs" role="tablist" aria-label="Home Assistant settings">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              aria-selected={activeTab === tab.key}
              className={activeTab === tab.key ? "ha-tab active" : "ha-tab"}
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              role="tab"
              type="button"
            >
              <Icon size={16} />
              <span>
                <strong>{tab.label}</strong>
                <small>{tab.meta}</small>
              </span>
            </button>
          );
        })}
      </div>
      {discoveryError ? <div className="auth-error inline-error">{discoveryError}</div> : null}

      <div className="ha-tab-panel" role="tabpanel">
        {activeTab === "setup" ? (
          <section className="ha-setup-panel">
            <div className="ha-section-heading">
              <div>
                <strong>Connection</strong>
                <span>{discovery ? "Entities loaded from Home Assistant" : "Save credentials, then refresh discovery"}</span>
              </div>
              <button className="secondary-button ha-refresh-button" onClick={onReload} disabled={discoveryLoading} type="button">
                <RefreshCcw size={15} /> {discoveryLoading ? "Refreshing..." : "Refresh"}
              </button>
            </div>

            <div className="ha-setup-grid">
              <SettingField
                field={{ key: "home_assistant_url", label: "URL" }}
                value={form.home_assistant_url ?? ""}
                onChange={(value) => onChange("home_assistant_url", value)}
              />
              <SettingField
                field={{ key: "home_assistant_token", label: "Long-lived token", type: "password" }}
                value={form.home_assistant_token ?? ""}
                onChange={(value) => onChange("home_assistant_token", value)}
              />
              <SettingField
                field={{ key: "home_assistant_gate_open_service", label: "Cover open service" }}
                value={form.home_assistant_gate_open_service ?? ""}
                onChange={(value) => onChange("home_assistant_gate_open_service", value)}
              />
              <SettingField
                field={{ key: "home_assistant_tts_service", label: "TTS service" }}
                value={form.home_assistant_tts_service ?? ""}
                onChange={(value) => onChange("home_assistant_tts_service", value)}
              />
              <div className="ha-grid-wide">
                <EntitySelectField
                  label="Default media player"
                  value={form.home_assistant_default_media_player ?? ""}
                  entities={discovery?.media_player_entities ?? []}
                  domainLabel="media_player"
                  onChange={(value) => onChange("home_assistant_default_media_player", value)}
                />
              </div>
            </div>
          </section>
        ) : null}

        {activeTab === "gates" ? (
        <HomeAssistantCoverTable
          addLabel="Add Gate"
          autoDetectLabel="Auto Detect"
          emptyLabel="No gate entities configured"
          entities={gateEntities}
          icon={DoorOpen}
          availableEntities={discovery?.cover_entities ?? []}
          description="Gates opened when access is granted."
          onAutoDetect={autoDetectGateEntities}
          onChange={updateGateEntities}
          schedules={schedules}
          title="Gate entities"
        />
        ) : null}

        {activeTab === "garages" ? (
        <HomeAssistantCoverTable
          addLabel="Add Door"
          autoDetectLabel="Auto Detect"
          emptyLabel="No garage doors configured"
          entities={garageDoorEntities}
          icon={Warehouse}
          availableEntities={discovery?.cover_entities ?? []}
          description="Garage doors available in each person profile."
          onAutoDetect={autoDetectGarageDoors}
          onChange={updateGarageDoorEntities}
          schedules={schedules}
          title="Garage doors"
        />
        ) : null}
      </div>
    </div>
  );
}

function HomeAssistantCoverTable({
  addLabel,
  autoDetectLabel,
  availableEntities,
  emptyLabel,
  entities,
  icon: Icon,
  description,
  onAutoDetect,
  onChange,
  schedules,
  title
}: {
  addLabel: string;
  autoDetectLabel: string;
  availableEntities: HomeAssistantEntity[];
  emptyLabel: string;
  entities: HomeAssistantManagedCover[];
  icon: React.ElementType;
  description: string;
  onAutoDetect: () => void;
  onChange: (entities: HomeAssistantManagedCover[]) => void;
  schedules: Schedule[];
  title: string;
}) {
  const [selectedEntityId, setSelectedEntityId] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const selectedIds = React.useMemo(() => new Set(entities.map((entity) => entity.entity_id)), [entities]);
  const addableEntities = availableEntities.filter((entity) => entity.entity_id.startsWith("cover.") && !selectedIds.has(entity.entity_id));

  const addSelectedEntity = () => {
    if (!selectedEntityId) return;
    const entity = availableEntities.find((item) => item.entity_id === selectedEntityId);
    if (!entity) return;
    onChange([...entities, managedCoverFromEntity(entity)]);
    setSelectedEntityId("");
  };

  const updateEntity = (entityId: string, updates: Partial<HomeAssistantManagedCover>) => {
    onChange(entities.map((entity) => entity.entity_id === entityId ? { ...entity, ...updates } : entity));
  };

  const removeEntity = (entityId: string) => {
    onChange(entities.filter((entity) => entity.entity_id !== entityId));
  };

  return (
    <section className="ha-device-panel">
      <div className="ha-device-title">
        <span className="ha-device-icon"><Icon size={17} /></span>
        <div>
          <strong>{title}</strong>
          <span>{entities.length} configured - {description}</span>
        </div>
        <button className="secondary-button ha-auto-button" onClick={onAutoDetect} type="button">
          <RefreshCcw size={15} /> {autoDetectLabel}
        </button>
      </div>

      <div className="ha-entity-composer">
        <select value={selectedEntityId} onChange={(event) => setSelectedEntityId(event.target.value)}>
          <option value="">Select discovered cover entity</option>
          {addableEntities.map((entity) => (
            <option key={entity.entity_id} value={entity.entity_id}>
              {entity.name ? `${entity.name} - ${entity.entity_id}` : entity.entity_id}
            </option>
          ))}
        </select>
        <button className="primary-button ha-add-button" onClick={addSelectedEntity} disabled={!selectedEntityId} type="button">
          <Plus size={15} /> {addLabel}
        </button>
      </div>

      <div className="ha-cover-list">
        {entities.length ? entities.map((entity) => (
          <div className="ha-cover-row" key={entity.entity_id}>
            <div className="ha-cover-identity">
              <input
                value={entity.name}
                onChange={(event) => updateEntity(entity.entity_id, { name: event.target.value })}
                aria-label={`${entity.entity_id} name`}
              />
              <code>{entity.entity_id}</code>
            </div>
            <select
              aria-label={`${entity.name || entity.entity_id} schedule`}
              className="ha-cover-schedule-select"
              value={entity.schedule_id ?? ""}
              onChange={(event) => updateEntity(entity.entity_id, { schedule_id: event.target.value || null })}
            >
              <option value="">{defaultPolicyOptionLabel}</option>
              {schedules.map((schedule) => (
                <option key={schedule.id} value={schedule.id}>{schedule.name}</option>
              ))}
            </select>
            <label className={entity.enabled === false ? "entity-toggle" : "entity-toggle active"}>
              <input
                checked={entity.enabled !== false}
                onChange={(event) => updateEntity(entity.entity_id, { enabled: event.target.checked })}
                type="checkbox"
              />
              <span>{entity.enabled === false ? "Disabled" : "Enabled"}</span>
            </label>
            <button className="icon-button danger" onClick={() => removeEntity(entity.entity_id)} type="button" aria-label={`Remove ${entity.name || entity.entity_id}`}>
              <Trash2 size={15} />
            </button>
          </div>
        )) : (
          <div className="ha-entity-empty">
            <Icon size={18} />
            <span>{emptyLabel}</span>
          </div>
        )}
      </div>
    </section>
  );
}

function EntitySelectField({
  label,
  value,
  entities,
  domainLabel,
  onChange
}: {
  label: string;
  value: string;
  entities: HomeAssistantEntity[];
  domainLabel: string;
  onChange: (value: string) => void;
}) {
  const hasCurrentValue = value && !entities.some((entity) => entity.entity_id === value);
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select {domainLabel} entity</option>
        {hasCurrentValue ? <option value={value}>{value}</option> : null}
        {entities.map((entity) => (
          <option key={entity.entity_id} value={entity.entity_id}>
            {entity.name ? `${entity.name} - ${entity.entity_id}` : entity.entity_id}
          </option>
        ))}
      </select>
    </label>
  );
}

function MobileAppNotifySelectField({
  label,
  value,
  services,
  onChange
}: {
  label: string;
  value: string;
  services: HomeAssistantMobileAppService[];
  onChange: (value: string) => void;
}) {
  const hasCurrentValue = value && !services.some((service) => service.service_id === value);
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select mobile app service</option>
        {hasCurrentValue ? <option value={value}>{value}</option> : null}
        {services.map((service) => (
          <option key={service.service_id} value={service.service_id}>
            {service.name ? `${service.name} - ${service.service_id}` : service.service_id}
          </option>
        ))}
      </select>
    </label>
  );
}

type LogsTabKey = "lpr" | "gate" | "maintenance" | "ai" | "crud" | "api" | "integrations" | "updates" | "live";

const logsTabs: Array<{ key: LogsTabKey; label: string; icon: React.ElementType; description: string }> = [
  { key: "lpr", label: "LPR Telemetry", icon: Car, description: "Plate reads, access decisions, and gate timing." },
  { key: "gate", label: "Gate Events", icon: DoorOpen, description: "Malfunctions, recovery attempts, notifications, and resolution." },
  { key: "maintenance", label: "Maintenance Mode", icon: Construction, description: "Kill-switch changes, actor, duration, and HA sync." },
  { key: "ai", label: "AI Audit", icon: Bot, description: "Alfred tools, provider use, and outcomes." },
  { key: "crud", label: "System CRUD", icon: Database, description: "Directory, schedules, notification rules, users, and settings." },
  { key: "api", label: "Webhooks & API", icon: GitBranch, description: "Inbound requests and webhook execution times." },
  { key: "integrations", label: "Integrations", icon: PlugZap, description: "Home Assistant, notifications, DVLA, and provider actions." },
  { key: "updates", label: "Updates & Rollbacks", icon: RefreshCcw, description: "Enrollment, analysis, backups, update jobs, and restores." },
  { key: "live", label: "Live Stream", icon: Terminal, description: "Current websocket event stream." }
];

const traceCategories: Partial<Record<LogsTabKey, string>> = {
  lpr: "lpr_telemetry",
  gate: "gate_malfunction",
  api: "webhooks_api",
  updates: "dependency_updates"
};

const auditCategories: Partial<Record<LogsTabKey, string>> = {
  ai: "alfred_ai",
  crud: "entity_management",
  integrations: "integrations"
};

const auditActionPrefixes: Partial<Record<LogsTabKey, string>> = {
  maintenance: "maintenance_mode."
};

function auditLogBelongsToTab(log: AuditLog, tab: LogsTabKey) {
  const category = auditCategories[tab];
  const actionPrefix = auditActionPrefixes[tab];
  return Boolean((category && log.category === category) || (actionPrefix && log.action.startsWith(actionPrefix)));
}

function auditLogMatchesFilters(log: AuditLog, query: string, level: string) {
  if (level !== "all" && log.level !== level) return false;
  const trimmedQuery = query.trim().toLowerCase();
  if (!trimmedQuery) return true;
  return [
    log.action,
    log.actor,
    log.target_entity,
    log.target_id,
    log.target_label,
    JSON.stringify(log.diff),
    JSON.stringify(log.metadata)
  ].some((value) => String(value || "").toLowerCase().includes(trimmedQuery));
}

function realtimeLogKey(log: RealtimeMessage) {
  return [
    log.type,
    log.created_at || "",
    stringPayload(log.payload.id),
    stringPayload(log.payload.action),
    stringPayload(log.payload.category)
  ].join("|");
}

function LogsView({ logs, onClearRealtime }: { logs: RealtimeMessage[]; onClearRealtime: () => void }) {
  const [tab, setTab] = React.useState<LogsTabKey>("lpr");
  const [query, setQuery] = React.useState("");
  const [level, setLevel] = React.useState("all");
  const [status, setStatus] = React.useState("all");
  const [traces, setTraces] = React.useState<TelemetryTrace[]>([]);
  const [traceCursor, setTraceCursor] = React.useState<string | null>(null);
  const [traceDetails, setTraceDetails] = React.useState<Record<string, TelemetryTraceDetail>>({});
  const [expandedTraceId, setExpandedTraceId] = React.useState<string | null>(null);
  const [auditLogs, setAuditLogs] = React.useState<AuditLog[]>([]);
  const [auditCursor, setAuditCursor] = React.useState<string | null>(null);
  const [expandedAuditId, setExpandedAuditId] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [clearing, setClearing] = React.useState(false);
  const [liveFilter, setLiveFilter] = React.useState("all");
  const [countdownNow, setCountdownNow] = React.useState(() => Date.now());
  const reloadTimerRef = React.useRef<number | null>(null);
  const processedRealtimeKeysRef = React.useRef<Set<string>>(new Set());

  const isTraceTab = Boolean(traceCategories[tab]);
  const isAuditTab = Boolean(auditCategories[tab] || auditActionPrefixes[tab]);
  const visibleLiveLogs = logs.filter((log) => liveFilter === "all" || log.type.includes(liveFilter));
  const hasActiveGateMalfunction = tab === "gate" && traces.some((trace) => trace.status === "active");

  const clearScheduledTelemetryReload = React.useCallback(() => {
    if (reloadTimerRef.current === null) return;
    window.clearTimeout(reloadTimerRef.current);
    reloadTimerRef.current = null;
  }, []);

  async function loadTelemetry(mode: "reset" | "append" = "reset") {
    if (tab === "live") return;
    setError("");
    mode === "reset" ? setLoading(true) : setLoadingMore(true);
    try {
      if (isTraceTab) {
        if (tab === "gate") {
          const params = new URLSearchParams({ limit: "40" });
          if (status !== "all") params.set("status", status);
          if (mode === "append" && traceCursor) params.set("cursor", traceCursor);
          const response = await api.get<PaginatedResponse<GateMalfunctionRecord>>(`/api/v1/gate-malfunctions/history?${params}`);
          const items = response.items
            .map(gateMalfunctionRecordToTrace)
            .filter((trace) => gateTraceMatchesFilters(trace, query, level, status));
          setTraces((current) => mode === "append" ? [...current, ...items] : items);
          setTraceCursor(response.next_cursor);
        } else {
          const category = traceCategories[tab];
          const params = new URLSearchParams({ limit: "40" });
          if (category) params.set("category", category);
          if (query.trim()) params.set("q", query.trim());
          if (level !== "all") params.set("level", level);
          if (status !== "all") params.set("status", status);
          if (mode === "append" && traceCursor) params.set("cursor", traceCursor);
          const response = await api.get<PaginatedResponse<TelemetryTrace>>(`/api/v1/telemetry/traces?${params}`);
          setTraces((current) => mode === "append" ? [...current, ...response.items] : response.items);
          setTraceCursor(response.next_cursor);
        }
      } else if (isAuditTab) {
        const category = auditCategories[tab];
        const actionPrefix = auditActionPrefixes[tab];
        const params = new URLSearchParams({ limit: "40" });
        if (category) params.set("category", category);
        if (actionPrefix) params.set("action_prefix", actionPrefix);
        if (query.trim()) params.set("q", query.trim());
        if (level !== "all") params.set("level", level);
        if (mode === "append" && auditCursor) params.set("cursor", auditCursor);
        const response = await api.get<PaginatedResponse<AuditLog>>(`/api/v1/telemetry/audit?${params}`);
        setAuditLogs((current) => mode === "append" ? [...current, ...response.items] : response.items);
        setAuditCursor(response.next_cursor);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load telemetry");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }

  async function clearLogs() {
    if (!window.confirm("Clear all telemetry, audit, and live log records?")) return;
    setClearing(true);
    setError("");
    setNotice("");
    try {
      await api.delete("/api/v1/telemetry/purge");
      setTraces([]);
      setTraceCursor(null);
      setTraceDetails({});
      setExpandedTraceId(null);
      setAuditLogs([]);
      setAuditCursor(null);
      setExpandedAuditId(null);
      processedRealtimeKeysRef.current.clear();
      onClearRealtime();
      setNotice("Logs cleared");
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : "Unable to clear logs");
    } finally {
      setClearing(false);
    }
  }

  React.useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(""), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  React.useEffect(() => {
    setExpandedTraceId(null);
    setExpandedAuditId(null);
    clearScheduledTelemetryReload();
    loadTelemetry("reset").catch(() => undefined);
  }, [tab, query, level, status, clearScheduledTelemetryReload]);

  React.useEffect(() => {
    const gateStatuses = new Set(["all", "active", "resolved", "fubar"]);
    const traceStatuses = new Set(["all", "ok", "error"]);
    if (tab === "gate" && !gateStatuses.has(status)) setStatus("all");
    if (tab !== "gate" && isTraceTab && !traceStatuses.has(status)) setStatus("all");
  }, [tab, status, isTraceTab]);

  React.useEffect(() => () => clearScheduledTelemetryReload(), [clearScheduledTelemetryReload]);

  React.useEffect(() => {
    if (!hasActiveGateMalfunction) return undefined;
    const timer = window.setInterval(() => setCountdownNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveGateMalfunction]);

  React.useEffect(() => {
    if (!logs.length || tab === "live") return;
    let shouldReload = false;
    const actionPrefix = auditActionPrefixes[tab];
    const nextAuditLogs: AuditLog[] = [];
    const recentLogs = logs.slice(0, 20).reverse();
    for (const realtimeLog of recentLogs) {
      const realtimeKey = realtimeLogKey(realtimeLog);
      if (processedRealtimeKeysRef.current.has(realtimeKey)) continue;
      processedRealtimeKeysRef.current.add(realtimeKey);

      if (realtimeLog.type === "telemetry.trace.created" && realtimeLog.payload.category === traceCategories[tab]) {
        shouldReload = true;
      }
      if (realtimeLog.type === "audit.log.created" && realtimeLog.payload.category === auditCategories[tab]) {
        shouldReload = true;
      }
      if (
        realtimeLog.type === "audit.log.created" &&
        actionPrefix &&
        stringPayload(realtimeLog.payload.action).startsWith(actionPrefix)
      ) {
        shouldReload = true;
      }
      if (tab === "maintenance" && realtimeLog.type === "maintenance_mode.changed") {
        shouldReload = true;
      }
      if (tab === "gate" && realtimeLog.type.startsWith("gate_malfunction.")) {
        shouldReload = true;
      }

      if (realtimeLog.type === "audit.log.created") {
        const liveAuditLog = auditLogFromRealtimePayload(realtimeLog.payload);
        if (
          liveAuditLog &&
          auditLogBelongsToTab(liveAuditLog, tab) &&
          auditLogMatchesFilters(liveAuditLog, query, level)
        ) {
          nextAuditLogs.push(liveAuditLog);
        }
      }
    }
    if (processedRealtimeKeysRef.current.size > 200) {
      const staleKeys = Array.from(processedRealtimeKeysRef.current).slice(0, processedRealtimeKeysRef.current.size - 200);
      staleKeys.forEach((key) => processedRealtimeKeysRef.current.delete(key));
    }
    if (!shouldReload) return;
    if (nextAuditLogs.length) {
      setAuditLogs((current) => {
        let merged = current;
        nextAuditLogs.forEach((liveAuditLog) => {
          merged = [liveAuditLog, ...merged.filter((item) => item.id !== liveAuditLog.id)];
        });
        return merged.slice(0, 40);
      });
    }
    clearScheduledTelemetryReload();
    reloadTimerRef.current = window.setTimeout(() => {
      reloadTimerRef.current = null;
      loadTelemetry("reset").catch(() => undefined);
    }, 900);
  }, [logs, tab, query, level, status, clearScheduledTelemetryReload]);

  const toggleTrace = async (trace: TelemetryTrace) => {
    const traceId = trace.trace_id;
    if (expandedTraceId === traceId) {
      setExpandedTraceId(null);
      return;
    }
    setExpandedTraceId(traceId);
    if (!traceDetails[traceId]) {
      const malfunctionId = trace.category === "gate_malfunction"
        ? stringPayload(trace.context.malfunction_id || trace.context.id)
        : "";
      const detail = malfunctionId
        ? gateMalfunctionRecordToTraceDetail(
            await api.get<GateMalfunctionRecord>(`/api/v1/gate-malfunctions/${malfunctionId}/trace`)
          )
        : await api.get<TelemetryTraceDetail>(`/api/v1/telemetry/traces/${traceId}`);
      setTraceDetails((current) => ({ ...current, [traceId]: detail }));
    }
  };

  const activeTab = logsTabs.find((item) => item.key === tab) ?? logsTabs[0];
  const count = tab === "live" ? visibleLiveLogs.length : isTraceTab ? traces.length : auditLogs.length;

  return (
    <section className="view-stack telemetry-workspace">
      <Toolbar title="Telemetry & Audit" count={count} icon={activeTab.icon}>
        <button className="danger-button" onClick={clearLogs} type="button" disabled={clearing}>
          <Trash2 size={15} /> {clearing ? "Clearing..." : "Clear Logs"}
        </button>
        <button className="secondary-button" onClick={() => loadTelemetry("reset")} type="button">
          <RefreshCcw size={15} /> Refresh
        </button>
      </Toolbar>

      <div className="telemetry-layout">
        <aside className="telemetry-tabs" aria-label="Log categories">
          {logsTabs.map((item) => {
            const Icon = item.icon;
            return (
              <button className={tab === item.key ? "telemetry-tab active" : "telemetry-tab"} key={item.key} onClick={() => setTab(item.key)} type="button">
                <Icon size={17} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
              </button>
            );
          })}
        </aside>

        <div className="telemetry-panel">
          <div className="telemetry-filterbar">
            <label className="search telemetry-search">
              <Search size={16} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search telemetry, actors, plates, payloads..." disabled={tab === "live"} />
            </label>
            {tab === "live" ? (
              <select value={liveFilter} onChange={(event) => setLiveFilter(event.target.value)}>
                <option value="all">All live events</option>
                <option value="event">Access events</option>
                <option value="chat">Chat</option>
                <option value="gate">Gate</option>
                <option value="maintenance">Maintenance</option>
                <option value="telemetry">Telemetry</option>
              </select>
            ) : (
              <>
                <select value={level} onChange={(event) => setLevel(event.target.value)}>
                  <option value="all">All levels</option>
                  <option value="info">INFO</option>
                  <option value="warning">WARN</option>
                  <option value="error">ERROR</option>
                  <option value="purple">AI ACTION</option>
                </select>
	                {isTraceTab ? (
	                  <select value={status} onChange={(event) => setStatus(event.target.value)}>
	                    <option value="all">All statuses</option>
	                    {tab === "gate" ? (
	                      <>
	                        <option value="active">Active</option>
	                        <option value="resolved">Resolved</option>
	                        <option value="fubar">FUBAR</option>
	                      </>
	                    ) : (
	                      <>
	                        <option value="ok">OK</option>
	                        <option value="error">Error</option>
	                      </>
	                    )}
	                  </select>
	                ) : null}
              </>
            )}
          </div>

          {error ? <div className="error-banner">{error}</div> : null}
          {notice ? <div className="success-banner">{notice}</div> : null}
          {loading ? <div className="loading-panel">Loading telemetry</div> : null}

          {!loading && tab === "live" ? <LiveLogStream logs={visibleLiveLogs} /> : null}
          {!loading && isTraceTab ? (
            <TraceList
              details={traceDetails}
              expandedTraceId={expandedTraceId}
              now={countdownNow}
              onToggle={toggleTrace}
              traces={traces}
            />
          ) : null}
          {!loading && isAuditTab ? (
            <AuditLogList
              expandedAuditId={expandedAuditId}
              logs={auditLogs}
              onToggle={(id) => setExpandedAuditId((current) => current === id ? null : id)}
            />
          ) : null}

          {!loading && tab !== "live" && ((isTraceTab && !traces.length) || (isAuditTab && !auditLogs.length)) ? (
            <EmptyState icon={Terminal} label="No telemetry records match these filters." />
          ) : null}

          {tab !== "live" && ((isTraceTab && traceCursor) || (isAuditTab && auditCursor)) ? (
            <div className="telemetry-load-more">
              <button className="secondary-button" disabled={loadingMore} onClick={() => loadTelemetry("append")} type="button">
                {loadingMore ? "Loading..." : "Load More"}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function TraceList({
  traces,
  details,
  expandedTraceId,
  now,
  onToggle
}: {
  traces: TelemetryTrace[];
  details: Record<string, TelemetryTraceDetail>;
  expandedTraceId: string | null;
  now: number;
  onToggle: (trace: TelemetryTrace) => void;
}) {
  const parentRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: traces.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 126,
    overscan: 6
  });
  return (
    <div className="telemetry-virtual-list" ref={parentRef}>
      <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const trace = traces[virtualRow.index];
          const expanded = expandedTraceId === trace.trace_id;
          return (
            <div
              className="telemetry-virtual-row"
              data-index={virtualRow.index}
              key={trace.trace_id}
              ref={rowVirtualizer.measureElement}
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              <TraceRow detail={details[trace.trace_id]} expanded={expanded} now={now} onToggle={() => onToggle(trace)} trace={trace} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TraceRow({
  trace,
  detail,
  expanded,
  now,
  onToggle
}: {
  trace: TelemetryTrace;
  detail?: TelemetryTraceDetail;
  expanded: boolean;
  now: number;
  onToggle: () => void;
}) {
  const display = traceDisplay(trace);
  const Icon = display.icon;
  const countdown = gateMalfunctionCountdown(trace, now);
  return (
    <article className={expanded ? "telemetry-card expanded" : "telemetry-card"}>
      <button className="telemetry-row-button" onClick={onToggle} type="button">
        <span className={`telemetry-row-icon ${display.tone}`}>
          <Icon size={18} />
        </span>
        <span className="telemetry-row-main">
          <strong>{display.title}</strong>
          <small>{trace.summary || trace.name}</small>
        </span>
        <span className="telemetry-row-meta">
          <Badge tone={levelTone(trace.level)}>{levelLabel(trace.level)}</Badge>
          {countdown ? <Badge tone={countdown.overdue ? "red" : "amber"}>{countdown.label}</Badge> : null}
          <code>{formatDuration(trace.duration_ms)}</code>
          <time>{formatDate(trace.started_at)}</time>
        </span>
        {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
      </button>
      {expanded ? (
        detail ? <TraceWaterfall trace={detail} /> : <div className="telemetry-detail-loading">Loading trace spans...</div>
      ) : null}
    </article>
  );
}

function TraceWaterfall({ trace }: { trace: TelemetryTraceDetail }) {
  if (trace.category === "gate_malfunction") {
    return <GateMalfunctionTimeline trace={trace} />;
  }
  const traceStart = Date.parse(trace.started_at);
  const totalMs = Math.max(trace.duration_ms || 0, ...trace.spans.map((span) => {
    const end = span.ended_at ? Date.parse(span.ended_at) : Date.parse(span.started_at);
    return Math.max(0, end - traceStart);
  }), 1);
  return (
    <div className="trace-waterfall">
      <div className="trace-summary-grid">
        <TelemetryFact label="Trace ID" value={trace.trace_id} mono />
        <TelemetryFact label="Source" value={trace.source || "unknown"} />
        <TelemetryFact label="Plate" value={trace.registration_number || "n/a"} mono />
        <TelemetryFact label="Total Duration" value={formatDuration(totalMs)} mono />
      </div>
      <div className="waterfall-steps">
        {trace.spans.map((span, index) => {
          const started = Date.parse(span.started_at);
          const offset = Math.max(0, ((started - traceStart) / totalMs) * 100);
          const width = Math.max(1.5, ((span.duration_ms || 0) / totalMs) * 100);
          const artifact = artifactFromSpan(span);
          return (
            <div className="waterfall-step" key={span.span_id}>
              <div className="waterfall-step-marker">
                <span>{index + 1}</span>
              </div>
              <div className="waterfall-step-body">
                <div className="waterfall-step-header">
                  <strong>{span.name}</strong>
                  <code>{formatDuration(span.duration_ms)}</code>
                </div>
                <div className="waterfall-bar-track">
                  <span className={span.status === "error" ? "waterfall-bar error" : "waterfall-bar"} style={{ left: `${offset}%`, width: `${Math.min(width, 100 - offset)}%` }} />
                </div>
                {artifact ? (
                  <div className="trace-artifact">
                    <img alt="Camera snapshot used for direction analysis" src={String(artifact.url)} />
                    <span>
                      <strong>Vision Snapshot</strong>
                      <small>{String(artifact.content_type || "image")} · {formatFileSize(numberPayload(artifact.size_bytes))}</small>
                    </span>
                  </div>
                ) : null}
                <TraceSpanPayload span={span} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function GateMalfunctionTimeline({ trace }: { trace: TelemetryTraceDetail }) {
  const context = trace.context;
  const status = stringPayload(context.status || trace.status);
  const nextAttempt = stringPayload(context.next_attempt_scheduled_at);
  const downtimeSeconds = numberPayload(context.total_downtime_seconds);
  const attempts = stringPayload(context.fix_attempts_count || "0");
  const gateName = stringPayload(context.gate_name || trace.source || "Primary gate");
  const sortedSpans = [...trace.spans].sort((left, right) => Date.parse(left.started_at) - Date.parse(right.started_at));
  return (
    <div className="gate-trace">
      <div className="trace-summary-grid">
        <TelemetryFact label="Gate" value={gateName} />
        <TelemetryFact label="Status" value={titleCase(status || "unknown")} />
        <TelemetryFact label="Attempts" value={attempts} mono />
        <TelemetryFact label={status === "active" ? "Open Duration" : "Total Downtime"} value={formatSecondsDuration(downtimeSeconds)} mono />
      </div>
      {status === "active" && nextAttempt ? (
        <div className="gate-trace-countdown">
          <Clock3 size={16} />
          <span>
            <strong>{gateMalfunctionCountdownLabel(nextAttempt, Date.now())}</strong>
            <small>Next automated recovery attempt</small>
          </span>
        </div>
      ) : null}
      <div className="gate-timeline">
        {sortedSpans.map((span, index) => {
          const details = span.output_payload || {};
          const kind = stringPayload(span.attributes.kind || "");
          return (
            <div className={`gate-timeline-step ${span.status === "error" ? "error" : ""}`} key={span.span_id}>
              <div className="gate-timeline-marker">
                <span>{index + 1}</span>
              </div>
              <div className="gate-timeline-body">
                <div className="gate-timeline-header">
                  <strong>{span.name}</strong>
                  <time>{formatDate(span.started_at)}</time>
                </div>
                <p>{gateTimelineSummary(kind, details)}</p>
                {Object.keys(details).length ? <JsonBlock value={details} /> : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TraceSpanPayload({ span }: { span: TelemetrySpan }) {
  const payload = {
    attributes: span.attributes,
    input: span.input_payload,
    output: span.output_payload,
    error: span.error
  };
  if (!Object.values(payload).some((value) => value && (typeof value !== "object" || Object.keys(value as Record<string, unknown>).length))) {
    return null;
  }
  return <JsonBlock value={payload} />;
}

function AuditLogList({
  logs,
  expandedAuditId,
  onToggle
}: {
  logs: AuditLog[];
  expandedAuditId: string | null;
  onToggle: (id: string) => void;
}) {
  const parentRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: logs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 116,
    overscan: 6
  });
  return (
    <div className="telemetry-virtual-list" ref={parentRef}>
      <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const log = logs[virtualRow.index];
          return (
            <div
              className="telemetry-virtual-row"
              data-index={virtualRow.index}
              key={log.id}
              ref={rowVirtualizer.measureElement}
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              <AuditLogRow expanded={expandedAuditId === log.id} log={log} onToggle={() => onToggle(log.id)} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AuditLogRow({ log, expanded, onToggle }: { log: AuditLog; expanded: boolean; onToggle: () => void }) {
  const [showEditor, setShowEditor] = React.useState(false);
  const oldValue = isRecord(log.diff.old) ? log.diff.old : {};
  const newValue = isRecord(log.diff.new) ? log.diff.new : {};
  const auditSummary = typeof log.metadata.summary === "string" ? log.metadata.summary : "";
  const delta = React.useMemo(() => jsonDiff(oldValue, newValue) || {}, [log.diff]);
  const original = stringifyJson(oldValue);
  const modified = stringifyJson(newValue);
  const AuditIcon = log.category === "alfred_ai"
    ? Bot
    : log.category === "integrations"
      ? PlugZap
      : log.action.startsWith("maintenance_mode.")
        ? Construction
        : Database;
  const auditTone = log.category === "alfred_ai" ? "purple" : log.action.startsWith("maintenance_mode.") ? "amber" : levelTone(log.level);
  return (
    <article className={expanded ? "telemetry-card expanded" : "telemetry-card"}>
      <button className="telemetry-row-button" onClick={onToggle} type="button">
        <span className={`telemetry-row-icon ${auditTone}`}>
          <AuditIcon size={18} />
        </span>
        <span className="telemetry-row-main">
          <strong>{titleCase(log.action.replace(/\./g, " "))}</strong>
          <small>{log.target_label || log.target_id || log.target_entity || "System"} · {log.actor}</small>
          {auditSummary ? <small className="telemetry-row-summary">{auditSummary}</small> : null}
        </span>
        <span className="telemetry-row-meta">
          <Badge tone={auditTone}>{log.category === "alfred_ai" ? "AI ACTION" : log.action.startsWith("maintenance_mode.") ? "MAINTENANCE" : levelLabel(log.level)}</Badge>
          <Badge tone={outcomeTone(log.outcome)}>{titleCase(log.outcome)}</Badge>
          <time>{formatDate(log.timestamp)}</time>
        </span>
        {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
      </button>
      {expanded ? (
        <div className="audit-detail">
          <div className="trace-summary-grid">
            <TelemetryFact label="Actor" value={log.actor} />
            <TelemetryFact label="Target" value={log.target_entity || "System"} />
            <TelemetryFact label="Request" value={log.request_id || "n/a"} mono />
            <TelemetryFact label="Trace" value={log.trace_id || "n/a"} mono />
          </div>
          <div className="audit-diff-toolbar">
            <strong>JSON Diff</strong>
            <button className="secondary-button" onClick={() => setShowEditor((current) => !current)} type="button">
              <FileText size={15} /> {showEditor ? "Show Summary" : "Open Diff Editor"}
            </button>
          </div>
          {showEditor ? (
            <React.Suspense fallback={<div className="telemetry-detail-loading">Loading diff editor...</div>}>
              <MonacoDiffEditor
                height="280px"
                language="json"
                modified={modified}
                options={{ readOnly: true, minimap: { enabled: false }, renderSideBySide: false, scrollBeyondLastLine: false }}
                original={original}
              />
            </React.Suspense>
          ) : (
            <div className="audit-diff-grid">
              <JsonBlock label="Changed Fields" value={delta} />
              <JsonBlock label="Metadata" value={log.metadata} />
            </div>
          )}
        </div>
      ) : null}
    </article>
  );
}

function LiveLogStream({ logs }: { logs: RealtimeMessage[] }) {
  return (
    <div className="log-console telemetry-live-console">
      {logs.map((log, index) => (
        <div className="log-line" key={`${log.type}-${log.created_at}-${index}`}>
          <time>{log.created_at ? formatDate(log.created_at) : "now"}</time>
          <strong>{log.type}</strong>
          <code>{JSON.stringify(log.payload)}</code>
        </div>
      ))}
      {!logs.length ? <EmptyState icon={Terminal} label="No live events in this filter." /> : null}
    </div>
  );
}

function TelemetryFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="telemetry-fact">
      <span>{label}</span>
      <strong className={mono ? "mono" : undefined}>{value}</strong>
    </div>
  );
}

function JsonBlock({ value, label }: { value: unknown; label?: string }) {
  return (
    <div className="json-block">
      {label ? <strong>{label}</strong> : null}
      <pre>{stringifyJson(value)}</pre>
    </div>
  );
}

function gateMalfunctionTraceId(record: GateMalfunctionRecord) {
  return record.telemetry_trace_id || record.id;
}

function gateMalfunctionLevel(status: string) {
  if (status === "fubar") return "error";
  if (status === "active") return "warning";
  return "info";
}

function gateMalfunctionRecordToTrace(record: GateMalfunctionRecord): TelemetryTrace {
  return {
    trace_id: gateMalfunctionTraceId(record),
    name: `Gate Malfunction - ${record.gate_name || record.gate_entity_id}`,
    category: "gate_malfunction",
    status: record.status,
    level: gateMalfunctionLevel(record.status),
    started_at: record.opened_at,
    ended_at: record.resolved_at || record.fubar_at,
    duration_ms: Math.max(0, record.total_downtime_seconds || 0) * 1000,
    actor: "System",
    source: record.gate_entity_id,
    registration_number: null,
    access_event_id: record.last_known_vehicle_event_id,
    summary: record.summary,
    context: {
      ...record,
      malfunction_id: record.id,
      status: record.status,
      gate_entity_id: record.gate_entity_id,
      gate_name: record.gate_name,
      next_attempt_scheduled_at: record.next_attempt_scheduled_at,
      fix_attempts_count: record.fix_attempts_count,
      total_downtime_seconds: record.total_downtime_seconds
    },
    error: null
  };
}

function gateMalfunctionRecordToTraceDetail(record: GateMalfunctionRecord): TelemetryTraceDetail {
  const trace = gateMalfunctionRecordToTrace(record);
  return {
    ...trace,
    spans: (record.timeline || []).map((event, index) => {
      const failed = event.status === "error" || event.kind === "fubar" || event.kind === "notification_failed" || event.details.accepted === false;
      return {
        id: event.id,
        span_id: event.telemetry_span_id || event.id.replace(/-/g, "").slice(0, 16),
        trace_id: trace.trace_id,
        parent_span_id: null,
        name: event.title,
        category: "gate_malfunction",
        step_order: index + 1,
        started_at: event.occurred_at,
        ended_at: event.occurred_at,
        duration_ms: 0,
        status: failed ? "error" : "ok",
        attributes: { kind: event.kind, attempt_number: event.attempt_number },
        input_payload: {},
        output_payload: {
          ...event.details,
          notification_trigger: event.notification_trigger,
          notification_channel: event.notification_channel
        },
        error: null
      };
    })
  };
}

function gateTraceMatchesFilters(trace: TelemetryTrace, query: string, level: string, status: string) {
  if (level !== "all" && trace.level !== level) return false;
  if (status !== "all" && trace.status !== status) return false;
  const trimmedQuery = query.trim().toLowerCase();
  if (!trimmedQuery) return true;
  return [
    trace.name,
    trace.summary,
    trace.source,
    trace.context.gate_name,
    trace.context.last_known_vehicle,
    JSON.stringify(trace.context)
  ].some((value) => String(value || "").toLowerCase().includes(trimmedQuery));
}

function traceDisplay(trace: TelemetryTrace): { title: string; icon: React.ElementType; tone: BadgeTone } {
  if (trace.category === "gate_malfunction") {
    const status = stringPayload(trace.context.status || trace.status).toLowerCase();
    const gate = stringPayload(trace.context.gate_name || trace.source || "Primary gate");
    if (status === "fubar") return { title: `Gate Malfunction - ${gate}`, icon: AlertTriangle, tone: "red" };
    if (status === "resolved") return { title: `Gate Resolved - ${gate}`, icon: CheckCircle2, tone: "green" };
    return { title: `Gate Malfunction - ${gate}`, icon: DoorOpen, tone: "amber" };
  }
  if (trace.category === "dependency_updates") {
    return { title: trace.summary || trace.name, icon: RefreshCcw, tone: trace.status === "error" ? "red" : "blue" };
  }
  const decision = stringPayload(trace.context.decision).toLowerCase();
  const direction = stringPayload(trace.context.direction).toLowerCase();
  const plate = trace.registration_number || "unknown plate";
  if (decision === "denied") return { title: `Entry Denied - Plate ${plate}`, icon: AlertTriangle, tone: "red" };
  if (direction === "exit") return { title: `Exit Granted - Plate ${plate}`, icon: LogOut, tone: "gray" };
  if (decision === "granted") return { title: `Entry Granted - Plate ${plate}`, icon: LogIn, tone: "green" };
  if (trace.status === "error") return { title: trace.name, icon: AlertTriangle, tone: "red" };
  return { title: trace.name, icon: Activity, tone: "blue" };
}

function gateMalfunctionCountdown(trace: TelemetryTrace, now: number): { label: string; overdue: boolean } | null {
  if (trace.category !== "gate_malfunction" || trace.status !== "active") return null;
  const scheduledAt = stringPayload(trace.context.next_attempt_scheduled_at);
  if (!scheduledAt) return null;
  return {
    label: gateMalfunctionCountdownLabel(scheduledAt, now),
    overdue: Date.parse(scheduledAt) <= now
  };
}

function gateMalfunctionCountdownLabel(scheduledAt: string, now: number) {
  const target = Date.parse(scheduledAt);
  if (!Number.isFinite(target)) return "Next attempt pending";
  const remainingSeconds = Math.ceil((target - now) / 1000);
  if (remainingSeconds <= 0) return "Attempt due now";
  return `Next attempt in ${formatSecondsDuration(remainingSeconds)}`;
}

function gateTimelineSummary(kind: string, details: Record<string, unknown>) {
  if (kind === "preceding_event") {
    return "Closest entry or exit event before the gate was declared malfunctioning.";
  }
  if (kind === "declared") {
    return `Declared after the gate remained ${stringPayload(details.gate_state || "open")}.`;
  }
  if (kind === "attempt" || kind === "manual_attempt") {
    const accepted = details.accepted === true ? "accepted" : "failed";
    return `Recovery command ${accepted}; gate state reported ${stringPayload(details.state || "unknown")}.`;
  }
  if (kind.startsWith("notification")) {
    const channel = stringPayload(details.channel || details.trigger || "workflow");
    return `Notification workflow update via ${channel}.`;
  }
  if (kind === "resolved") {
    return `Resolved after ${formatSecondsDuration(numberPayload(details.total_downtime_seconds))}.`;
  }
  if (kind === "fubar") {
    return "Automated recovery has stopped and manual intervention is required.";
  }
  return stringPayload(details.detail || details.reason || "Gate malfunction timeline event.");
}

function artifactFromSpan(span: TelemetrySpan): Record<string, unknown> | null {
  const artifact = span.output_payload.artifact;
  return isRecord(artifact) && typeof artifact.url === "string" ? artifact : null;
}

function levelTone(level: string | null | undefined): BadgeTone {
  const normalized = String(level || "").toLowerCase();
  if (normalized === "error" || normalized === "critical") return "red";
  if (normalized === "warning" || normalized === "warn") return "amber";
  if (normalized === "purple") return "purple";
  if (normalized === "success" || normalized === "ok") return "green";
  return "blue";
}

function outcomeTone(outcome: string): BadgeTone {
  if (outcome === "success") return "green";
  if (outcome === "failed") return "red";
  if (outcome === "pending_confirmation") return "amber";
  return "gray";
}

function levelLabel(level: string | null | undefined) {
  const normalized = String(level || "info").toLowerCase();
  if (normalized === "warning") return "WARN";
  if (normalized === "purple") return "AI ACTION";
  return normalized.toUpperCase();
}

function formatDuration(value: number | null | undefined) {
  const ms = Math.max(0, Number(value || 0));
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10_000 ? 1 : 2)}s`;
  return `${ms.toFixed(ms >= 100 ? 0 : 1)}ms`;
}

function formatSecondsDuration(value: number | null | undefined) {
  const totalSeconds = Math.max(0, Math.floor(Number(value || 0)));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function stringifyJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

const notificationChannelMeta: Record<NotificationChannelId, {
  label: string;
  icon: React.ElementType;
  tone: BadgeTone;
  description: string;
}> = {
  mobile: {
    label: "Mobile Notification",
    icon: Smartphone,
    tone: "blue",
    description: "Apprise or Home Assistant mobile app delivery."
  },
  in_app: {
    label: "In-App Notification",
    icon: Monitor,
    tone: "green",
    description: "Realtime dashboard alert for signed-in users."
  },
  voice: {
    label: "Voice Notification",
    icon: Volume2,
    tone: "amber",
    description: "Home Assistant TTS announcement to media players."
  },
  discord: {
    label: "Discord Notification",
    icon: MessageCircle,
    tone: "purple",
    description: "Discord embed delivery to selected channels."
  }
};

const fallbackNotificationTriggers: NotificationTriggerGroup[] = [
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
      { value: "gate_malfunction_2hrs", label: "Gate Malfunction - 2hrs", severity: "critical", description: "The gate malfunction has been active for at least two hours." },
      { value: "gate_malfunction_30m", label: "Gate Malfunction - 30m", severity: "warning", description: "The gate malfunction has been active for at least 30 minutes." },
      { value: "gate_malfunction_60m", label: "Gate Malfunction - 60m", severity: "critical", description: "The gate malfunction has been active for at least 60 minutes." },
      { value: "gate_malfunction_fubar", label: "Gate Malfunction - FUBAR", severity: "critical", description: "Automated gate recovery attempts have been exhausted." },
      { value: "gate_malfunction_initial", label: "Gate Malfunction - Initial", severity: "warning", description: "The primary gate has remained open for more than five minutes." }
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
      { value: "visitor_pass_cancelled", label: "Visitor Pass Cancelled", severity: "info", description: "A scheduled or active Visitor Pass was cancelled." },
      { value: "visitor_pass_created", label: "Visitor Pass Created", severity: "info", description: "A new Visitor Pass was created." },
      { value: "visitor_pass_expired", label: "Visitor Pass Expired", severity: "warning", description: "A Visitor Pass window elapsed without being used." },
      { value: "visitor_pass_used", label: "Visitor Pass Used", severity: "info", description: "A Visitor Pass was matched to an arriving vehicle." }
    ]
  }
];

const fallbackNotificationVariables: NotificationVariableGroup[] = [
  {
    group: "Person",
    items: [
      { name: "FirstName", token: "@FirstName", label: "First name" },
      { name: "LastName", token: "@LastName", label: "Last name" },
      { name: "GroupName", token: "@GroupName", label: "Group name" }
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
    group: "Visitor Pass",
    items: [
      { name: "VisitorPassVehicleRegistration", token: "@VisitorPassVehicleRegistration", label: "Visitor Pass vehicle registration" },
      { name: "VisitorPassVehicleMake", token: "@VisitorPassVehicleMake", label: "Visitor Pass vehicle make" },
      { name: "VisitorPassVehicleColour", token: "@VisitorPassVehicleColour", label: "Visitor Pass vehicle colour" },
      { name: "VisitorPassDurationOnSite", token: "@VisitorPassDurationOnSite", label: "Visitor Pass duration on site" }
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
      { name: "LastKnownVehicle", token: "@LastKnownVehicle", label: "Last known vehicle" }
    ]
  }
];

const mockNotificationContext: Record<string, string> = {
  FirstName: "Steph",
  LastName: "Smith",
  DisplayName: "Steph Smith",
  GroupName: "Family",
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
  VisitorPassVehicleRegistration: "PE70DHX",
  VisitorPassVehicleMake: "Peugeot",
  VisitorPassVehicleColour: "Silver",
  VisitorPassDurationOnSite: "1h 25m",
  NewWinnerName: "Steph Smith",
  OvertakenName: "Jason Smith",
  ReadCount: "42"
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

function AutomationsView({ people, vehicles }: { people: Person[]; vehicles: Vehicle[] }) {
  const [catalog, setCatalog] = React.useState<AutomationCatalogResponse | null>(null);
  const [rules, setRules] = React.useState<AutomationRule[]>([]);
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
      const [nextCatalog, nextRules] = await Promise.all([
        api.get<AutomationCatalogResponse>("/api/v1/automations/catalog"),
        api.get<AutomationRule[]>("/api/v1/automations/rules")
      ]);
      setCatalog(nextCatalog);
      setRules(nextRules);
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
      const saved = draft.id.startsWith("draft-")
        ? await api.post<AutomationRule>("/api/v1/automations/rules", payload)
        : await api.patch<AutomationRule>(`/api/v1/automations/rules/${draft.id}`, payload);
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
      await api.delete(`/api/v1/automations/rules/${rule.id}`);
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
      const updated = await api.patch<AutomationRule>(`/api/v1/automations/rules/${rule.id}`, { is_active: isActive });
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

function AutomationWorkflowList({
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

function AutomationRuleMenu({
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

function AutomationWorkflowEmptyState({
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

function AutomationNodeStack({
  actionMeta,
  conditionMeta,
  garageDoors,
  kind,
  nodes,
  notificationRules = [],
  people,
  triggerMeta,
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

function AutomationNodeCard({
  garageDoors,
  kind,
  meta,
  node,
  notificationRules,
  people,
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
  variables: Array<AutomationVariable & { group: string }>;
  vehicles: Vehicle[];
  onChange: (node: AutomationNode | AutomationAction) => void;
  onParseAiSchedule?: (node: AutomationNode) => void;
  onRemove: () => void;
}) {
  const Icon = automationNodeIcon(node.type);
  const updateConfig = (config: Record<string, unknown>) => onChange({ ...node, config: { ...node.config, ...config } });
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

      {node.type.startsWith("integration.") ? (
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

function AutomationSelectionModal({
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
              return (
                <button className="two-pane-item-card automation-selector-card" key={item.type} onClick={() => onSelect(createAutomationNode(kind, item.type, item))} type="button">
                  <Icon size={18} />
                  <span><strong>{item.label}</strong><small>{item.description ?? item.type}</small></span>
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

function AutomationPreviewPanel({
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

function createAutomationDraft(): AutomationRule {
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

function createAutomationNode(kind: "trigger" | "condition" | "action", type: string, meta?: AutomationCatalogItem): AutomationNode | AutomationAction {
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
  return {};
}

function defaultAutomationReason(type: string) {
  if (type === "gate.open") return "Automation opened the gate for @DisplayName.";
  if (type.startsWith("garage_door.")) return "Automation ran @EventType for @DisplayName.";
  if (type.startsWith("maintenance_mode.")) return "Automation changed Maintenance Mode: @Subject.";
  if (type.startsWith("integration.")) return "Automation ran integration action from @EventType.";
  return "Automation action from @EventType.";
}

function automationRulePayload(rule: AutomationRule) {
  return {
    name: rule.name.trim() || "Automation Rule",
    description: rule.description,
    is_active: rule.is_active,
    triggers: rule.triggers,
    conditions: rule.conditions,
    actions: rule.actions,
  };
}

function cloneAutomationRule(rule: AutomationRule): AutomationRule {
  return JSON.parse(JSON.stringify(rule)) as AutomationRule;
}

function groupAutomationRulesByTriggerCategory(
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

function automationVariablesForTrigger(groups: AutomationVariableGroup[], triggerType: string) {
  return groups.flatMap((group) => group.items
    .filter((item) => !triggerType || !item.trigger_types?.length || item.trigger_types.includes(triggerType))
    .map((item) => ({ ...item, group: group.group })));
}

function automationCategoryIcon(groupId: string) {
  if (groupId.includes("time")) return Clock3;
  if (groupId.includes("vehicle")) return Car;
  if (groupId.includes("maintenance")) return Construction;
  if (groupId.includes("visitor")) return UserPlus;
  if (groupId.includes("webhook")) return PlugZap;
  if (groupId.includes("icloud") || groupId.includes("calendar")) return CalendarDays;
  if (groupId.includes("integration")) return PlugZap;
  if (groupId.includes("notification")) return Bell;
  if (groupId.includes("garage")) return Warehouse;
  if (groupId.includes("gate")) return DoorOpen;
  if (groupId.includes("ai")) return Bot;
  return GitBranch;
}

function automationNodeIcon(type: string) {
  if (type.startsWith("time.")) return Clock3;
  if (type.startsWith("vehicle.")) return Car;
  if (type.startsWith("maintenance_mode.")) return Construction;
  if (type.startsWith("visitor_pass.")) return UserPlus;
  if (type.startsWith("webhook.")) return PlugZap;
  if (type.startsWith("integration.icloud_calendar")) return CalendarDays;
  if (type.startsWith("integration.")) return PlugZap;
  if (type.startsWith("notification.")) return Bell;
  if (type.startsWith("garage_door.")) return Warehouse;
  if (type.startsWith("gate.")) return DoorOpen;
  if (type.startsWith("ai.")) return Bot;
  if (type.startsWith("person.")) return UserRound;
  return GitBranch;
}

function toggleStringList(value: unknown, item: string) {
  const current = Array.isArray(value) ? value.map(String) : [];
  return current.includes(item) ? current.filter((entry) => entry !== item) : [...current, item];
}

function toDateTimeLocal(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function fromDateTimeLocal(value: string) {
  return value ? new Date(value).toISOString() : "";
}

function NotificationsView({ currentUser, people, schedules }: { currentUser: UserAccount; people: Person[]; schedules: Schedule[] }) {
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

  const triggerGroups = catalog?.triggers.length ? catalog.triggers : fallbackNotificationTriggers;
  const variableGroups = catalog?.variables.length ? catalog.variables : fallbackNotificationVariables;
  const variables = React.useMemo(() => variableGroups.flatMap((group) => group.items.map((item) => ({ ...item, group: group.group }))), [variableGroups]);
  const triggerOptions = React.useMemo(() => triggerGroups.flatMap((group) => group.events), [triggerGroups]);
  const triggerByValue = React.useMemo(() => new Map(triggerOptions.map((trigger) => [trigger.value, trigger])), [triggerOptions]);
  const activeDraft = draft;
  const workflowModalMode: "editor" | "trigger" | "action" = modal === "trigger" || modal === "action" ? modal : "editor";
  const previewContext = catalog?.mock_context && Object.keys(catalog.mock_context).length ? catalog.mock_context : mockNotificationContext;
  const previewActions = activeDraft ? renderWorkflowPreview(activeDraft.actions, previewContext) : [];
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
      await api.delete(`/api/v1/notifications/rules/${rule.id}`);
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
      const updated = await api.patch<NotificationRule>(`/api/v1/notifications/rules/${rule.id}`, { is_active: isActive });
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
      const created = await api.post<NotificationRule>("/api/v1/notifications/rules", payload);
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
      const saved = activeDraft.id.startsWith("draft-")
        ? await api.post<NotificationRule>("/api/v1/notifications/rules", payload)
        : await api.patch<NotificationRule>(`/api/v1/notifications/rules/${activeDraft.id}`, payload);
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
      await api.post("/api/v1/notifications/rules/test", {
        rule: workflowRulePayload(activeDraft)
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
                      updateDraft((rule) => ({ ...rule, trigger_event: triggerEvent, name: rule.name === "New Notification" ? notificationEventLabel(triggerEvent, triggerByValue) : rule.name }));
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

function NotificationWorkflowList({
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

function NotificationRuleMenu({
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

function WorkflowStatusFilters({
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

function NotificationConfigChip({ count, icon: Icon, label }: { count: number; icon: React.ElementType; label: string }) {
  const tooltipId = React.useId();
  const [tooltipPosition, setTooltipPosition] = React.useState<NotificationConfigTooltipState | null>(null);
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

function NotificationWorkflowEmptyState({
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

function groupNotificationRulesByTriggerCategory(
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

function NotificationWorkflowEditor({
  cameras,
  feedback,
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
  cameras: UnifiProtectCamera[];
  feedback: { tone: "success" | "error" | "info"; text: string } | null;
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
                    cameras={cameras}
                    integration={integrationById.get(action.type)}
                    key={action.id}
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

function WorkflowBlock({
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

function NotificationConditionCard({
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

type TemplateEditorProps = {
  label: string;
  multiline?: boolean;
  value: string;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (value: string) => void;
};

class TemplateEditorBoundary extends React.Component<
  { children: React.ReactNode; fallback: React.ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: unknown) {
    console.error("Notification template editor failed to render", error);
  }

  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; message: string }
> {
  state = { hasError: false, message: "" };

  static getDerivedStateFromError(error: unknown) {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : "The dashboard hit a rendering error.",
    };
  }

  componentDidCatch(error: unknown) {
    console.error("Dashboard failed to render", error);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <main className="app-crash-panel">
        <section>
          <AlertTriangle size={22} />
          <h1>Dashboard needs a reload</h1>
          <p>{this.state.message}</p>
          <div>
            <button className="primary-button" onClick={() => window.location.reload()} type="button">
              <RefreshCw size={15} /> Reload
            </button>
            <button
              className="secondary-button"
              onClick={() => {
                localStorage.removeItem("iacs-active-view");
                window.history.replaceState({ view: "dashboard" }, "", "/");
                window.location.reload();
              }}
              type="button"
            >
              <LayoutDashboard size={15} /> Reset view
            </button>
          </div>
        </section>
      </main>
    );
  }
}

function SafeVariableRichTextEditor(props: TemplateEditorProps) {
  const safeProps = {
    ...props,
    value: stringifyTemplateValue(props.value),
  };
  return (
    <TemplateEditorBoundary fallback={<PlainTemplateEditor {...safeProps} />}>
      <React.Suspense fallback={<div className="loading-panel compact">Loading template editor</div>}>
        <VariableRichTextEditor {...safeProps} />
      </React.Suspense>
    </TemplateEditorBoundary>
  );
}

function PlainTemplateEditor({ label, multiline = false, value, onChange }: TemplateEditorProps) {
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

function NotificationActionCard({
  action,
  cameras,
  integration,
  variables,
  onChange,
  onRemove
}: {
  action: NotificationAction;
  cameras: UnifiProtectCamera[];
  integration?: NotificationIntegration;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (action: NotificationAction) => void;
  onRemove: () => void;
}) {
  const meta = notificationChannelMeta[action.type];
  const Icon = meta.icon;
  const supportsTitle = action.type !== "voice";
  const supportsMedia = action.type === "mobile" || action.type === "in_app" || action.type === "discord";
  const actionMedia = normalizeNotificationMedia(action.media);
  const selectedCamera = cameras.find((camera) => camera.id === actionMedia.camera_id);
  const cameraSnapshotUrl = selectedCamera
    ? `/api/v1/integrations/unifi-protect/cameras/${selectedCamera.id}/snapshot?width=320&height=180`
    : "";
  const targetChips = notificationActionTargetChips(action, integration);
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

      {supportsTitle ? (
        <SafeVariableRichTextEditor
          label="Title"
          value={action.title_template}
          variables={variables}
          onChange={(title_template) => onChange({ ...action, title_template })}
        />
      ) : null}
      <SafeVariableRichTextEditor
        label={action.type === "voice" ? "Spoken message" : "Message"}
        multiline
        value={action.message_template}
        variables={variables}
        onChange={(message_template) => onChange({ ...action, message_template })}
      />

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
        </section>
      ) : null}
    </article>
  );
}

function NotificationLivePreviewPanel({
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

function TwoPaneSelectionModal({
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

function NotificationTriggerModal({
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

function NotificationConditionModal({
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

function NotificationActionModal({
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

function normalizeTriggerGroups(groups: NotificationTriggerGroup[], selected: string): NotificationTriggerGroup[] {
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

function notificationTriggerGroupIcon(groupId: string) {
  if (groupId === "ai_agents") return Bot;
  if (groupId === "compliance") return ShieldCheck;
  if (groupId === "gate_actions") return DoorOpen;
  if (groupId === "gate_malfunctions") return AlertTriangle;
  if (groupId === "leaderboard") return Trophy;
  if (groupId === "maintenance_mode") return Construction;
  if (groupId === "vehicle_detections") return Car;
  if (groupId === "visitor_pass") return UserPlus;
  return Bell;
}

function notificationActionCategories(): TwoPaneCategory[] {
  return (["mobile", "discord", "voice", "in_app"] as NotificationActionType[])
    .map((id) => {
      const meta = notificationChannelMeta[id];
      return { id, label: meta.label, count: 0, icon: meta.icon };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

function buildNotificationActionMethods(
  integrations: NotificationIntegration[],
  currentUserPerson: Person | null
): Record<NotificationActionType, NotificationActionMethod[]> {
  const integrationById = new Map(integrations.map((integration) => [integration.id, integration]));
  const mobileIntegration = integrationById.get("mobile");
  const voiceIntegration = integrationById.get("voice");
  const inAppIntegration = integrationById.get("in_app");
  const discordIntegration = integrationById.get("discord");
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

  return {
    discord: discordMethods.sort(sortNotificationMethods),
    in_app: inAppMethods.sort(sortNotificationMethods),
    mobile: mobileMethods.sort(sortNotificationMethods),
    voice: voiceMethods.sort(sortNotificationMethods),
  };
}

function sortNotificationMethods(a: NotificationActionMethod, b: NotificationActionMethod) {
  return `${a.label} ${a.detail}`.localeCompare(`${b.label} ${b.detail}`);
}

function concreteNotificationEndpoints(endpoints: NotificationEndpoint[]) {
  return endpoints.filter((endpoint) => !endpoint.id.endsWith(":*"));
}

function findCurrentUserPerson(people: Person[], currentUser: UserAccount): Person | null {
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

function normalizeIdentityName(value: string) {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function matchesSearchText(value: string, query: string) {
  if (!query) return true;
  return value.toLowerCase().includes(query);
}

function notificationActionTargetChips(action: NotificationAction, integration?: NotificationIntegration) {
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
  if (targetId.startsWith("home_assistant_mobile:") || targetId.startsWith("home_assistant_tts:")) return "Home Assistant";
  if (targetId === "dashboard") return "Dashboard";
  return integration?.provider ?? "Target";
}

function unavailableNotificationTargetLabel(targetId: string) {
  const raw = targetId.includes(":") ? targetId.split(":").slice(1).join(":") : targetId;
  return `${raw || "Unknown target"} unavailable`;
}

function createWorkflowDraft(): NotificationRule {
  return {
    id: draftId("workflow"),
    name: "New Notification",
    trigger_event: "",
    conditions: [],
    actions: [],
    is_active: true
  };
}

function createWorkflowAction(
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
    media: { attach_camera_snapshot: false, camera_id: "" }
  };
}

function cloneNotificationRule(rule: NotificationRule): NotificationRule {
  return normalizeNotificationRule(JSON.parse(JSON.stringify(rule)) as Partial<NotificationRule>);
}

function workflowRulePayload(rule: NotificationRule) {
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
  return {
    id: stringifyTemplateValue(rule.id) || draftId("workflow"),
    name: stringifyTemplateValue(rule.name) || "Notification Workflow",
    trigger_event: stringifyTemplateValue(rule.trigger_event),
    conditions: Array.isArray(rule.conditions) ? rule.conditions.map(normalizeNotificationCondition) : [],
    actions: Array.isArray(rule.actions) ? rule.actions.map(normalizeNotificationAction) : [],
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
    media: normalizeNotificationMedia(action.media),
  };
}

function isNotificationActionType(value: string): value is NotificationActionType {
  return value === "mobile" || value === "in_app" || value === "voice" || value === "discord";
}

function normalizeNotificationTargetMode(value: unknown): NotificationTargetMode {
  if (value === "many" || value === "selected" || value === "all") return value;
  return "all";
}

function normalizeNotificationMedia(media: unknown): NotificationAction["media"] {
  const raw = media && typeof media === "object" ? media as Partial<NotificationAction["media"]> : {};
  return {
    attach_camera_snapshot: raw.attach_camera_snapshot === true,
    camera_id: stringifyTemplateValue(raw.camera_id),
  };
}

function stringifyTemplateValue(value: unknown) {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function notificationEventLabel(value: string, triggerByValue?: Map<string, NotificationTriggerOption>) {
  return triggerByValue?.get(value)?.label ?? titleCase(value);
}

function pluralize(word: string, count: number) {
  return count === 1 ? word : `${word}s`;
}

function notificationSeverityTone(value: string): BadgeTone {
  if (value === "critical") return "red";
  if (value === "warning") return "amber";
  if (value === "info") return "blue";
  return "gray";
}

function renderWorkflowPreview(actions: NotificationAction[], context: Record<string, string>) {
  return actions.map(normalizeNotificationAction).map((action) => {
    const title = renderWorkflowTemplate(action.title_template, context);
    const message = renderWorkflowTemplate(action.message_template, context);
    return {
      ...action,
      title,
      message,
      phoneticsApplied: action.type === "voice" && hasVehicleTtsPhoneticMatch(message),
    };
  });
}

function renderWorkflowTemplate(template: string, context: Record<string, string>) {
  return template.replace(/@([A-Za-z][A-Za-z0-9_]*)/g, (_, token: string) => context[token] ?? "").trim();
}

function hasVehicleTtsPhoneticMatch(message: string) {
  return vehicleTtsPhoneticPattern.test(message);
}

function draftId(prefix: string) {
  return `draft-${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function SettingsView({
  currentUser,
  groups,
  schedules,
  vehicles
}: {
  currentUser: UserAccount;
  groups: Group[];
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  return (
    <section className="dashboard-grid settings-grid">
      <div className="card span-2">
        <CardHeader icon={SlidersHorizontal} title="Appearance" />
        <div className="settings-list">
          <SettingRow label="Default mode" value="System" />
          <SettingRow label="Status palette" value="Blue, green, gray, amber, red" />
          <SettingRow label="Card radius" value="8px" />
        </div>
      </div>
      <div className="card">
        <CardHeader icon={Users} title="User Accounts" />
        <div className="compact-row">
          <UserAvatar user={currentUser} />
          <div>
            <strong>{displayUserName(currentUser)}</strong>
            <span>{currentUser.role === "admin" ? "Administrator" : "Standard access"}</span>
          </div>
          <Badge tone="green">protected</Badge>
        </div>
      </div>
      <div className="card span-3">
        <CardHeader icon={Database} title="Operational Data" action={<Badge tone="blue">current</Badge>} />
        <div className="settings-list">
          <SettingRow label="Access schedules" value={String(schedules.length)} />
          <SettingRow label="Access groups" value={String(groups.length)} />
          <SettingRow label="Active vehicles" value={`${activeVehicles}/${vehicles.length}`} />
        </div>
      </div>
    </section>
  );
}

function DynamicSettingsView({
  category,
  title,
  icon: Icon,
  maintenanceStatus,
  onMaintenanceStatusChanged
}: {
  category: "general" | "auth" | "lpr";
  title: string;
  icon: React.ElementType;
  maintenanceStatus?: MaintenanceStatus | null;
  onMaintenanceStatusChanged?: (status: MaintenanceStatus) => void;
}) {
  const { values, loading, error, save } = useSettings(category);
  const [form, setForm] = React.useState<Record<string, string>>({});
  const [saved, setSaved] = React.useState("");
  const fields = settingsFields(category);

  React.useEffect(() => {
    const next: Record<string, string> = {};
    for (const field of fields) {
      next[field.key] = stringifySetting(values[field.key]);
    }
    setForm(next);
  }, [values, category]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaved("");
    await save(coerceSettingsPayload(form));
    setSaved("Settings saved.");
  };

  return (
    <section className="view-stack settings-page">
      <Toolbar title={title} count={fields.length} icon={Icon} />
      <form className="dashboard-grid settings-grid" onSubmit={submit}>
        <div className="card span-2">
          <CardHeader icon={Icon} title={title} action={<Badge tone={loading ? "gray" : "green"}>{loading ? "loading" : "database"}</Badge>} />
          {category === "general" ? (
            <MaintenanceModeSettings
              status={maintenanceStatus ?? null}
              onStatusChanged={onMaintenanceStatusChanged}
            />
          ) : null}
          <div className="settings-form-grid">
            {fields.map((field) => (
              <SettingField
                field={field}
                key={field.key}
                value={form[field.key] ?? ""}
                onChange={(value) => setForm((current) => ({ ...current, [field.key]: value }))}
              />
            ))}
          </div>
          {error ? <div className="auth-error inline-error">{error}</div> : null}
          {saved ? <div className="success-note">{saved}</div> : null}
          <div className="modal-actions">
            <button className="primary-button" type="submit">Save Settings</button>
          </div>
        </div>
        <div className="card">
          <CardHeader icon={Database} title="Source" />
          <div className="settings-list">
            <SettingRow label="Storage" value="Database" />
            <SettingRow label="Secrets" value="Encrypted at rest" />
            <SettingRow label="Bootstrap" value=".env only" />
          </div>
        </div>
      </form>
    </section>
  );
}

function MaintenanceModeSettings({
  status,
  onStatusChanged
}: {
  status: MaintenanceStatus | null;
  onStatusChanged?: (status: MaintenanceStatus) => void;
}) {
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const active = status?.is_active === true;
  const toggle = async () => {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      const path = active ? "/api/v1/maintenance/disable" : "/api/v1/maintenance/enable";
      const next = await api.post<MaintenanceStatus>(path, {
        reason: active ? "Disabled from Settings General" : "Enabled from Settings General"
      });
      onStatusChanged?.(next);
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : "Unable to update Maintenance Mode.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className={active ? "maintenance-settings active" : "maintenance-settings"}>
      <div className="maintenance-settings-copy">
        <span className="maintenance-settings-icon">
          <Construction size={20} strokeWidth={1} />
        </span>
        <div>
          <strong>Maintenance Mode</strong>
          <span>{active ? "All automated actions are disabled" : "Automated actions are available"}</span>
          {active && status?.enabled_by ? <small>Enabled by {status.enabled_by}{status.duration_label ? ` for ${status.duration_label}` : ""}</small> : null}
        </div>
      </div>
      <label className={active ? "maintenance-switch active" : "maintenance-switch"}>
        <input checked={active} disabled={saving || !status} onChange={toggle} type="checkbox" />
        <span>{saving ? "Updating" : active ? "Enabled" : "Disabled"}</span>
      </label>
      {error ? <div className="auth-error inline-error maintenance-settings-error">{error}</div> : null}
    </div>
  );
}

function UsersView({
  currentUser,
  onCurrentUserUpdated
}: {
  currentUser: UserAccount;
  onCurrentUserUpdated: (user: UserAccount) => void;
}) {
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [people, setPeople] = React.useState<Person[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [modal, setModal] = React.useState<"create" | "edit" | null>(null);
  const [selectedUser, setSelectedUser] = React.useState<UserAccount | null>(null);
  const [temporaryPassword, setTemporaryPassword] = React.useState<string | null>(null);
  const isAdmin = currentUser.role === "admin";

  const loadUsers = React.useCallback(async () => {
    setError("");
    try {
      const [nextUsers, nextPeople] = await Promise.all([
        api.get<UserAccount[]>("/api/v1/users"),
        api.get<Person[]>("/api/v1/people")
      ]);
      setUsers(nextUsers);
      setPeople(nextPeople);
    } catch (userError) {
      setError(userError instanceof Error ? userError.message : "Unable to load users");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    loadUsers().catch(() => undefined);
  }, [loadUsers]);

  const openCreate = () => {
    setTemporaryPassword(null);
    setSelectedUser(null);
    setModal("create");
  };

  const openEdit = (user: UserAccount) => {
    setTemporaryPassword(null);
    setSelectedUser(user);
    setModal("edit");
  };

  const closeModal = () => {
    setModal(null);
    setSelectedUser(null);
  };

  const deleteUser = async (user: UserAccount) => {
    if (!window.confirm(`Delete ${displayUserName(user)}?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/users/${user.id}`);
      await loadUsers();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete user");
    }
  };

  const toggleActive = async (user: UserAccount) => {
    setError("");
    try {
      const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, { is_active: !user.is_active });
      if (savedUser.id === currentUser.id) {
        onCurrentUserUpdated(savedUser);
      }
      await loadUsers();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Unable to update user");
    }
  };

  const resetPassword = async (user: UserAccount) => {
    setError("");
    try {
      const result = await api.post<{ temporary_password: string }>(`/api/v1/users/${user.id}/reset-password`, {
        generate_password: true
      });
      setSelectedUser(user);
      setTemporaryPassword(result.temporary_password);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Unable to reset password");
    }
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Settings</span>
          <h1>Users</h1>
          <p>{isAdmin ? "Manage dashboard access for family members." : "View system account roster."}</p>
        </div>
        {isAdmin ? (
          <button className="primary-button" onClick={openCreate} type="button">
            <UserPlus size={17} /> Add User
          </button>
        ) : (
          <Badge tone="gray">View Only</Badge>
        )}
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {temporaryPassword ? (
        <div className="temporary-password-card card">
          <div>
            <strong>Temporary password for {selectedUser ? displayUserName(selectedUser) : "user"}</strong>
            <span>{temporaryPassword}</span>
          </div>
          <button className="secondary-button" onClick={() => navigator.clipboard?.writeText(temporaryPassword)} type="button">
            Copy
          </button>
        </div>
      ) : null}

      <div className="card users-card">
        <PanelHeader title="User Roster" action={`${users.length} accounts`} actionKind="select" />
        {loading ? (
          <div className="empty-state">Loading users</div>
        ) : (
          <div className="users-table">
            {users.map((user) => (
              <article className="user-row" key={user.id}>
                <UserAvatar user={user} />
                <div>
                  <strong>{displayUserName(user)}</strong>
                  <span>
                    @{user.username}{user.email ? ` • ${user.email}` : ""}
                    {user.person_id ? ` • linked to ${people.find((person) => person.id === user.person_id)?.display_name ?? "directory person"}` : ""}
                  </span>
                </div>
                <Badge tone={user.role === "admin" ? "blue" : "gray"}>{user.role === "admin" ? "Admin" : "Standard"}</Badge>
                <Badge tone={user.is_active ? "green" : "amber"}>{user.is_active ? "Active" : "Inactive"}</Badge>
                <time>{user.last_login_at ? formatDate(user.last_login_at) : "Never signed in"}</time>
                {isAdmin ? (
                  <div className="user-actions">
                    <button className="secondary-button" onClick={() => openEdit(user)} type="button">Edit</button>
                    <button className="secondary-button" onClick={() => resetPassword(user)} type="button">Reset</button>
                    <button className="secondary-button" onClick={() => toggleActive(user)} type="button">{user.is_active ? "Deactivate" : "Activate"}</button>
                    <button className="icon-button danger" onClick={() => deleteUser(user)} type="button" aria-label={`Delete ${displayUserName(user)}`}>
                      <Trash2 size={16} />
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>

      {modal ? (
        <UserModal
          mode={modal}
          people={people}
          user={selectedUser}
          onClose={closeModal}
          onSaved={async (password, savedUser) => {
            setTemporaryPassword(password);
            if (savedUser?.id === currentUser.id) {
              onCurrentUserUpdated(savedUser);
            }
            await loadUsers();
            closeModal();
            setSelectedUser(savedUser ?? null);
          }}
        />
      ) : null}
    </section>
  );
}

function UserModal({
  mode,
  people,
  user,
  onClose,
  onSaved
}: {
  mode: "create" | "edit";
  people: Person[];
  user: UserAccount | null;
  onClose: () => void;
  onSaved: (temporaryPassword: string | null, savedUser?: UserAccount) => Promise<void>;
}) {
  const [form, setForm] = React.useState({
    username: user?.username ?? "",
    first_name: user?.first_name ?? "",
    last_name: user?.last_name ?? "",
    email: user?.email ?? "",
    profile_photo_data_url: user?.profile_photo_data_url ?? "",
    person_id: user?.person_id ?? "",
    role: user?.role ?? "standard",
    is_active: user?.is_active ?? true,
    temporary_password: "",
    generate_password: mode === "create"
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const update = (field: keyof typeof form, value: string | boolean) => setForm((current) => ({ ...current, [field]: value }));

  const uploadPhoto = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Please choose an image file.");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setError("Profile images must be 8 MB or smaller.");
      return;
    }
    setError("");
    update("profile_photo_data_url", await fileToDataUrl(file));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "create") {
        const result = await api.post<{ user: UserAccount; temporary_password: string | null }>("/api/v1/users", {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          profile_photo_data_url: form.profile_photo_data_url || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active,
          temporary_password: form.generate_password ? null : form.temporary_password,
          generate_password: form.generate_password
        });
        await onSaved(result.temporary_password, result.user);
      } else if (user) {
        const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          profile_photo_data_url: form.profile_photo_data_url || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active
        });
        await onSaved(null, savedUser);
      }
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save user");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "create" ? "Add User" : "Edit User"}</h2>
            <p>{mode === "create" ? "Create a dashboard login." : "Update account access."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="profile-upload-row">
          <UserAvatar
            user={{
              id: user?.id ?? "preview",
              username: form.username,
              first_name: String(form.first_name),
              last_name: String(form.last_name),
              full_name: `${form.first_name} ${form.last_name}`.trim(),
              profile_photo_data_url: String(form.profile_photo_data_url || "") || null,
              email: form.email || null,
              role: form.role as UserRole,
              is_active: Boolean(form.is_active),
              last_login_at: user?.last_login_at ?? null,
              person_id: String(form.person_id || "") || null,
              preferences: user?.preferences ?? { sidebarCollapsed: false },
              created_at: user?.created_at ?? new Date().toISOString(),
              updated_at: user?.updated_at ?? new Date().toISOString()
            }}
            size="large"
          />
          <label className="upload-button">
            <Camera size={16} />
            <span>{form.profile_photo_data_url ? "Change photo" : "Upload profile picture"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {form.profile_photo_data_url ? (
            <button className="secondary-button" onClick={() => update("profile_photo_data_url", "")} type="button">
              Remove
            </button>
          ) : null}
        </div>
        <div className="field-grid">
          <label className="field">
            <span>First name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} required />
            </div>
          </label>
          <label className="field">
            <span>Last name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} required />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Username</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.username} onChange={(event) => update("username", event.target.value)} required />
            </div>
          </label>
        </div>
        <label className="field">
          <span>Email</span>
          <div className="field-control">
            <MessageCircle size={17} />
            <input value={form.email} onChange={(event) => update("email", event.target.value)} type="email" />
          </div>
        </label>
        <label className="field">
          <span>Directory person</span>
          <select value={form.person_id} onChange={(event) => update("person_id", event.target.value)}>
            <option value="">No linked person</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>{person.display_name}</option>
            ))}
          </select>
        </label>
        <div className="field-grid">
          <label className="field">
            <span>Role</span>
            <select value={form.role} onChange={(event) => update("role", event.target.value)}>
              <option value="standard">Standard User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <label className="field">
            <span>Status</span>
            <select value={form.is_active ? "active" : "inactive"} onChange={(event) => update("is_active", event.target.value === "active")}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        {mode === "create" ? (
          <>
            <label className="check-row">
              <input checked={form.generate_password} onChange={(event) => update("generate_password", event.target.checked)} type="checkbox" />
              <span>Generate a temporary password</span>
            </label>
            {!form.generate_password ? (
              <label className="field">
                <span>Temporary password</span>
                <div className="field-control">
                  <Key size={17} />
                  <input value={form.temporary_password} onChange={(event) => update("temporary_password", event.target.value)} type="password" minLength={10} required />
                </div>
              </label>
            ) : null}
          </>
        ) : null}
        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "Saving..." : "Save User"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Toolbar({ title, count, icon: Icon, children }: { title: string; count: number; icon: React.ElementType; children?: React.ReactNode }) {
  return (
    <div className="toolbar">
      <div className="card-title">
        <Icon size={18} />
        <h2>{title}</h2>
        <Badge tone="gray">{count}</Badge>
      </div>
      {children}
    </div>
  );
}

function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="setting-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function EmptyState({ icon: Icon, label }: { icon: React.ElementType; label: string }) {
  return (
    <div className="empty-state">
      <Icon size={22} />
      <span>{label}</span>
    </div>
  );
}

type BadgeTone = "green" | "gray" | "amber" | "red" | "blue" | "purple";

type SettingFieldDefinition = {
  key: string;
  label: string;
  type?: "text" | "password" | "number" | "textarea" | "select";
  options?: string[];
  min?: number;
  max?: number;
  step?: number;
  href?: string;
  help?: string;
};

const secretSettingKeys = new Set([
  "home_assistant_token",
  "apprise_urls",
  "discord_bot_token",
  "dvla_api_key",
  "unifi_protect_username",
  "unifi_protect_password",
  "unifi_protect_api_key",
  "openai_api_key",
  "gemini_api_key",
  "anthropic_api_key"
]);

const discordListSettingKeys = new Set([
  "discord_guild_allowlist",
  "discord_channel_allowlist",
  "discord_user_allowlist",
  "discord_role_allowlist",
  "discord_admin_role_ids"
]);
const listSettingKeys = new Set([
  ...discordListSettingKeys,
  "lpr_allowed_smart_zones"
]);

function SettingField({
  field,
  isConfiguredSecret = false,
  value,
  onChange
}: {
  field: SettingFieldDefinition;
  isConfiguredSecret?: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  const secretPlaceholder = isConfiguredSecret ? "Configured. Paste a new value to replace it." : undefined;
  return (
    <label className="field">
      <span>
        {field.label}
        {field.href ? <a href={field.href} rel="noreferrer" target="_blank">Get key</a> : null}
      </span>
      {field.type === "textarea" ? (
        <textarea value={value} onChange={(event) => onChange(event.target.value)} placeholder={secretPlaceholder} rows={4} />
      ) : field.type === "select" ? (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          {field.options?.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      ) : (
        <div className="field-control">
          {field.type === "password" ? <Key size={17} /> : <SlidersHorizontal size={17} />}
          <input
            min={field.min}
            max={field.max}
            step={field.step}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={field.type === "password" ? secretPlaceholder ?? "Leave blank to keep existing secret" : undefined}
            type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
          />
        </div>
      )}
      {field.help ? <small className="field-hint">{field.help}</small> : null}
      {isConfiguredSecret ? <small className="field-hint">A value is saved securely. Leave this blank to keep the current configuration.</small> : null}
    </label>
  );
}

function useSettings(category?: string) {
  const [settingsRows, setSettingsRows] = React.useState<SystemSetting[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  const load = React.useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const suffix = category ? `?category=${encodeURIComponent(category)}` : "";
      setSettingsRows(await api.get<SystemSetting[]>(`/api/v1/settings${suffix}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load settings");
    } finally {
      setLoading(false);
    }
  }, [category]);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const save = React.useCallback(async (updates: Record<string, unknown>) => {
    await api.patch<SystemSetting[]>("/api/v1/settings", { values: updates });
    await load();
  }, [load]);

  return {
    rows: settingsRows,
    values: settingsRows.reduce<SettingsMap>((acc, row) => {
      acc[row.key] = row.value;
      return acc;
    }, {}),
    loading,
    error,
    save,
    reload: load
  };
}

function settingsFields(category: "general" | "auth" | "lpr"): SettingFieldDefinition[] {
  if (category === "general") {
    return [
      { key: "app_name", label: "App name" },
      { key: "site_timezone", label: "Timezone" },
      { key: "log_level", label: "Log level", type: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"] }
    ];
  }
  if (category === "auth") {
    return [
      { key: "auth_cookie_name", label: "Cookie name" },
      { key: "auth_access_token_minutes", label: "Access token minutes", type: "number", min: 5, step: 5 },
      { key: "auth_remember_days", label: "Remember-me days", type: "number", min: 1, step: 1 },
      { key: "auth_cookie_secure", label: "Secure cookie", type: "select", options: ["true", "false"] }
    ];
  }
  return [
    { key: "lpr_debounce_quiet_seconds", label: "Debounce quiet seconds", type: "number", min: 0.5, step: 0.1 },
    { key: "lpr_debounce_max_seconds", label: "Debounce max seconds", type: "number", min: 1, step: 0.1 },
    { key: "lpr_similarity_threshold", label: "Similarity threshold", type: "number", min: 0, max: 1, step: 0.01 },
    {
      key: "lpr_allowed_smart_zones",
      label: "Accepted smart zones",
      type: "textarea",
      help: "One UniFi smart zone name or ID per line. Default is default. Empty or * accepts all zones."
    }
  ];
}

function integrationInitialValues(definition: IntegrationDefinition, values: SettingsMap) {
  const defaults: Record<string, string> = {
    openai_model: "gpt-4o",
    gemini_model: "gemini-1.5-pro",
    anthropic_model: "claude-3-5-sonnet-latest",
    ollama_model: "llama3",
    openai_base_url: "https://api.openai.com/v1",
    gemini_base_url: "https://generativelanguage.googleapis.com/v1beta",
    anthropic_base_url: "https://api.anthropic.com/v1",
    ollama_base_url: "http://host.docker.internal:11434",
    dvla_vehicle_enquiry_url: "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles",
    dvla_test_registration_number: "AA19AAA",
    dvla_timeout_seconds: "10",
    unifi_protect_port: "443",
    unifi_protect_verify_ssl: "false",
    unifi_protect_snapshot_width: "1280",
    unifi_protect_snapshot_height: "720",
    home_assistant_gate_entities: "[]",
    home_assistant_garage_door_entities: "[]",
    discord_guild_allowlist: "",
    discord_channel_allowlist: "",
    discord_user_allowlist: "",
    discord_role_allowlist: "",
    discord_admin_role_ids: "",
    discord_allow_direct_messages: "false",
    discord_require_mention: "true"
  };
  return definition.fields.reduce<Record<string, string>>((acc, field) => {
    const current = values[field.key];
    const currentOrDefault = current !== undefined && current !== null ? current : defaults[field.key] || "";
    if (secretSettingKeys.has(field.key)) {
      acc[field.key] = "";
    } else if (discordListSettingKeys.has(field.key)) {
      acc[field.key] = Array.isArray(current) ? current.map(String).join("\n") : stringifySetting(currentOrDefault);
    } else if (["home_assistant_gate_entities", "home_assistant_garage_door_entities"].includes(field.key) && typeof current === "object") {
      acc[field.key] = JSON.stringify(current ?? {}, null, 2);
    } else {
      acc[field.key] = stringifySetting(currentOrDefault);
    }
    return acc;
  }, {});
}

function stringifySetting(value: unknown) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.map((item) => String(item)).join("\n");
  if (typeof value === "object" && value !== null) return JSON.stringify(value, null, 2);
  return value == null ? "" : String(value);
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function suggestHomeAssistantPersonIntegrations(
  firstName: string,
  lastName: string,
  discovery: HomeAssistantDiscovery
): HomeAssistantPersonSuggestion {
  const displayName = `${firstName} ${lastName}`.trim();
  const mobile = bestHomeAssistantMatch(
    displayName,
    discovery.mobile_app_notification_services.map((service) => ({
      id: service.service_id,
      label: service.name ? `${service.name} ${service.service_id}` : service.service_id
    })),
    0.45
  );
  return {
    mobile: mobile ? { id: mobile.id, label: titleFromEntityId(mobile.id), confidence: mobile.confidence } : undefined
  };
}

function bestHomeAssistantMatch(
  personName: string,
  candidates: Array<{ id: string; label: string }>,
  threshold: number
): { id: string; confidence: number } | null {
  const personTokens = homeAssistantNameTokens(personName);
  if (!personTokens.size) return null;
  let best: { id: string; confidence: number } | null = null;
  for (const candidate of candidates) {
    const candidateTokens = homeAssistantNameTokens(`${candidate.id} ${candidate.label}`);
    if (!candidateTokens.size) continue;
    const overlap = [...personTokens].filter((token) => candidateTokens.has(token)).length / personTokens.size;
    const personCompact = [...personTokens].sort().join("");
    const candidateCompact = [...candidateTokens].sort().join("");
    const substringScore = candidateCompact.includes(personCompact) || [...personTokens].some((token) => candidateCompact.includes(token))
      ? 0.7
      : 0;
    const confidence = Math.max(overlap, substringScore);
    if (!best || confidence > best.confidence) {
      best = { id: candidate.id, confidence };
    }
  }
  return best && best.confidence >= threshold ? best : null;
}

function homeAssistantNameTokens(value: string) {
  return new Set(
    value
      .toLowerCase()
      .replace(/notify\.mobile_app_/g, " ")
      .split(/[^a-z0-9]+/)
      .filter(Boolean)
  );
}

function parseManagedCovers(value: unknown): HomeAssistantManagedCover[] {
  const raw = parseJsonArray(value);
  const seen = new Set<string>();
  const covers: HomeAssistantManagedCover[] = [];
  for (const item of raw) {
    const cover = normalizeManagedCover(item);
    if (!cover || seen.has(cover.entity_id)) continue;
    covers.push(cover);
    seen.add(cover.entity_id);
  }
  return covers;
}

function parseJsonArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function normalizeManagedCover(value: unknown): HomeAssistantManagedCover | null {
  if (typeof value === "string") {
    const entityId = value.trim();
    return entityId.startsWith("cover.")
      ? { entity_id: entityId, name: titleFromEntityId(entityId), enabled: true, open_service: "cover.open_cover", close_service: "cover.close_cover" }
      : null;
  }
  if (!value || typeof value !== "object") return null;
  const raw = value as Partial<HomeAssistantManagedCover>;
  const entityId = String(raw.entity_id ?? "").trim();
  if (!entityId.startsWith("cover.")) return null;
  return {
    entity_id: entityId,
    name: String(raw.name || titleFromEntityId(entityId)),
    enabled: raw.enabled !== false,
    schedule_id: raw.schedule_id ? String(raw.schedule_id) : null,
    open_service: String(raw.open_service || "cover.open_cover"),
    close_service: String(raw.close_service || "cover.close_cover"),
    state: raw.state ?? null
  };
}

function normalizeManagedCoversForSave(entities: HomeAssistantManagedCover[]) {
  return entities.map((entity) => ({
    entity_id: entity.entity_id,
    name: entity.name || titleFromEntityId(entity.entity_id),
    enabled: entity.enabled !== false,
    schedule_id: entity.schedule_id || null
  }));
}

function managedCoverFromEntity(entity: HomeAssistantEntity): HomeAssistantManagedCover {
  return {
    entity_id: entity.entity_id,
    name: entity.name || titleFromEntityId(entity.entity_id),
    enabled: true,
    open_service: "cover.open_cover",
    close_service: "cover.close_cover",
    state: entity.state
  };
}

function mergeManagedCovers(current: HomeAssistantManagedCover[], incoming: HomeAssistantManagedCover[]) {
  const byEntityId = new Map(current.map((entity) => [entity.entity_id, entity]));
  for (const entity of incoming) {
    if (!byEntityId.has(entity.entity_id)) {
      byEntityId.set(entity.entity_id, entity);
    }
  }
  return Array.from(byEntityId.values());
}

function isGarageDoorCandidate(entity: HomeAssistantEntity) {
  const label = `${entity.entity_id} ${entity.name ?? ""}`.toLowerCase();
  return entity.device_class === "garage" || label.includes("garage");
}

function isGateCandidate(entity: HomeAssistantEntity) {
  const label = `${entity.entity_id} ${entity.name ?? ""}`.toLowerCase();
  return entity.device_class === "gate" || label.includes("gate");
}

function titleFromEntityId(entityId: string) {
  return entityId.split(".", 2).pop()?.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) || entityId;
}

function coerceSettingsPayload(form: Record<string, string>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(form)) {
    if (
      key.endsWith("_api_key") ||
      key === "home_assistant_token" ||
      key === "apprise_urls" ||
      key === "discord_bot_token" ||
      key === "unifi_protect_username" ||
      key === "unifi_protect_password"
    ) {
      if (!value.trim()) continue;
    }
    if (key === "home_assistant_gate_entities" || key === "home_assistant_garage_door_entities") {
      try {
        const parsed = value.trim() ? JSON.parse(value) : [];
        payload[key] = Array.isArray(parsed) ? parsed : [];
      } catch {
        payload[key] = [];
      }
    } else if (listSettingKeys.has(key)) {
      payload[key] = value.replace(/,/g, "\n").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    } else if (["auth_cookie_secure", "unifi_protect_verify_ssl", "discord_allow_direct_messages", "discord_require_mention"].includes(key)) {
      payload[key] = value === "true";
    } else if ([
      "auth_access_token_minutes",
      "auth_remember_days",
      "lpr_debounce_quiet_seconds",
      "lpr_debounce_max_seconds",
      "lpr_similarity_threshold",
      "llm_timeout_seconds",
      "dvla_timeout_seconds",
      "unifi_protect_port",
      "unifi_protect_snapshot_width",
      "unifi_protect_snapshot_height"
    ].includes(key)) {
      payload[key] = Number(value);
    } else {
      payload[key] = value;
    }
  }
  return payload;
}

function Badge({ children, tone }: { children: React.ReactNode; tone: BadgeTone }) {
  return <span className={`badge ${tone}`}><span className="badge-label">{children}</span></span>;
}

function ThemeControl({ theme, setTheme }: { theme: ThemeMode; setTheme: (mode: ThemeMode) => void }) {
  const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <button className="icon-button theme-button" onClick={() => setTheme(next)} type="button" aria-label="Theme">
      <Icon size={17} />
      <span>{theme}</span>
    </button>
  );
}

const chatMessageVariants = {
  hidden: { opacity: 0, y: 14, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: 8, scale: 0.98 }
};

function ChatWidget({ currentUser, maintenanceStatus }: { currentUser: UserAccount; maintenanceStatus: MaintenanceStatus | null }) {
  const llmSettings = useSettings("llm");
  const [open, setOpen] = React.useState(false);
  const teaserStorageKey = `iacs-chat-teaser-dismissed:${currentUser.id}`;
  const [showTeaser, setShowTeaser] = React.useState(() => sessionStorage.getItem(teaserStorageKey) !== "true");
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<ChatMessageItem[]>([]);
  const [draft, setDraft] = React.useState("");
  const [llmPickerOpen, setLlmPickerOpen] = React.useState(false);
  const [llmSaving, setLlmSaving] = React.useState(false);
  const [llmFeedback, setLlmFeedback] = React.useState("");
  const [pendingAttachments, setPendingAttachments] = React.useState<ChatAttachmentDraft[]>([]);
  const [connected, setConnected] = React.useState(false);
  const [connectionNonce, setConnectionNonce] = React.useState(0);
  const [thinking, setThinking] = React.useState(false);
  const [toolStatus, setToolStatus] = React.useState("");
  const [toolActivities, setToolActivities] = React.useState<ChatToolActivity[]>([]);
  const [dragActive, setDragActive] = React.useState(false);
  const [copyMenu, setCopyMenu] = React.useState<ChatCopyMenu | null>(null);
  const [copiedMessageId, setCopiedMessageId] = React.useState<string | null>(null);
  const [viewportHeight, setViewportHeight] = React.useState(() => window.visualViewport?.height ?? window.innerHeight);
  const [viewportTop, setViewportTop] = React.useState(() => window.visualViewport?.offsetTop ?? 0);
  const socketRef = React.useRef<WebSocket | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  const composerRef = React.useRef<HTMLTextAreaElement | null>(null);
  const feedRef = React.useRef<HTMLDivElement | null>(null);
  const activeAssistantMessageRef = React.useRef<string | null>(null);
  const pendingAttachmentsRef = React.useRef<ChatAttachmentDraft[]>([]);
  const greetingInsertedRef = React.useRef(false);
  const firstName = currentUser.first_name || displayUserName(currentUser).split(" ")[0] || "there";
  const activeLlmProvider = normalizeLlmProvider(llmSettings.values.llm_provider);
  const maintenanceActive = maintenanceStatus?.is_active === true;
  const uploading = pendingAttachments.some((attachment) => attachment.uploadState === "uploading");
  const readyAttachments = pendingAttachments.filter((attachment) => attachment.uploadState === "ready");
  const canSend = Boolean((draft.trim() || readyAttachments.length) && connected && !uploading);
  const widgetStyle = {
    "--chat-vvh": `${Math.round(viewportHeight)}px`,
    "--chat-vv-top": `${Math.round(viewportTop)}px`
  } as React.CSSProperties;

  React.useEffect(() => {
    setShowTeaser(sessionStorage.getItem(teaserStorageKey) !== "true");
  }, [teaserStorageKey]);

  React.useEffect(() => {
    pendingAttachmentsRef.current = pendingAttachments;
  }, [pendingAttachments]);

  React.useEffect(() => {
    return () => {
      pendingAttachmentsRef.current.forEach((attachment) => {
        if (attachment.preview_url) URL.revokeObjectURL(attachment.preview_url);
      });
    };
  }, []);

  React.useEffect(() => {
    if (!open) return;
    if (greetingInsertedRef.current) return;
    setMessages((current) => {
      greetingInsertedRef.current = true;
      if (current.length) return current;
      return [{ id: clientId("alfred"), role: "assistant", text: `Hi ${firstName}, how can I help?` }];
    });
  }, [firstName, open]);

  React.useEffect(() => {
    if (!open) return;
    const focusTimer = window.setTimeout(() => {
      composerRef.current?.focus({ preventScroll: true });
    }, 120);
    return () => window.clearTimeout(focusTimer);
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const updateViewportHeight = () => {
      const viewport = window.visualViewport;
      setViewportHeight(viewport?.height ?? window.innerHeight);
      setViewportTop(viewport?.offsetTop ?? 0);
    };
    updateViewportHeight();
    window.visualViewport?.addEventListener("resize", updateViewportHeight);
    window.visualViewport?.addEventListener("scroll", updateViewportHeight);
    window.addEventListener("resize", updateViewportHeight);
    return () => {
      window.visualViewport?.removeEventListener("resize", updateViewportHeight);
      window.visualViewport?.removeEventListener("scroll", updateViewportHeight);
      window.removeEventListener("resize", updateViewportHeight);
    };
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    if (!window.matchMedia("(max-width: 720px)").matches) return;
    const scrollY = window.scrollY;
    const originalOverflow = document.body.style.overflow;
    const originalHtmlOverflow = document.documentElement.style.overflow;
    document.body.classList.add("alfred-chat-open");
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.body.classList.remove("alfred-chat-open");
      document.body.style.overflow = originalOverflow;
      document.documentElement.style.overflow = originalHtmlOverflow;
      window.scrollTo(0, scrollY);
    };
  }, [open]);

  React.useEffect(() => {
    if (!copyMenu) return undefined;
    const close = () => setCopyMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [copyMenu]);

  React.useEffect(() => {
    if (!open) setCopyMenu(null);
  }, [open]);

  React.useEffect(() => {
    if (!open) {
      setLlmPickerOpen(false);
      setLlmFeedback("");
    }
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let connectionTimeoutId: number | null = null;
    let reconnectTimerId: number | null = null;
    const clearConnectionTimeout = () => {
      if (connectionTimeoutId) window.clearTimeout(connectionTimeoutId);
      connectionTimeoutId = null;
    };
    const clearReconnectTimer = () => {
      if (reconnectTimerId) window.clearTimeout(reconnectTimerId);
      reconnectTimerId = null;
    };
    const socket = new WebSocket(wsUrl("/api/v1/ai/chat/ws"));
    setConnected(false);
    connectionTimeoutId = window.setTimeout(() => {
      if (socket.readyState !== WebSocket.OPEN) socket.close();
    }, 10000);
    socket.onopen = () => {
      clearConnectionTimeout();
      setConnected(true);
    };
    socket.onmessage = (event) => {
      let data: { type: string; payload?: Record<string, unknown> };
      try {
        data = JSON.parse(event.data) as { type: string; payload?: Record<string, unknown> };
      } catch {
        return;
      }
      const payload = data.payload ?? {};
      if (data.type === "connection.ready") {
        setConnected(true);
        return;
      }
      if (data.type === "chat.thinking") {
        setThinking(true);
        setToolStatus("Thinking...");
        setToolActivities([]);
        return;
      }
      if (data.type === "chat.tool_batch") {
        const batchId = typeof payload.batch_id === "string" ? payload.batch_id : clientId("tool-batch");
        const status = typeof payload.status === "string" ? payload.status : "";
        const tools = Array.isArray(payload.tools) ? payload.tools : [];
        if (status === "completed") {
          setToolActivities((current) => current.filter((item) => item.batchId !== batchId));
        } else {
          setToolActivities((current) => {
            const next = current.filter((item) => item.batchId !== batchId);
            tools.forEach((tool) => {
              if (!isRecord(tool)) return;
              const callId = String(tool.call_id || tool.tool || clientId("tool"));
              next.push({
                id: callId,
                batchId,
                tool: String(tool.tool || "tool"),
                label: String(tool.label || "Running system tool..."),
                status: "queued"
              });
            });
            return next;
          });
        }
        return;
      }
      if (data.type === "chat.tool_status") {
        setThinking(true);
        const label = typeof payload.label === "string" ? payload.label : "Running system tool...";
        setToolStatus(label);
        const tool = typeof payload.tool === "string" ? payload.tool : "tool";
        const status = ["queued", "running", "succeeded", "failed", "requires_confirmation"].includes(String(payload.status))
          ? String(payload.status) as ChatToolActivity["status"]
          : "running";
        const id = typeof payload.call_id === "string" ? payload.call_id : `${tool}:${String(payload.batch_id || "single")}`;
        setToolActivities((current) => {
          const existing = current.find((item) => item.id === id);
          if (status === "succeeded") return current.filter((item) => item.id !== id);
          if (existing) {
            return current.map((item) => item.id === id ? { ...item, label, status } : item);
          }
          return [
            ...current,
            {
              id,
              batchId: typeof payload.batch_id === "string" ? payload.batch_id : undefined,
              tool,
              label,
              status
            }
          ];
        });
        return;
      }
      if (data.type === "chat.confirmation_required") {
        setThinking(true);
        setToolStatus("Waiting for confirmation...");
        return;
      }
      if (data.type === "chat.response.delta") {
        const chunk = typeof payload.chunk === "string" ? payload.chunk : "";
        if (!chunk) return;
        setThinking(true);
        setMessages((current) => {
          const activeId = activeAssistantMessageRef.current ?? clientId("alfred-stream");
          activeAssistantMessageRef.current = activeId;
          const existing = current.find((message) => message.id === activeId);
          if (existing) {
            return current.map((message) => message.id === activeId ? { ...message, text: message.text + chunk, streaming: true } : message);
          }
          return [...current, { id: activeId, role: "assistant", text: chunk, streaming: true }];
        });
        return;
      }
      if (data.type === "chat.response") {
        const text = typeof payload.text === "string" ? payload.text : "";
        const responseAttachments = Array.isArray(payload.attachments) ? payload.attachments as ChatAttachment[] : [];
        const confirmationAction = chatPendingAction(payload.pending_action) ?? chatConfirmationAction(payload.tool_results);
        if (typeof payload.session_id === "string") setSessionId(payload.session_id);
        setMessages((current) => {
          const activeId = activeAssistantMessageRef.current ?? clientId("alfred");
          activeAssistantMessageRef.current = null;
          const existing = current.find((message) => message.id === activeId);
          if (existing) {
            return current.map((message) =>
              message.id === activeId
                ? {
                  ...message,
                  text,
                  attachments: responseAttachments,
                  confirmationAction,
                  streaming: false
                }
                : message
            );
          }
          return [
            ...current,
            {
              id: activeId,
              role: "assistant",
              text,
              attachments: responseAttachments,
              confirmationAction
            }
          ];
        });
        setThinking(false);
        setToolStatus("");
        setToolActivities([]);
        return;
      }
      if (data.type === "chat.error") {
        setMessages((current) => [
          ...current,
          {
            id: clientId("alfred-error"),
            role: "assistant",
            text: typeof payload.message === "string" ? payload.message : "Alfred could not complete that request."
          }
        ]);
        setThinking(false);
        setToolStatus("");
        setToolActivities([]);
      }
    };
    socket.onerror = () => {
      socket.close();
    };
    socket.onclose = () => {
      clearConnectionTimeout();
      if (socketRef.current === socket) socketRef.current = null;
      setConnected(false);
      setThinking(false);
      setToolStatus("");
      setToolActivities([]);
      if (!cancelled) {
        const delay = Math.min(8000, 700 + connectionNonce * 600);
        reconnectTimerId = window.setTimeout(() => {
          setConnectionNonce((current) => current + 1);
        }, delay);
      }
    };
    socketRef.current = socket;
    return () => {
      cancelled = true;
      clearReconnectTimer();
      clearConnectionTimeout();
      socket.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [connectionNonce, open]);

  React.useEffect(() => {
    if (!feedRef.current) return;
    window.requestAnimationFrame(() => {
      if (feedRef.current) {
        feedRef.current.scrollTop = feedRef.current.scrollHeight;
      }
    });
  }, [messages, thinking, toolStatus, toolActivities]);

  const dismissTeaser = React.useCallback(() => {
    sessionStorage.setItem(teaserStorageKey, "true");
    setShowTeaser(false);
  }, [teaserStorageKey]);

  const removeAttachment = React.useCallback((id: string) => {
    setPendingAttachments((current) => {
      const removed = current.find((attachment) => attachment.id === id);
      if (removed?.preview_url) URL.revokeObjectURL(removed.preview_url);
      return current.filter((attachment) => attachment.id !== id);
    });
  }, []);

  const addFiles = React.useCallback((fileList: FileList | File[]) => {
    const files = Array.from(fileList).slice(0, 6);
    if (!files.length) return;
    const drafts: ChatAttachmentDraft[] = files.map((file) => ({
      id: clientId("upload"),
      filename: file.name || "Attachment",
      content_type: file.type || "application/octet-stream",
      size_bytes: file.size,
      kind: file.type.startsWith("image/") ? "image" : "document",
      url: "",
      uploadState: "uploading",
      preview_url: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined
    }));
    setPendingAttachments((current) => [...current, ...drafts]);
    drafts.forEach((draftAttachment, index) => {
      uploadChatAttachment(files[index], sessionId)
        .then((uploaded) => {
          if (draftAttachment.preview_url) URL.revokeObjectURL(draftAttachment.preview_url);
          setPendingAttachments((current) =>
            current.map((attachment) =>
              attachment.id === draftAttachment.id
                ? { ...uploaded, uploadState: "ready" }
                : attachment
            )
          );
        })
        .catch((error: unknown) => {
          setPendingAttachments((current) =>
            current.map((attachment) =>
              attachment.id === draftAttachment.id
                ? {
                  ...attachment,
                  uploadState: "error",
                  error: error instanceof Error ? error.message : "Upload failed"
                }
                : attachment
            )
          );
        });
    });
  }, [sessionId]);

  const sendConfirmationAction = React.useCallback((messageId: string, action: ChatConfirmationAction, decision: "confirm" | "cancel" = "confirm") => {
    const socket = socketRef.current;
    if (!connected || thinking || !socket || socket.readyState !== WebSocket.OPEN || action.sent) return;
    const userEcho = decision === "confirm" ? action.userEcho : `Cancelled: ${action.displayTarget}`;
    setMessages((current) => [
      ...current.map((message) =>
        message.id === messageId && message.confirmationAction
          ? { ...message, confirmationAction: { ...message.confirmationAction, sent: true } }
          : message
      ),
      {
        id: clientId("user"),
        role: "user",
        text: userEcho
      }
    ]);
    socket.send(JSON.stringify({
      message: userEcho,
      session_id: sessionId,
      attachments: [],
      client_context: chatClientContext(),
      tool_confirmation: {
        id: action.confirmationId,
        confirmation_id: action.confirmationId,
        decision
      }
    }));
    setThinking(true);
    setToolStatus(decision === "confirm" ? action.statusLabel : "Cancelling action...");
  }, [connected, sessionId, thinking]);

  const selectLlmProvider = React.useCallback(async (provider: LlmProviderKey) => {
    if (llmSaving || llmSettings.loading) return;
    const definition = llmProviderDefinitions.find((item) => item.key === provider);
    const label = definition?.label ?? provider;
    if (!isLlmProviderConfigured(provider, llmSettings.values)) {
      setLlmFeedback(`${label} is not configured yet.`);
      return;
    }
    setLlmSaving(true);
    setLlmFeedback("");
    try {
      await llmSettings.save({ llm_provider: provider });
      setLlmPickerOpen(false);
      setMessages((current) => [
        ...current,
        {
          id: clientId("alfred-llm"),
          role: "assistant",
          text: `System LLM set to ${label}.`
        }
      ]);
      window.setTimeout(() => composerRef.current?.focus({ preventScroll: true }), 20);
    } catch (error) {
      setLlmFeedback(error instanceof Error ? error.message : "Unable to update the system LLM.");
    } finally {
      setLlmSaving(false);
    }
  }, [llmSaving, llmSettings]);

  const updateDraft = React.useCallback((value: string) => {
    if (value.trim().toLowerCase() === "/llm") {
      setDraft("");
      setLlmFeedback("");
      setLlmPickerOpen(true);
      return;
    }
    setDraft(value);
  }, []);

  const sendMessage = React.useCallback(() => {
    const socket = socketRef.current;
    if (draft.trim().toLowerCase() === "/llm") {
      setDraft("");
      setLlmFeedback("");
      setLlmPickerOpen(true);
      return;
    }
    if (!canSend || !socket || socket.readyState !== WebSocket.OPEN) return;
    const text = draft.trim() || "Please inspect the attached file.";
    const attachments = readyAttachments.map(publicChatAttachment);
    setMessages((current) => [
      ...current,
      { id: clientId("user"), role: "user", text, attachments }
    ]);
    socket.send(JSON.stringify({ message: text, session_id: sessionId, attachments, client_context: chatClientContext() }));
    setDraft("");
    setLlmPickerOpen(false);
    setLlmFeedback("");
    setPendingAttachments((current) => current.filter((attachment) => attachment.uploadState === "error"));
    setThinking(true);
    setToolStatus("Thinking...");
  }, [canSend, draft, readyAttachments, sessionId]);

  const handleComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Escape" && llmPickerOpen) {
      event.preventDefault();
      setLlmPickerOpen(false);
      setLlmFeedback("");
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  };

  const handleDrop = React.useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    addFiles(event.dataTransfer.files);
  }, [addFiles]);

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (!nextTarget || !event.currentTarget.contains(nextTarget as Node)) {
      setDragActive(false);
    }
  };

  return (
    <div className={open ? "chat-widget open" : "chat-widget"} style={widgetStyle}>
      <AnimatePresence>
        {open ? (
          <motion.div
            className={dragActive ? "chat-panel drag-active" : "chat-panel"}
            initial={{ opacity: 0, scale: 0.86, y: 28 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 24 }}
            transition={{ type: "spring", stiffness: 360, damping: 32 }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <div className="chat-header">
              <div className="alfred-identity">
                <span className="alfred-avatar">
                  <Bot size={18} aria-hidden="true" />
                  {maintenanceActive ? <HardHat className="alfred-maintenance-icon" size={14} strokeWidth={1} aria-hidden="true" /> : null}
                </span>
                <span>
                  <strong>Alfred {maintenanceActive ? <HardHat className="alfred-header-maintenance" size={15} strokeWidth={1} aria-label="Maintenance Mode active" /> : null}</strong>
                  <small><span className={connected ? "alfred-status online" : "alfred-status"} />{connected ? "Online" : "Connecting"}</small>
                </span>
              </div>
              <button className="icon-button chat-close" onClick={() => setOpen(false)} type="button" aria-label="Close Alfred">
                <X size={16} />
              </button>
            </div>

            <div className="chat-feed" ref={feedRef}>
              <AnimatePresence initial={false}>
                {messages.map((message, index) => (
                  <ChatMessageBubble
                    index={index}
                    key={message.id}
                    message={message}
                    onConfirm={sendConfirmationAction}
                    onOpenCopyMenu={setCopyMenu}
                    senderName={message.role === "assistant" ? "Alfred" : firstName}
                  />
                ))}
              </AnimatePresence>
              {thinking ? <TypingIndicator activities={toolActivities} status={toolStatus} /> : null}
            </div>

            <div className="chat-composer">
              <AnimatePresence>
                {llmPickerOpen ? (
                  <ChatLlmProviderPopover
                    activeProvider={activeLlmProvider}
                    error={llmSettings.error || llmFeedback}
                    loading={llmSettings.loading}
                    saving={llmSaving}
                    values={llmSettings.values}
                    onClose={() => {
                      setLlmPickerOpen(false);
                      setLlmFeedback("");
                      composerRef.current?.focus({ preventScroll: true });
                    }}
                    onSelect={selectLlmProvider}
                  />
                ) : null}
              </AnimatePresence>
              {pendingAttachments.length ? (
                <div className="chat-composer-attachments" aria-label="Pending attachments">
                  {pendingAttachments.map((attachment) => (
                    <ChatAttachmentPreview attachment={attachment} key={attachment.id} onRemove={removeAttachment} />
                  ))}
                </div>
              ) : null}
              <div className="chat-input">
                <input
                  className="chat-file-input"
                  multiple
                  onChange={(event) => {
                    if (event.currentTarget.files) addFiles(event.currentTarget.files);
                    event.currentTarget.value = "";
                  }}
                  ref={fileInputRef}
                  type="file"
                />
                <button className="icon-button attach" onClick={() => fileInputRef.current?.click()} type="button" aria-label="Attach files">
                  <Paperclip size={17} />
                </button>
                <textarea
                  value={draft}
                  onChange={(event) => updateDraft(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder="Ask Alfred..."
                  ref={composerRef}
                  rows={1}
                />
                <button className="icon-button send" disabled={!canSend} onClick={sendMessage} type="button" aria-label="Send message">
                  <Send size={17} />
                </button>
              </div>
              {dragActive ? (
                <div className="chat-drop-overlay">
                  <Sparkles size={18} />
                  <span>Drop files for Alfred</span>
                </div>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {!open && showTeaser ? (
          <motion.div
            className="chat-teaser"
            initial={{ opacity: 0, y: 10, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.96 }}
          >
            <button className="teaser-close" onClick={dismissTeaser} type="button" aria-label="Dismiss chat prompt">
              <X size={16} />
            </button>
            <strong>Alfred is ready</strong>
            <p>Hi {firstName}, how can I help?</p>
          </motion.div>
        ) : null}
      </AnimatePresence>
      {!open ? (
        <motion.button
          className="chat-pill"
          onClick={() => setOpen(true)}
          type="button"
          aria-label="Open Alfred"
          whileTap={{ scale: 0.97 }}
        >
          <MessageCircle size={18} />
          <span>Alfred</span>
        </motion.button>
      ) : null}
      {copyMenu ? (
        <ChatCopyMenu
          copied={copiedMessageId === copyMenu.messageId}
          menu={copyMenu}
          onCopy={async () => {
            await copyToClipboard(copyMenu.text);
            setCopiedMessageId(copyMenu.messageId);
            window.setTimeout(() => setCopyMenu(null), 450);
          }}
        />
      ) : null}
    </div>
  );
}

function ChatLlmProviderPopover({
  activeProvider,
  error,
  loading,
  saving,
  values,
  onClose,
  onSelect
}: {
  activeProvider: LlmProviderKey;
  error: string;
  loading: boolean;
  saving: boolean;
  values: SettingsMap;
  onClose: () => void;
  onSelect: (provider: LlmProviderKey) => Promise<void>;
}) {
  return (
    <motion.div
      className="chat-llm-popover"
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.98 }}
      transition={{ type: "spring", stiffness: 420, damping: 34 }}
    >
      <div className="chat-llm-popover-head">
        <span>
          <Bot size={15} />
          <strong>System LLM</strong>
        </span>
        <button className="icon-button" onClick={onClose} type="button" aria-label="Close LLM selector">
          <X size={14} />
        </button>
      </div>
      <div className="chat-llm-provider-grid">
        {llmProviderDefinitions.map((provider) => {
          const configured = isLlmProviderConfigured(provider.key, values);
          const active = provider.key === activeProvider;
          const disabled = saving || loading || (!configured && !active);
          const Icon = provider.key === "gemini"
            ? CircleDot
            : provider.key === "anthropic"
              ? MessageCircle
              : provider.key === "ollama"
                ? Terminal
                : Bot;
          return (
            <button
              className={active ? "chat-llm-provider active" : "chat-llm-provider"}
              disabled={disabled}
              key={provider.key}
              onClick={() => onSelect(provider.key)}
              type="button"
            >
              <Icon size={16} />
              <span>
                <strong>{provider.label}</strong>
                <small>{active ? "Active" : configured ? "Ready" : "Not configured"}</small>
              </span>
              {saving && active ? <Loader2 className="spin" size={14} /> : null}
            </button>
          );
        })}
      </div>
      {error ? <p className="chat-llm-feedback" role="status">{error}</p> : null}
    </motion.div>
  );
}

function ChatMessageBubble({
  message,
  index,
  senderName,
  onOpenCopyMenu,
  onConfirm
}: {
  message: ChatMessageItem;
  index: number;
  senderName: string;
  onOpenCopyMenu: (menu: ChatCopyMenu) => void;
  onConfirm: (messageId: string, action: ChatConfirmationAction, decision?: "confirm" | "cancel") => void;
}) {
  const longPressTimerRef = React.useRef<number | null>(null);
  const displayText = cleanChatText(message.text, message.attachments ?? []);
  const clearLongPress = React.useCallback(() => {
    if (!longPressTimerRef.current) return;
    window.clearTimeout(longPressTimerRef.current);
    longPressTimerRef.current = null;
  }, []);
  const openCopyMenu = React.useCallback((x: number, y: number) => {
    if (!displayText) return;
    onOpenCopyMenu({ messageId: message.id, text: displayText, x, y });
  }, [displayText, message.id, onOpenCopyMenu]);
  return (
    <motion.div
      className={`chat-message ${message.role}`}
      variants={chatMessageVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      transition={{ type: "spring", stiffness: 420, damping: 34, delay: Math.min(index * 0.025, 0.18) }}
      layout
    >
      <div className="chat-message-stack">
        <span className="chat-sender-label">{senderName}</span>
        <div
          className={`chat-bubble ${message.role}`}
          onContextMenu={(event) => {
            event.preventDefault();
            openCopyMenu(event.clientX, event.clientY);
          }}
          onPointerCancel={clearLongPress}
          onPointerDown={(event) => {
            if (event.pointerType !== "touch" && event.pointerType !== "pen") return;
            clearLongPress();
            const { clientX, clientY } = event;
            longPressTimerRef.current = window.setTimeout(() => openCopyMenu(clientX, clientY), 560);
          }}
          onPointerLeave={clearLongPress}
          onPointerMove={clearLongPress}
          onPointerUp={clearLongPress}
        >
          {displayText ? <p>{displayText}</p> : null}
          {message.confirmationAction ? (
            <ChatConfirmationCard
              action={message.confirmationAction}
              onConfirm={() => onConfirm(message.id, message.confirmationAction as ChatConfirmationAction)}
              onCancel={() => onConfirm(message.id, message.confirmationAction as ChatConfirmationAction, "cancel")}
            />
          ) : null}
          {message.attachments?.length ? (
            <div className="chat-bubble-attachments">
              {message.attachments.map((attachment) => (
                <ChatAttachmentCard attachment={attachment} key={attachment.id} />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </motion.div>
  );
}

function ChatCopyMenu({
  copied,
  menu,
  onCopy
}: {
  copied: boolean;
  menu: ChatCopyMenu;
  onCopy: () => void;
}) {
  const left = Math.max(8, Math.min(menu.x, window.innerWidth - 112));
  const top = Math.max(8, Math.min(menu.y, window.innerHeight - 48));
  return (
    <div
      className="chat-copy-menu"
      onClick={(event) => event.stopPropagation()}
      role="menu"
      style={{ left, top }}
    >
      <button onClick={onCopy} role="menuitem" type="button">
        <Copy size={14} />
        <span>{copied ? "Copied" : "Copy"}</span>
      </button>
    </div>
  );
}

function ChatConfirmationCard({
  action,
  onCancel,
  onConfirm
}: {
  action: ChatConfirmationAction;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="chat-confirm-card">
      <span className="chat-confirm-icon">
        {action.type === "update_schedule" ? <Clock3 size={17} /> : action.type === "delete_schedule" ? <Trash2 size={17} /> : <DoorOpen size={17} />}
      </span>
      <span>
        <strong>{action.title}</strong>
        <small>{action.description}</small>
      </span>
      <span className="chat-confirm-actions">
        <button className="chat-confirm-button secondary" disabled={action.sent} onClick={onCancel} type="button">
          <X size={14} />
          <span>Cancel</span>
        </button>
        <button className="chat-confirm-button" disabled={action.sent} onClick={onConfirm} type="button">
          <ShieldCheck size={14} />
          <span>{action.sent ? action.pendingLabel : action.buttonLabel}</span>
        </button>
      </span>
    </div>
  );
}

function ChatAttachmentCard({ attachment }: { attachment: ChatAttachment }) {
  const url = attachment.url || attachment.download_url || "#";
  if (attachment.kind === "image") {
    return (
      <a className="chat-image-attachment" href={url} target="_blank" rel="noreferrer">
        <img alt={attachment.filename} src={url} />
      </a>
    );
  }
  return (
    <div className="chat-download-card">
      <span className="chat-file-icon"><FileText size={18} /></span>
      <span>
        <strong>{attachment.filename}</strong>
        <small>{formatFileSize(attachment.size_bytes)} · {attachment.content_type}</small>
      </span>
      <a className="chat-download-button" href={attachment.download_url || url} download>
        <Download size={14} />
        <span>Download</span>
      </a>
    </div>
  );
}

function ChatAttachmentPreview({
  attachment,
  onRemove
}: {
  attachment: ChatAttachmentDraft;
  onRemove: (id: string) => void;
}) {
  const isImage = attachment.kind === "image";
  const Icon = isImage ? FileImage : FileIcon;
  return (
    <div className={`chat-attachment-pill ${attachment.uploadState}`}>
      {isImage && (attachment.preview_url || attachment.url) ? (
        <img alt="" src={attachment.preview_url || attachment.url} />
      ) : (
        <Icon size={15} />
      )}
      <span>
        <strong>{attachment.filename}</strong>
        <small>{attachment.uploadState === "error" ? attachment.error : formatFileSize(attachment.size_bytes)}</small>
      </span>
      {attachment.uploadState === "uploading" ? <Loader2 className="spin" size={14} /> : null}
      <button onClick={() => onRemove(attachment.id)} type="button" aria-label={`Remove ${attachment.filename}`}>
        <X size={13} />
      </button>
    </div>
  );
}

function TypingIndicator({ activities, status }: { activities: ChatToolActivity[]; status: string }) {
  return (
    <motion.div
      className="typing-row"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
    >
      {activities.length ? (
        <span className="typing-activities">
          {activities.slice(0, 3).map((activity) => (
            <span className={`typing-activity ${activity.status}`} key={activity.id}>
              {activity.label}
            </span>
          ))}
        </span>
      ) : status ? <span className="typing-status">{status}</span> : null}
      <span className="typing-bubble" aria-label="Alfred is typing">
        <i />
        <i />
        <i />
      </span>
    </motion.div>
  );
}

function publicChatAttachment(attachment: ChatAttachmentDraft): ChatAttachment {
  return {
    id: attachment.id,
    filename: attachment.filename,
    content_type: attachment.content_type,
    size_bytes: attachment.size_bytes,
    kind: attachment.kind,
    url: attachment.url,
    download_url: attachment.download_url,
    source: attachment.source,
    created_at: attachment.created_at
  };
}

function chatClientContext() {
  return {
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    locale: navigator.language
  };
}

function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  const [theme, setThemeState] = React.useState<ThemeMode>(() => (localStorage.getItem("iacs-theme") as ThemeMode | null) ?? "system");

  React.useEffect(() => {
    localStorage.setItem("iacs-theme", theme);
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return [theme, setThemeState];
}

function useProfilePreferences(user: UserAccount | null): [ProfilePreferences, (next: Partial<ProfilePreferences>) => void] {
  const [preferences, setPreferences] = React.useState<ProfilePreferences>(() => {
    try {
      const stored = localStorage.getItem("iacs-profile-preferences");
      return { sidebarCollapsed: stored ? Boolean(JSON.parse(stored).sidebarCollapsed) : false };
    } catch {
      return { sidebarCollapsed: false };
    }
  });

  React.useEffect(() => {
    if (!user?.preferences) return;
    const profilePreferences = {
      sidebarCollapsed: Boolean(user.preferences.sidebarCollapsed)
    };
    setPreferences(profilePreferences);
    localStorage.setItem("iacs-profile-preferences", JSON.stringify(profilePreferences));
  }, [user?.id, user?.preferences]);

  const updatePreferences = React.useCallback((next: Partial<ProfilePreferences>) => {
    setPreferences((current) => {
      const merged = { ...current, ...next };
      localStorage.setItem("iacs-profile-preferences", JSON.stringify(merged));
      if (user) {
        api.patch<UserAccount>("/api/v1/auth/me/preferences", merged).catch(() => undefined);
      }
      return merged;
    });
  }, [user]);

  return [preferences, updatePreferences];
}

async function simulate(path: string, refresh: () => Promise<void>) {
  await api.post(path);
  window.setTimeout(() => refresh().catch(() => undefined), 3200);
}

function visitorPassMatches(visitorPass: VisitorPass, query: string) {
  return (
    matches(visitorPass.visitor_name, query) ||
    matches(visitorPass.number_plate ?? "", query) ||
    matches(visitorPass.vehicle_make ?? "", query) ||
    matches(visitorPass.vehicle_colour ?? "", query) ||
    matches(visitorPass.status, query)
  );
}

function visitorPassMatchesStatus(visitorPass: VisitorPass, filters: Set<VisitorPassStatus>) {
  return !filters.size || filters.size === visitorPassStatuses.length || filters.has(visitorPass.status);
}

function isVisitorPassRealtimeEvent(event: RealtimeMessage) {
  return event.type.startsWith("visitor_pass.");
}

function visitorPassFromRealtime(event: RealtimeMessage): VisitorPass | null {
  const candidate = event.payload.visitor_pass;
  if (!isRecord(candidate)) return null;
  const status = stringPayload(candidate.status) as VisitorPassStatus;
  if (!visitorPassStatuses.includes(status)) return null;
  const id = stringPayload(candidate.id);
  const visitorName = stringPayload(candidate.visitor_name);
  const expectedTime = stringPayload(candidate.expected_time);
  if (!id || !visitorName || !expectedTime) return null;
  return {
    id,
    visitor_name: visitorName,
    expected_time: expectedTime,
    window_minutes: numberPayload(candidate.window_minutes) || 30,
    valid_from: stringPayload(candidate.valid_from) || null,
    valid_until: stringPayload(candidate.valid_until) || null,
    window_start: stringPayload(candidate.window_start),
    window_end: stringPayload(candidate.window_end),
    status,
    creation_source: stringPayload(candidate.creation_source) || "unknown",
    source_reference: stringPayload(candidate.source_reference) || null,
    source_metadata: isRecord(candidate.source_metadata) ? candidate.source_metadata : null,
    created_by_user_id: stringPayload(candidate.created_by_user_id) || null,
    created_by: stringPayload(candidate.created_by) || null,
    arrival_time: stringPayload(candidate.arrival_time) || null,
    departure_time: stringPayload(candidate.departure_time) || null,
    number_plate: stringPayload(candidate.number_plate) || null,
    vehicle_make: stringPayload(candidate.vehicle_make) || null,
    vehicle_colour: stringPayload(candidate.vehicle_colour) || null,
    duration_on_site_seconds: typeof candidate.duration_on_site_seconds === "number" ? candidate.duration_on_site_seconds : null,
    duration_human: stringPayload(candidate.duration_human) || null,
    arrival_event_id: stringPayload(candidate.arrival_event_id) || null,
    departure_event_id: stringPayload(candidate.departure_event_id) || null,
    telemetry_trace_id: stringPayload(candidate.telemetry_trace_id) || null,
    created_at: stringPayload(candidate.created_at),
    updated_at: stringPayload(candidate.updated_at)
  };
}

function visitorPassStatusTone(status: VisitorPassStatus): BadgeTone {
  if (status === "active") return "green";
  if (status === "scheduled") return "blue";
  if (status === "used") return "purple";
  if (status === "cancelled") return "red";
  return "gray";
}

function visitorPassWindowLabel(visitorPass: VisitorPass) {
  return visitorPass.creation_source === "icloud_calendar" ? "Calendar Sync" : `+/- ${visitorPass.window_minutes}m`;
}

function visitorPassSourceLabel(source: string) {
  if (source === "icloud_calendar") return "iCloud Calendar";
  return titleCase(source);
}

function visitorPassVehicleSummary(visitorPass: VisitorPass) {
  const vehicle = [visitorPass.vehicle_colour, visitorPass.vehicle_make].filter(Boolean).join(" ");
  return [vehicle, visitorPass.number_plate].filter(Boolean).join(" - ");
}

function nextVisitorPassDate() {
  const next = new Date();
  next.setMinutes(Math.ceil(next.getMinutes() / 15) * 15, 0, 0);
  if (next.getMinutes() === 60) {
    next.setHours(next.getHours() + 1, 0, 0, 0);
  }
  return next;
}

function visitorDateKey(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function visitorCalendarDays(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - mondayOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    return day;
  });
}

function visitorTimeOptions(selected?: string) {
  const options = Array.from({ length: 96 }, (_, index) => {
    const minutes = index * 15;
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  });
  if (selected && !options.includes(selected)) {
    options.push(selected);
    options.sort();
  }
  return options;
}

function matches(value: string, query: string) {
  return !query.trim() || value.toLowerCase().includes(query.trim().toLowerCase());
}

function titleCase(value: string | null | undefined) {
  return String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function groupCategoryTone(category: string): BadgeTone {
  if (category === "family") return "green";
  if (category === "friends") return "blue";
  if (category === "visitors") return "amber";
  if (category === "contractors") return "gray";
  return "gray";
}

function vehicleTitle(vehicle: Vehicle) {
  return vehicle.description || [vehicle.color, vehicle.make, vehicle.model].filter(Boolean).join(" ") || "Vehicle details pending";
}

function normalizePlateInput(value: string) {
  return value.replace(/[^a-z0-9]/gi, "").toUpperCase();
}

function initials(value: string) {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "");
}

function displayUserName(user: Pick<UserAccount, "first_name" | "last_name" | "full_name">) {
  return `${user.first_name || ""} ${user.last_name || ""}`.trim() || user.full_name;
}

function userInitials(user: Pick<UserAccount, "first_name" | "last_name" | "full_name">) {
  const first = user.first_name?.trim()[0] ?? "";
  const last = user.last_name?.trim()[0] ?? "";
  return (first + last || initials(user.full_name)).toUpperCase();
}

function personInitials(person: Pick<Person, "first_name" | "last_name" | "display_name">) {
  const first = person.first_name?.trim()[0] ?? "";
  const last = person.last_name?.trim()[0] ?? "";
  return (first + last || initials(person.display_name)).toUpperCase();
}

function UserAvatar({ user, size = "normal" }: { user: UserAccount; size?: "normal" | "large" }) {
  return (
    <span className={size === "large" ? "profile-photo large" : "profile-photo"} aria-label={displayUserName(user)}>
      {user.profile_photo_data_url ? <img alt="" src={user.profile_photo_data_url} /> : userInitials(user)}
    </span>
  );
}

function PersonAvatar({ person, size = "normal" }: { person: Person; size?: "normal" | "large" }) {
  return (
    <span className={size === "large" ? "profile-photo large" : "profile-photo"} aria-label={person.display_name}>
      {person.profile_photo_data_url ? <img alt="" src={person.profile_photo_data_url} /> : personInitials(person)}
    </span>
  );
}

function VehiclePhoto({ vehicle, size = "normal" }: { vehicle: Vehicle; size?: "normal" | "large" }) {
  return (
    <span className={size === "large" ? "vehicle-photo large" : "vehicle-photo"} aria-label={vehicle.registration_number}>
      {vehicle.vehicle_photo_data_url ? <img alt="" src={vehicle.vehicle_photo_data_url} /> : <Car size={size === "large" ? 24 : 18} />}
    </span>
  );
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Unable to read profile image"));
    reader.readAsDataURL(file);
  });
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short"
  }).format(new Date(value));
}

function formatCompactLastFired(value?: string | null) {
  if (!value) return "never";
  return formatRelativeTime(value);
}

function formatRelativeTime(value: string) {
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

function formatDateOnly(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(dateOnlyToDate(value));
}

function dateOnlyKey(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;
  return localDateKey(new Date(value));
}

function localDateKey(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function dateOnlyToDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return new Date(value);
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(new Date(value));
}

function formatLongDate(value: Date) {
  const date = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric"
  }).format(value);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(value);
  return `${date} • ${time}`;
}

function greetingForDate(value: Date) {
  const hour = value.getHours();
  if (hour < 12) return "Good Morning";
  if (hour < 17) return "Good Afternoon";
  if (hour < 22) return "Good Evening";
  return "Good Night";
}

function isToday(value: string, now = new Date()) {
  const date = new Date(value);
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  );
}

function clearChatTeaserDismissals() {
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith("iacs-chat-teaser-dismissed")) {
      sessionStorage.removeItem(key);
    }
  }
}

function formatSimulatorDate(value: Date) {
  const date = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric"
  }).format(value);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(value);
  return `${date}  ${time}`;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </React.StrictMode>
);
