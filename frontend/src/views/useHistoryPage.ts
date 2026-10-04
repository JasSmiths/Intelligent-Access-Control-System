import React from "react";

import { getHistory } from "../api/history";
import type { HistoryPage } from "../api/history";
import { isAbortError } from "../api/client";

const EMPTY_ITEMS: never[] = [];

export function useHistoryPage<T>(path: string, filters: URLSearchParams, refreshToken = 0, resetToken = 0) {
  const filterKey = filters.toString();
  const [pages, setPages] = React.useState<Array<HistoryPage<T>>>([]);
  const [index, setIndex] = React.useState(0);
  const page = pages[index] ?? null;
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [newActivity, setNewActivity] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);
  const sequenceRef = React.useRef(0);
  const previousFiltersRef = React.useRef(filterKey);
  const previousRefreshRef = React.useRef(refreshToken);
  const previousResetRef = React.useRef(resetToken);

  const load = React.useCallback(async (cursor: string | null, onSuccess: (result: HistoryPage<T>) => void) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const sequence = ++sequenceRef.current;
    setLoading(true);
    setError("");
    try {
      const result = await getHistory<T>(path, new URLSearchParams(filterKey), cursor, controller.signal);
      if (sequence !== sequenceRef.current || controller.signal.aborted) return;
      onSuccess(result);
    } catch (loadError) {
      if (isAbortError(loadError) || sequence !== sequenceRef.current) return;
      setError(loadError instanceof Error ? loadError.message : "History is unavailable.");
    } finally {
      if (sequence === sequenceRef.current) setLoading(false);
    }
  }, [filterKey, path]);

  React.useEffect(() => {
    if (previousFiltersRef.current !== filterKey) {
      previousFiltersRef.current = filterKey;
      setIndex(0);
      setPages([]);
      setNewActivity(false);
    }
    void load(null, (result) => setPages([result]));
    return () => { abortRef.current?.abort(); sequenceRef.current += 1; };
    // Cursor navigation calls load directly; this effect reacts only to filters/path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  React.useEffect(() => {
    if (previousRefreshRef.current === refreshToken) return;
    previousRefreshRef.current = refreshToken;
    if (index > 0) setNewActivity(true);
    else void load(null, (result) => setPages([result]));
  }, [index, load, refreshToken]);

  const next = () => {
    if (!page?.next_cursor || loading) return;
    const nextIndex = index + 1;
    if (pages[nextIndex]) {
      setIndex(nextIndex);
      return;
    }
    const cursor = page.next_cursor;
    void load(cursor, (result) => {
      setPages((current) => [...current.slice(0, nextIndex), result]);
      setIndex(nextIndex);
    });
  };
  const previous = () => {
    if (index === 0 || loading) return;
    const previousIndex = index - 1;
    const previousPage = pages[previousIndex];
    if (!previousPage) return;
    setIndex(previousIndex);
  };
  const refresh = () => {
    setIndex(0);
    setNewActivity(false);
    setPages([]);
    void load(null, (result) => setPages([result]));
  };
  React.useEffect(() => {
    if (previousResetRef.current === resetToken) return;
    previousResetRef.current = resetToken;
    refresh();
  // Reset is intentionally driven by the shell's explicit refresh signal.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetToken]);
  return { items: page?.items ?? EMPTY_ITEMS, asOf: page?.as_of ?? null, nextCursor: page?.next_cursor ?? null,
    index, loading, error, newActivity, next, previous, refresh };
}
