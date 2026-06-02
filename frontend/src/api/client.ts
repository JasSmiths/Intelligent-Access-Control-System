import type { ActionConfirmation, ActionConfirmationOptions } from "./types";
export type ApiRequestOptions = {
  signal?: AbortSignal;
};
const LARGE_JSON_PARSE_YIELD_BYTES = 512 * 1024;
export const CHAT_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024;
export const CHAT_ATTACHMENT_MAX_LABEL = "25 MB";
export function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
function yieldBeforeLargeJsonParse(text: string) {
  if (text.length < LARGE_JSON_PARSE_YIELD_BYTES || typeof window === "undefined") {
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => window.setTimeout(resolve, 0));
}
async function parseApiResponse<T>(response: Response, path: string): Promise<T> {
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text.trim()) return undefined as T;
  try {
    await yieldBeforeLargeJsonParse(text);
    return JSON.parse(text) as T;
  } catch (error) {
    throw new Error(`Malformed JSON response from ${path}: ${error instanceof Error ? error.message : "unable to parse response"}`);
  }
}
export const api = {
  async get<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    const response = await fetch(path, { credentials: "include", signal: options.signal });
    if (!response.ok) throw await apiError(response);
    return parseApiResponse<T>(response, path);
  },
  async post<T>(path: string, body?: unknown, options: ApiRequestOptions = {}): Promise<T> {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      signal: options.signal,
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return parseApiResponse<T>(response, path);
  },
  async patch<T>(path: string, body?: unknown, options: ApiRequestOptions = {}): Promise<T> {
    const response = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      signal: options.signal,
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return parseApiResponse<T>(response, path);
  },
  async put<T>(path: string, body?: unknown, options: ApiRequestOptions = {}): Promise<T> {
    const response = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      signal: options.signal,
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return parseApiResponse<T>(response, path);
  },
  async delete<T = void>(path: string, body?: unknown, options: ApiRequestOptions = {}): Promise<T> {
    const response = await fetch(path, {
      method: "DELETE",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      credentials: "include",
      signal: options.signal,
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return parseApiResponse<T>(response, path);
  }
};
export async function createActionConfirmation(
  action: string,
  payload: Record<string, unknown>,
  options: ActionConfirmationOptions = {}
): Promise<ActionConfirmation> {
  return api.post<ActionConfirmation>("/api/v1/action-confirmations", {
    action,
    payload,
    ...options
  });
}
export async function apiError(response: Response) {
  const statusLabel = `${response.status} ${response.statusText || "Request failed"}`;
  let detail: string | null = null;
  const body = await response.text().catch(() => "");
  if (body.trim()) {
    try {
      const payload = JSON.parse(body) as unknown;
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        const record = payload as Record<string, unknown>;
        detail =
          describeApiErrorDetail(record.detail) ||
          describeApiErrorDetail(record.message) ||
          describeApiErrorDetail(record.error);
      } else {
        detail = describeApiErrorDetail(payload);
      }
    } catch {
      detail = body.trim();
    }
  }
  return new Error(detail && detail !== statusLabel ? `${statusLabel}: ${detail}` : statusLabel);
}
function describeApiErrorDetail(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value.map(describeApiErrorDetail).filter((part): part is string => Boolean(part));
    return parts.length ? parts.join("; ") : null;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const message =
      describeApiErrorDetail(record.msg) ||
      describeApiErrorDetail(record.message) ||
      describeApiErrorDetail(record.detail);
    const location = Array.isArray(record.loc)
      ? record.loc.filter(Boolean).join(".")
      : typeof record.loc === "string"
        ? record.loc
        : null;
    if (message && location) return `${location}: ${message}`;
    if (message) return message;
    return JSON.stringify(record);
  }
  return null;
}
export function wsUrl(path: string) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${path}`;
}
