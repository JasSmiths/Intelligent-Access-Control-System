import React from "react";
import ReactDOM from "react-dom/client";
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
  Database,
  DoorClosed,
  DoorOpen,
  FileText,
  Gauge,
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
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  Terminal,
  Trash2,
  UserPlus,
  UserRound,
  Users,
  Warehouse,
  X
} from "lucide-react";
import "./styles.css";

type DoorCommandAction = "open" | "close";
type DashboardCommand = {
  target: "top_gate" | "main_garage_door" | "mums_garage_door";
  label: string;
  action: DoorCommandAction;
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
  is_active: boolean;
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
  media_player_entities: HomeAssistantEntity[];
  person_entities: HomeAssistantEntity[];
  presence_mappings: HomeAssistantPresenceSuggestion[];
};

type AppriseUrlSummary = {
  index: number;
  type: string;
  scheme: string;
  preview: string;
};

type DvlaLookupResponse = {
  registration_number: string;
  vehicle: {
    make?: string | null;
    model?: string | null;
    colour?: string | null;
    color?: string | null;
  } & Record<string, unknown>;
};

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
  | "vehicles"
  | "events"
  | "reports"
  | "integrations"
  | "logs"
  | "settings"
  | "settings_general"
  | "settings_auth"
  | "settings_lpr"
  | "users";

const primaryNavItems: Array<{ key: Exclude<ViewKey, "users">; label: string; icon: React.ElementType }> = [
  { key: "dashboard", label: "Dashboard", icon: Home },
  { key: "people", label: "People", icon: UserRound },
  { key: "groups", label: "Groups", icon: Users },
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
  { key: "settings_lpr", label: "LPR Tuning", icon: Gauge },
  { key: "users", label: "Users", icon: Users }
];

const viewPaths: Record<ViewKey, string> = {
  dashboard: "/",
  people: "/people",
  groups: "/groups",
  vehicles: "/vehicles",
  events: "/events",
  reports: "/reports",
  integrations: "/integrations",
  logs: "/logs",
  settings: "/settings",
  settings_general: "/settings/general",
  settings_auth: "/settings/auth-security",
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
    if (!state) return false;
    setIntegrationStatus((current) => current ? { ...current, current_gate_state: state, last_gate_state: state } : current);
    return true;
  }

  if (event.type === "door.state_changed") {
    const door = stringPayload(event.payload.door);
    const state = stringPayload(event.payload.state);
    const stateKey = doorStateKey(door);
    if (!state || !stateKey) return false;
    setIntegrationStatus((current) => current ? { ...current, [stateKey]: state } : current);
    return true;
  }

  return false;
}

