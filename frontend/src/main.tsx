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
import "./styles.css";

import {
  AccessEvent,
  alertSeverityLabel,
  alertSeverityTone,
  Anomaly,
  api,
  Badge,
  displayUserName,
  EmptyState,
  formatDate,
  Group,
  HomeAssistantManagedCover,
  IntegrationStatus,
  isActionableAlert,
  isRecord,
  MaintenanceStatus,
  NavigateToView,
  notificationEventLabel,
  nullableString,
  numberPayload,
  Person,
  Presence,
  ProfilePreferences,
  RealtimeMessage,
  Schedule,
  stringPayload,
  titleCase,
  UserAccount,
  UserAvatar,
  Vehicle,
  ViewKey,
  wsUrl
} from "./shared";

const Dashboard = React.lazy(() => import("./views/DashboardView").then((module) => ({ default: module.Dashboard })));
const GroupsView = React.lazy(() => import("./views/DirectoryViews").then((module) => ({ default: module.GroupsView })));
const PeopleView = React.lazy(() => import("./views/DirectoryViews").then((module) => ({ default: module.PeopleView })));
const VehiclesView = React.lazy(() => import("./views/DirectoryViews").then((module) => ({ default: module.VehiclesView })));
const SchedulesView = React.lazy(() => import("./views/SchedulesView").then((module) => ({ default: module.SchedulesView })));
const PassesView = React.lazy(() => import("./views/PassesView").then((module) => ({ default: module.PassesView })));
const TopChartsView = React.lazy(() => import("./views/TopChartsView").then((module) => ({ default: module.TopChartsView })));
const EventsView = React.lazy(() => import("./views/EventsView").then((module) => ({ default: module.EventsView })));
const AlertsView = React.lazy(() => import("./views/AlertsView").then((module) => ({ default: module.AlertsView })));
const ReportsView = React.lazy(() => import("./views/ReportsView").then((module) => ({ default: module.ReportsView })));
const IntegrationsView = React.lazy(() => import("./views/IntegrationsView").then((module) => ({ default: module.IntegrationsView })));
const LogsView = React.lazy(() => import("./views/LogsView").then((module) => ({ default: module.LogsView })));
const AlfredTrainingView = React.lazy(() => import("./views/AlfredTrainingView").then((module) => ({ default: module.AlfredTrainingView })));
const AutomationsView = React.lazy(() => import("./views/WorkflowViews").then((module) => ({ default: module.AutomationsView })));
const NotificationsView = React.lazy(() => import("./views/WorkflowViews").then((module) => ({ default: module.NotificationsView })));
const SettingsView = React.lazy(() => import("./views/SettingsViews").then((module) => ({ default: module.SettingsView })));
const DynamicSettingsView = React.lazy(() => import("./views/SettingsViews").then((module) => ({ default: module.DynamicSettingsView })));
const UsersView = React.lazy(() => import("./views/SettingsViews").then((module) => ({ default: module.UsersView })));
const ChatWidget = React.lazy(() => import("./views/ChatWidgetView").then((module) => ({ default: module.ChatWidget })));

function RouteLoading() {
  return <div className="loading-panel">Loading view</div>;
}

const REALTIME_REFRESH_MIN_INTERVAL_MS = 5000;

const REALTIME_DATA_REFRESH_EVENTS = new Set([
  "access_event.finalize_failed",
  "alerts.updated",
  "visitor_pass.created",
  "visitor_pass.updated",
  "visitor_pass.cancelled",
  "visitor_pass.deleted",
  "visitor_pass.status_changed",
  "visitor_pass.used",
  "visitor_pass.departure_recorded"
]);

type NotificationToast = {
  id: string;
  title: string;
  body: string;
  event_type: string;
  severity: "info" | "warning" | "critical";
  snapshot_url?: string;
  actions?: NotificationToastAction[];
};

type NotificationToastAction = {
  id: string;
  label: string;
  method: "POST";
  path: string;
};

type AuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user: UserAccount | null;
};

type ThemeMode = "system" | "light" | "dark";

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

