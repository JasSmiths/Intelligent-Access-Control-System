import React from "react";
import ReactDOM from "react-dom/client";
import { EditorContent, useEditor } from "@tiptap/react";
import { Mention } from "@tiptap/extension-mention";
import StarterKit from "@tiptap/starter-kit";
import {
  Activity,
  AlertTriangle,
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
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  FileText,
  Gauge,
  GitBranch,
  Home,
  Key,
  KeyRound,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  Play,
  PlugZap,
  Plus,
  RefreshCcw,
  Search,
  Send,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Sun,
  Terminal,
  Trash2,
  Type,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X
} from "lucide-react";
import "./styles.css";

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
};

type Anomaly = {
  id: string;
  type: string;
  severity: "info" | "warning" | "critical";
  message: string;
  created_at: string;
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
  garage_door_entity_ids: string[];
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
  person_id?: string | null;
  owner?: string | null;
  schedule_id?: string | null;
  schedule?: string | null;
  is_active?: boolean;
};

type TimeSlot = {
  id: string;
  name: string;
  kind: string;
  days_of_week: number[] | null;
  start_time: string | null;
  end_time: string | null;
  is_active: boolean;
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

type RealtimeMessage = {
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
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

type HomeAssistantPresenceSuggestion = {
  user_id: string;
  username: string;
  full_name: string;
  suggested_entity_id: string | null;
  suggested_name: string | null;
  confidence: number;
};

type HomeAssistantDiscovery = {
  cover_entities: HomeAssistantEntity[];
  gate_suggestions?: HomeAssistantManagedCover[];
  garage_door_suggestions?: HomeAssistantManagedCover[];
  media_player_entities: HomeAssistantEntity[];
  person_entities: HomeAssistantEntity[];
  presence_mappings: HomeAssistantPresenceSuggestion[];
};

type AppriseUrlSummary = {
  id?: string;
  index: number;
  type: string;
  scheme: string;
  preview: string;
};

type NotificationChannelId = "mobile" | "in_app" | "voice";
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
  preferences: ProfilePreferences & Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type AuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user: UserAccount | null;
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
  | "vehicles"
  | "events"
  | "reports"
  | "integrations"
  | "logs"
  | "settings"
  | "settings_general"
  | "settings_auth"
  | "settings_notifications"
  | "settings_lpr"
  | "users";

const primaryNavItems: Array<{ key: Exclude<ViewKey, "users">; label: string; icon: React.ElementType }> = [
  { key: "dashboard", label: "Dashboard", icon: Home },
  { key: "people", label: "People", icon: UserRound },
  { key: "groups", label: "Groups", icon: Users },
  { key: "schedules", label: "Schedules", icon: Clock3 },
  { key: "vehicles", label: "Vehicles", icon: Car },
  { key: "events", label: "Events", icon: CalendarDays },
  { key: "reports", label: "Reports", icon: BarChart3 },
  { key: "integrations", label: "API & Integrations", icon: PlugZap },
  { key: "logs", label: "Logs", icon: FileText },
  { key: "settings", label: "Settings", icon: Settings }
];

const settingsNavItems: Array<{ key: ViewKey; label: string; icon: React.ElementType }> = [
  { key: "settings_general", label: "General", icon: SlidersHorizontal },
  { key: "settings_auth", label: "Auth & Security", icon: Lock },
  { key: "settings_notifications", label: "Notifications", icon: Bell },
  { key: "settings_lpr", label: "LPR Tuning", icon: Gauge },
  { key: "users", label: "Users", icon: Users }
];

const viewPaths: Record<ViewKey, string> = {
  dashboard: "/",
  people: "/people",
  groups: "/groups",
  schedules: "/schedules",
  vehicles: "/vehicles",
  events: "/events",
  reports: "/reports",
  integrations: "/integrations",
  logs: "/logs",
  settings: "/settings",
  settings_general: "/settings/general",
  settings_auth: "/settings/auth-security",
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
  async delete(path: string): Promise<void> {
    const response = await fetch(path, { method: "DELETE", credentials: "include" });
    if (!response.ok) throw await apiError(response);
  }
};

async function apiError(response: Response) {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const payload = await response.json();
    detail = typeof payload.detail === "string" ? payload.detail : detail;
  } catch {
    // Keep the HTTP status text when the response is not JSON.
  }
  return new Error(detail);
}

function wsUrl(path: string) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${path}`;
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
    anomaly_count: numberPayload(event.payload.anomaly_count)
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
  const [timeSlots, setTimeSlots] = React.useState<TimeSlot[]>([]);
  const [integrationStatus, setIntegrationStatus] = React.useState<IntegrationStatus | null>(null);
  const [realtime, setRealtime] = React.useState<RealtimeMessage[]>([]);
  const [notificationToasts, setNotificationToasts] = React.useState<NotificationToast[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [settingsExpanded, setSettingsExpanded] = React.useState(false);

  const navigateToView = React.useCallback((nextView: ViewKey, options?: { replace?: boolean }) => {
    setView(nextView);
    localStorage.setItem("iacs-active-view", nextView);
    const nextPath = viewPaths[nextView];
    if (window.location.pathname !== nextPath) {
      if (options?.replace) {
        window.history.replaceState({ view: nextView }, "", nextPath);
      } else {
        window.history.pushState({ view: nextView }, "", nextPath);
      }
    }
  }, []);

  React.useEffect(() => {
    const onPopState = () => {
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
    const [nextPresence, nextEvents, nextAnomalies, nextPeople, nextVehicles, nextGroups, nextSchedules, nextSlots, nextStatus] =
      await Promise.all([
        api.get<Presence[]>("/api/v1/presence"),
        api.get<AccessEvent[]>("/api/v1/events?limit=40"),
        api.get<Anomaly[]>("/api/v1/anomalies?limit=30"),
        api.get<Person[]>("/api/v1/people"),
        api.get<Vehicle[]>("/api/v1/vehicles"),
        api.get<Group[]>("/api/v1/groups"),
        api.get<Schedule[]>("/api/v1/schedules"),
        api.get<TimeSlot[]>("/api/v1/time-slots"),
        api.get<IntegrationStatus>("/api/v1/integrations/home-assistant/status")
      ]);
    setPresence(nextPresence);
    setEvents(nextEvents);
    setAnomalies(nextAnomalies);
    setPeople(nextPeople);
    setVehicles(nextVehicles);
    setGroups(nextGroups);
    setSchedules(nextSchedules);
    setTimeSlots(nextSlots);
    setIntegrationStatus(nextStatus);
    setLoading(false);
  }, []);

  const refreshIntegrationStatus = React.useCallback(async () => {
    setIntegrationStatus(await api.get<IntegrationStatus>("/api/v1/integrations/home-assistant/status"));
  }, []);

  React.useEffect(() => {
    if (!authStatus?.authenticated) return;
    refresh().catch(() => setLoading(false));
  }, [authStatus?.authenticated, refresh]);

  React.useEffect(() => {
    if (!authStatus?.authenticated) return;
    const timer = window.setInterval(() => {
      refreshIntegrationStatus().catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [authStatus?.authenticated, refreshIntegrationStatus]);

  React.useEffect(() => {
    if (!authStatus?.authenticated) return;
    const socket = new WebSocket(wsUrl("/api/v1/realtime/ws"));
    socket.onmessage = (event) => {
      const parsed = JSON.parse(event.data) as RealtimeMessage;
      setRealtime((current) => [parsed, ...current].slice(0, 80));
      const notificationToast = notificationToastFromRealtime(parsed);
      if (notificationToast) {
        setNotificationToasts((current) => [notificationToast, ...current].slice(0, 4));
        return;
      }
      if (applyIntegrationRealtimeEvent(parsed, setIntegrationStatus)) {
        return;
      }
      const finalizedEvent = accessEventFromRealtime(parsed);
      if (finalizedEvent) {
        setEvents((current) => [finalizedEvent, ...current.filter((item) => item.id !== finalizedEvent.id)].slice(0, 40));
        refresh().catch(() => undefined);
        return;
      }
      if (parsed.type !== "connection.ready") {
        refresh().catch(() => undefined);
      }
    };
    return () => socket.close();
  }, [authStatus?.authenticated, refresh]);

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
  const settingsActive = view === "settings" || view.startsWith("settings_") || view === "users";

  React.useEffect(() => {
    if (settingsActive && !sidebarCollapsed) {
      setSettingsExpanded(true);
    }
  }, [settingsActive, sidebarCollapsed]);

  if (!authStatus) {
    return <AuthLoading />;
  }

  if (authStatus.setup_required) {
    return <SetupPage onComplete={(user) => setAuthStatus({ setup_required: false, authenticated: true, user })} />;
  }

  if (!authStatus.authenticated || !currentUser) {
    return <LoginPage onLogin={(user) => setAuthStatus({ setup_required: false, authenticated: true, user })} />;
  }

  return (
    <div className={sidebarCollapsed ? "app-shell sidebar-collapsed" : "app-shell"}>
      <aside className="sidebar" id="site-sidebar">
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={28} />
          </div>
          <div>
            <strong>Intelligent</strong>
            <span>Access Control</span>
          </div>
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
                    title={sidebarCollapsed ? item.label : undefined}
                    aria-expanded={settingsExpanded && !sidebarCollapsed}
                  >
                    <Icon size={18} />
                    <span>{item.label}</span>
                    <ChevronDown className="nav-chevron" size={15} />
                  </button>
                  {settingsExpanded && !sidebarCollapsed ? (
                    <div className="nav-submenu">
                      {settingsNavItems.map((subItem) => {
                        const SubIcon = subItem.icon;
                        return (
                          <button
                            className={subItem.key === view ? "nav-item nested active" : "nav-item nested"}
                            key={subItem.key}
                            onClick={() => navigateToView(subItem.key)}
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
                onClick={() => navigateToView(item.key)}
                type="button"
                title={sidebarCollapsed ? item.label : undefined}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <button className="profile-switcher" type="button">
            <UserAvatar user={currentUser} />
            <span>
              <strong>{displayUserName(currentUser)}</strong>
              <small>{currentUser.role === "admin" ? "Owner" : "Standard User"}</small>
            </span>
            <ChevronDown size={16} />
          </button>
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
              aria-expanded={!sidebarCollapsed}
              aria-label={sidebarCollapsed ? "Expand navigation sidebar" : "Collapse navigation sidebar"}
              onClick={() => {
                if (window.matchMedia("(max-width: 720px)").matches) return;
                setProfilePreferences({ sidebarCollapsed: !sidebarCollapsed });
              }}
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
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search people, vehicles, events..." />
            </label>
            <button className="icon-button notification-button" onClick={() => refresh()} type="button" aria-label="Refresh alerts">
              <Bell size={20} />
              {anomalies.length ? <span>{Math.min(anomalies.length, 99)}</span> : null}
            </button>
            <button className="icon-button refresh-button" onClick={() => refresh()} type="button" aria-label="Refresh">
              <RefreshCcw size={17} />
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
            timeSlots={timeSlots}
            integrationStatus={integrationStatus}
            realtime={realtime}
            refresh={refresh}
            currentUser={currentUser}
            onCurrentUserUpdated={(user) =>
              setAuthStatus((current) => current ? { ...current, user } : current)
            }
          />
        )}
      </main>
      <NotificationToastStack
        notifications={notificationToasts}
        onDismiss={(id) => setNotificationToasts((current) => current.filter((item) => item.id !== id))}
      />
      <ChatWidget currentUser={currentUser} />
    </div>
  );
}

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
  timeSlots: TimeSlot[];
  integrationStatus: IntegrationStatus | null;
  realtime: RealtimeMessage[];
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  onCurrentUserUpdated: (user: UserAccount) => void;
}) {
  switch (props.view) {
    case "people":
      return <PeopleView garageDoors={props.integrationStatus?.garage_door_entities ?? []} groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
    case "groups":
      return <GroupsView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} />;
    case "schedules":
      return <SchedulesView schedules={props.schedules} query={props.search} refresh={props.refresh} />;
    case "vehicles":
      return <VehiclesView people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
    case "events":
      return <EventsView events={props.events} query={props.search} />;
    case "reports":
      return <ReportsView events={props.events} presence={props.presence} />;
    case "integrations":
      return <IntegrationsView schedules={props.schedules} status={props.integrationStatus} refresh={props.refresh} />;
    case "logs":
      return <LogsView logs={props.realtime} />;
    case "settings_general":
      return <DynamicSettingsView category="general" title="General Settings" icon={SlidersHorizontal} />;
    case "settings_auth":
      return <DynamicSettingsView category="auth" title="Auth & Security" icon={Lock} />;
    case "settings_notifications":
      return <NotificationsView people={props.people} schedules={props.schedules} />;
    case "settings_lpr":
      return <DynamicSettingsView category="lpr" title="LPR Tuning" icon={Gauge} />;
    case "settings":
      return <SettingsView slots={props.timeSlots} />;
    case "users":
      return <UsersView currentUser={props.currentUser} onCurrentUserUpdated={props.onCurrentUserUpdated} />;
    default:
      return <Dashboard {...props} currentUser={props.currentUser} />;
  }
}

function Dashboard({
  presence,
  events,
  anomalies,
  integrationStatus,
  people,
  vehicles,
  refresh,
  currentUser
}: {
  presence: Presence[];
  events: AccessEvent[];
  anomalies: Anomaly[];
  integrationStatus: IntegrationStatus | null;
  people: Person[];
  vehicles: Vehicle[];
  refresh: () => Promise<void>;
  currentUser: UserAccount;
}) {
  const [now, setNow] = React.useState(() => new Date());
  const [simulatorPlate, setSimulatorPlate] = React.useState("");
  const [pendingCommand, setPendingCommand] = React.useState<DashboardCommand | null>(null);
  const [commandLoading, setCommandLoading] = React.useState(false);
  const [commandError, setCommandError] = React.useState("");
  const present = presence.filter((item) => item.state === "present").length;
  const exited = presence.filter((item) => item.state === "exited").length;
  const unknown = Math.max(presence.length - present - exited, 0);
  const latestEvent = events[0];
  const critical = anomalies.filter((item) => item.severity === "critical").length;
  const displayEvents = getDashboardEvents(events, vehicles, people);
  const displayAnomalies = getDashboardAnomalies(anomalies);
  const expected = Math.max(people.length, presence.length);
  const todayEvents = events.filter((event) => isToday(event.occurred_at, now));
  const exitedToday = todayEvents.filter((event) => event.direction === "exit").length;
  const deniedToday = todayEvents.filter((event) => event.decision === "denied").length;
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  const liveSources = new Set(events.map((event) => event.source).filter(Boolean)).size;
  const gateEntities = activeManagedCovers(integrationStatus?.gate_entities);
  const garageDoorEntities = activeManagedCovers(integrationStatus?.garage_door_entities);
  const topGateState = gateEntities[0]?.state ?? integrationStatus?.current_gate_state ?? integrationStatus?.last_gate_state ?? "unknown";
  const siteStatusTitle = critical ? "Action needed" : deniedToday ? "Attention required" : "All systems normal";
  const siteStatusDetail = critical
    ? `${critical} critical alert${critical === 1 ? "" : "s"}`
    : deniedToday
      ? `${deniedToday} denied attempt${deniedToday === 1 ? "" : "s"} today`
      : "No active alerts";
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
          <div className="site-status-main">
            <ShieldCheck size={54} />
            <div>
              <strong>{siteStatusTitle}</strong>
              <span>{siteStatusDetail}</span>
            </div>
          </div>
          <div className="status-metrics">
            <StatusMetric label="People tracked" value={String(people.length)} />
            <StatusMetric label="Active vehicles" value={String(activeVehicles)} />
            <StatusMetric label="Live sources" value={String(liveSources)} />
          </div>
        </div>

        <div className="card gate-card">
          <PanelHeader title="Status" action="View all" />
          <div className="gate-list">
            {gateEntities.length ? gateEntities.map((gate) => (
              <GateRow
                icon={Car}
                key={gate.entity_id}
                label={gate.name || "Gate"}
                state={commandLoading && pendingCommand?.kind === "gate" ? "opening" : gate.state ?? topGateState}
                onActionClick={commandForGate(gate.name || "Gate", gate.state ?? topGateState, setPendingCommand, setCommandError)}
              />
            )) : (
              <GateRow
                icon={Car}
                label="Top Gate"
                state={commandLoading && pendingCommand?.kind === "gate" ? "opening" : topGateState}
                onActionClick={commandForGate("Top Gate", topGateState, setPendingCommand, setCommandError)}
              />
            )}
            {garageDoorEntities.map((door) => (
              <GarageDoorRow
                key={door.entity_id}
                label={door.name || door.entity_id}
                state={commandLoading && pendingCommand?.kind === "garage_door" && pendingCommand.entity_id === door.entity_id ? inProgressState(pendingCommand.action) : door.state ?? "unknown"}
                onActionClick={commandForGarageDoor(door, setPendingCommand, setCommandError)}
              />
            ))}
            <DoorRow label="Back Door" state={integrationStatus?.back_door_state ?? "unknown"} />
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
                  <Badge tone={event.status === "IN" ? "green" : "gray"}>{event.status}</Badge>
                </div>
              );
            }) : <EmptyState icon={CalendarDays} label="No recent events" />}
          </div>
          <p className="card-footnote">Showing latest 5 events</p>
        </div>

        <div className="card anomaly-card">
          <PanelHeader title="Anomalies" action="View all" />
          <div className="anomaly-feed">
            {displayAnomalies.length ? displayAnomalies.map((item) => (
              <div className="anomaly-feed-row" key={`${item.time}-${item.title}`}>
                <span className={`anomaly-icon ${item.severity}`}>
                  <AlertTriangle size={20} />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.detail}</span>
                </div>
                <time>{item.time}</time>
              </div>
            )) : <EmptyState icon={CheckCircle2} label="No anomalies" />}
          </div>
          <p className="unresolved-count">{anomalies.length} unresolved</p>
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
    </section>
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

function PanelHeader({ title, action, actionKind }: { title: string; action?: string; actionKind?: "link" | "select" }) {
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
          <button className="panel-link" type="button">{action}</button>
        )
      ) : null}
    </div>
  );
}

function StatusMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span><i />{label}</span>
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
  tone: "green" | "blue" | "gray";
  icon: React.ElementType;
};

function getDashboardEvents(events: AccessEvent[], vehicles: Vehicle[], people: Person[]): DashboardEvent[] {
  const peopleById = new Map(people.map((person) => [person.id, person]));
  const vehiclesByRegistration = new Map(vehicles.map((vehicle) => [vehicle.registration_number.toUpperCase(), vehicle]));

  return events.slice(0, 5).map((event) => {
    const vehicle = vehiclesByRegistration.get(event.registration_number.toUpperCase());
    const owner = vehicle?.person_id ? peopleById.get(vehicle.person_id) : undefined;
    const ownerFirstName = owner?.first_name || vehicle?.owner?.split(" ")[0] || "";

    return {
      id: event.id,
      time: formatTime(event.occurred_at),
      label: ownerFirstName || "Unknown",
      subtitle: `${event.registration_number}  •  LPR`,
      status: event.direction === "exit" ? "OUT" : "IN",
      tone: event.decision === "denied" ? "gray" : event.direction === "entry" ? "green" : "blue",
      icon: event.direction === "exit" ? LogOut : event.decision === "denied" ? AlertTriangle : Car
    };
  });
}

type DashboardAnomaly = {
  title: string;
  detail: string;
  time: string;
  severity: "warning" | "critical";
};

function getDashboardAnomalies(anomalies: Anomaly[]): DashboardAnomaly[] {
  return anomalies.slice(0, 4).map((item) => ({
    title: titleCase(item.type),
    detail: item.message,
    time: formatTime(item.created_at),
    severity: item.severity === "critical" ? "critical" : "warning"
  }));
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
            <strong>{event.registration_number}</strong>
            <span>{event.direction} · {event.source}</span>
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
  if (!anomalies.length) return <EmptyState icon={CheckCircle2} label="No anomalies" />;
  return (
    <div className="compact-list">
      {anomalies.map((item) => (
        <div className="compact-row anomaly-row" key={item.id}>
          <AlertTriangle size={18} />
          <div>
            <strong>{item.type.replaceAll("_", " ")}</strong>
            <span>{item.message}</span>
          </div>
          <Badge tone={item.severity === "critical" ? "red" : "amber"}>{item.severity}</Badge>
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
  const filtered = schedules.filter((schedule) =>
    matches(schedule.name, query) ||
    matches(schedule.description ?? "", query) ||
    matches(scheduleSummary(schedule.time_blocks), query)
  );

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

  return (
    <section className="view-stack schedules-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Access Control</span>
          <h1>Schedules</h1>
          <p>Reusable weekly access templates for people, vehicles, gates, and garage doors.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> New Schedule
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      <div className="schedule-card-grid">
        {filtered.length ? filtered.map((schedule) => (
          <article className="card schedule-card" key={schedule.id}>
            <button className="schedule-card-main" onClick={() => openEdit(schedule)} type="button">
              <div className="schedule-card-icon">
                <Clock3 size={18} />
              </div>
              <div>
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
  const availableGarageDoors = React.useMemo(() => activeManagedCovers(garageDoors), [garageDoors]);
  const filtered = people.filter((item) =>
    matches(item.display_name, query) ||
    matches(item.group ?? "", query) ||
    item.vehicles.some((vehicle) => matches(vehicle.registration_number, query)) ||
    (item.garage_door_entity_ids ?? []).some((entityId) => matches(garageDoors.find((door) => door.entity_id === entityId)?.name ?? entityId, query))
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
    is_active: person?.is_active ?? true
	  });
  const [error, setError] = React.useState("");
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
    garage_door_entity_ids: form.garage_door_entity_ids,
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
              <option value="">No schedule - default policy</option>
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
        <PanelHeader title="Fleet Roster" action={`${filtered.length} vehicles`} actionKind="select" />
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
                <div>
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
          mode={selectedVehicle ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          people={people}
          schedules={schedules}
          setPageError={setError}
          vehicle={selectedVehicle}
        />
      ) : null}
    </section>
  );
}

function VehicleModal({
  mode,
  onClose,
  onSaved,
  people,
  schedules,
  setPageError,
  vehicle
}: {
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  people: Person[];
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
    person_id: vehicle?.person_id ?? "",
    schedule_id: vehicle?.schedule_id ?? "",
    is_active: vehicle?.is_active ?? true
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
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
        const make = typeof displayVehicle.make === "string" ? displayVehicle.make : "";
        const model = typeof displayVehicle.model === "string" ? displayVehicle.model : "";
        const color = typeof (displayVehicle.colour ?? displayVehicle.color) === "string" ? String(displayVehicle.colour ?? displayVehicle.color) : "";
        setForm((current) => ({
          ...current,
          registration_number: result.registration_number || current.registration_number,
          make: make || current.make,
          model: model || current.model,
          color: color || current.color
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
    description: vehicle?.description ?? null,
    make: form.make || null,
    model: form.model || null,
    color: form.color || null,
    person_id: form.person_id || null,
    owner: people.find((person) => person.id === form.person_id)?.display_name ?? null,
    schedule_id: form.schedule_id || null,
    schedule: schedules.find((schedule) => schedule.id === form.schedule_id)?.name ?? null,
    is_active: form.is_active
  };

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
            <option value="">Inherit from Owner</option>
            {schedules.map((schedule) => (
              <option key={schedule.id} value={schedule.id}>{schedule.name}</option>
            ))}
          </select>
        </label>
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

function EventsView({ events, query }: { events: AccessEvent[]; query: string }) {
  const filtered = events.filter((item) => matches(item.registration_number, query) || matches(item.source, query));
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
              <th>Anomalies</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((event) => (
              <tr key={event.id}>
                <td><strong>{event.registration_number}</strong></td>
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

function IntegrationsView({ schedules, status, refresh }: { schedules: Schedule[]; status: IntegrationStatus | null; refresh: () => Promise<void> }) {
  const { values, loading, save, reload } = useSettings();
  const [active, setActive] = React.useState<IntegrationDefinition | null>(null);
  const [activeTab, setActiveTab] = React.useState<ProtectIntegrationTab>("general");
  const [protectStatus, setProtectStatus] = React.useState<UnifiProtectStatus | null>(null);
  const [protectCameras, setProtectCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [protectSnapshotRefreshToken, setProtectSnapshotRefreshToken] = React.useState(0);
  const [protectUpdateStatus, setProtectUpdateStatus] = React.useState<UnifiProtectUpdateStatus | null>(null);
  const [protectLoading, setProtectLoading] = React.useState(false);
  const [protectError, setProtectError] = React.useState("");
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
  const reloadSettingsAndProtect = React.useCallback(async () => {
    await reload();
    await loadProtect(true);
    await loadProtectUpdateStatus();
  }, [loadProtect, loadProtectUpdateStatus, reload]);

  React.useEffect(() => {
    loadProtect(false).catch(() => undefined);
    loadProtectUpdateStatus().catch(() => undefined);
  }, [loadProtect, loadProtectUpdateStatus]);

  const tiles = integrationDefinitions(status, values, protectStatus, protectUpdateStatus);
  const groupedTiles = integrationCategories
    .map((category) => ({
      ...category,
      tiles: tiles.filter((tile) => tile.category === category.key)
    }))
    .filter((category) => category.tiles.length);
  return (
    <section className="view-stack integrations-page">
      <Toolbar title="API & Integrations" count={tiles.length} icon={PlugZap} />
      <div className="integration-category-stack">
        {groupedTiles.map((category) => (
          <section className="integration-category" key={category.key}>
            <div className="integration-category-header">
              <div>
                <strong>{category.label}</strong>
                <span>{category.description}</span>
              </div>
              <Badge tone="gray">{category.tiles.length}</Badge>
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
                        setActiveTab(tile.key === "unifi_protect" && tile.updateAvailable ? "updates" : "general");
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
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      <div className="card compact-command-card">
        <CardHeader icon={DoorOpen} title="Gate Command" />
        <button className="primary-button full" onClick={() => api.post("/api/v1/integrations/gate/open", { reason: "Dashboard command" }).finally(refresh)} type="button">
          <KeyRound size={16} /> Open Gate
        </button>
      </div>
      {active ? (
        <IntegrationModal
          definition={active}
          initialTab={active.key === "unifi_protect" ? activeTab : "general"}
          loading={loading}
          protectCameras={protectCameras}
          protectError={protectError || protectStatus?.last_error || ""}
          protectLoading={protectLoading}
          protectStatus={protectStatus}
          protectUpdateStatus={protectUpdateStatus}
          schedules={schedules}
          values={values}
          onClose={() => setActive(null)}
          onProtectUpdateChanged={async () => {
            await loadProtectUpdateStatus();
            await loadProtect(true);
          }}
          onProtectRefresh={() => loadProtect(true)}
          onSettingsChanged={reloadSettingsAndProtect}
          onSaved={async (updates) => {
            await save(updates);
            await loadProtect(true);
            setActive(null);
          }}
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

function integrationDefinitions(
  status: IntegrationStatus | null,
  values: SettingsMap,
  protectStatus: UnifiProtectStatus | null,
  protectUpdateStatus: UnifiProtectUpdateStatus | null
): IntegrationDefinition[] {
  const activeProvider = String(values.llm_provider || "local");
  const providerStatus = (key: string, secretKey?: string): Pick<IntegrationDefinition, "statusLabel" | "statusTone"> => {
    if (activeProvider === key) return { statusLabel: "Connected", statusTone: "green" };
    if (secretKey && values[secretKey]) return { statusLabel: "Configured", statusTone: "blue" };
    if (key === "ollama" && values.ollama_base_url) return { statusLabel: "Configured", statusTone: "blue" };
    return { statusLabel: "Not Configured", statusTone: "gray" };
  };

  const protectUpdateAvailable = Boolean(protectStatus?.connected && protectUpdateStatus?.update_available);

  return [
    {
      key: "home_assistant",
      title: "Home Assistant",
      description: "Gate control, TTS announcements, and state sync.",
      category: "access",
      icon: Home,
      statusLabel: status?.configured ? "Connected" : "Not Configured",
      statusTone: status?.configured ? "green" : "gray",
      notificationChannels: ["voice"],
      fields: [
        { key: "home_assistant_url", label: "URL" },
        { key: "home_assistant_token", label: "Long-lived token", type: "password" },
        { key: "home_assistant_gate_entities", label: "Gate entities" },
        { key: "home_assistant_gate_open_service", label: "Cover open service" },
        { key: "home_assistant_garage_door_entities", label: "Garage doors" },
        { key: "home_assistant_tts_service", label: "TTS service" },
        { key: "home_assistant_default_media_player", label: "Default media player" },
        { key: "home_assistant_presence_entities", label: "Presence mapping" }
      ]
    },
    {
      key: "apprise",
      title: "Apprise",
      description: "Mobile and push notification fan-out.",
      category: "notifications",
      icon: Bell,
      statusLabel: values.apprise_urls ? "Configured" : "Not Configured",
      statusTone: values.apprise_urls ? "green" : "gray",
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
      key: "dvla",
      title: "DVLA Lookup",
      description: "Vehicle Enquiry Service API plate lookups.",
      category: "data",
      icon: Search,
      statusLabel: values.dvla_api_key ? "Configured" : "Not Configured",
      statusTone: values.dvla_api_key ? "green" : "gray",
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
        { key: "llm_provider", label: "Active provider", type: "select", options: ["local", "openai", "gemini", "anthropic", "ollama"] },
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
        { key: "llm_provider", label: "Active provider", type: "select", options: ["local", "openai", "gemini", "anthropic", "ollama"] },
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
        { key: "llm_provider", label: "Active provider", type: "select", options: ["local", "openai", "gemini", "anthropic", "ollama"] },
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
        { key: "llm_provider", label: "Active provider", type: "select", options: ["local", "openai", "gemini", "anthropic", "ollama"] },
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
  schedules,
  onClose,
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
  schedules: Schedule[];
  onClose: () => void;
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
      setForm((current) => {
        const existing = parsePresenceMapping(current.home_assistant_presence_entities);
        const suggested = discovery.presence_mappings.reduce<Record<string, string>>((acc, mapping) => {
          if (mapping.suggested_entity_id && !acc[mapping.full_name]) acc[mapping.full_name] = mapping.suggested_entity_id;
          return acc;
        }, { ...existing });
        return { ...current, home_assistant_presence_entities: JSON.stringify(suggested, null, 2) };
      });
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
        detail: "Delivering through Apprise.",
        activeStep: 1
      });
      await api.post("/api/v1/integrations/notifications/test", {
        subject: "IACS test notification",
        severity: "info",
        message: "This is a test notification from API & Integrations."
      });
      setFeedback({
        tone: "success",
        title: "Test notification sent",
        detail: "Apprise accepted the notification request."
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
        {isUnifiProtect ? (
          <div className="integration-modal-tabs" role="tablist" aria-label="UniFi Protect settings sections">
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
              aria-selected={activeTab === "exposes"}
              className={activeTab === "exposes" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("exposes")}
              role="tab"
              type="button"
            >
              <Activity size={15} /> Exposes
            </button>
            <button
              aria-selected={activeTab === "updates"}
              className={activeTab === "updates" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("updates")}
              role="tab"
              type="button"
            >
              <RefreshCcw size={15} /> Updates
            </button>
          </div>
        ) : null}
        {isUnifiProtect && activeTab === "exposes" ? (
          <UnifiProtectExposesPanel
            cameras={protectCameras ?? []}
            error={protectError ?? ""}
            loading={Boolean(protectLoading)}
            onRefresh={onProtectRefresh ?? onSettingsChanged}
            status={protectStatus ?? null}
          />
        ) : isUnifiProtect && activeTab === "updates" ? (
          <UnifiProtectUpdatesPanel
            status={protectUpdateStatus ?? null}
            onChanged={onProtectUpdateChanged ?? onSettingsChanged}
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
          {isApprise ? (
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
  type HomeAssistantTab = "setup" | "gates" | "garages" | "presence";
  const [activeTab, setActiveTab] = React.useState<HomeAssistantTab>("setup");
  const presenceMapping = parsePresenceMapping(form.home_assistant_presence_entities);
  const gateEntities = parseManagedCovers(form.home_assistant_gate_entities);
  const garageDoorEntities = parseManagedCovers(form.home_assistant_garage_door_entities);
  const tabs: Array<{ key: HomeAssistantTab; label: string; meta: string; icon: React.ElementType }> = [
    { key: "setup", label: "Setup", meta: discovery ? "Discovery ready" : "Credentials", icon: Home },
    { key: "gates", label: "Gates", meta: `${gateEntities.length} configured`, icon: DoorOpen },
    { key: "garages", label: "Garage doors", meta: `${garageDoorEntities.length} configured`, icon: Warehouse },
    { key: "presence", label: "Presence", meta: `${Object.keys(presenceMapping).length} mapped`, icon: Users }
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

  const updatePresenceMapping = (localName: string, entityId: string) => {
    const next = { ...presenceMapping };
    if (entityId) {
      next[localName] = entityId;
    } else {
      delete next[localName];
    }
    onChange("home_assistant_presence_entities", JSON.stringify(next, null, 2));
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

        {activeTab === "presence" ? (
      <section className="presence-mapping-card">
        <div className="presence-mapping-title">
          <strong>Presence mapping</strong>
          <span>Auto-detected from local users and Home Assistant person entities.</span>
        </div>
        {discovery?.presence_mappings.length ? (
          <div className="presence-mapping-list">
            {discovery.presence_mappings.map((mapping) => (
              <div className="presence-mapping-row" key={mapping.user_id}>
                <div>
                  <strong>{mapping.full_name}</strong>
                  <span>
                    {mapping.suggested_entity_id
                      ? `Suggested ${mapping.suggested_entity_id} (${Math.round(mapping.confidence * 100)}%)`
                      : "No confident match found"}
                  </span>
                </div>
                <select
                  value={presenceMapping[mapping.full_name] ?? mapping.suggested_entity_id ?? ""}
                  onChange={(event) => updatePresenceMapping(mapping.full_name, event.target.value)}
                >
                  <option value="">Not mapped</option>
                  {discovery.person_entities.map((entity) => (
                    <option key={entity.entity_id} value={entity.entity_id}>
                      {entity.name ? `${entity.name} - ${entity.entity_id}` : entity.entity_id}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state">No Home Assistant person entities discovered</div>
        )}
      </section>
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
              <option value="">No schedule</option>
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

function LogsView({ logs }: { logs: RealtimeMessage[] }) {
  const [level, setLevel] = React.useState("all");
  const filtered = logs.filter((log) => level === "all" || log.type.includes(level));
  return (
    <section className="view-stack">
      <Toolbar title="Live Logs" count={filtered.length} icon={Terminal}>
        <select value={level} onChange={(event) => setLevel(event.target.value)}>
          <option value="all">All</option>
          <option value="event">Events</option>
          <option value="chat">Chat</option>
          <option value="gate">Gate</option>
        </select>
      </Toolbar>
      <div className="log-console">
        {filtered.map((log, index) => (
          <div className="log-line" key={`${log.type}-${index}`}>
            <time>{log.created_at ? formatDate(log.created_at) : "now"}</time>
            <strong>{log.type}</strong>
            <code>{JSON.stringify(log.payload)}</code>
          </div>
        ))}
      </div>
    </section>
  );
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
    description: "Apprise delivery to mobile, email, chat, and push endpoints."
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
  }
};

const fallbackNotificationTriggers: NotificationTriggerGroup[] = [
  {
    id: "events",
    label: "Events",
    events: [
      { value: "authorized_entry", label: "Authorised Vehicle Detected", severity: "info", description: "A known vehicle is granted entry inside its access policy." },
      { value: "unauthorized_plate", label: "Unauthorised Vehicle Detected", severity: "critical", description: "A plate is denied because it is unknown or inactive." }
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
      { name: "VehicleMake", token: "@VehicleMake", label: "Vehicle make" }
    ]
  },
  {
    group: "Event",
    items: [
      { name: "Time", token: "@Time", label: "Event time" },
      { name: "GateStatus", token: "@GateStatus", label: "Gate status" },
      { name: "Message", token: "@Message", label: "Message" }
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
  VehicleModel: "Model Y Dual Motor Long Range",
  VehicleColor: "Pearl white",
  Time: "18:42",
  GateStatus: "opening",
  Direction: "entry",
  Decision: "granted",
  Source: "Driveway LPR",
  Severity: "Info",
  EventType: "Authorised Entry",
  Subject: "Steph arrived at the gate",
  Message: "Steph arrived in the 2026 Tesla Model Y Dual Motor Long Range."
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
  }
};

function NotificationsView({ people, schedules }: { people: Person[]; schedules: Schedule[] }) {
  const [catalog, setCatalog] = React.useState<NotificationCatalogResponse | null>(null);
  const [rules, setRules] = React.useState<NotificationRule[]>([]);
  const [cameras, setCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [, setSelectedRuleId] = React.useState("");
  const [draft, setDraft] = React.useState<NotificationRule | null>(null);
  const [modal, setModal] = React.useState<"trigger" | "condition" | "action" | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const [feedback, setFeedback] = React.useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [error, setError] = React.useState("");

  const triggerGroups = catalog?.triggers.length ? catalog.triggers : fallbackNotificationTriggers;
  const variableGroups = catalog?.variables.length ? catalog.variables : fallbackNotificationVariables;
  const variables = React.useMemo(() => variableGroups.flatMap((group) => group.items.map((item) => ({ ...item, group: group.group }))), [variableGroups]);
  const triggerOptions = React.useMemo(() => triggerGroups.flatMap((group) => group.events), [triggerGroups]);
  const triggerByValue = React.useMemo(() => new Map(triggerOptions.map((trigger) => [trigger.value, trigger])), [triggerOptions]);
  const activeDraft = draft;
  const previewContext = catalog?.mock_context && Object.keys(catalog.mock_context).length ? catalog.mock_context : mockNotificationContext;
  const previewActions = activeDraft ? renderWorkflowPreview(activeDraft.actions, previewContext) : [];

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
      {feedback ? <div className={`notification-feedback ${feedback.tone}`}>{feedback.text}</div> : null}

      <div className="workflow-notification-shell list-only">
        <NotificationWorkflowList
          activeId={activeDraft?.id ?? ""}
          rules={rules}
          triggerByValue={triggerByValue}
          onAdd={addWorkflow}
          onDelete={deleteRule}
          onSelect={selectRule}
        />
      </div>

      {activeDraft ? (
        <div className="modal-backdrop workflow-editor-backdrop" role="presentation">
          <div className="modal-card workflow-editor-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-editor-title">
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
          </div>
        </div>
      ) : null}

      {activeDraft && modal === "trigger" ? (
        <NotificationTriggerModal
          groups={triggerGroups}
          selected={activeDraft.trigger_event}
          onClose={() => setModal(null)}
          onSelect={(triggerEvent) => {
            updateDraft((rule) => ({ ...rule, trigger_event: triggerEvent, name: rule.name === "New Notification" ? notificationEventLabel(triggerEvent, triggerByValue) : rule.name }));
            setModal(null);
          }}
        />
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

      {activeDraft && modal === "action" ? (
        <NotificationActionModal
          onClose={() => setModal(null)}
          onSelect={(actionType) => {
            updateDraft((rule) => ({ ...rule, actions: [...rule.actions, createWorkflowAction(actionType)] }));
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
  triggerByValue,
  onAdd,
  onDelete,
  onSelect
}: {
  activeId: string;
  rules: NotificationRule[];
  triggerByValue: Map<string, NotificationTriggerOption>;
  onAdd: () => void;
  onDelete: (rule: NotificationRule) => void | Promise<void>;
  onSelect: (rule: NotificationRule) => void;
}) {
  return (
    <aside className="workflow-rule-table card" aria-label="Notification workflows">
      <div className="workflow-rule-table-header">
        <div>
          <strong>Workflows</strong>
          <span>DB-backed rules only</span>
        </div>
        <button className="icon-button" onClick={onAdd} type="button" aria-label="Add notification workflow">
          <Plus size={16} />
        </button>
      </div>
      {rules.length ? (
        <div className="workflow-rule-rows">
          {rules.map((rule) => {
            const active = activeId === rule.id;
            return (
              <article className={active ? "workflow-rule-row active" : "workflow-rule-row"} key={rule.id}>
                <button onClick={() => onSelect(rule)} type="button">
                  <span>
                    <strong>{rule.name}</strong>
                    <small>{notificationEventLabel(rule.trigger_event, triggerByValue)}</small>
                  </span>
                  <Badge tone={rule.is_active ? "green" : "gray"}>{rule.is_active ? "Active" : "Paused"}</Badge>
                  <span className="workflow-rule-icons" aria-label="Workflow summary">
                    <Badge tone={rule.conditions.length ? "amber" : "gray"}>{rule.conditions.length} if</Badge>
                    <Badge tone={rule.actions.length ? "blue" : "red"}>{rule.actions.length} then</Badge>
                  </span>
                </button>
                <button className="icon-button danger" onClick={() => onDelete(rule)} type="button" aria-label={`Delete ${rule.name}`}>
                  <Trash2 size={15} />
                </button>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="notification-empty-list workflow-empty-list">
          <Bell size={20} />
          <strong>No notification workflows</strong>
          <span>Start from a clean workflow table.</span>
          <button className="primary-button" onClick={onAdd} type="button">
            <Plus size={15} /> Add Notification
          </button>
        </div>
      )}
    </aside>
  );
}

function NotificationWorkflowEditor({
  cameras,
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
            <button className="secondary-button" onClick={onCancel} type="button">
              Cancel
            </button>
            <button className="secondary-button" onClick={onSendTest} disabled={testing} type="button">
              <Send size={15} /> {testing ? "Sending..." : "Send Test"}
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
  const supportsMedia = action.type === "mobile" || action.type === "in_app";
  const targetLabel = action.type === "voice" ? "Voice targets" : action.type === "mobile" ? "Apprise targets" : "Dashboard targets";
  const selectedCamera = cameras.find((camera) => camera.id === action.media.camera_id);
  const cameraSnapshotUrl = selectedCamera
    ? `/api/v1/integrations/unifi-protect/cameras/${selectedCamera.id}/snapshot?width=320&height=180`
    : "";
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

      <div className="workflow-action-grid">
        <label className="field compact-field">
          <span>{targetLabel}</span>
          <select value={action.target_mode} onChange={(event) => onChange({ ...action, target_mode: event.target.value as NotificationTargetMode })}>
            <option value="all">All</option>
            {action.type === "voice" ? <option value="many">Many</option> : null}
            <option value="selected">Specific target</option>
          </select>
        </label>
        {action.target_mode !== "all" ? (
          <EndpointMultiSelect
            action={action}
            endpoints={integration?.endpoints ?? []}
            onChange={(target_ids) => onChange({ ...action, target_ids })}
          />
        ) : (
          <div className="workflow-target-summary">
            <Badge tone={integration?.configured ? meta.tone : "gray"}>{integration?.configured ? "Configured" : "Unavailable"}</Badge>
            <span>{integration?.endpoints.length ? `${integration.endpoints.length} endpoint${integration.endpoints.length === 1 ? "" : "s"}` : "No endpoints discovered"}</span>
          </div>
        )}
      </div>

      {supportsTitle ? (
        <VariableRichTextEditor
          label="Title"
          value={action.title_template}
          variables={variables}
          onChange={(title_template) => onChange({ ...action, title_template })}
        />
      ) : null}
      <VariableRichTextEditor
        label={action.type === "voice" ? "Spoken message" : "Message"}
        multiline
        value={action.message_template}
        variables={variables}
        onChange={(message_template) => onChange({ ...action, message_template })}
      />

      {supportsMedia ? (
        <section className="workflow-media-row">
          <label className={action.media.attach_camera_snapshot ? "notification-switch active" : "notification-switch"}>
            <input
              checked={action.media.attach_camera_snapshot}
              onChange={(event) => onChange({ ...action, media: { ...action.media, attach_camera_snapshot: event.target.checked } })}
              type="checkbox"
            />
            <span>Camera Screenshot</span>
          </label>
          {action.media.attach_camera_snapshot ? (
            <select value={action.media.camera_id} onChange={(event) => onChange({ ...action, media: { ...action.media, camera_id: event.target.value } })}>
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

function EndpointMultiSelect({
  action,
  endpoints,
  onChange
}: {
  action: NotificationAction;
  endpoints: NotificationEndpoint[];
  onChange: (targetIds: string[]) => void;
}) {
  const selected = new Set(action.target_ids);
  const toggle = (endpointId: string) => {
    const next = new Set(selected);
    if (next.has(endpointId)) next.delete(endpointId);
    else next.add(endpointId);
    onChange(Array.from(next));
  };
  if (!endpoints.length) {
    return <div className="workflow-target-summary"><span>No endpoints available</span></div>;
  }
  return (
    <div className="workflow-endpoint-picks">
      {endpoints.filter((endpoint) => !endpoint.id.endsWith(":*")).map((endpoint) => (
        <label className="workflow-endpoint-pick" key={endpoint.id}>
          <input checked={selected.has(endpoint.id)} onChange={() => toggle(endpoint.id)} type="checkbox" />
          <span>
            <strong>{endpoint.label}</strong>
            <small>{endpoint.detail}</small>
          </span>
        </label>
      ))}
    </div>
  );
}

function VariableRichTextEditor({
  label,
  multiline = false,
  value,
  variables,
  onChange
}: {
  label: string;
  multiline?: boolean;
  value: string;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (value: string) => void;
}) {
  const [suggestion, setSuggestion] = React.useState<{ query: string; from: number; to: number } | null>(null);
  const valueRef = React.useRef(value);
  const variablesRef = React.useRef(variables);
  variablesRef.current = variables;

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        blockquote: false,
        bulletList: false,
        codeBlock: false,
        heading: false,
        horizontalRule: false,
        orderedList: false
      }),
      Mention.configure({
        HTMLAttributes: { class: "variable-pill" },
        renderText({ node }) {
          return `@${node.attrs.label ?? node.attrs.id}`;
        },
        renderHTML({ node }) {
          return ["span", { class: "variable-pill", "data-variable": node.attrs.label ?? node.attrs.id }, `@${node.attrs.label ?? node.attrs.id}`];
        }
      })
    ],
    content: templateToTiptapDoc(value, variables),
    editorProps: {
      attributes: {
        class: multiline ? "variable-editor-content multiline" : "variable-editor-content"
      }
    },
    onUpdate({ editor: activeEditor }) {
      const next = tiptapDocToTemplate(activeEditor.getJSON());
      valueRef.current = next;
      onChange(next);
      setSuggestion(findMentionSuggestion(activeEditor));
    },
    onSelectionUpdate({ editor: activeEditor }) {
      setSuggestion(findMentionSuggestion(activeEditor));
    }
  }, []);

  React.useEffect(() => {
    if (!editor || value === valueRef.current) return;
    valueRef.current = value;
    editor.commands.setContent(templateToTiptapDoc(value, variablesRef.current), { emitUpdate: false });
  }, [editor, value]);

  React.useEffect(() => {
    if (!editor) return undefined;
    const element = editor.view.dom;
    const onClick = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest(".variable-pill") : null;
      if (!target) return;
      const pos = editor.view.posAtDOM(target, 0);
      editor.commands.setTextSelection({ from: pos, to: pos + 1 });
      setSuggestion({ query: "", from: pos, to: pos + 1 });
    };
    element.addEventListener("click", onClick);
    return () => element.removeEventListener("click", onClick);
  }, [editor]);

  const filtered = React.useMemo(() => {
    const query = suggestion?.query.toLowerCase() ?? "";
    return variables.filter((variable) => `${variable.name} ${variable.label} ${variable.group}`.toLowerCase().includes(query)).slice(0, 10);
  }, [suggestion?.query, variables]);

  const insertVariable = (variable: NotificationVariable) => {
    if (!editor || !suggestion) return;
    editor.chain().focus().deleteRange({ from: suggestion.from, to: suggestion.to }).insertContent({ type: "mention", attrs: { id: variable.name, label: variable.name } }).insertContent(" ").run();
    setSuggestion(null);
  };

  return (
    <label className="field variable-editor-field">
      <span>{label}</span>
      <div className="variable-editor-wrap">
        <EditorContent editor={editor} />
        {suggestion && filtered.length ? (
          <div className="variable-suggestion-menu">
            {groupVariables(filtered).map((group) => (
              <div className="variable-suggestion-group" key={group.group}>
                <strong>{group.group}</strong>
                {group.items.map((variable) => (
                  <button key={variable.name} onMouseDown={(event) => event.preventDefault()} onClick={() => insertVariable(variable)} type="button">
                    <code>{variable.token}</code>
                    <span>{variable.label}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </label>
  );
}

function NotificationLivePreviewPanel({
  actions
}: {
  actions: Array<NotificationAction & { title: string; message: string }>;
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

function NotificationTriggerModal({
  groups,
  selected,
  onClose,
  onSelect
}: {
  groups: NotificationTriggerGroup[];
  selected: string;
  onClose: () => void;
  onSelect: (triggerEvent: string) => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card notification-add-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-trigger-title">
        <div className="modal-header">
          <div>
            <h2 id="workflow-trigger-title">Add Trigger</h2>
            <p>Select one event that starts this workflow.</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close trigger selector"><X size={16} /></button>
        </div>
        <div className="notification-add-groups">
          {groups.map((group) => (
            <section className="notification-add-group" key={group.id}>
              <div className="notification-subtitle">
                <strong>{group.label}</strong>
                <Badge tone="gray">{group.events.length}</Badge>
              </div>
              <div>
                {group.events.map((event) => (
                  <button className={selected === event.value ? "notification-add-option configured" : "notification-add-option"} key={event.value} onClick={() => onSelect(event.value)} type="button">
                    <span>
                      <strong>{event.label}</strong>
                      <small>{event.description}</small>
                    </span>
                    <Badge tone={selected === event.value ? "green" : notificationSeverityTone(event.severity)}>{selected === event.value ? "Selected" : titleCase(event.severity)}</Badge>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
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
  onClose,
  onSelect
}: {
  onClose: () => void;
  onSelect: (actionType: NotificationActionType) => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card notification-add-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-action-title">
        <div className="modal-header">
          <div>
            <h2 id="workflow-action-title">Add Action</h2>
            <p>Stack one or more outputs for this workflow.</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close action selector"><X size={16} /></button>
        </div>
        <div className="notification-add-groups">
          <section className="notification-add-group">
            <div className="notification-subtitle"><strong>Outputs</strong><Badge tone="gray">3</Badge></div>
            <div>
              {(["mobile", "voice", "in_app"] as NotificationActionType[]).map((actionType) => {
                const meta = notificationChannelMeta[actionType];
                const Icon = meta.icon;
                return (
                  <button className="notification-add-option" key={actionType} onClick={() => onSelect(actionType)} type="button">
                    <span><strong>{meta.label}</strong><small>{meta.description}</small></span>
                    <Icon size={18} />
                  </button>
                );
              })}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
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

function createWorkflowAction(type: NotificationActionType): NotificationAction {
  const templates = defaultWorkflowActionTemplates[type];
  return {
    id: draftId("action"),
    type,
    target_mode: "all",
    target_ids: [],
    title_template: templates.title_template,
    message_template: templates.message_template,
    media: { attach_camera_snapshot: false, camera_id: "" }
  };
}

function cloneNotificationRule(rule: NotificationRule): NotificationRule {
  return JSON.parse(JSON.stringify(rule)) as NotificationRule;
}

function workflowRulePayload(rule: NotificationRule) {
  return {
    name: rule.name.trim() || "Notification Workflow",
    trigger_event: rule.trigger_event,
    conditions: rule.conditions,
    actions: rule.actions,
    is_active: rule.is_active
  };
}

function notificationEventLabel(value: string, triggerByValue?: Map<string, NotificationTriggerOption>) {
  return triggerByValue?.get(value)?.label ?? titleCase(value);
}

function notificationSeverityTone(value: string): BadgeTone {
  if (value === "critical") return "red";
  if (value === "warning") return "amber";
  if (value === "info") return "blue";
  return "gray";
}

function renderWorkflowPreview(actions: NotificationAction[], context: Record<string, string>) {
  return actions.map((action) => ({
    ...action,
    title: renderWorkflowTemplate(action.title_template, context),
    message: renderWorkflowTemplate(action.message_template, context)
  }));
}

function renderWorkflowTemplate(template: string, context: Record<string, string>) {
  return template.replace(/@([A-Za-z][A-Za-z0-9_]*)/g, (_, token: string) => context[token] ?? "").trim();
}

function templateToTiptapDoc(template: string, variables: Array<NotificationVariable & { group?: string }>) {
  const names = new Set(variables.map((variable) => variable.name));
  const paragraphs = (template || "").split(/\n/);
  return {
    type: "doc",
    content: paragraphs.map((paragraph) => ({
      type: "paragraph",
      content: templateLineToTiptap(paragraph, names)
    }))
  };
}

function templateLineToTiptap(line: string, names: Set<string>) {
  const content: Array<Record<string, unknown>> = [];
  const pattern = /@([A-Za-z][A-Za-z0-9_]*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line))) {
    if (match.index > lastIndex) content.push({ type: "text", text: line.slice(lastIndex, match.index) });
    if (names.has(match[1])) {
      content.push({ type: "mention", attrs: { id: match[1], label: match[1] } });
    } else {
      content.push({ type: "text", text: match[0] });
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < line.length) content.push({ type: "text", text: line.slice(lastIndex) });
  return content.length ? content : undefined;
}

function tiptapDocToTemplate(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const raw = node as { type?: string; text?: string; attrs?: Record<string, unknown>; content?: unknown[] };
  if (raw.type === "text") return raw.text ?? "";
  if (raw.type === "mention") return `@${String(raw.attrs?.label ?? raw.attrs?.id ?? "")}`;
  const children = raw.content?.map(tiptapDocToTemplate) ?? [];
  if (raw.type === "doc") return children.join("\n");
  if (raw.type === "paragraph") return children.join("");
  return children.join("");
}

function findMentionSuggestion(editor: NonNullable<ReturnType<typeof useEditor>>) {
  const { from } = editor.state.selection;
  const start = Math.max(1, from - 48);
  const text = editor.state.doc.textBetween(start, from, "\n", " ");
  const match = text.match(/(?:^|\s)@([A-Za-z0-9_]*)$/);
  if (!match) return null;
  const query = match[1];
  return { query, from: from - query.length - 1, to: from };
}

function groupVariables(variables: Array<NotificationVariable & { group: string }>) {
  const grouped = new Map<string, Array<NotificationVariable & { group: string }>>();
  for (const variable of variables) {
    const rows = grouped.get(variable.group) ?? [];
    rows.push(variable);
    grouped.set(variable.group, rows);
  }
  return Array.from(grouped.entries()).map(([group, items]) => ({ group, items }));
}

function draftId(prefix: string) {
  return `draft-${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function SettingsView({ slots }: { slots: TimeSlot[] }) {
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
          <div className="avatar">F</div>
          <div>
            <strong>Dashboard logins</strong>
            <span>Local auth phase</span>
          </div>
          <Badge tone="amber">pending</Badge>
        </div>
      </div>
      <div className="card span-3">
        <CardHeader icon={Clock3} title="Time Slots" action={<Badge tone="blue">{slots.length}</Badge>} />
        <div className="slot-grid">
          {slots.map((slot) => (
            <div className="slot-tile" key={slot.id}>
              <strong>{slot.name}</strong>
              <span>{slot.kind}</span>
              <Badge tone={slot.is_active ? "green" : "gray"}>{slot.is_active ? "active" : "inactive"}</Badge>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function DynamicSettingsView({
  category,
  title,
  icon: Icon
}: {
  category: "general" | "auth" | "lpr";
  title: string;
  icon: React.ElementType;
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

function UsersView({
  currentUser,
  onCurrentUserUpdated
}: {
  currentUser: UserAccount;
  onCurrentUserUpdated: (user: UserAccount) => void;
}) {
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [modal, setModal] = React.useState<"create" | "edit" | null>(null);
  const [selectedUser, setSelectedUser] = React.useState<UserAccount | null>(null);
  const [temporaryPassword, setTemporaryPassword] = React.useState<string | null>(null);
  const isAdmin = currentUser.role === "admin";

  const loadUsers = React.useCallback(async () => {
    setError("");
    try {
      setUsers(await api.get<UserAccount[]>("/api/v1/users"));
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
                  <span>@{user.username}{user.email ? ` • ${user.email}` : ""}</span>
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
  user,
  onClose,
  onSaved
}: {
  mode: "create" | "edit";
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

type BadgeTone = "green" | "gray" | "amber" | "red" | "blue";

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
  "dvla_api_key",
  "unifi_protect_username",
  "unifi_protect_password",
  "unifi_protect_api_key",
  "openai_api_key",
  "gemini_api_key",
  "anthropic_api_key"
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
    { key: "lpr_similarity_threshold", label: "Similarity threshold", type: "number", min: 0, max: 1, step: 0.01 }
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
    home_assistant_garage_door_entities: "[]"
  };
  return definition.fields.reduce<Record<string, string>>((acc, field) => {
    const current = values[field.key];
    if (secretSettingKeys.has(field.key)) {
      acc[field.key] = "";
    } else if (["home_assistant_presence_entities", "home_assistant_gate_entities", "home_assistant_garage_door_entities"].includes(field.key) && typeof current === "object") {
      acc[field.key] = JSON.stringify(current ?? {}, null, 2);
    } else {
      acc[field.key] = stringifySetting(current || defaults[field.key] || "");
    }
    return acc;
  }, {});
}

function stringifySetting(value: unknown) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object" && value !== null) return JSON.stringify(value, null, 2);
  return value == null ? "" : String(value);
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function parsePresenceMapping(value: unknown): Record<string, string> {
  if (!value) return {};
  if (typeof value === "object" && !Array.isArray(value)) return value as Record<string, string>;
  if (typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? parsed as Record<string, string> : {};
  } catch {
    return {};
  }
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
      key === "unifi_protect_username" ||
      key === "unifi_protect_password"
    ) {
      if (!value.trim()) continue;
    }
    if (key === "home_assistant_presence_entities") {
      try {
        payload[key] = value.trim() ? JSON.parse(value) : {};
      } catch {
        payload[key] = {};
      }
    } else if (key === "home_assistant_gate_entities" || key === "home_assistant_garage_door_entities") {
      try {
        const parsed = value.trim() ? JSON.parse(value) : [];
        payload[key] = Array.isArray(parsed) ? parsed : [];
      } catch {
        payload[key] = [];
      }
    } else if (["auth_cookie_secure", "unifi_protect_verify_ssl"].includes(key)) {
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
  return <span className={`badge ${tone}`}>{children}</span>;
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

function ChatWidget({ currentUser }: { currentUser: UserAccount }) {
  const [open, setOpen] = React.useState(false);
  const teaserStorageKey = `iacs-chat-teaser-dismissed:${currentUser.id}`;
  const [showTeaser, setShowTeaser] = React.useState(() => sessionStorage.getItem(teaserStorageKey) !== "true");
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<Array<{ role: "user" | "assistant"; text: string }>>([
    { role: "assistant", text: "Site agent ready." }
  ]);
  const [draft, setDraft] = React.useState("");
  const socketRef = React.useRef<WebSocket | null>(null);
  const firstName = currentUser.first_name || displayUserName(currentUser).split(" ")[0] || "there";

  React.useEffect(() => {
    setShowTeaser(sessionStorage.getItem(teaserStorageKey) !== "true");
  }, [teaserStorageKey]);

  const dismissTeaser = () => {
    sessionStorage.setItem(teaserStorageKey, "true");
    setShowTeaser(false);
  };

  React.useEffect(() => {
    if (!open || socketRef.current) return;
    const socket = new WebSocket(wsUrl("/api/v1/ai/chat/ws"));
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "chat.response") {
        setSessionId(data.payload.session_id);
        setMessages((current) => [...current, { role: "assistant", text: data.payload.text }]);
      }
    };
    socket.onclose = () => {
      socketRef.current = null;
    };
    socketRef.current = socket;
    return () => socket.close();
  }, [open]);

  const sendMessage = () => {
    const message = draft.trim();
    const socket = socketRef.current;
    if (!message || !socket || socket.readyState !== WebSocket.OPEN) return;
    setMessages((current) => [...current, { role: "user", text: message }]);
    socket.send(JSON.stringify({ message, session_id: sessionId }));
    setDraft("");
  };

  return (
    <div className={open ? "chat-widget open" : "chat-widget"}>
      {open ? (
        <div className="chat-panel">
          <div className="chat-header">
            <div className="card-title">
              <Bot size={18} />
              <h2>Chat with me</h2>
            </div>
            <button className="icon-button" onClick={() => setOpen(false)} type="button" aria-label="Close chat">
              <X size={16} />
            </button>
          </div>
          <div className="chat-feed">
            {messages.map((message, index) => (
              <div className={`chat-bubble ${message.role}`} key={`${message.role}-${index}`}>
                {message.text}
              </div>
            ))}
          </div>
          <div className="chat-input">
            <input value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => event.key === "Enter" && sendMessage()} placeholder="Ask about presence" />
            <button className="icon-button send" onClick={sendMessage} type="button" aria-label="Send">
              <Send size={17} />
            </button>
          </div>
        </div>
      ) : null}
      {!open && showTeaser ? (
        <div className="chat-teaser">
          <button className="teaser-close" onClick={dismissTeaser} type="button" aria-label="Dismiss chat prompt">
            <X size={16} />
          </button>
          <strong>Hi {firstName}!</strong>
          <p>Need help with something? I can help you check events, run reports, and more.</p>
        </div>
      ) : null}
      {!open ? (
        <button className="chat-pill" onClick={() => setOpen(true)} type="button">
          <MessageCircle size={18} />
          Chat with me
        </button>
      ) : null}
    </div>
  );
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
  return [vehicle.color, vehicle.make, vehicle.model].filter(Boolean).join(" ") || vehicle.description || "Vehicle details pending";
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
    <App />
  </React.StrictMode>
);