function stringPayload(value: unknown) {
  return typeof value === "string" ? value : "";
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
  const [timeSlots, setTimeSlots] = React.useState<TimeSlot[]>([]);
  const [integrationStatus, setIntegrationStatus] = React.useState<IntegrationStatus | null>(null);
  const [realtime, setRealtime] = React.useState<RealtimeMessage[]>([]);
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
    const [nextPresence, nextEvents, nextAnomalies, nextPeople, nextVehicles, nextGroups, nextSlots, nextStatus] =
      await Promise.all([
        api.get<Presence[]>("/api/v1/presence"),
        api.get<AccessEvent[]>("/api/v1/events?limit=40"),
        api.get<Anomaly[]>("/api/v1/anomalies?limit=30"),
        api.get<Person[]>("/api/v1/people"),
        api.get<Vehicle[]>("/api/v1/vehicles"),
        api.get<Group[]>("/api/v1/groups"),
        api.get<TimeSlot[]>("/api/v1/time-slots"),
        api.get<IntegrationStatus>("/api/v1/integrations/home-assistant/status")
      ]);
    setPresence(nextPresence);
    setEvents(nextEvents);
    setAnomalies(nextAnomalies);
    setPeople(nextPeople);
    setVehicles(nextVehicles);
    setGroups(nextGroups);
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
      if (applyIntegrationRealtimeEvent(parsed, setIntegrationStatus)) {
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
      <ChatWidget currentUser={currentUser} />
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
  timeSlots: TimeSlot[];
  integrationStatus: IntegrationStatus | null;
  realtime: RealtimeMessage[];
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  onCurrentUserUpdated: (user: UserAccount) => void;
}) {
  switch (props.view) {
    case "people":
      return <PeopleView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} vehicles={props.vehicles} />;
    case "groups":
      return <GroupsView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} />;
    case "vehicles":
      return <VehiclesView people={props.people} query={props.search} refresh={props.refresh} vehicles={props.vehicles} />;
    case "events":
      return <EventsView events={props.events} query={props.search} />;
    case "reports":
      return <ReportsView events={props.events} presence={props.presence} />;
    case "integrations":
      return <IntegrationsView status={props.integrationStatus} refresh={props.refresh} />;
    case "logs":
      return <LogsView logs={props.realtime} />;
    case "settings_general":
      return <DynamicSettingsView category="general" title="General Settings" icon={SlidersHorizontal} />;
    case "settings_auth":
      return <DynamicSettingsView category="auth" title="Auth & Security" icon={Lock} />;
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
  const displayEvents = getDashboardEvents(events);
  const displayAnomalies = getDashboardAnomalies(anomalies);
  const expected = Math.max(people.length, presence.length);
  const todayEvents = events.filter((event) => isToday(event.occurred_at, now));
  const exitedToday = todayEvents.filter((event) => event.direction === "exit").length;
  const deniedToday = todayEvents.filter((event) => event.decision === "denied").length;
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  const liveSources = new Set(events.map((event) => event.source).filter(Boolean)).size;
  const topGateState = integrationStatus?.current_gate_state ?? integrationStatus?.last_gate_state ?? "unknown";
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
      if (pendingCommand.target === "top_gate") {
        await api.post("/api/v1/integrations/gate/open", { reason: "Dashboard Top Gate status command" });
      } else {
        await api.post("/api/v1/integrations/cover/command", {
          target: pendingCommand.target,
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
            <GateRow
              icon={Car}
              label="Top Gate"
              state={commandLoading && pendingCommand?.target === "top_gate" ? "opening" : topGateState}
              onActionClick={commandForDevice("Top Gate", "top_gate", topGateState, ["closed"], setPendingCommand, setCommandError)}
            />
            <GarageDoorRow
              label="Main Garage Door"
              state={commandLoading && pendingCommand?.target === "main_garage_door" ? inProgressState(pendingCommand.action) : integrationStatus?.main_garage_door_state ?? "unknown"}
              onActionClick={commandForDevice("Main Garage Door", "main_garage_door", integrationStatus?.main_garage_door_state ?? "unknown", ["open", "closed"], setPendingCommand, setCommandError)}
            />
            <GarageDoorRow
              label="Mums Garage Door"
              state={commandLoading && pendingCommand?.target === "mums_garage_door" ? inProgressState(pendingCommand.action) : integrationStatus?.mums_garage_door_state ?? "unknown"}
              onActionClick={commandForDevice("Mums Garage Door", "mums_garage_door", integrationStatus?.mums_garage_door_state ?? "unknown", ["open", "closed"], setPendingCommand, setCommandError)}
            />
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
                <div className="event-feed-row" key={`${event.time}-${event.label}`}>
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

function commandForDevice(
  label: string,
  target: DashboardCommand["target"],
  state: string,
  allowedStates: Array<"open" | "closed">,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>
) {
  const normalized = normalizeGateState(state);
  if (!allowedStates.includes(normalized as "open" | "closed")) return undefined;
  const action = normalized === "open" ? "close" : "open";
  return () => {
    setCommandError("");
    setPendingCommand({ target, label, action });
  };
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
  time: string;
  label: string;
  subtitle: string;
  status: "IN" | "OUT";
  tone: "green" | "blue" | "gray";
  icon: React.ElementType;
};

function getDashboardEvents(events: AccessEvent[]): DashboardEvent[] {
  return events.slice(0, 5).map((event) => ({
    time: formatTime(event.occurred_at),
    label: event.registration_number,
    subtitle: `${titleCase(event.source)}  •  LPR`,
    status: event.direction === "exit" ? "OUT" : "IN",
    tone: event.decision === "denied" ? "gray" : event.direction === "entry" ? "green" : "blue",
    icon: event.direction === "exit" ? LogOut : event.decision === "denied" ? AlertTriangle : Car
  }));
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

function PeopleView({
  groups,
  people,
  query,
  refresh,
  vehicles
}: {
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
  vehicles: Vehicle[];
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedPerson, setSelectedPerson] = React.useState<Person | null>(null);
  const [error, setError] = React.useState("");
  const filtered = people.filter((item) =>
    matches(item.display_name, query) ||
    matches(item.group ?? "", query) ||
    item.vehicles.some((vehicle) => matches(vehicle.registration_number, query))
  );
  const assignedVehicleIds = React.useMemo(() => new Set(people.flatMap((person) => person.vehicles.map((vehicle) => vehicle.id))), [people]);

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
                  {person.vehicles.length ? person.vehicles.map((vehicle) => (
                    <span className="vehicle-chip" key={vehicle.id}>{vehicle.registration_number}</span>
                  )) : <span className="muted-value">No vehicles</span>}
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
          groups={groups}
          mode={selectedPerson ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          person={selectedPerson}
          setPageError={setError}
          vehicles={vehicles}
        />
      ) : null}
    </section>
  );
}

function PersonModal({
  assignedVehicleIds,
  groups,
  mode,
  onClose,
  onSaved,
  person,
  setPageError,
  vehicles
}: {
  assignedVehicleIds: Set<string>;
  groups: Group[];
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  person: Person | null;
  setPageError: (message: string) => void;
  vehicles: Vehicle[];
}) {
  const [form, setForm] = React.useState({
    first_name: person?.first_name ?? "",
    last_name: person?.last_name ?? "",
    profile_photo_data_url: person?.profile_photo_data_url ?? "",
    group_id: person?.group_id ?? groups[0]?.id ?? "",
    vehicle_ids: person?.vehicles.map((vehicle) => vehicle.id) ?? ([] as string[]),
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
      vehicle_ids: form.vehicle_ids,
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
    is_active: form.is_active,
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
  vehicles
}: {
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
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
  setPageError,
  vehicle
}: {
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  people: Person[];
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
        const make = typeof result.vehicle.make === "string" ? result.vehicle.make : "";
        const model = typeof result.vehicle.model === "string" ? result.vehicle.model : "";
        const color = typeof (result.vehicle.colour ?? result.vehicle.color) === "string" ? String(result.vehicle.colour ?? result.vehicle.color) : "";
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

function IntegrationsView({ status, refresh }: { status: IntegrationStatus | null; refresh: () => Promise<void> }) {
  const { values, loading, save, reload } = useSettings();
  const [active, setActive] = React.useState<IntegrationDefinition | null>(null);
  const tiles = integrationDefinitions(status, values);
  return (
    <section className="view-stack integrations-page">
      <Toolbar title="API & Integrations" count={tiles.length} icon={PlugZap} />
      <div className="integration-tile-grid">
        {tiles.map((tile) => {
          const Icon = tile.icon;
          return (
            <button className="card integration-tile" key={tile.key} onClick={() => setActive(tile)} type="button">
              <span className="integration-icon"><Icon size={22} /></span>
              <div>
                <strong>{tile.title}</strong>
                <span>{tile.description}</span>
              </div>
              <Badge tone={tile.statusTone}>{tile.statusLabel}</Badge>
            </button>
          );
        })}
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
          loading={loading}
          values={values}
          onClose={() => setActive(null)}
          onSettingsChanged={reload}
          onSaved={async (updates) => {
            await save(updates);
            setActive(null);
          }}
        />
      ) : null}
    </section>
  );
}

type IntegrationDefinition = {
  key: string;
  title: string;
  description: string;
  icon: React.ElementType;
  fields: SettingFieldDefinition[];
  statusLabel: string;
  statusTone: BadgeTone;
  oauth?: boolean;
};

type IntegrationFeedback = {
  tone: "progress" | "success" | "error" | "info";
  title: string;
  detail: string;
  activeStep?: number;
};

function integrationDefinitions(status: IntegrationStatus | null, values: SettingsMap): IntegrationDefinition[] {
  const activeProvider = String(values.llm_provider || "local");
  const providerStatus = (key: string, secretKey?: string): Pick<IntegrationDefinition, "statusLabel" | "statusTone"> => {
    if (activeProvider === key) return { statusLabel: "Connected", statusTone: "green" };
    if (secretKey && values[secretKey]) return { statusLabel: "Configured", statusTone: "blue" };
    if (key === "ollama" && values.ollama_base_url) return { statusLabel: "Configured", statusTone: "blue" };
    return { statusLabel: "Not Configured", statusTone: "gray" };
  };

  return [
    {
      key: "home_assistant",
      title: "Home Assistant",
      description: "Gate control, TTS announcements, and state sync.",
      icon: Home,
      statusLabel: status?.configured ? "Connected" : "Not Configured",
      statusTone: status?.configured ? "green" : "gray",
      fields: [
        { key: "home_assistant_url", label: "URL" },
        { key: "home_assistant_token", label: "Long-lived token", type: "password" },
        { key: "home_assistant_gate_entity_id", label: "Gate entity" },
        { key: "home_assistant_gate_open_service", label: "Open service" },
        { key: "home_assistant_tts_service", label: "TTS service" },
        { key: "home_assistant_default_media_player", label: "Default media player" },
        { key: "home_assistant_presence_entities", label: "Presence mapping" }
      ]
    },
    {
      key: "apprise",
      title: "Apprise",
      description: "Mobile and push notification fan-out.",
      icon: Bell,
      statusLabel: values.apprise_urls ? "Configured" : "Not Configured",
      statusTone: values.apprise_urls ? "green" : "gray",
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
      key: "openai",
      title: "OpenAI",
      description: "Responses API provider for tool-capable chat.",
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

function IntegrationModal({
  definition,
  values,
  loading,
  onClose,
  onSettingsChanged,
  onSaved
}: {
  definition: IntegrationDefinition;
  values: SettingsMap;
  loading: boolean;
  onClose: () => void;
  onSettingsChanged: () => Promise<void>;
  onSaved: (updates: Record<string, unknown>) => Promise<void>;
}) {
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

  React.useEffect(() => {
    setForm(integrationInitialValues(definition, values));
    setFeedback(null);
    setHaDiscovery(null);
    setHaDiscoveryError("");
    setAppriseUrls([]);
  }, [definition.key]);

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
      <form className="modal-card integration-modal" onSubmit={save}>
        <div className="modal-header">
          <div>
            <h2>{definition.title}</h2>
            <p>{loading ? "Loading settings..." : definition.description}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close"><X size={16} /></button>
        </div>
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
  onReload
}: {
  discovery: HomeAssistantDiscovery | null;
  discoveryError: string;
  discoveryLoading: boolean;
  form: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onReload: () => Promise<void>;
}) {
  const presenceMapping = parsePresenceMapping(form.home_assistant_presence_entities);

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
    <div className="ha-config-stack">
      <div className="settings-form-grid">
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
          field={{ key: "home_assistant_gate_open_service", label: "Open service" }}
          value={form.home_assistant_gate_open_service ?? ""}
          onChange={(value) => onChange("home_assistant_gate_open_service", value)}
        />
        <SettingField
          field={{ key: "home_assistant_tts_service", label: "TTS service" }}
          value={form.home_assistant_tts_service ?? ""}
          onChange={(value) => onChange("home_assistant_tts_service", value)}
        />
      </div>

      <div className="ha-discovery-header">
        <div>
          <strong>Home Assistant entities</strong>
          <span>{discovery ? "Pulled from the configured Home Assistant instance." : "Save valid credentials, then refresh entity discovery."}</span>
        </div>
        <button className="secondary-button" onClick={onReload} disabled={discoveryLoading} type="button">
          <RefreshCcw size={15} /> {discoveryLoading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {discoveryError ? <div className="auth-error inline-error">{discoveryError}</div> : null}

      <div className="settings-form-grid">
        <EntitySelectField
          label="Gate entity"
          value={form.home_assistant_gate_entity_id ?? ""}
          entities={discovery?.cover_entities ?? []}
          domainLabel="cover"
          onChange={(value) => onChange("home_assistant_gate_entity_id", value)}
        />
        <EntitySelectField
          label="Default media player"
          value={form.home_assistant_default_media_player ?? ""}
          entities={discovery?.media_player_entities ?? []}
          domainLabel="media_player"
          onChange={(value) => onChange("home_assistant_default_media_player", value)}
        />
      </div>

      <div className="presence-mapping-card">
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
      </div>
    </div>
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

const secretSettingKeys = new Set(["home_assistant_token", "apprise_urls", "dvla_api_key", "openai_api_key", "gemini_api_key", "anthropic_api_key"]);

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
    dvla_timeout_seconds: "10"
  };
  return definition.fields.reduce<Record<string, string>>((acc, field) => {
    const current = values[field.key];
    if (secretSettingKeys.has(field.key)) {
      acc[field.key] = "";
    } else if (field.key === "home_assistant_presence_entities" && typeof current === "object") {
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

function coerceSettingsPayload(form: Record<string, string>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(form)) {
    if (key.endsWith("_api_key") || key === "home_assistant_token" || key === "apprise_urls") {
      if (!value.trim()) continue;
    }
    if (key === "home_assistant_presence_entities") {
      try {
        payload[key] = value.trim() ? JSON.parse(value) : {};
      } catch {
        payload[key] = {};
      }
    } else if (["auth_cookie_secure"].includes(key)) {
      payload[key] = value === "true";
    } else if ([
      "auth_access_token_minutes",
      "auth_remember_days",
      "lpr_debounce_quiet_seconds",
      "lpr_debounce_max_seconds",
      "lpr_similarity_threshold",
      "llm_timeout_seconds",
      "dvla_timeout_seconds"
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

function titleCase(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
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
