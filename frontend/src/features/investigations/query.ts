import type { InvestigationQuery } from "./types";

export const DEFAULT_INVESTIGATION_QUERY: InvestigationQuery = {
  range: "24h",
  from: "",
  to: "",
  device: "",
  automation: "",
  schedule: "",
  integration: "",
  category: "",
  outcome: "",
  severity: "",
  actor: "",
  trigger: "",
  trace: "",
  q: "",
  includeRoutine: false
};

const RANGE_VALUES = new Set<InvestigationQuery["range"]>(["today", "yesterday", "24h", "7d", "custom"]);

export function readInvestigationQuery(search = window.location.search): InvestigationQuery {
  const params = new URLSearchParams(search);
  const rawRange = params.get("range") as InvestigationQuery["range"] | null;
  return {
    range: rawRange && RANGE_VALUES.has(rawRange) ? rawRange : DEFAULT_INVESTIGATION_QUERY.range,
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
    device: params.get("device") ?? "",
    automation: params.get("automation") ?? "",
    schedule: params.get("schedule") ?? "",
    integration: params.get("integration") ?? "",
    category: params.get("category") ?? "",
    outcome: params.get("outcome") ?? "",
    severity: params.get("severity") ?? "",
    actor: params.get("actor") ?? "",
    trigger: params.get("trigger") ?? "",
    trace: params.get("trace") ?? "",
    q: params.get("q") ?? "",
    includeRoutine: params.get("include_routine") === "true"
  };
}

export function queryToSearchParams(query: InvestigationQuery, includeDefaults = false): URLSearchParams {
  const params = new URLSearchParams();
  if (includeDefaults || query.range !== DEFAULT_INVESTIGATION_QUERY.range) params.set("range", query.range);
  if (query.range === "custom" && query.from) params.set("from", query.from);
  if (query.range === "custom" && query.to) params.set("to", query.to);
  if (query.device) params.set("device", query.device);
  if (query.automation) params.set("automation", query.automation);
  if (query.schedule) params.set("schedule", query.schedule);
  if (query.integration) params.set("integration", query.integration);
  if (query.category) params.set("category", query.category);
  if (query.outcome) params.set("outcome", query.outcome);
  if (query.severity) params.set("severity", query.severity);
  if (query.actor) params.set("actor", query.actor);
  if (query.trigger) params.set("trigger", query.trigger);
  if (query.trace) params.set("trace", query.trace);
  if (query.q.trim()) params.set("q", query.q.trim());
  if (query.includeRoutine) params.set("include_routine", "true");
  return params;
}

export function writeInvestigationQuery(query: InvestigationQuery) {
  const params = queryToSearchParams(query);
  const search = params.toString();
  const nextUrl = `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`;
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (nextUrl !== currentUrl) window.history.replaceState(window.history.state, "", nextUrl);
}

export function activeFilterCount(query: InvestigationQuery) {
  return [
    query.range !== DEFAULT_INVESTIGATION_QUERY.range,
    query.device,
    query.automation,
    query.schedule,
    query.integration,
    query.category,
    query.outcome,
    query.severity,
    query.actor,
    query.trigger,
    query.trace,
    query.q.trim(),
    query.includeRoutine
  ].filter(Boolean).length;
}

function zonedParts(date: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
  return Date.UTC(values.year, values.month - 1, values.day, values.hour, values.minute, values.second);
}

/** Converts an unambiguous wall-clock input using the site's IANA timezone, never the browser timezone. */
export function zonedWallTimeToIso(value: string, timezone: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) return value;
  const wallUtc = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]), Number(match[6] ?? 0));
  let candidate = wallUtc;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const difference = zonedParts(new Date(candidate), timezone) - wallUtc;
    if (!difference) break;
    candidate -= difference;
  }
  return new Date(candidate).toISOString();
}
