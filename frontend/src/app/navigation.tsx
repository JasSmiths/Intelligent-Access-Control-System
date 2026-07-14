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
export const primaryNavItems: Array<{ key: Exclude<ViewKey, "users">; label: string; icon: React.ElementType }> = [
  { key: "dashboard", label: "Dashboard", icon: Home },
  { key: "people", label: "People", icon: UserRound },
  { key: "schedules", label: "Schedules", icon: Clock3 },
  { key: "passes", label: "Passes", icon: ClipboardPaste },
  { key: "vehicles", label: "Vehicles", icon: Car },
  { key: "top_charts", label: "Top Charts", icon: Trophy },
  { key: "events", label: "Events", icon: CalendarDays },
  { key: "movements", label: "Movements", icon: MoveHorizontal },
  { key: "reports", label: "Reports", icon: BarChart3 },
  { key: "settings", label: "Settings", icon: Settings }
];
export const settingsNavItems: Array<{ key: ViewKey; label: string; icon: React.ElementType; adminOnly?: boolean }> = [
  { key: "settings_general", label: "General", icon: SlidersHorizontal },
  { key: "groups", label: "Groups", icon: Users },
  { key: "settings_gates", label: "Gates", icon: DoorOpen },
  { key: "settings_garage_doors", label: "Garage Doors", icon: Warehouse },
  { key: "settings_auth", label: "Auth & Security", icon: Lock },
  { key: "integrations", label: "API & Integrations", icon: PlugZap },
  { key: "alfred_training", label: "Alfred Training", icon: Bot, adminOnly: true },
  { key: "settings_automations", label: "Automations", icon: GitBranch },
  { key: "settings_notifications", label: "Notifications", icon: Bell },
  { key: "alerts", label: "Alerts", icon: Bell },
  { key: "settings_lpr", label: "LPR Tuning", icon: Gauge },
  { key: "settings_zones", label: "Zones", icon: MapPinned },
  { key: "logs", label: "Investigations", icon: FileText, adminOnly: true },
  { key: "users", label: "Users", icon: Users, adminOnly: true }
];
export const settingsNavViewKeys = new Set<ViewKey>(settingsNavItems.map((item) => item.key));
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
function isViewKey(value: string | null): value is ViewKey {
  return Boolean(value && Object.prototype.hasOwnProperty.call(viewPaths, value));
}
export function viewFromPath(pathname: string): ViewKey | null {
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return pathViews[normalized] ?? null;
}
export function initialViewFromLocation(): ViewKey {
  const routeView = viewFromPath(window.location.pathname);
  if (routeView) return routeView;
  const storedView = localStorage.getItem("iacs-active-view");
  return isViewKey(storedView) ? storedView : "dashboard";
}
