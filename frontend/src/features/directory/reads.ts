import React from "react";
import { readDirectory, lookupDirectory, type DirectoryItem, type DirectoryKind, type DirectoryPage } from "../../api/directory";
import { ApiError, isAbortError } from "../../api/client";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { directoryEventMatches } from "./invalidation";

type DirectoryReadOptions = { query?: string; active?: boolean; groupId?: string; enabled?: boolean };
export function useDirectoryPage<K extends DirectoryKind>(kind: K, options: DirectoryReadOptions = {}) {
  const { query = "", active, groupId, enabled = true } = options;
  const [page, setPage] = React.useState<DirectoryPage<DirectoryItem<K>>>({ items: [], total: 0, next_cursor: null });
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [previous, setPrevious] = React.useState<(string | null)[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [ready, setReady] = React.useState(false);
  const [error, setError] = React.useState("");
  // A changed query cannot reuse the previous query's cursor.
  const [scope, setScope] = React.useState({ query, active, groupId });
  const sameScope = scope.query === query && scope.active === active && scope.groupId === groupId;
  React.useEffect(() => {
    if (sameScope) return;
    setScope({ query, active, groupId }); setCursor(null); setPrevious([]);
  }, [query, active, groupId, sameScope]);
  const eventMatches = React.useCallback((event: Parameters<typeof directoryEventMatches>[1]) => directoryEventMatches(kind, event), [kind]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setLoading(true); setError("");
    try {
      const next = await readDirectory(kind, { q: query, active, cursor, group_id: groupId }, { signal });
      if (!signal.aborted) { setPage(next); setReady(true); }
    } catch (failure) {
      if (!signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Unable to load directory");
      throw failure;
    } finally { if (!signal.aborted) setLoading(false); }
  }, [kind, query, active, cursor, groupId]);
  const refresh = useCoordinatedRead(read, eventMatches, enabled && sameScope, query ? 150 : 0);
  return {
    ...page, loading, ready, error, refresh, removeAccepted: (id: string) => setPage((current) => ({ ...current, items: current.items.filter((item) => item.id !== id), total: Math.max(0, current.total - Number(current.items.some((item) => item.id === id))) })), canPrevious: previous.length > 0,
    next: () => { if (page.next_cursor) { setPrevious((current) => [...current, cursor]); setCursor(page.next_cursor); } },
    previous: () => { if (previous.length) { setCursor(previous[previous.length - 1]); setPrevious((current) => current.slice(0, -1)); } }
  };
}
export function useDirectoryIdentities<K extends DirectoryKind>(kind: K, selectedIds: string[], enabled = true) {
  const key = [...new Set(selectedIds)].sort().join("|");
  const identity = `${kind}:${key}`;
  const [resolved, setResolved] = React.useState<{ identity: string; items: DirectoryItem<K>[] } | null>(null);
  const [failure, setFailure] = React.useState<{ identity: string; message: string } | null>(null);
  const eventMatches = React.useCallback((event: Parameters<typeof directoryEventMatches>[1]) => directoryEventMatches(kind, event), [kind]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setFailure(null);
    try {
      const items = await lookupDirectory(kind, key.split("|"), { signal });
      if (!signal.aborted) setResolved({ identity, items });
    } catch (error) {
      if (!signal.aborted && !isAbortError(error)) {
        setFailure({ identity, message: error instanceof Error ? error.message : "Unable to load selected directory records" });
        if (error instanceof ApiError && error.status === 404) setResolved({ identity, items: [] });
      }
      throw error;
    }
  }, [key, kind, identity]);
  const refresh = useCoordinatedRead(read, eventMatches, enabled && Boolean(key));
  const items = resolved?.identity === identity ? resolved.items : [];
  const ready = !key || resolved?.identity === identity;
  return { items, ready, refresh, error: enabled && key && failure?.identity === identity ? failure.message : "",
    missingIds: ready ? selectedIds.filter((id) => !items.some((item) => item.id === id)) : [] };
}
export function useDirectoryOptions<K extends DirectoryKind>(kind: K, selectedIds: string[] = [], enabled = true) {
  const [query, setQuery] = React.useState("");
  const page = useDirectoryPage(kind, { query, enabled });
  const selected = useDirectoryIdentities(kind, selectedIds, enabled);
  const rows = React.useMemo(() => {
    const merged = new Map<string, DirectoryItem<K>>();
    for (const item of page.items) if (!selected.missingIds.includes(item.id)) merged.set(item.id, item);
    for (const item of selected.items) merged.set(item.id, item);
    return { items: [...merged.values()], pageItems: page.items.filter((item) => !selected.missingIds.includes(item.id)).map((item) => merged.get(item.id) ?? item) };
  }, [selected.items, selected.missingIds.join("|"), page.items]);
  const refresh = React.useCallback(async () => {
    const results = await Promise.allSettled([page.refresh(), selected.refresh()]);
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  }, [page.refresh, selected.refresh]);
  return { ...page, ...rows, query, setQuery, refresh, error: page.error || selected.error };
}
