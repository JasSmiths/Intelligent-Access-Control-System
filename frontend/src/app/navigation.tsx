import { BarChart3, Bell, Bot, CalendarDays, Car, ClipboardPaste, Clock3, DoorOpen, FileText, Gauge, GitBranch, Home, Lock, MapPinned, MoveHorizontal, PlugZap, Settings, SlidersHorizontal, Trophy, UserRound, Users, Warehouse } from "lucide-react";
import type React from "react";
import type { UserAccount, ViewKey } from "../api/types";
export type ShellDataKey =
  | "presence"
  | "expectedPresence"
  | "events"
  | "anomalies"
  | "people"
  | "vehicles"
  | "groups"
  | "schedules"
  | "integrationStatus"
  | "maintenanceStatus";
const UNIVERSAL_SHELL_DATA_KEYS: ShellDataKey[] = ["anomalies", "maintenanceStatus"];
const ROUTE_SHELL_DATA_KEYS: Record<ViewKey, ShellDataKey[]> = {
  dashboard: ["presence", "expectedPresence", "events", "people", "vehicles", "integrationStatus"],
  people: ["people", "vehicles", "groups", "schedules", "integrationStatus"],
  groups: ["people", "groups"],
  schedules: ["schedules"],
  passes: [],
  vehicles: ["people", "vehicles", "groups", "schedules"],
  top_charts: [],
  events: ["events"],
  movements: [],
  alerts: [],
  reports: ["people", "presence"],
  integrations: ["people", "integrationStatus"],
  logs: [],
  settings: ["groups", "schedules", "vehicles"],
  settings_general: [],
  settings_gates: ["schedules"],
  settings_garage_doors: ["schedules"],
  settings_auth: [],
  alfred_training: [],
  settings_automations: ["people", "vehicles"],
  settings_notifications: ["people", "schedules"],
  settings_lpr: [],
  settings_zones: [],
  users: []
};
export function shellDataKeysForView(view: ViewKey, currentUser: UserAccount | null) {
  const keys = new Set<ShellDataKey>(UNIVERSAL_SHELL_DATA_KEYS);
  (ROUTE_SHELL_DATA_KEYS[view] ?? ROUTE_SHELL_DATA_KEYS.dashboard).forEach((key) => keys.add(key));
  if ((view === "users" || view === "alfred_training") && currentUser?.role !== "admin") {
    ROUTE_SHELL_DATA_KEYS.settings.forEach((key) => keys.add(key));
  }
  return keys;
}
export function criticalShellDataKeysForView(view: ViewKey): ShellDataKey[] {
  if (["people", "groups", "vehicles", "schedules", "settings_gates", "settings_garage_doors", "settings_automations", "settings_notifications"].includes(view)) {
    return ROUTE_SHELL_DATA_KEYS[view];
  }
  return [];
}
export type NavigationItem = { key: ViewKey; label: string; icon: React.ElementType; group: "Operations" | "Access" | "Insights" | "Settings"; adminOnly?: boolean };
export const primaryNavItems: NavigationItem[] = [
  { key: "dashboard", label: "Dashboard", icon: Home, group: "Operations" },
  { key: "events", label: "Events", icon: CalendarDays, group: "Operations" },
  { key: "movements", label: "Movements", icon: MoveHorizontal, group: "Operations" },
  { key: "alerts", label: "Alerts", icon: Bell, group: "Operations" },
  { key: "people", label: "People", icon: UserRound, group: "Access" },
  { key: "groups", label: "Groups", icon: Users, group: "Access" },
  { key: "vehicles", label: "Vehicles", icon: Car, group: "Access" },
  { key: "schedules", label: "Schedules", icon: Clock3, group: "Access" },
  { key: "passes", label: "Passes", icon: ClipboardPaste, group: "Access" },
  { key: "reports", label: "Reports", icon: BarChart3, group: "Insights" },
  { key: "top_charts", label: "Top Charts", icon: Trophy, group: "Insights" },
  { key: "logs", label: "Investigations", icon: FileText, group: "Insights", adminOnly: true },
  { key: "settings", label: "Settings", icon: Settings, group: "Settings" }
];
export const settingsNavItems: Array<{ key: ViewKey; label: string; icon: React.ElementType; adminOnly?: boolean }> = [
  { key: "settings_general", label: "General", icon: SlidersHorizontal },
  { key: "settings_gates", label: "Gates", icon: DoorOpen },
  { key: "settings_garage_doors", label: "Garage Doors", icon: Warehouse },
  { key: "settings_auth", label: "Auth & Security", icon: Lock },
  { key: "integrations", label: "API & Integrations", icon: PlugZap, adminOnly: true },
  { key: "settings_automations", label: "Automations", icon: GitBranch, adminOnly: true },
  { key: "settings_notifications", label: "Notifications", icon: Bell, adminOnly: true },
  { key: "settings_lpr", label: "LPR Tuning", icon: Gauge },
  { key: "settings_zones", label: "Zones", icon: MapPinned },
  { key: "users", label: "Users", icon: Users, adminOnly: true },
  { key: "alfred_training", label: "Alfred Training", icon: Bot, adminOnly: true }
];
export const settingsNavViewKeys = new Set<ViewKey>(settingsNavItems.map((item) => item.key));
export const navigationItems: NavigationItem[] = [...primaryNavItems, ...settingsNavItems.map((item) => ({ ...item, group: "Settings" as const }))];
export function canAccessView(view: ViewKey, user: UserAccount | null) {
  const item = navigationItems.find((candidate) => candidate.key === view);
  return !item?.adminOnly || user?.role === "admin";
}
export function viewLabel(view: ViewKey) {
  return navigationItems.find((item) => item.key === view)?.label ?? "Dashboard";
}
export const viewPaths: Record<ViewKey, string> = {
  dashboard: "/",
  people: "/people",
  groups: "/groups",
  schedules: "/schedules",
  passes: "/passes",
  vehicles: "/vehicles",
  top_charts: "/top-charts",
  events: "/events",
  movements: "/movements",
  alerts: "/alerts",
  reports: "/reports",
  integrations: "/integrations",
  logs: "/logs",
  settings: "/settings",
  settings_general: "/settings/general",
  settings_gates: "/settings/gates",
  settings_garage_doors: "/settings/garage-doors",
  settings_auth: "/settings/auth-security",
  alfred_training: "/settings/alfred-training",
  settings_automations: "/settings/automations",
  settings_notifications: "/settings/notifications",
  settings_lpr: "/settings/lpr-tuning",
  settings_zones: "/settings/zones",
  users: "/settings/users"
};
const pathViews = Object.entries(viewPaths).reduce<Record<string, ViewKey>>((acc, [viewKey, path]) => {
  acc[path] = viewKey as ViewKey;
  return acc;
}, {});
export function viewFromPath(pathname: string): ViewKey | null {
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return pathViews[normalized] ?? null;
}
export function initialViewFromLocation(): ViewKey {
  const routeView = viewFromPath(window.location.pathname);
  if (routeView) return routeView;
  return "dashboard";
}
