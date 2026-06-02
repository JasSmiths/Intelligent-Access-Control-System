import { apiError, CHAT_ATTACHMENT_MAX_BYTES, CHAT_ATTACHMENT_MAX_LABEL } from "./client";
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
