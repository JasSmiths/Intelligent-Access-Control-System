import { Bell, ChevronDown, Loader2, LogOut, Menu, RefreshCcw, Search, ShieldCheck, X } from "lucide-react";
import React from "react";
import { api, isAbortError, wsUrl } from "../api/client";
import type { SearchPaletteItem } from "../api/search";
import type { AccessEvent, Anomaly, ExpectedPresenceSummary, Group, IntegrationStatus, MaintenanceStatus, NavigateToView, Person, Presence, RealtimeMessage, Schedule, Vehicle, ViewKey } from "../api/types";
import { displayUserName, isRecord } from "../lib/format";
import { UserAvatar } from "../lib/media";
import { AlertTray, isBellAlert } from "./alerts";
import { AuthLoading, LoginPage, SetupPage, type AuthStatus } from "./auth";
import { DeferredChatWidget } from "./chatLauncher";
import { initialViewFromLocation, primaryNavItems, settingsNavItems, settingsNavViewKeys, shellDataKeysForView, viewFromPath, viewPaths, type ShellDataKey } from "./navigation";
import { useProfilePreferences } from "./profile";
import {
  REALTIME_CLIENT_PING_INTERVAL_MS,
  REALTIME_DEFER_PARSE_BYTES,
  REALTIME_PROBE_TIMEOUT_MS,
  REALTIME_RECONNECT_DELAY_MS,
  REALTIME_REFRESH_MIN_INTERVAL_MS,
  REALTIME_RESUME_RECONNECT_AFTER_MS,
  REALTIME_RESUME_REFRESH_MIN_INTERVAL_MS,
  accessEventFromRealtime,
  applyIntegrationRealtimeEvent,
  applyMaintenanceRealtimeEvent,
  notificationToastFromRealtime,
  realtimeMessageForRouteConsumers,
  realtimeProbeDetail,
  realtimeStatus,
  shouldRefreshDataForRealtimeEvent,
  type NotificationToast,
  type NotificationToastAction,
  type RealtimeConnectionState,
  type RealtimeConnectionStatus
} from "./realtimeEvents";
import { View } from "./routes";
import { SearchPalette } from "./searchPalette";
import { ThemeControl, useTheme } from "./theme";
import { NotificationToastStack } from "./toasts";
export function App() {
  const [view, setView] = React.useState<ViewKey>(() => initialViewFromLocation());
  const [theme, setTheme] = useTheme();
  const [authStatus, setAuthStatus] = React.useState<AuthStatus | null>(null);
  const currentUser = authStatus?.user ?? null;
  const [profilePreferences, setProfilePreferences] = useProfilePreferences(currentUser);
  const [presence, setPresence] = React.useState<Presence[]>([]);
  const [expectedPresence, setExpectedPresence] = React.useState<ExpectedPresenceSummary | null>(null);
  const [events, setEvents] = React.useState<AccessEvent[]>([]);
  const [anomalies, setAnomalies] = React.useState<Anomaly[]>([]);
  const [people, setPeople] = React.useState<Person[]>([]);
  const [vehicles, setVehicles] = React.useState<Vehicle[]>([]);
  const [groups, setGroups] = React.useState<Group[]>([]);
  const [schedules, setSchedules] = React.useState<Schedule[]>([]);
  const [integrationStatus, setIntegrationStatus] = React.useState<IntegrationStatus | null>(null);
  const [maintenanceStatus, setMaintenanceStatus] = React.useState<MaintenanceStatus | null>(null);
  const [latestRealtime, setLatestRealtime] = React.useState<RealtimeMessage | null>(null);
  const [dataRefreshToken, setDataRefreshToken] = React.useState(0);
  const [notificationToasts, setNotificationToasts] = React.useState<NotificationToast[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [dashboardRefreshing, setDashboardRefreshing] = React.useState(false);
  const [realtimeConnection, setRealtimeConnection] = React.useState<RealtimeConnectionState>(() =>
    realtimeStatus("connecting", "Preparing live updates")
  );
  const realtimeConnectionStatus = realtimeConnection.status;
  const [search, setSearch] = React.useState("");
  const [searchPaletteOpen, setSearchPaletteOpen] = React.useState(false);
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
  const setRealtimeStatus = React.useCallback((status: RealtimeConnectionStatus, detail: string) => {
    setRealtimeConnection(realtimeStatus(status, detail));
  }, []);
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
  const refreshAuth = React.useCallback(async (includePhoto = false) => {
    const status = await api.get<AuthStatus>(`/api/v1/auth/status?include_photo=${includePhoto ? "true" : "false"}`);
    setAuthStatus(status);
  }, []);
  React.useEffect(() => {
    refreshAuth().catch(() => setAuthStatus({ setup_required: false, authenticated: false, user: null }));
  }, [refreshAuth]);
  const refreshPromiseRef = React.useRef<Promise<void> | null>(null);
  const refreshPromiseKeyRef = React.useRef<string | null>(null);
  const refreshAbortRef = React.useRef<AbortController | null>(null);
  const refreshSequenceRef = React.useRef(0);
  const refreshLastStartedAtRef = React.useRef(0);
  const loadedShellDataRef = React.useRef(new Set<ShellDataKey>());
  const refresh = React.useCallback(async () => {
    const keys = shellDataKeysForView(view, currentUser);
    const refreshKey = `${view}:${currentUser?.role ?? "unknown"}`;
    if (refreshPromiseRef.current && refreshPromiseKeyRef.current === refreshKey) {
      return refreshPromiseRef.current;
    }
    refreshAbortRef.current?.abort();
    const controller = new AbortController();
    const sequence = refreshSequenceRef.current + 1;
    refreshSequenceRef.current = sequence;
    refreshAbortRef.current = controller;
    if ([...keys].some((key) => !loadedShellDataRef.current.has(key))) {
      setLoading(true);
    }
    refreshLastStartedAtRef.current = Date.now();
    const tasks: Promise<void>[] = [];
    const addTask = <T,>(key: ShellDataKey, path: string, setter: (value: T) => void) => {
      if (!keys.has(key)) return;
      tasks.push(
        api.get<T>(path, { signal: controller.signal }).then((value) => {
          if (controller.signal.aborted || refreshSequenceRef.current !== sequence) return;
          setter(value);
          loadedShellDataRef.current.add(key);
        })
      );
    };
    addTask<Presence[]>("presence", "/api/v1/presence", setPresence);
    addTask<ExpectedPresenceSummary>("expectedPresence", "/api/v1/presence/expected-today", setExpectedPresence);
    addTask<AccessEvent[]>("events", "/api/v1/events?limit=40", setEvents);
    addTask<Anomaly[]>("anomalies", "/api/v1/alerts?status=open&limit=100", setAnomalies);
    addTask<Person[]>("people", "/api/v1/people?include_media=false", setPeople);
    addTask<Vehicle[]>("vehicles", "/api/v1/vehicles?include_media=false", setVehicles);
    addTask<Group[]>("groups", "/api/v1/groups", setGroups);
    addTask<Schedule[]>("schedules", "/api/v1/schedules", setSchedules);
    addTask<IntegrationStatus>("integrationStatus", "/api/v1/integrations/gate/status", setIntegrationStatus);
    addTask<MaintenanceStatus>("maintenanceStatus", "/api/v1/maintenance/status", setMaintenanceStatus);
    const run = Promise.all(tasks)
      .then(() => {
        if (controller.signal.aborted || refreshSequenceRef.current !== sequence) return;
        setDataRefreshToken((current) => current + 1);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || refreshSequenceRef.current !== sequence || isAbortError(error)) return;
        throw error;
      })
      .finally(() => {
        if (refreshPromiseRef.current === run) {
          setLoading(false);
          refreshPromiseRef.current = null;
          refreshPromiseKeyRef.current = null;
        }
        if (refreshAbortRef.current === controller) refreshAbortRef.current = null;
      });
    refreshPromiseRef.current = run;
    refreshPromiseKeyRef.current = refreshKey;
    return run;
  }, [currentUser, view]);
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
  const realtimeLifecycleRefreshLastRunRef = React.useRef(0);
  const realtimeRefreshTimerRef = React.useRef<number | null>(null);
  const realtimeRefreshInFlightRef = React.useRef(false);
  const realtimeRefreshPendingLifecycleRef = React.useRef(false);
  const runQueuedRealtimeRefresh = React.useCallback((lifecycle: boolean) => {
    realtimeRefreshInFlightRef.current = true;
    realtimeRefreshPendingLifecycleRef.current = false;
    const now = Date.now();
    realtimeRefreshLastRunRef.current = now;
    if (lifecycle) realtimeLifecycleRefreshLastRunRef.current = now;
    setRealtimeStatus(
      "refreshing",
      lifecycle ? "Pulling current site state" : "Applying live update"
    );
    refresh()
      .then(() => setRealtimeStatus(
        "live",
        lifecycle ? "Data refreshed just now" : "Live update applied"
      ))
      .catch(() => setRealtimeStatus(
        "reconnecting",
        lifecycle ? "Refresh failed; retrying with the stream" : "Refresh failed; waiting for the stream"
      ))
      .finally(() => {
        realtimeRefreshInFlightRef.current = false;
      });
  }, [refresh, setRealtimeStatus]);
  const queueRealtimeRefresh = React.useCallback((lifecycle = false) => {
    const now = Date.now();
    const minInterval = lifecycle ? REALTIME_RESUME_REFRESH_MIN_INTERVAL_MS : REALTIME_REFRESH_MIN_INTERVAL_MS;
    const lastRunAt = lifecycle ? realtimeLifecycleRefreshLastRunRef.current : realtimeRefreshLastRunRef.current;
    const refreshAge = now - refreshLastStartedAtRef.current;
    const dueIn = Math.max(
      0,
      minInterval - (now - lastRunAt),
      lifecycle ? REALTIME_REFRESH_MIN_INTERVAL_MS - refreshAge : 0
    );
    realtimeRefreshPendingLifecycleRef.current = realtimeRefreshPendingLifecycleRef.current || lifecycle;
    if (!realtimeRefreshInFlightRef.current && dueIn === 0) {
      runQueuedRealtimeRefresh(realtimeRefreshPendingLifecycleRef.current);
      return;
    }
    if (realtimeRefreshTimerRef.current !== null) return;
    realtimeRefreshTimerRef.current = window.setTimeout(() => {
      realtimeRefreshTimerRef.current = null;
      runQueuedRealtimeRefresh(realtimeRefreshPendingLifecycleRef.current);
    }, Math.max(50, dueIn || REALTIME_REFRESH_MIN_INTERVAL_MS));
  }, [runQueuedRealtimeRefresh]);
  const refreshFromRealtime = React.useCallback(() => {
    queueRealtimeRefresh(false);
  }, [queueRealtimeRefresh]);
  const refreshFromRealtimeLifecycle = React.useCallback(() => {
    queueRealtimeRefresh(true);
  }, [queueRealtimeRefresh]);
  React.useEffect(() => () => {
    if (realtimeRefreshTimerRef.current !== null) {
      window.clearTimeout(realtimeRefreshTimerRef.current);
    }
    refreshAbortRef.current?.abort();
  }, []);
  React.useEffect(() => {
    if (!authStatus?.authenticated) return;
    refresh().catch(() => setLoading(false));
  }, [authStatus?.authenticated, refresh]);
  React.useEffect(() => {
    if (!authStatus?.authenticated) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let pingTimer: number | null = null;
    let probeTimer: number | null = null;
    let pendingProbeId: string | null = null;
    let verifiedSocket: WebSocket | null = null;
    let stopped = false;
    let backgroundedAt: number | null = document.visibilityState === "hidden" ? Date.now() : null;
    let unfocusedAt: number | null = document.hasFocus() ? null : Date.now();
    let lastSocketActivityAt = Date.now();
    let hasVerifiedSocket = false;
    const handleMessageData = (data: unknown, sourceSocket: WebSocket) => {
      lastSocketActivityAt = Date.now();
      let parsed: RealtimeMessage;
      try {
        parsed = JSON.parse(String(data)) as RealtimeMessage;
      } catch (parseError) {
        console.warn("Ignored malformed realtime stream message", {
          error: parseError instanceof Error ? parseError.message : String(parseError),
          bytes: typeof data === "string" ? data.length : undefined
        });
        setRealtimeStatus("degraded", "Ignored malformed stream data; waiting for next event");
        return;
      }
      if (parsed.type === "connection.pong") {
        markSocketVerified(sourceSocket, parsed);
        return;
      }
      const routeRealtime = realtimeMessageForRouteConsumers(parsed);
      if (routeRealtime) {
        setLatestRealtime(routeRealtime);
      }
      if (parsed.type === "connection.ready") {
        if (pendingProbeId) {
          setRealtimeStatus("checking", "Server accepted stream; waiting for health reply");
        } else {
          markSocketVerified(sourceSocket, parsed);
        }
        return;
      }
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
      if (parsed.type.startsWith("telemetry.")) {
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
    const handleMessage = (event: MessageEvent, sourceSocket: WebSocket) => {
      lastSocketActivityAt = Date.now();
      if (typeof event.data === "string" && event.data.length >= REALTIME_DEFER_PARSE_BYTES) {
        window.setTimeout(() => {
          if (!stopped && socket === sourceSocket) {
            handleMessageData(event.data, sourceSocket);
          }
        }, 0);
        return;
      }
      handleMessageData(event.data, sourceSocket);
    };
    const clearProbeTimer = () => {
      if (probeTimer === null) return;
      window.clearTimeout(probeTimer);
      probeTimer = null;
    };
    const clearReconnectTimer = () => {
      if (reconnectTimer === null) return;
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };
    const markSocketVerified = (target: WebSocket, message: RealtimeMessage) => {
      if (stopped || socket !== target) return;
      const payload = isRecord(message.payload) ? message.payload : {};
      const messageProbeId = typeof payload.id === "string" ? payload.id : null;
      if (message.type === "connection.pong" && pendingProbeId && messageProbeId !== pendingProbeId) return;
      const firstVerificationForSocket = verifiedSocket !== target;
      verifiedSocket = target;
      pendingProbeId = null;
      lastSocketActivityAt = Date.now();
      clearProbeTimer();
      setRealtimeStatus(
        "live",
        message.type === "connection.pong" ? "Stream verified just now" : "Server accepted stream"
      );
      if (firstVerificationForSocket) {
        if (hasVerifiedSocket) {
          refreshFromRealtimeLifecycle();
        }
        hasVerifiedSocket = true;
      }
    };
    const nextProbeId = () => {
      if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
      }
      return `${Date.now()}:${Math.random().toString(36).slice(2)}`;
    };
    const sendSocketProbe = (target: WebSocket, reason: string) => {
      if (target.readyState !== WebSocket.OPEN) return false;
      const probeId = nextProbeId();
      clearProbeTimer();
      pendingProbeId = probeId;
      setRealtimeStatus("checking", realtimeProbeDetail(reason));
      try {
        target.send(JSON.stringify({
          type: "client.ping",
          payload: {
            id: probeId,
            reason,
            at: new Date().toISOString()
          }
        }));
        probeTimer = window.setTimeout(() => {
          if (stopped || socket !== target || pendingProbeId !== probeId) return;
          pendingProbeId = null;
          reconnectNow(`probe_timeout:${reason}`);
        }, REALTIME_PROBE_TIMEOUT_MS);
        return true;
      } catch {
        console.warn("Realtime probe send failed; reconnecting stream", { reason });
        try {
          target.close();
        } catch {
          // The resume handler will replace sockets that cannot be probed.
        }
        return false;
      }
    };
    const scheduleReconnect = (closedSocket: WebSocket | null) => {
      if (closedSocket && socket !== closedSocket) return;
      if (closedSocket) {
        socket = null;
      }
      if (stopped || reconnectTimer !== null) return;
      pendingProbeId = null;
      verifiedSocket = null;
      clearProbeTimer();
      setRealtimeStatus("reconnecting", "Opening a fresh stream shortly");
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        openSocket();
      }, REALTIME_RECONNECT_DELAY_MS);
    };
    const reconnectNow = (reason: string) => {
      if (stopped) return;
      clearReconnectTimer();
      clearProbeTimer();
      const currentSocket = socket;
      socket = null;
      pendingProbeId = null;
      verifiedSocket = null;
      setRealtimeStatus("reconnecting", realtimeProbeDetail(reason));
      if (currentSocket && currentSocket.readyState !== WebSocket.CLOSED) {
        try {
          currentSocket.close(4000, reason.slice(0, 100));
        } catch {
          try {
            currentSocket.close();
          } catch {
            // The follow-up open below replaces the failed socket either way.
          }
        }
      }
      openSocket();
    };
    const handleResume = (reason: string) => {
      if (stopped) return;
      const now = Date.now();
      const inactiveFor = Math.max(
        backgroundedAt === null ? 0 : now - backgroundedAt,
        unfocusedAt === null ? 0 : now - unfocusedAt
      );
      backgroundedAt = null;
      unfocusedAt = null;
      const currentSocket = socket;
      const connectingTimedOut =
        currentSocket?.readyState === WebSocket.CONNECTING &&
        now - lastSocketActivityAt >= REALTIME_RESUME_RECONNECT_AFTER_MS;
      const shouldReconnect =
        !currentSocket ||
        currentSocket.readyState === WebSocket.CLOSED ||
        currentSocket.readyState === WebSocket.CLOSING ||
        connectingTimedOut ||
        inactiveFor >= REALTIME_RESUME_RECONNECT_AFTER_MS;
      if (shouldReconnect) {
        reconnectNow(reason);
      } else if (currentSocket.readyState === WebSocket.OPEN && !sendSocketProbe(currentSocket, reason)) {
        reconnectNow(`${reason}:probe_failed`);
      } else if (currentSocket.readyState === WebSocket.CONNECTING) {
        setRealtimeStatus("connecting", "Connection is still opening");
      }
      refreshFromRealtimeLifecycle();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        backgroundedAt = backgroundedAt ?? Date.now();
        return;
      }
      handleResume("visibilitychange");
    };
    const handleWindowBlur = () => {
      unfocusedAt = unfocusedAt ?? Date.now();
    };
    const handleWindowFocus = () => handleResume("focus");
    const handlePageHide = () => {
      backgroundedAt = backgroundedAt ?? Date.now();
      unfocusedAt = unfocusedAt ?? Date.now();
    };
    const handlePageShow = () => handleResume("pageshow");
    const handleOnline = () => handleResume("online");
    const handleOffline = () => {
      setRealtimeStatus("offline", "Waiting for network to return");
      backgroundedAt = backgroundedAt ?? Date.now();
    };
    function openSocket() {
      if (stopped) return;
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
      clearReconnectTimer();
      const nextSocket = new WebSocket(wsUrl("/api/v1/realtime/ws"));
      socket = nextSocket;
      lastSocketActivityAt = Date.now();
      setRealtimeStatus("connecting", "Opening /api/v1/realtime/ws");
      nextSocket.onopen = () => {
        if (stopped || socket !== nextSocket) return;
        lastSocketActivityAt = Date.now();
        sendSocketProbe(nextSocket, "open");
      };
      nextSocket.onmessage = (event) => handleMessage(event, nextSocket);
      nextSocket.onclose = () => scheduleReconnect(nextSocket);
      nextSocket.onerror = () => {
        console.warn("Realtime stream socket error; reconnecting");
        if (socket === nextSocket) {
          nextSocket.close();
        }
      };
    }
    openSocket();
    pingTimer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      const currentSocket = socket;
      if (!currentSocket) {
        scheduleReconnect(null);
        return;
      }
      if (currentSocket.readyState === WebSocket.OPEN) {
        sendSocketProbe(currentSocket, "interval");
        return;
      }
      if (
        currentSocket.readyState === WebSocket.CONNECTING &&
        Date.now() - lastSocketActivityAt >= REALTIME_RESUME_RECONNECT_AFTER_MS
      ) {
        reconnectNow("connect_timeout");
        return;
      }
      if (currentSocket.readyState === WebSocket.CLOSED || currentSocket.readyState === WebSocket.CLOSING) {
        scheduleReconnect(currentSocket);
      }
    }, REALTIME_CLIENT_PING_INTERVAL_MS);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleWindowFocus);
    window.addEventListener("blur", handleWindowBlur);
    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("pageshow", handlePageShow);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      stopped = true;
      clearReconnectTimer();
      clearProbeTimer();
      if (pingTimer !== null) {
        window.clearInterval(pingTimer);
      }
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleWindowFocus);
      window.removeEventListener("blur", handleWindowBlur);
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("pageshow", handlePageShow);
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
        socket.close();
      }
      socket = null;
      verifiedSocket = null;
    };
  }, [authStatus?.authenticated, refreshFromRealtime, refreshFromRealtimeLifecycle, setRealtimeStatus]);
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
    if (!authStatus?.authenticated) return;
    if (currentUser?.role !== "admin" && view === "users") {
      navigateToView("settings", { replace: true });
    }
  }, [authStatus?.authenticated, currentUser?.role, navigateToView, view]);
  const sidebarCollapsed = profilePreferences.sidebarCollapsed;
  const navigationCollapsed = !isMobileNavigation && sidebarCollapsed;
  const navigationExpanded = isMobileNavigation ? mobileNavOpen : !sidebarCollapsed;
  const settingsActive = view === "settings" || settingsNavViewKeys.has(view);
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
    setSearchPaletteOpen(false);
    try {
      await api.post<{ status: string }>("/api/v1/auth/logout");
      setAuthStatus({ setup_required: false, authenticated: false, user: null });
      setPresence([]);
      setExpectedPresence(null);
      setEvents([]);
      setAnomalies([]);
      setPeople([]);
      setVehicles([]);
      setGroups([]);
      setSchedules([]);
      setIntegrationStatus(null);
      setMaintenanceStatus(null);
      refreshPromiseRef.current = null;
      refreshPromiseKeyRef.current = null;
      loadedShellDataRef.current.clear();
      setLatestRealtime(null);
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
  const openSearchResult = React.useCallback((result: SearchPaletteItem) => {
    setSearch(result.filter_value);
    setSearchPaletteOpen(false);
    navigateToView(result.target.view, { search: result.target.route_search ?? "" });
    if (isMobileNavigation) {
      setMobileNavOpen(false);
    }
  }, [isMobileNavigation, navigateToView]);
  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") return;
      if (!authStatus?.authenticated) return;
      event.preventDefault();
      setSearchPaletteOpen(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [authStatus?.authenticated]);
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
          {view === "logs" ? null : (
            <div className="sidebar-status" aria-live="polite" title={`${realtimeConnection.title}: ${realtimeConnection.detail}`}>
              <span className={`dot ${realtimeConnectionStatus}`} aria-hidden="true" />
              <span className="sidebar-status-copy">
                <strong>{realtimeConnection.title}</strong>
                <small>{realtimeConnection.detail}</small>
              </span>
            </div>
          )}
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
              <span>Crest House</span>
            </button>
          </div>
          <div className="topbar-actions">
            <button
              className={search ? "search global-search-trigger has-value" : "search global-search-trigger"}
              onClick={() => setSearchPaletteOpen(true)}
              type="button"
            >
              <Search size={16} />
              <span>{search || "Search Anything..."}</span>
            </button>
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
            expectedPresence={expectedPresence}
            events={events}
            anomalies={anomalies}
            people={people}
            vehicles={vehicles}
            groups={groups}
            schedules={schedules}
            integrationStatus={integrationStatus}
            maintenanceStatus={maintenanceStatus}
            latestRealtime={latestRealtime}
            dataRefreshToken={dataRefreshToken}
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
      <SearchPalette
        currentUser={currentUser}
        initialQuery={search}
        onClose={() => setSearchPaletteOpen(false)}
        onOpenResult={openSearchResult}
        open={searchPaletteOpen}
      />
      <NotificationToastStack
        notifications={notificationToasts}
        onAction={handleNotificationAction}
        onDismiss={(id) => setNotificationToasts((current) => current.filter((item) => item.id !== id))}
      />
      <DeferredChatWidget currentUser={currentUser} maintenanceStatus={maintenanceStatus} />
    </div>
  );
}
