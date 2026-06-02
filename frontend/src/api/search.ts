import { api } from "./client";
import type { ViewKey } from "./types";
export type GlobalSearchResultType = "person" | "vehicle" | "group" | "schedule" | "visitor_pass" | "access_event" | "alert" | "user" | "automation_rule" | "notification_rule";
export type GlobalSearchResult = { id: string; type: GlobalSearchResultType; label: string; subtitle: string; filter_value: string; target: { view: ViewKey; route_search?: string }; preview: { title: string; body?: string | null; badges: string[]; facts: Array<{ label: string; value: string }> } };
export type SearchPaletteItem = Omit<GlobalSearchResult, "type"> & { type: GlobalSearchResultType | "shortcut" };
export const searchApi = {
  search(query: string, options: { signal?: AbortSignal } = {}) {
    const params = new URLSearchParams({ q: query, limit: "12" });
    return api.get<GlobalSearchResult[]>(`/api/v1/search?${params}`, options);
  }
};
