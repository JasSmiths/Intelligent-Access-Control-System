import { LoadingState } from "../ui/primitives";
import { useModalFocus } from "../ui/useModalFocus";
import { Bell, ChevronDown, Loader2, LogOut, Menu, RefreshCcw, Search, ShieldCheck, X } from "lucide-react";
import React from "react";
import { api } from "../api/client";
import type { SearchPaletteItem } from "../api/search";
import type { AccessEvent, Anomaly, ExpectedPresenceSummary, Group, IntegrationStatus, MaintenanceStatus, NavigateToView, Person, Presence, RealtimeMessage, Schedule, Vehicle, ViewKey } from "../api/types";
import { displayUserName } from "../lib/format";
import { UserAvatar } from "../lib/media";
import { AlertTray, isBellAlert } from "./alerts";
import { AuthLoading, LoginPage, SetupPage, type AuthStatus } from "./auth";
import { DeferredChatWidget } from "./chatLauncher";
import { canAccessView, initialViewFromLocation, primaryNavItems, settingsNavItems, settingsNavViewKeys, viewFromPath, viewPaths, type ShellDataKey } from "./navigation";
import { useProfilePreferences } from "./profile";
import {
  accessEventFromRealtime,
  applyIntegrationRealtimeEvent,
  applyMaintenanceRealtimeEvent,
  notificationToastFromRealtime,
  realtimeMessageForRouteConsumers,
  realtimeStatus,
  type NotificationToast,
  type NotificationToastAction,
  type RealtimeConnectionState,
  type RealtimeConnectionStatus
} from "./realtimeEvents";
import { View } from "./routes";
import { SearchPalette } from "./searchPalette";
import { ThemeControl, useTheme } from "./theme";
import { NotificationToastStack } from "./toasts";
import { useShellRefresh } from "./useShellRefresh";
import { useRealtimeConnection } from "./useRealtimeConnection";
export function App() {
  const [view, setView] = React.useState<ViewKey>(() => initialViewFromLocation());
  const [locationSearch, setLocationSearch] = React.useState(() => window.location.search);
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
  const [notificationToasts, setNotificationToasts] = React.useState<NotificationToast[]>([]);
  const [dashboardRefreshing, setDashboardRefreshing] = React.useState(false);
  const [historyResetToken, setHistoryResetToken] = React.useState(0);
  const [realtimeConnection, setRealtimeConnection] = React.useState<RealtimeConnectionState>(() =>
    realtimeStatus("connecting", "Preparing live updates")
  );
  const realtimeConnectionStatus = realtimeConnection.status;
  const [routeSearch, setRouteSearch] = React.useState<{ view: ViewKey; value: string } | null>(null);
  const [searchPaletteOpen, setSearchPaletteOpen] = React.useState(false);
  const [settingsExpanded, setSettingsExpanded] = React.useState(false);
  const [alertsOpen, setAlertsOpen] = React.useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = React.useState(false);
  const [loggingOut, setLoggingOut] = React.useState(false);
  const [isMobileNavigation, setIsMobileNavigation] = React.useState(() =>
    typeof window !== "undefined" ? window.matchMedia("(max-width: 980px)").matches : false
  );
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const sidebarRef = React.useRef<HTMLElement | null>(null);
  const navigationButtonRef = React.useRef<HTMLButtonElement | null>(null);
  const alertsButtonRef = React.useRef<HTMLButtonElement | null>(null);
  const alertsTrayRef = React.useRef<HTMLDivElement | null>(null);
  const profileMenuRef = React.useRef<HTMLDivElement | null>(null);
  const profileButtonRef = React.useRef<HTMLButtonElement | null>(null);
  useModalFocus(sidebarRef, isMobileNavigation && mobileNavOpen, () => {
    if (profileMenuOpen) {
      setProfileMenuOpen(false);
      profileButtonRef.current?.focus({ preventScroll: true });
    } else {
      setMobileNavOpen(false);
    }
  });
  const setRealtimeStatus = React.useCallback((status: RealtimeConnectionStatus, detail: string) => {
    setRealtimeConnection(realtimeStatus(status, detail));
  }, []);
  const navigateToView = React.useCallback<NavigateToView>((nextView, options) => {
    setRouteSearch(null);
    setView(nextView);
    setLocationSearch(options?.search ?? "");
    setSettingsExpanded(nextView === "settings" || settingsNavViewKeys.has(nextView));
    setMobileNavOpen(false);
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
        setRouteSearch(null);
        setView(nextView);
        setLocationSearch(window.location.search);
        setSettingsExpanded(nextView === "settings" || settingsNavViewKeys.has(nextView));
        setMobileNavOpen(false);
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
  const { loading, readState, failedKeys, dataRefreshToken, refresh, initialRefresh, refreshRealtime, selectionForEvent, resetRefresh } = useShellRefresh(view, currentUser, {
    presence: setPresence, expectedPresence: setExpectedPresence, events: setEvents, anomalies: setAnomalies,
    people: setPeople, vehicles: setVehicles, groups: setGroups, schedules: setSchedules,
    integrationStatus: setIntegrationStatus, maintenanceStatus: setMaintenanceStatus
  });
  const failedLabels: Record<ShellDataKey, string> = { presence: "presence", expectedPresence: "expected arrivals", events: "events", anomalies: "alerts", people: "people", vehicles: "vehicles", groups: "groups", schedules: "schedules", integrationStatus: "integration status", maintenanceStatus: "maintenance status" };
  const failedDescription = failedKeys.map((key) => failedLabels[key]).join(", ");
  const refreshDashboard = React.useCallback(async () => {
    setDashboardRefreshing(true);
    try {
      await refresh();
      setHistoryResetToken((token) => token + 1);
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
  const refreshFromRealtime = React.useCallback((event: RealtimeMessage) => {
    const selection = selectionForEvent(event);
    if (!selection.keys.size && !selection.route) return;
    setRealtimeStatus("refreshing", "Applying live update");
    refreshRealtime(selection)
      .then(() => setRealtimeStatus("live", "Live update applied"))
      .catch(() => setRealtimeStatus("reconnecting", "Refresh failed; waiting for the stream"));
  }, [refreshRealtime, selectionForEvent, setRealtimeStatus]);
  const refreshFromRealtimeLifecycle = React.useCallback(() => {
    setRealtimeStatus("refreshing", "Pulling current site state");
    refreshRealtime()
      .then(() => setRealtimeStatus("live", "Data refreshed just now"))
      .catch(() => setRealtimeStatus("reconnecting", "Refresh failed; retrying with the stream"));
  }, [refreshRealtime, setRealtimeStatus]);
  React.useEffect(() => {
    if (!authStatus?.authenticated) return;
    initialRefresh().catch(() => undefined);
  }, [authStatus?.authenticated, initialRefresh]);
  const realtimeSessionKey = authStatus?.authenticated && currentUser
    ? `${currentUser.id}:${currentUser.role}`
    : null;
  React.useLayoutEffect(() => {
    setLatestRealtime(null);
    setNotificationToasts([]);
  }, [realtimeSessionKey]);
  const handleRealtimeMessage = React.useCallback((parsed: RealtimeMessage) => {
    const routeRealtime = realtimeMessageForRouteConsumers(parsed);
    if (routeRealtime) setLatestRealtime(routeRealtime);
    const notificationToast = notificationToastFromRealtime(parsed);
    if (notificationToast) {
      setNotificationToasts((current) => [notificationToast, ...current].slice(0, 4));
      return;
    }
    if (applyMaintenanceRealtimeEvent(parsed, setMaintenanceStatus)) return;
    if (applyIntegrationRealtimeEvent(parsed, setIntegrationStatus)) return;
    if (parsed.type.startsWith("telemetry.")) return;
    const finalizedEvent = accessEventFromRealtime(parsed);
    if (finalizedEvent) {
      setEvents((current) => [finalizedEvent, ...current.filter((item) => item.id !== finalizedEvent.id)].slice(0, 40));
    }
    refreshFromRealtime(parsed);
  }, [refreshFromRealtime]);
  useRealtimeConnection({
    sessionKey: realtimeSessionKey,
    onMessage: handleRealtimeMessage,
    onRefresh: refreshFromRealtimeLifecycle,
    onStatus: setRealtimeStatus
  });
  React.useEffect(() => {
    const media = window.matchMedia("(max-width: 980px)");
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
  const restrictedView = !canAccessView(view, currentUser);
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
      if (!mobileNavOpen) navigationButtonRef.current?.focus({ preventScroll: true });
      setMobileNavOpen((current) => !current);
      return;
    }
    setProfilePreferences({ sidebarCollapsed: !sidebarCollapsed });
  }, [isMobileNavigation, mobileNavOpen, setProfilePreferences, sidebarCollapsed]);
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
      resetRefresh();
      setLatestRealtime(null);
      setNotificationToasts([]);
      setRouteSearch(null);
      setSettingsExpanded(false);
      setView("dashboard");
      setLocationSearch("");
      localStorage.removeItem("iacs-active-view");
      setMobileNavOpen(false);
      window.history.replaceState({}, "", "/login");
    } catch (logoutError) {
      window.alert(logoutError instanceof Error ? logoutError.message : "Unable to log out. Please try again.");
    } finally {
      setLoggingOut(false);
    }
  }, [loggingOut]);
  const openSearchResult = React.useCallback((result: SearchPaletteItem) => {
    setSearchPaletteOpen(false);
    navigateToView(result.target.view, { search: result.target.route_search ?? "" });
    if (result.filter_value && !["events", "movements", "alerts"].includes(result.target.view)) setRouteSearch({ view: result.target.view, value: result.filter_value });
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
    setSettingsExpanded(settingsActive);
  }, [view]);
  React.useEffect(() => {
    if (!settingsExpanded && !isMobileNavigation) return;
    let frame = 0;
    const scrollActiveIntoView = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const sidebar = sidebarRef.current;
        const list = sidebar?.querySelector<HTMLElement>(".nav-list");
        const active = sidebar?.querySelector<HTMLElement>(".nav-item.nested.active") ?? sidebar?.querySelector<HTMLElement>(".nav-item.active");
        if (!list || !active) return;
        const listBounds = list.getBoundingClientRect();
        const activeBounds = active.getBoundingClientRect();
        if (activeBounds.bottom > listBounds.bottom) list.scrollTop += activeBounds.bottom - listBounds.bottom + 8;
        else if (activeBounds.top < listBounds.top) list.scrollTop -= listBounds.top - activeBounds.top + 8;
      });
    };
    scrollActiveIntoView();
    const submenu = sidebarRef.current?.querySelector(".nav-submenu-region");
    const onSubmenuSettled = (event: Event) => {
      if (event.target === submenu) scrollActiveIntoView();
    };
    submenu?.addEventListener("transitionend", onSubmenuSettled);
    window.addEventListener("resize", scrollActiveIntoView);
    return () => { window.cancelAnimationFrame(frame); window.removeEventListener("resize", scrollActiveIntoView); submenu?.removeEventListener("transitionend", onSubmenuSettled); };
  }, [view, settingsExpanded, isMobileNavigation, mobileNavOpen, authStatus?.authenticated]);
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
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside role={isMobileNavigation && mobileNavOpen ? "dialog" : undefined} aria-modal={isMobileNavigation && mobileNavOpen ? true : undefined} aria-label="Site navigation" inert={isMobileNavigation && !mobileNavOpen} className="sidebar" id="site-sidebar" aria-hidden={isMobileNavigation && !mobileNavOpen} ref={sidebarRef}>
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
            if (item.adminOnly && currentUser.role !== "admin") return null;
            const Icon = item.icon;
            if (item.key === "settings") {
              return (
                <div className="nav-group" key={item.key}>
                  <div className="nav-settings-row"><button
                    className={settingsActive ? "nav-item active" : "nav-item"}
                    onClick={() => navigateFromNav("settings")}
                    type="button"
                    title={item.label}
                    aria-label={item.label}
                    aria-current={view === "settings" ? "page" : undefined}
                  >
                    <Icon size={18} />
                    <span>{item.label}</span>
                  </button><button className="nav-disclosure" type="button" aria-label={settingsExpanded ? "Collapse Settings" : "Expand Settings"} aria-expanded={settingsExpanded && !navigationCollapsed} aria-controls="settings-submenu" onClick={() => setSettingsExpanded((current) => !current)}><ChevronDown size={15} /></button></div>
                  <div className={`nav-submenu-region${settingsExpanded && !navigationCollapsed ? " open" : ""}`} id="settings-submenu" inert={!settingsExpanded || navigationCollapsed} aria-hidden={!settingsExpanded || navigationCollapsed}>
                    <div className="nav-submenu">
                      {visibleSettingsNavItems.map((subItem) => {
                        const SubIcon = subItem.icon;
                        return (
                          <button
                            className={subItem.key === view ? "nav-item nested active" : "nav-item nested"}
                            key={subItem.key}
                            aria-label={subItem.label}
                            aria-current={subItem.key === view ? "page" : undefined}
                            title={subItem.label}
                            onClick={() => navigateFromNav(subItem.key)}
                            type="button"
                          >
                            <SubIcon size={16} />
                            <span>{subItem.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              );
            }
            return (
              <React.Fragment key={item.key}>
              {(item.key === "dashboard" || item.key === "people" || item.key === "reports") ? <div className="nav-section-label">{item.group}</div> : null}
              <button
                key={item.key}
                aria-label={item.label}
                aria-current={item.key === view ? "page" : undefined}
                className={item.key === view ? "nav-item active" : "nav-item"}
                onClick={() => navigateFromNav(item.key)}
                type="button"
                title={item.label}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
              </React.Fragment>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="profile-menu-shell">
            <button
              aria-label={`Account menu for ${displayUserName(currentUser)}`}
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
      <main className="main" id="main-content" tabIndex={-1}>
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="icon-button topbar-menu"
              type="button"
              aria-controls="site-sidebar"
              aria-expanded={navigationExpanded}
              aria-label={navigationExpanded ? "Collapse navigation sidebar" : "Expand navigation sidebar"}
              onClick={toggleNavigation}
              ref={navigationButtonRef}
            >
              <Menu size={20} />
            </button>
            <div className="topbar-location"><span className="estate-select" aria-label="Current site"><span>Crest House</span></span><span className="location-divider" aria-hidden="true">/</span><span className="topbar-page" key={view}>{[...primaryNavItems, ...settingsNavItems].find((item) => item.key === view)?.label ?? "Console"}</span></div>
          </div>
          <div className="topbar-actions">
            <button
              className="search global-search-trigger"
              aria-label="Search anything"
              aria-keyshortcuts="Meta+K Control+K"
              onClick={() => setSearchPaletteOpen(true)}
              type="button"
            >
              <Search size={16} />
              <span>Search anything…</span><kbd aria-hidden="true">⌘ K</kbd>
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
            <button className="icon-button refresh-button" onClick={() => refreshDashboard().catch(() => undefined)} type="button" aria-label="Refresh" aria-busy={dashboardRefreshing} disabled={dashboardRefreshing}>
              <RefreshCcw className={dashboardRefreshing ? "spin" : undefined} size={17} />
            </button>
            <ThemeControl theme={theme} setTheme={setTheme} />
            <DeferredChatWidget currentUser={currentUser} maintenanceStatus={maintenanceStatus} />
          </div>
        </header>
        {routeSearch?.view === view && routeSearch.value && !restrictedView ? <div className="route-filter-bar"><label><Search size={16} /><span>Page filter</span><input aria-label="Page filter" value={routeSearch.value} onChange={(event) => setRouteSearch({ view, value: event.target.value })} /></label><button className="secondary-button" type="button" onClick={() => setRouteSearch(null)}>Clear</button></div> : null}
        {restrictedView ? (
          <section className="view-stack permission-state" role="alert"><h1>Administrator access required</h1><p>This page is restricted to administrators. Your account can continue using the other console pages.</p><button className="secondary-button" type="button" onClick={() => navigateToView("dashboard")}>Go to Dashboard</button></section>
        ) : loading ? (
          <LoadingState label="Loading live site data" />
        ) : readState === "unavailable" ? (
          <section className="view-stack permission-state" role="alert"><h1>Site data unavailable</h1><p>Required {failedDescription} data could not be loaded. Editors are paused until a successful read.</p><button className="secondary-button" onClick={() => refreshDashboard().catch(() => undefined)} type="button">Retry</button></section>
        ) : (
          <>{readState === "stale" ? <div className="shell-stale-banner" role="status">Some site data is unavailable ({failedDescription}). Displayed information may be incomplete. <button className="secondary-button" onClick={() => refreshDashboard().catch(() => undefined)} type="button">Retry</button></div> : null}<View
            view={view}
            locationSearch={locationSearch}
            search={routeSearch?.view === view ? routeSearch.value : ""}
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
            historyResetToken={historyResetToken}
            refresh={refresh}
            currentUser={currentUser}
            navigateToView={navigateToView}
            onCurrentUserUpdated={(user) =>
              setAuthStatus((current) => current ? { ...current, user } : current)
            }
            onMaintenanceStatusChanged={setMaintenanceStatus}
          /></>
        )}
      </main>
      <SearchPalette
        currentUser={currentUser}
        initialQuery=""
        onClose={() => setSearchPaletteOpen(false)}
        onOpenResult={openSearchResult}
        open={searchPaletteOpen}
      />
      <NotificationToastStack
        notifications={notificationToasts}
        onAction={handleNotificationAction}
        onDismiss={(id) => setNotificationToasts((current) => current.filter((item) => item.id !== id))}
      />
    </div>
  );
}
