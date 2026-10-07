import { api, type ApiRequestOptions } from "./client";
import type { VisitorPass } from "../features/passes/types";
export const visitorPassesApi = {
  search(query: string, options: ApiRequestOptions = {}) {
    const params = new URLSearchParams({ limit: "8" });
    if (query.trim()) params.set("q", query.trim());
    return api.get<VisitorPass[]>(`/api/v1/visitor-passes?${params}`, options);
  },
  get(id: string, options: ApiRequestOptions = {}) { return api.get<VisitorPass>(`/api/v1/visitor-passes/${encodeURIComponent(id)}`, options); }
};
