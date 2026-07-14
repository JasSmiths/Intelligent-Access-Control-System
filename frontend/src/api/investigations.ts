import { api, type ApiRequestOptions } from "./client";
import type {
  ActivityEpisodeDetail,
  ActivityPage,
  InvestigationAnswer,
  InvestigationFilterCatalog,
  InvestigationOverview,
  InvestigationQuery
} from "../features/investigations/types";
import { queryToSearchParams, zonedWallTimeToIso } from "../features/investigations/query";

const BASE = "/api/v1/telemetry";

function activityParams(query: InvestigationQuery, timezone: string, limit: number, cursor?: string | null) {
  const params = queryToSearchParams(query, true);
  params.delete("range");
  params.set("time", {
    today: "today",
    yesterday: "yesterday",
    "24h": "last_24_hours",
    "7d": "last_7_days",
    custom: "custom"
  }[query.range]);
  if (query.range === "custom") {
    if (query.from) params.set("from", zonedWallTimeToIso(query.from, timezone));
    if (query.to) params.set("to", zonedWallTimeToIso(query.to, timezone));
  }
  params.set("limit", String(limit));
  if (cursor) params.set("cursor", cursor);
  return params;
}

export function getInvestigationFilters(options: ApiRequestOptions = {}) {
  return api.get<InvestigationFilterCatalog>(`${BASE}/investigation-filters`, options);
}

export function getInvestigationOverview(options: ApiRequestOptions = {}) {
  return api.get<InvestigationOverview>(`${BASE}/investigation-overview`, options);
}

export function getActivityPage(
  query: InvestigationQuery,
  timezone: string,
  cursor: string | null = null,
  limit = 30,
  options: ApiRequestOptions = {}
) {
  const params = activityParams(query, timezone, limit, cursor);
  return api.get<ActivityPage>(`${BASE}/activity?${params.toString()}`, options);
}

export function getActivityEpisode(episodeId: string, options: ApiRequestOptions = {}) {
  return api.get<ActivityEpisodeDetail>(`${BASE}/activity/${encodeURIComponent(episodeId)}`, options);
}

export function investigateQuestion(
  question: string,
  scope: InvestigationQuery,
  timezone: string,
  options: ApiRequestOptions = {}
) {
  return api.post<InvestigationAnswer>(`${BASE}/investigate`, {
    question,
    scope: {
      time: {
        today: "today",
        yesterday: "yesterday",
        "24h": "last_24_hours",
        "7d": "last_7_days",
        custom: "custom"
      }[scope.range],
      from_at: scope.from ? zonedWallTimeToIso(scope.from, timezone) : undefined,
      to_at: scope.to ? zonedWallTimeToIso(scope.to, timezone) : undefined,
      device: scope.device || undefined,
      automation: scope.automation || undefined,
      schedule: scope.schedule || undefined,
      integration: scope.integration || undefined,
      category: scope.category || undefined,
      outcome: scope.outcome || undefined,
      severity: scope.severity || undefined,
      actor: scope.actor || undefined,
      trigger: scope.trigger || undefined,
      trace: scope.trace || undefined,
      q: scope.q || undefined,
      include_routine: scope.includeRoutine
    }
  }, options);
}
