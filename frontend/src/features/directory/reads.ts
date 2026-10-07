import React from "react";
import { readDirectory, lookupDirectory, type DirectoryItem, type DirectoryKind, type DirectoryPage } from "../../api/directory";
import { ApiError, isAbortError } from "../../api/client";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { directoryEventMatches } from "./invalidation";

type DirectoryReadOptions = { query?: string; active?: boolean; groupId?: string; enabled?: boolean; refreshToken?: string | number };

export function useDirectoryPage<K extends DirectoryKind>(kind: K, seedItems: DirectoryItem<K>[], options: DirectoryReadOptions = {}) {
  const { query = "", active, groupId, enabled = true } = options;
  const refreshToken = options.refreshToken ?? 0;
  const [page, setPage] = React.useState<DirectoryPage<DirectoryItem<K>>>({ items: seedItems, total: seedItems.length, next_cursor: null });
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [previous, setPrevious] = React.useState<(string | null)[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [ready, setReady] = React.useState(false);
  const [error, setError] = React.useState("");
  // Store the page's query alongside its cursor so a changed search never reuses an old cursor.
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
  }, [kind, query, active, cursor, groupId, refreshToken]);
  const refreshRead = useCoordinatedRead(read, eventMatches, enabled && sameScope, query ? 150 : 0);
  const refresh = React.useCallback(() => { void refreshRead().catch(() => undefined); }, [refreshRead]);
  return {
    ...page, loading, ready, error, refresh, canPrevious: previous.length > 0,
    next: () => { if (page.next_cursor) { setPrevious((current) => [...current, cursor]); setCursor(page.next_cursor); } },
    previous: () => { if (previous.length) { setCursor(previous[previous.length - 1]); setPrevious((current) => current.slice(0, -1)); } }
  };
}

export function useDirectoryIdentities<K extends DirectoryKind>(kind: K, seedItems: DirectoryItem<K>[], selectedIds: string[], providedRefreshToken?: string | number, enabled = true) {
  const refreshToken = providedRefreshToken ?? 0;
  const [selectedItems, setSelectedItems] = React.useState<DirectoryItem<K>[]>([]);
  const [error, setError] = React.useState("");
  const key = [...new Set(selectedIds)].sort().join("|");
  const seedSignature = JSON.stringify(seedItems);
  const [resolvedKey, setResolvedKey] = React.useState<string | null>(null);
  const eventMatches = React.useCallback((event: Parameters<typeof directoryEventMatches>[1]) => directoryEventMatches(kind, event), [kind]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setError("");
    try {
      const items = await lookupDirectory(kind, key.split("|"), { signal });
      if (!signal.aborted) { setSelectedItems(items); setResolvedKey(key); }
    } catch (failure) {
      if (!signal.aborted && !isAbortError(failure)) {
        setError(failure instanceof Error ? failure.message : "Unable to load selected directory records");
        if (failure instanceof ApiError && failure.status === 404) { setSelectedItems([]); setResolvedKey(key); }
      }
      throw failure;
    }
  }, [key, kind, seedSignature, refreshToken]);
  useCoordinatedRead(read, eventMatches, enabled && Boolean(key));
  const items = React.useMemo(() => {
    const selected = new Set(selectedIds);
    const merged = new Map<string, DirectoryItem<K>>();
    for (const item of seedItems) if (resolvedKey !== key && selected.has(item.id)) merged.set(item.id, item);
    for (const item of selectedItems) if (selected.has(item.id)) merged.set(item.id, item);
    return [...merged.values()];
  }, [seedItems, selectedItems, key, resolvedKey]);
  const missingIds = resolvedKey === key ? selectedIds.filter((id) => !selectedItems.some((item) => item.id === id)) : [];
  return { items, error, missingIds, ready: !key || resolvedKey === key };
}

export function useDirectoryOptions<K extends DirectoryKind>(kind: K, seedItems: DirectoryItem<K>[], selectedIds: string[] = [], enabled = true, providedRefreshToken?: string | number) {
  const refreshToken = providedRefreshToken ?? 0;
  const [query, setQuery] = React.useState("");
  const seedSignature = JSON.stringify(seedItems);
  const page = useDirectoryPage(kind, seedItems, { query, enabled, refreshToken: `${refreshToken}:${seedSignature}` });
  const selected = useDirectoryIdentities(kind, seedItems, selectedIds, refreshToken, enabled);
  const rows = React.useMemo(() => {
    const merged = new Map<string, DirectoryItem<K>>();
    for (const item of page.items) if (!selected.missingIds.includes(item.id)) merged.set(item.id, item);
    for (const item of selected.items) merged.set(item.id, item);
    return { items: [...merged.values()], pageItems: page.items.filter((item) => !selected.missingIds.includes(item.id)).map((item) => merged.get(item.id) ?? item) };
  }, [selected.items, selected.missingIds.join("|"), page.items]);
  return { ...page, ...rows, query, setQuery, error: page.error || selected.error };
}