const settingsNavItems: Array<{ key: ViewKey; label: string; icon: React.ElementType; adminOnly?: boolean }> = [
  { key: "settings_general", label: "General", icon: SlidersHorizontal },
  { key: "settings_auth", label: "Auth & Security", icon: Lock },
  { key: "alfred_training", label: "Alfred Training", icon: Bot, adminOnly: true },
  { key: "settings_automations", label: "Automations", icon: GitBranch },
  { key: "settings_notifications", label: "Notifications", icon: Bell },
  { key: "settings_lpr", label: "LPR Tuning", icon: Gauge },
  { key: "users", label: "Users", icon: Users, adminOnly: true }
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
  alfred_training: "/settings/alfred-training",
  settings_automations: "/settings/automations",
  settings_notifications: "/settings/notifications",
  settings_lpr: "/settings/lpr-tuning",
  users: "/settings/users"
};

const pathViews = Object.entries(viewPaths).reduce<Record<string, ViewKey>>((acc, [viewKey, path]) => {
  acc[path] = viewKey as ViewKey;
  return acc;
}, {});

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
    visitor_pass_mode: stringPayload(event.payload.visitor_pass_mode) || null,
    snapshot_url: stringPayload(event.payload.snapshot_url) || null,
    snapshot_captured_at: stringPayload(event.payload.snapshot_captured_at) || null,
    snapshot_bytes: nullableNumber(event.payload.snapshot_bytes),
    snapshot_width: nullableNumber(event.payload.snapshot_width),
    snapshot_height: nullableNumber(event.payload.snapshot_height),
    snapshot_camera: stringPayload(event.payload.snapshot_camera) || null
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
  const actions = notificationToastActions(event.payload.actions);
  if (!title && !body) return null;
  return {
    id: `${event.created_at ?? Date.now()}-${eventType}-${Math.random().toString(16).slice(2)}`,
    title: title || titleCase(eventType),
    body,
    event_type: eventType,
    severity: isNotificationSeverity(severity) ? severity : "info",
    snapshot_url: snapshotUrl || undefined,
    actions: actions.length ? actions : undefined,
  };
}

