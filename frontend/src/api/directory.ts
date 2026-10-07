import { api, type ApiRequestOptions } from "./client";
import type { Person, Vehicle } from "./types";

export type DirectoryPage<T> = { items: T[]; total: number; next_cursor: string | null };
export type DirectoryQuery = { q?: string; active?: boolean; cursor?: string | null; limit?: number; group_id?: string; ids?: string[]; registrations?: string[] };
export type DirectoryKind = "people" | "vehicles";
export type DirectoryItem<K extends DirectoryKind> = K extends "people" ? Person : Vehicle;

function path(kind: DirectoryKind, query: DirectoryQuery) {
  const params = new URLSearchParams({ include_media: "false" });
  if (query.group_id) params.set("group_id", query.group_id);
  if (query.q) params.set("q", query.q);
  if (query.active !== undefined) params.set("active", String(query.active));
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  for (const id of query.ids ?? []) params.append("ids", id);
  for (const registration of query.registrations ?? []) params.append("registrations", registration);
  return `/api/v1/${kind}?${params}`;
}
export function readDirectory<K extends DirectoryKind>(kind: K, query: DirectoryQuery = {}, options: ApiRequestOptions = {}) {
  return api.get<DirectoryPage<DirectoryItem<K>>>(path(kind, query), options);
}
export const listPeople = (query: DirectoryQuery = {}, options: ApiRequestOptions = {}) => readDirectory("people", query, options);
export const listVehicles = (query: DirectoryQuery = {}, options: ApiRequestOptions = {}) => readDirectory("vehicles", query, options);
export const getPerson = (id: string, options: ApiRequestOptions = {}) => api.get<Person>(`/api/v1/people/${encodeURIComponent(id)}`, options);
export const getVehicle = (id: string, options: ApiRequestOptions = {}) => api.get<Vehicle>(`/api/v1/vehicles/${encodeURIComponent(id)}`, options);

export async function lookupDirectory<K extends DirectoryKind>(kind: K, ids: string[], options: ApiRequestOptions = {}): Promise<DirectoryItem<K>[]> {
  const unique = [...new Set(ids)];
  const items: DirectoryItem<K>[] = [];
  // An explicit identity set is bounded per request, and never expands to the whole directory.
  for (let offset = 0; offset < unique.length; offset += 200) {
    const page = await readDirectory(kind, { ids: unique.slice(offset, offset + 200), limit: 200 }, options);
    items.push(...page.items);
  }
  return items;
}
export const lookupPeople = (ids: string[], options: ApiRequestOptions = {}) => lookupDirectory("people", ids, options);
export const lookupVehicles = (ids: string[], options: ApiRequestOptions = {}) => lookupDirectory("vehicles", ids, options);

export async function lookupVehicleRegistrations(registrations: string[], options: ApiRequestOptions = {}): Promise<Vehicle[]> {
  const unique = [...new Set(registrations.map((value) => value.trim().toUpperCase().replaceAll(" ", "")))];
  const items: Vehicle[] = [];
  for (let offset = 0; offset < unique.length; offset += 200) {
    const page = await listVehicles({ registrations: unique.slice(offset, offset + 200), limit: 200 }, options);
    items.push(...page.items);
  }
  return items;
}
