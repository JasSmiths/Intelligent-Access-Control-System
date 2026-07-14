import React from "react";
import {
  getActivityEpisode,
  getActivityPage,
  getInvestigationFilters,
  getInvestigationOverview,
  investigateQuestion
} from "../../api/investigations";
import { isAbortError } from "../../api/client";
import { DEFAULT_INVESTIGATION_QUERY, readInvestigationQuery, writeInvestigationQuery } from "./query";
import type {
  ActivityEpisode,
  ActivityEpisodeDetail,
  ActivityPage,
  InvestigationAnswer,
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

export function useInvestigationData(query: InvestigationQuery, refreshToken: number) {
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

  React.useEffect(() => {
    const controller = new AbortController();
    getInvestigationFilters({ signal: controller.signal })
      .then(setFilters)
      .catch((error) => {
        if (!isAbortError(error)) {
          if (String(error).includes("403")) setForbidden(true);
          setError(errorMessage(error, "Unable to load investigation filters"));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [refreshToken]);

  React.useEffect(() => {
    const controller = new AbortController();
    getInvestigationOverview({ signal: controller.signal })
      .then(setOverview)
      .catch((error) => {
        if (!isAbortError(error)) {
          if (String(error).includes("403")) setForbidden(true);
          setError(errorMessage(error, "Unable to load the investigation overview"));
        }
      });
    return () => controller.abort();
  }, [refreshToken]);

  React.useEffect(() => {
    if (!filters) return undefined;
    const controller = new AbortController();
    loadMoreAbortRef.current?.abort();
    const sequence = ++sequenceRef.current;
    setLoading(true);
    setError("");
    setForbidden(false);
    getActivityPage(query, filters.site_timezone, null, 30, { signal: controller.signal }).then((activityResult) => {
      if (sequence !== sequenceRef.current || controller.signal.aborted) return;
      setPage(activityResult);
      setItems(activityResult.items);
      setLoading(false);
    }).catch((error) => {
      if (sequence !== sequenceRef.current || controller.signal.aborted || isAbortError(error)) return;
      if (String(error).includes("403")) setForbidden(true);
      setPage(null);
      setItems([]);
      setError(errorMessage(error, "Unable to load recorded activity"));
      setLoading(false);
    });
    return () => controller.abort();
  }, [filters, query, refreshToken]);

  const loadMore = React.useCallback(async () => {
    if (!page?.next_cursor || loadingMore) return;
    loadMoreAbortRef.current?.abort();
    const controller = new AbortController();
    loadMoreAbortRef.current = controller;
    setLoadingMore(true);
    try {
      const nextPage = await getActivityPage(query, page.site_timezone, page.next_cursor, 30, { signal: controller.signal });
      setPage(nextPage);
      setItems((current) => {
        const seen = new Set(current.map((item) => item.episode_id));
        return [...current, ...nextPage.items.filter((item) => !seen.has(item.episode_id))];
      });
    } catch (error) {
      if (!isAbortError(error)) setError(errorMessage(error, "Unable to load more activity"));
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  }, [loadingMore, page, query]);

  React.useEffect(() => () => loadMoreAbortRef.current?.abort(), []);

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
      setDetails((current) => ({ ...current, [episodeId]: detail }));
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

export function useQuestionInvestigation(scope: InvestigationQuery, timezone: string) {
  const [answer, setAnswer] = React.useState<InvestigationAnswer | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const controllerRef = React.useRef<AbortController | null>(null);

  const submit = React.useCallback(async (question: string) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError("");
    try {
      setAnswer(await investigateQuestion(question, scope, timezone, { signal: controller.signal }));
    } catch (error) {
      if (!isAbortError(error)) setError(errorMessage(error, "Unable to investigate that question"));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [scope, timezone]);

  React.useEffect(() => () => controllerRef.current?.abort(), []);
  return { answer, loading, error, submit, clear: () => setAnswer(null) };
}
