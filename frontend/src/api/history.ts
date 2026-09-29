import { api, isAbortError } from "./client";

export type HistoryPage<T> = {
  items: T[];
  next_cursor: string | null;
  as_of: string;
};

export function historyUrl(path: string, filters: URLSearchParams, cursor: string | null, limit = 50) {
  const params = new URLSearchParams(filters);
  params.set("limit", String(limit));
  if (cursor) params.set("cursor", cursor);
  return `${path}?${params}`;
}

export function getHistory<T>(path: string, filters: URLSearchParams, cursor: string | null, signal?: AbortSignal) {
  return api.get<HistoryPage<T>>(historyUrl(path, filters, cursor), { signal });
}
