import { api, type ApiRequestOptions } from "./client";

export type IncomingMessageProvider = "whatsapp" | "discord";
export type IncomingReply = {
  index: number | null;
  operation_id: string | null;
  delivery: string;
  attempted_at: string | null;
  completed_at: string | null;
};
export type IncomingMessage = {
  id: string;
  provider: string;
  provider_message_id: string | null;
  batch_id: string | null;
  state: string;
  origin_kind: string;
  user_id: string | null;
  visitor_pass_id: string | null;
  created_at: string | null;
  received_at: string | null;
  available_at: string | null;
  claimed_at: string | null;
  handled_at: string | null;
  lease_expired: boolean;
  requires_review: boolean;
  review_reason: string | null;
  replies: IncomingReply[];
  result_ids: Partial<Record<"session_id" | "confirmation_id" | "visitor_pass_id" | "notification_run_id" | "feedback_id", string>>;
};
export type IncomingMessagePage = { items: IncomingMessage[]; next_cursor: string | null };

export const incomingMessagesApi = {
  getPage: (provider: IncomingMessageProvider, beforeId?: string, options: ApiRequestOptions = {}) => {
    const query = new URLSearchParams({ limit: "25" });
    if (beforeId) query.set("before_id", beforeId);
    return api.get<IncomingMessagePage>(`/api/v1/integrations/${provider}/incoming?${query}`, options);
  },
  getDetail: (provider: IncomingMessageProvider, id: string, options: ApiRequestOptions = {}) =>
    api.get<IncomingMessage>(`/api/v1/integrations/${provider}/incoming/${encodeURIComponent(id)}`, options),
};
