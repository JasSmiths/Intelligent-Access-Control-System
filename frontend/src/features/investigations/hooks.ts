import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import type { RealtimeMessage } from "../../api/types";
const investigationEventMatches = (event: RealtimeMessage) => event.type === "audit.log.created" || event.type.startsWith("automation.run.") || event.type === "access_event.finalized" || event.type === "access_event.finalize_failed";
import React from "react";
import {
  getActivityEpisode,
  getActivityPage,
  getInvestigationFilters,
  getInvestigationOverview
} from "../../api/investigations";
import { isAbortError } from "../../api/client";
import { DEFAULT_INVESTIGATION_QUERY, readInvestigationQuery, writeInvestigationQuery } from "./query";
import type {
  ActivityEpisode,
  ActivityEpisodeDetail,
  ActivityPage,
  InvestigationFilterCatalog,
  InvestigationOverview,
  InvestigationQuery
} from "./types";

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export function useInvestigationQueryState() {
  const [query, setQuery] = React.useState<InvestigationQuery>(() => readInvestigationQuery());
  const [committedQuery, setCommittedQuery] = React.useState(query);

  React.useEffect(() => {
    const timer = window.setTimeout(() => setCommittedQuery(query), query.q !== committedQuery.q ? 350 : 0);
    return () => window.clearTimeout(timer);
  }, [committedQuery.q, query]);

  React.useEffect(() => writeInvestigationQuery(committedQuery), [committedQuery]);

  React.useEffect(() => {
    const onPopState = () => {
      const next = readInvestigationQuery();
      setQuery(next);
      setCommittedQuery(next);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const updateQuery = React.useCallback((patch: Partial<InvestigationQuery>) => {
    setQuery((current) => ({ ...current, ...patch }));
  }, []);

  const resetQuery = React.useCallback(() => {
    setQuery(DEFAULT_INVESTIGATION_QUERY);
    setCommittedQuery(DEFAULT_INVESTIGATION_QUERY);
  }, []);

  return { query, committedQuery, updateQuery, resetQuery };
}

export function useInvestigationData(query: InvestigationQuery) {
  const [filters, setFilters] = React.useState<InvestigationFilterCatalog | null>(null);
  const [overview, setOverview] = React.useState<InvestigationOverview | null>(null);
  const [page, setPage] = React.useState<ActivityPage | null>(null);
  const [items, setItems] = React.useState<ActivityEpisode[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  const [forbidden, setForbidden] = React.useState(false);
  const sequenceRef = React.useRef(0);
  const loadMoreAbortRef = React.useRef<AbortController | null>(null);

  const recordFailure = React.useCallback((failure: unknown, fallback: string) => {
    if (String(failure).includes("403")) setForbidden(true);
    setError(errorMessage(failure, fallback));
  }, []);
  const readFilters = React.useCallback(async (signal: AbortSignal) => {
    try { const result = await getInvestigationFilters({ signal }); if (!signal.aborted) setFilters(result); }
    catch (failure) { if (!signal.aborted && !isAbortError(failure)) { recordFailure(failure, "Unable to load investigation filters"); setLoading(false); } throw failure; }
  }, [recordFailure]);
  const readOverview = React.useCallback(async (signal: AbortSignal) => {
    try { const result = await getInvestigationOverview({ signal }); if (!signal.aborted) setOverview(result); }
    catch (failure) { if (!signal.aborted && !isAbortError(failure)) recordFailure(failure, "Unable to load the investigation overview"); throw failure; }
  }, [recordFailure]);
  useCoordinatedRead(readFilters, investigationEventMatches);
  useCoordinatedRead(readOverview, investigationEventMatches);
  const timezone = filters?.site_timezone;
  const readPage = React.useCallback(async (signal: AbortSignal) => {
    if (!timezone) return;
    loadMoreAbortRef.current?.abort(); loadMoreAbortRef.current = null;
    const sequence = ++sequenceRef.current;
    setLoadingMore(false); setPage(null); setLoading(true); setError(""); setForbidden(false);
    try {
      const result = await getActivityPage(query, timezone, null, 30, { signal });
      if (!signal.aborted && sequence === sequenceRef.current) { setPage(result); setItems(result.items); }
    } catch (failure) {
      if (!signal.aborted && sequence === sequenceRef.current && !isAbortError(failure)) { recordFailure(failure, "Unable to load recorded activity"); setPage(null); setItems([]); }
      throw failure;
    } finally { if (!signal.aborted && sequence === sequenceRef.current) setLoading(false); }
  }, [query, timezone, recordFailure]);
  useCoordinatedRead(readPage, investigationEventMatches, Boolean(timezone));

  const loadMore = React.useCallback(async () => {
    if (!page?.next_cursor || loading || loadingMore) return;
    loadMoreAbortRef.current?.abort();
    const controller = new AbortController();
    const sequence = sequenceRef.current;
    loadMoreAbortRef.current = controller;
    setLoadingMore(true);
    try {
      const nextPage = await getActivityPage(query, page.site_timezone, page.next_cursor, 30, { signal: controller.signal });
      if (controller.signal.aborted || sequence !== sequenceRef.current) return;
      setPage(nextPage);
      setItems((current) => {
        const seen = new Set(current.map((item) => item.episode_id));
        return [...current, ...nextPage.items.filter((item) => !seen.has(item.episode_id))];
      });
    } catch (error) {
      if (!controller.signal.aborted && sequence === sequenceRef.current && !isAbortError(error)) {
        setError(errorMessage(error, "Unable to load more activity"));
      }
    } finally {
      if (loadMoreAbortRef.current === controller) {
        loadMoreAbortRef.current = null;
        setLoadingMore(false);
      }
    }
  }, [loading, loadingMore, page, query]);

  React.useEffect(() => () => {
    loadMoreAbortRef.current?.abort();
    loadMoreAbortRef.current = null;
  }, []);

  return {
    filters,
    overview,
    page,
    items,
    loading,
    loadingMore,
    error,
    forbidden,
    loadMore
  };
}

export function useEpisodeDetails() {
  const [details, setDetails] = React.useState<Record<string, ActivityEpisodeDetail>>({});
  const [loadingIds, setLoadingIds] = React.useState<Set<string>>(() => new Set());
  const [errors, setErrors] = React.useState<Record<string, string>>({});
  const controllers = React.useRef(new Map<string, AbortController>());

  const load = React.useCallback(async (episodeId: string) => {
    if (details[episodeId] || loadingIds.has(episodeId)) return;
    const controller = new AbortController();
    controllers.current.set(episodeId, controller);
    setLoadingIds((current) => new Set(current).add(episodeId));
    setErrors((current) => ({ ...current, [episodeId]: "" }));
    try {
      const detail = await getActivityEpisode(episodeId, { signal: controller.signal });
      if (!controller.signal.aborted) setDetails((current) => ({ ...current, [episodeId]: detail }));
    } catch (error) {
      if (!isAbortError(error)) setErrors((current) => ({ ...current, [episodeId]: errorMessage(error, "Unable to load evidence") }));
    } finally {
      setLoadingIds((current) => {
        const next = new Set(current);
        next.delete(episodeId);
        return next;
      });
      controllers.current.delete(episodeId);
    }
  }, [details, loadingIds]);

  React.useEffect(() => () => controllers.current.forEach((controller) => controller.abort()), []);
  return { details, loadingIds, errors, load };
}
