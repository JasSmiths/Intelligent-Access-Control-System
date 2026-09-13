import { api, type ApiRequestOptions, apiError, CHAT_ATTACHMENT_MAX_BYTES, CHAT_ATTACHMENT_MAX_LABEL } from "./client";
import { formatFileSize } from "../lib/format";

export type ChatAttachment = { id: string; filename: string; content_type: string; size_bytes: number; kind: "image" | "text" | "document" | string; url: string; download_url?: string | null; source?: string | null; created_at?: string | null };

export async function uploadChatAttachment(file: File, sessionId: string | null): Promise<ChatAttachment> {
  if (file.size > CHAT_ATTACHMENT_MAX_BYTES) throw new Error(`${file.name || "Attachment"} is ${formatFileSize(file.size)}. Attachments must be ${CHAT_ATTACHMENT_MAX_LABEL} or smaller.`);
  const body = new FormData();
  body.append("file", file);
  const suffix = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await fetch(`/api/v1/ai/chat/upload${suffix}`, { method: "POST", credentials: "include", body });
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<ChatAttachment>;
}


export type ChatResponse = {
  session_id: string;
  provider: string;
  text: string;
  tool_results: Array<Record<string, unknown>>;
  attachments: ChatAttachment[];
  pending_action: Record<string, unknown> | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
};
export type ChatApprovalInspection = {
  status: "pending" | "in_progress" | "completed" | "unknown" | "cancelled" | "expired" | "unavailable";
  pending_action: Record<string, unknown> | null;
  result: ChatResponse | null;
};
export function inspectChatApproval(sessionId: string, confirmationId: string, options: ApiRequestOptions = {}) {
  const query = new URLSearchParams({ session_id: sessionId });
  return api.get<ChatApprovalInspection>(`/api/v1/ai/chat/approvals/${encodeURIComponent(confirmationId)}?${query}`, options);
}

export type ChatApprovalReference = {
  confirmation_id: string;
  operation_id: string;
  session_id: string | null;
  status: Exclude<ChatApprovalInspection["status"], "unavailable">;
  created_at: string;
  expires_at: string;
};
export type ChatApprovalPage = { items: ChatApprovalReference[]; next_cursor: string | null };
export function listChatApprovals(beforeId?: string, options: ApiRequestOptions = {}) {
  const query = new URLSearchParams({ limit: "25" });
  if (beforeId) query.set("before_id", beforeId);
  return api.get<ChatApprovalPage>(`/api/v1/ai/chat/approvals?${query}`, options);
}
