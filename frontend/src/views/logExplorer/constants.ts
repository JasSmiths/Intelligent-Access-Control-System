import {
  Activity,
  Bot,
  Car,
  Construction,
  Database,
  DoorOpen,
  GitBranch,
  PlugZap,
  RefreshCcw,
  ShieldCheck,
  Terminal,
  Workflow
} from "lucide-react";
import type React from "react";

import { LogSourceKey, LogsFilters } from "./types";

export const defaultLogsFilters: LogsFilters = {
  query: "",
  timeRange: "15m",
  level: "all",
  status: "all",
  actor: "",
  subject: ""
};

export const savedFiltersStorageKey = "iacs.logs.savedFilters";

export const sourceTabs: Array<{
  key: LogSourceKey;
  label: string;
  shortLabel: string;
  icon: React.ElementType;
  description: string;
}> = [
  { key: "all", label: "All", shortLabel: "All", icon: Activity, description: "Every telemetry and audit stream." },
  { key: "lpr", label: "LPR", shortLabel: "LPR", icon: Car, description: "Plate reads and access decisions." },
  { key: "access", label: "Access", shortLabel: "Access", icon: ShieldCheck, description: "Presence, anomalies, and alert actions." },
  { key: "gate", label: "Gate", shortLabel: "Gate", icon: DoorOpen, description: "Gate malfunctions and recovery." },
  { key: "maintenance", label: "Maintenance", shortLabel: "Maint.", icon: Construction, description: "Kill-switch and maintenance changes." },
  { key: "automation", label: "Automations", shortLabel: "Auto", icon: Workflow, description: "Automation engine executions." },
  { key: "ai", label: "AI", shortLabel: "AI", icon: Bot, description: "Alfred tools and provider actions." },
  { key: "crud", label: "CRUD", shortLabel: "CRUD", icon: Database, description: "Users, directory, schedules, settings." },
  { key: "api", label: "API/Webhooks", shortLabel: "API", icon: GitBranch, description: "Inbound API and webhook traces." },
  { key: "integrations", label: "Integrations", shortLabel: "Integr.", icon: PlugZap, description: "HA, DVLA, notification providers." },
  { key: "updates", label: "Updates", shortLabel: "Updates", icon: RefreshCcw, description: "Dependency updates and rollbacks." },
  { key: "live", label: "Live", shortLabel: "Live", icon: Terminal, description: "Current realtime websocket stream." }
];

export const traceCategories: Partial<Record<LogSourceKey, string>> = {
  lpr: "lpr_telemetry",
  gate: "gate_malfunction",
  api: "webhooks_api",
  updates: "dependency_updates",
  automation: "automation_engine"
};

export const auditCategories: Partial<Record<LogSourceKey, string>> = {
  access: "access_presence",
  ai: "alfred_ai",
  crud: "entity_management",
  integrations: "integrations",
  maintenance: "maintenance_mode"
};

export const auditActionPrefixes: Partial<Record<LogSourceKey, string>> = {
  maintenance: "maintenance_mode."
};

export const traceCategorySources: Record<string, LogSourceKey> = {
  lpr_telemetry: "lpr",
  gate_malfunction: "gate",
  webhooks_api: "api",
  dependency_updates: "updates",
  automation_engine: "automation"
};

export const auditCategorySources: Record<string, LogSourceKey> = {
  access_presence: "access",
  alfred_ai: "ai",
  entity_management: "crud",
  integrations: "integrations",
  maintenance_mode: "maintenance"
};

export const timeRangeOptions = [
  { value: "15m", label: "Last 15 minutes", minutes: 15 },
  { value: "1h", label: "Last hour", minutes: 60 },
  { value: "24h", label: "Last 24 hours", minutes: 24 * 60 },
  { value: "7d", label: "Last 7 days", minutes: 7 * 24 * 60 },
  { value: "30d", label: "Last 30 days", minutes: 30 * 24 * 60 },
  { value: "all", label: "All time", minutes: null }
];

export const levelOptions = [
  { value: "all", label: "All levels" },
  { value: "info", label: "Info" },
  { value: "warning", label: "Warning" },
  { value: "error", label: "Error" },
  { value: "purple", label: "AI action" }
];

export const statusOptions = [
  { value: "all", label: "All statuses" },
  { value: "ok", label: "OK / Success" },
  { value: "warning", label: "Warning" },
  { value: "error", label: "Error / Failed" },
  { value: "pending_confirmation", label: "Pending confirmation" },
  { value: "active", label: "Active" },
  { value: "resolved", label: "Resolved" },
  { value: "fubar", label: "FUBAR" }
];
