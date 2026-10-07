import React from "react";
import { readDirectory, lookupDirectory, type DirectoryItem, type DirectoryKind, type DirectoryPage } from "../../api/directory";
import { isAbortError } from "../../api/client";

type DirectoryReadOptions = { query?: string; active?: boolean; groupId?: string; enabled?: boolean; refreshToken?: string | number };

// Editors nested inside a route share shell invalidation without forwarding it through every form.
export const DirectoryRefreshContext = React.createContext<string | number>(0);
function useDirectoryRefreshToken(refreshToken?: string | number) {
  const routeRefreshToken = React.useContext(DirectoryRefreshContext);
  return refreshToken ?? routeRefreshToken;
}

export function useDirectoryPage<K extends DirectoryKind>(kind: K, seedItems: DirectoryItem<K>[], options: DirectoryReadOptions = {}) {
  const { query = "", active, groupId, enabled = true } = options;
  const refreshToken = useDirectoryRefreshToken(options.refreshToken);
  const [page, setPage] = React.useState<DirectoryPage<DirectoryItem<K>>>({ items: seedItems, total: seedItems.length, next_cursor: null });
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [previous, setPrevious] = React.useState<(string | null)[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [version, setVersion] = React.useState(0);
  // Store the page's query alongside its cursor so a changed search never reuses an old cursor.
  const [scope, setScope] = React.useState({ query, active, groupId });
  const sameScope = scope.query === query && scope.active === active && scope.groupId === groupId;
  React.useEffect(() => {
    if (sameScope) return;
    setScope({ query, active, groupId }); setCursor(null); setPrevious([]);
  }, [query, active, groupId, sameScope]);
  React.useEffect(() => {
    if (!sameScope || !enabled) return;
    const controller = new AbortController();
    setLoading(true); setError("");
    const timer = window.setTimeout(() => {
      readDirectory(kind, { q: query, active, cursor, group_id: groupId }, { signal: controller.signal }).then((next) => {
        if (!controller.signal.aborted) setPage(next);
      }).catch((failure: unknown) => {
        if (!controller.signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Unable to load directory");
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, query ? 150 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [kind, query, active, cursor, groupId, version, sameScope, enabled, refreshToken]);
  const refresh = React.useCallback(() => { setVersion((current) => current + 1); }, []);
  return {
    ...page, loading, error, refresh, canPrevious: previous.length > 0,
    next: () => { if (page.next_cursor) { setPrevious((current) => [...current, cursor]); setCursor(page.next_cursor); } },
    previous: () => { if (previous.length) { setCursor(previous[previous.length - 1]); setPrevious((current) => current.slice(0, -1)); } }
  };
}

export function useDirectoryIdentities<K extends DirectoryKind>(kind: K, seedItems: DirectoryItem<K>[], selectedIds: string[], providedRefreshToken?: string | number, enabled = true) {
  const refreshToken = useDirectoryRefreshToken(providedRefreshToken);
  const [selectedItems, setSelectedItems] = React.useState<DirectoryItem<K>[]>([]);
  const [error, setError] = React.useState("");
  const key = [...new Set(selectedIds)].sort().join("|");
  const seedSignature = JSON.stringify(seedItems);
  React.useEffect(() => {
    const controller = new AbortController();
    setError("");
    if (!key) { setSelectedItems([]); return () => controller.abort(); }
    if (!enabled) return () => controller.abort();
    lookupDirectory(kind, key.split("|"), { signal: controller.signal }).then((items) => {
      if (!controller.signal.aborted) setSelectedItems(items);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Unable to load selected directory records");
    });
    return () => controller.abort();
  }, [key, kind, seedSignature, refreshToken, enabled]);
  const items = React.useMemo(() => {
    const selected = new Set(selectedIds);
    const merged = new Map<string, DirectoryItem<K>>();
    for (const item of seedItems) if (selected.has(item.id)) merged.set(item.id, item);
    for (const item of selectedItems) if (selected.has(item.id)) merged.set(item.id, item);
    return [...merged.values()];
  }, [seedItems, selectedItems, key]);
  return { items, error };
}

export function useDirectoryOptions<K extends DirectoryKind>(kind: K, seedItems: DirectoryItem<K>[], selectedIds: string[] = [], enabled = true, providedRefreshToken?: string | number) {
  const refreshToken = useDirectoryRefreshToken(providedRefreshToken);
  const [query, setQuery] = React.useState("");
  const seedSignature = JSON.stringify(seedItems);
  const page = useDirectoryPage(kind, seedItems, { query, enabled, refreshToken: `${refreshToken}:${seedSignature}` });
  const selected = useDirectoryIdentities(kind, seedItems, selectedIds, refreshToken, enabled);
  const rows = React.useMemo(() => {
    const merged = new Map<string, DirectoryItem<K>>();
    for (const item of page.items) merged.set(item.id, item);
    for (const item of selected.items) merged.set(item.id, item);
    return { items: [...merged.values()], pageItems: page.items.map((item) => merged.get(item.id) ?? item) };
  }, [selected.items, page.items]);
  return { ...page, ...rows, query, setQuery, error: page.error || selected.error };
}