function notificationToastActions(value: unknown): NotificationToastAction[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const method = stringPayload(item.method).toUpperCase();
    const action = {
      id: stringPayload(item.id),
      label: stringPayload(item.label),
      method: method === "POST" ? "POST" as const : null,
      path: stringPayload(item.path),
    };
    return action.id && action.label && action.method && action.path ? [action as NotificationToastAction] : [];
  });
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
  onAction,
  onDismiss
}: {
  notifications: NotificationToast[];
  onAction: (notificationId: string, action: NotificationToastAction) => Promise<void>;
  onDismiss: (id: string) => void;
}) {
  const [busyAction, setBusyAction] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<Record<string, string>>({});
  React.useEffect(() => {
    if (!notifications.length) return undefined;
    const timers = notifications.filter((notification) => !notification.actions?.length).map((notification) =>
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
            {notification.actions?.length ? (
              <div className="notification-toast-actions">
                {notification.actions.map((action) => {
                  const actionKey = `${notification.id}:${action.id}`;
                  return (
                    <button
                      className={action.id === "deny" ? "secondary-button danger" : "secondary-button"}
                      disabled={busyAction !== null}
                      key={action.id}
                      onClick={() => {
                        setBusyAction(actionKey);
                        setActionError((current) => ({ ...current, [notification.id]: "" }));
                        onAction(notification.id, action)
                          .catch((error) => setActionError((current) => ({
                            ...current,
                            [notification.id]: error instanceof Error ? error.message : "Unable to complete action"
                          })))
                          .finally(() => setBusyAction(null));
                      }}
                      type="button"
                    >
                      {busyAction === actionKey ? "Working..." : action.label}
                    </button>
                  );
                })}
              </div>
            ) : null}
            {actionError[notification.id] ? <small className="notification-toast-error">{actionError[notification.id]}</small> : null}
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

function nullableNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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

function isBellAlert(alert: Anomaly) {
  return isActionableAlert(alert) && alert.type !== "unauthorized_plate";
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

function clearChatTeaserDismissals() {
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith("iacs-chat-teaser-dismissed")) {
      sessionStorage.removeItem(key);
    }
  }
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

  const handleNotificationAction = React.useCallback(async (notificationId: string, action: NotificationToastAction) => {
    if (action.method !== "POST") return;
    await api.post(action.path);
    setNotificationToasts((current) => current.filter((item) => item.id !== notificationId));
    refresh().catch(() => undefined);
  }, [refresh]);

  const realtimeRefreshLastRunRef = React.useRef(0);

  const refreshFromRealtime = React.useCallback(() => {
    const now = Date.now();
    if (now - realtimeRefreshLastRunRef.current < REALTIME_REFRESH_MIN_INTERVAL_MS) return;
    realtimeRefreshLastRunRef.current = now;
    refresh().catch(() => undefined);
  }, [refresh]);

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

  React.useEffect(() => {
    if (currentUser?.role !== "admin" && view === "users") {
      navigateToView("settings", { replace: true });
    }
  }, [currentUser?.role, navigateToView, view]);

  const sidebarCollapsed = profilePreferences.sidebarCollapsed;
  const navigationCollapsed = !isMobileNavigation && sidebarCollapsed;
  const navigationExpanded = isMobileNavigation ? mobileNavOpen : !sidebarCollapsed;
  const settingsActive = view === "settings" || view.startsWith("settings_") || view === "users";
  const visibleSettingsNavItems = React.useMemo(
    () => settingsNavItems.filter((item) => !item.adminOnly || currentUser?.role === "admin"),
    [currentUser?.role]
  );
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
                      {visibleSettingsNavItems.map((subItem) => {
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
        onAction={handleNotificationAction}
        onDismiss={(id) => setNotificationToasts((current) => current.filter((item) => item.id !== id))}
      />
      <React.Suspense fallback={null}>
        <ChatWidget currentUser={currentUser} maintenanceStatus={maintenanceStatus} />
      </React.Suspense>
    </div>
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
  let content: React.ReactNode;
  switch (props.view) {
    case "people":
      content = <PeopleView garageDoors={props.integrationStatus?.garage_door_entities ?? []} groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    case "groups":
      content = <GroupsView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} />;
      break;
    case "schedules":
      content = <SchedulesView schedules={props.schedules} query={props.search} refresh={props.refresh} />;
      break;
    case "passes":
      content = <PassesView query={props.search} realtime={props.realtime} />;
      break;
    case "vehicles":
      content = <VehiclesView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    case "top_charts":
      content = <TopChartsView query={props.search} realtime={props.realtime} />;
      break;
    case "events":
      content = <EventsView events={props.events} query={props.search} />;
      break;
    case "alerts":
      content = <AlertsView refreshDashboard={props.refresh} />;
      break;
    case "reports":
      content = <ReportsView events={props.events} presence={props.presence} />;
      break;
    case "integrations":
      content = <IntegrationsView people={props.people} realtime={props.realtime} schedules={props.schedules} status={props.integrationStatus} />;
      break;
    case "logs":
      content = <LogsView logs={props.realtime} onClearRealtime={props.onClearRealtime} />;
      break;
    case "settings_general":
      content = <DynamicSettingsView category="general" title="General Settings" icon={SlidersHorizontal} currentUser={props.currentUser} maintenanceStatus={props.maintenanceStatus} onMaintenanceStatusChanged={props.onMaintenanceStatusChanged} />;
      break;
	    case "settings_auth":
	      content = <DynamicSettingsView category="auth" title="Auth & Security" icon={Lock} currentUser={props.currentUser} />;
	      break;
	    case "alfred_training":
	      content = props.currentUser.role === "admin"
	        ? <AlfredTrainingView />
	        : <SettingsView currentUser={props.currentUser} groups={props.groups} schedules={props.schedules} vehicles={props.vehicles} />;
	      break;
	    case "settings_automations":
      content = <AutomationsView people={props.people} vehicles={props.vehicles} />;
      break;
    case "settings_notifications":
      content = <NotificationsView currentUser={props.currentUser} people={props.people} schedules={props.schedules} />;
      break;
    case "settings_lpr":
      content = <DynamicSettingsView category="lpr" title="LPR Tuning" icon={Gauge} currentUser={props.currentUser} />;
      break;
    case "settings":
      content = <SettingsView currentUser={props.currentUser} groups={props.groups} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    case "users":
      content = props.currentUser.role === "admin"
        ? <UsersView currentUser={props.currentUser} onCurrentUserUpdated={props.onCurrentUserUpdated} />
        : <SettingsView currentUser={props.currentUser} groups={props.groups} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    default:
      content = <Dashboard {...props} currentUser={props.currentUser} navigateToView={props.navigateToView} />;
      break;
  }
  return <React.Suspense fallback={<RouteLoading />}>{content}</React.Suspense>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
