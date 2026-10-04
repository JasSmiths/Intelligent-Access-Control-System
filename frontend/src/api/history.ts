import { api } from "./client";

export type HistoryPage<T> = {
  items: T[];
  next_cursor: string | null;
  as_of: string;
};

export function getHistory<T>(path: string, filters: URLSearchParams, cursor: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams(filters);
  params.set("limit", "50");
  if (cursor) params.set("cursor", cursor);
  return api.get<HistoryPage<T>>(`${path}?${params}`, { signal });
}
