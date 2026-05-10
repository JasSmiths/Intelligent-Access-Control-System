import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Camera,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Command,
  ClipboardPaste,
  Construction,
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  Eye,
  File as FileIcon,
  FileImage,
  FileText,
  Gauge,
  GitBranch,
  HardHat,
  Home,
  Key,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  Loader2,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  MoreHorizontal,
  Play,
  PlugZap,
  Plus,
  Paperclip,
  Pencil,
  RefreshCcw,
  RefreshCw,
  Search,
  Send,
  Smile,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Split,
  Sparkles,
  Sun,
  Terminal,
  Ticket,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Trophy,
  Type,
  Unlock,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  WifiOff,
  X,
  Zap
} from "lucide-react";

import {
  api,
  apiError,
  displayUserName,
  formatFileSize,
  isLlmProviderConfigured,
  isRecord,
  llmProviderDefinitions,
  LlmProviderKey,
  MaintenanceStatus,
  normalizeLlmProvider,
  SettingsMap,
  UserAccount,
  useSettings,
  userInitials,
  wsUrl
} from "../shared";



export type ChatAttachment = {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  kind: "image" | "text" | "document" | string;
  url: string;
  download_url?: string | null;
  source?: string | null;
  created_at?: string | null;
};

export type ChatAttachmentDraft = ChatAttachment & {
  uploadState: "uploading" | "ready" | "error";
  preview_url?: string;
  error?: string;
};

export type ChatConfirmationAction = {
  type: string;
  confirmationId?: string;
  toolName: string;
  toolArguments: Record<string, unknown>;
  target: string;
  displayTarget: string;
  command: string;
  title: string;
  description: string;
  buttonLabel: string;
  pendingLabel: string;
  statusLabel: string;
  userEcho: string;
  sent?: boolean;
  decision?: "confirm" | "cancel";
};

export type ChatToolActivity = {
  id: string;
  batchId?: string;
  tool: string;
  label: string;
  status: "queued" | "running" | "succeeded" | "failed" | "requires_confirmation";
};

export type ChatRunActivity = {
  phase: string;
  label: string;
  detail: string;
  agentsRunning: number;
  activeToolCalls: number;
  completedToolSteps: number;
  awaitingConfirmation: boolean;
  providerError: boolean;
};

export type ChatRetryAction = {
  text: string;
  attachments: ChatAttachment[];
};

export type ChatMessageStatus =
  | "sent_local"
  | "streaming"
  | "completed"
  | "failed"
  | "awaiting_confirmation"
  | "cancelled";

export type ChatMessageItem = {
  id: string;
  role: "user" | "assistant";
  text: string;
  createdAt?: number;
  attachments?: ChatAttachment[];
  confirmationAction?: ChatConfirmationAction | null;
  streaming?: boolean;
  status?: ChatMessageStatus;
  localSeenAt?: number | null;
  retryAction?: ChatRetryAction | null;
  userMessageId?: string | null;
  assistantMessageId?: string | null;
  responseDurationMs?: number | null;
  feedback?: {
    rating?: "up" | "down";
    status?: "saving" | "saved" | "error";
    error?: string;
  };
};

type AlfredAgentStatus = {
  active_mode?: string;
  provider?: string;
  v3_ready?: boolean;
  local_provider_limitation?: string;
  provider_capability?: {
    provider?: string;
    configured?: boolean;
    agent_capable?: boolean;
    local_provider_limited?: boolean;
    reason?: string;
  };
  memory?: {
    enabled?: boolean;
    backend?: string;
  };
};

type ChatCopyMenuState = {
  messageId: string;
  text: string;
  x: number;
  y: number;
};

type ChatFeedbackDraft = {
  messageId: string;
  assistantMessageId: string;
  reason: string;
  idealAnswer: string;
  saving: boolean;
  error: string;
};

type AlfredFeedbackResponse = {
  corrected_answer?: string;
  feedback?: {
    id: string;
  };
};

export function chatRunActivityFromPayload(
  payload: Record<string, unknown>,
  previous: ChatRunActivity | null = null
): ChatRunActivity {
  const rawPhase = String(payload.phase || payload.state || previous?.phase || "working").trim();
  const phase = rawPhase || "working";
  const status = String(payload.status || "").trim();
  const awaitingConfirmation = phase === "awaiting_confirmation" || status === "requires_confirmation";
  const providerError = phase === "provider_error" || status === "provider_error";
  const label = typeof payload.label === "string" && payload.label.trim()
    ? payload.label.trim()
    : chatPhaseLabel(phase);
  const detail = typeof payload.detail === "string" ? payload.detail.trim() : previous?.detail ?? "";
  return {
    phase,
    label,
    detail,
    agentsRunning: nonNegativeNumber(payload.agents_running, previous?.agentsRunning ?? 0),
    activeToolCalls: nonNegativeNumber(payload.active_tool_calls, previous?.activeToolCalls ?? 0),
    completedToolSteps: nonNegativeNumber(payload.completed_tool_steps, previous?.completedToolSteps ?? 0),
    awaitingConfirmation,
    providerError
  };
}

export function chatPhaseLabel(phase: string) {
  const labels: Record<string, string> = {
    awaiting_confirmation: "Awaiting confirmation",
    composing: "Composing answer",
    provider_error: "Provider error",
    repairing_tool_plan: "Completing answer facts",
    selecting_tools: "Planning",
    starting: "Starting",
    tools_selected: "Tools selected",
    understanding: "Planning",
    using_tools: "Using tools"
  };
  return labels[phase] ?? "Working";
}

export function chatAgentRunningLabel(count: number) {
  if (count <= 0) return "";
  return `${count} Agent${count === 1 ? "" : "s"} Running`;
}

export function chatToolStepsLabel(count: number) {
  if (count <= 0) return "";
  return `${count} tool step${count === 1 ? "" : "s"} completed`;
}

function nonNegativeNumber(value: unknown, fallback: number) {
  const numberValue = typeof value === "number" ? value : Number.NaN;
  return Number.isFinite(numberValue) ? Math.max(0, Math.round(numberValue)) : fallback;
}

export async function uploadChatAttachment(file: File, sessionId: string | null): Promise<ChatAttachment> {
  const body = new FormData();
  body.append("file", file);
  const suffix = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await fetch(`/api/v1/ai/chat/upload${suffix}`, {
    method: "POST",
    credentials: "include",
    body
  });
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<ChatAttachment>;
}

export function clientId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function formatDeviceTargetName(value: string) {
  return value
    .replace(/\*\*/g, "")
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((part) => part ? part[0].toUpperCase() + part.slice(1) : part)
    .join(" ");
}

export function cleanChatText(text: string, attachments: ChatAttachment[] = []) {
  const fileLinkReplacement = attachments.length
    ? (attachments.some((attachment) => attachment.kind === "image") ? "the snapshot" : "the attached file")
    : "$1";
  let cleaned = text
    .replace(/\[([^\]]+)\]\((\/api\/v1\/ai\/chat\/files\/[^)]+)\)/g, fileLinkReplacement)
    .replace(/\s*\/api\/v1\/ai\/chat\/files\/[A-Za-z0-9_-]+\b/g, "")
    .replace(/\*\*\*([^*]+)\*\*\*/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*\*/g, "")
    .replace(/\bHome Assistant cover entity ID\b/gi, "device name")
    .replace(/\bHome Assistant entity ID\b/gi, "device name")
    .replace(/\bHome Assistant\b/gi, "the system")
    .replace(/\bcover entity ID\b/gi, "device name")
    .replace(/\bentity ID\b/gi, "device name")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  attachments.forEach((attachment) => {
    if (attachment.source === "system_media" && attachment.filename) {
      cleaned = cleaned.replaceAll(attachment.filename, "the snapshot");
    }
  });
  if (!cleaned && attachments.some((attachment) => attachment.kind === "image")) {
    cleaned = "Here's the latest snapshot.";
  }
  return cleaned;
}

export function formatChatResponseDuration(durationMs: number | null | undefined) {
  if (durationMs === null || durationMs === undefined) return "";
  const totalSeconds = Math.max(0, Math.round(durationMs / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

export function formatChatMessageTime(timestamp: number | null | undefined) {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(timestamp));
}

export function chatProviderLabel(provider: string | null | undefined) {
  const normalized = normalizeLlmProvider(provider);
  return llmProviderDefinitions.find((item) => item.key === normalized)?.label ?? normalized;
}

export function chatProviderReasonLabel(reason: string | null | undefined) {
  if (reason === "local_provider_non_agent") return "Diagnostic provider only";
  if (reason === "provider_not_configured") return "Provider not configured";
  if (reason === "provider_missing") return "Provider missing";
  return reason ? reason.replace(/_/g, " ") : "Provider unavailable";
}

export function chatProviderStatusLabel(
  status: AlfredAgentStatus | null,
  connected: boolean,
  fallbackProvider: LlmProviderKey
) {
  if (!connected) return "Connecting";
  if (!status) return "Online";
  const provider = chatProviderLabel(status.provider || fallbackProvider);
  const capability = status.provider_capability;
  if (status.v3_ready) return `${provider} · Agent ready`;
  return `${provider} · ${chatProviderReasonLabel(capability?.reason)}`;
}

export function chatMessageStateLabel(message: ChatMessageItem) {
  if (message.streaming || message.status === "streaming") return "Streaming";
  if (message.status === "awaiting_confirmation") return "Awaiting confirmation";
  if (message.status === "failed") return "Failed closed";
  if (message.status === "cancelled") return "Cancelled";
  if (message.role === "user") return "Sent locally";
  return "Completed";
}

export function chatDaypart() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export async function copyToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

export function chatPendingAction(pendingAction: unknown): ChatConfirmationAction | null {
  if (!isRecord(pendingAction)) return null;
  const confirmationId = String(pendingAction.confirmation_id || "").trim();
  const toolName = String(pendingAction.tool_name || "").trim();
  if (!confirmationId || !toolName) return null;
  const target = String(pendingAction.target || toolName.replace(/_/g, " ")).trim();
  const title = String(pendingAction.title || `Confirm ${target}?`);
  const description = String(pendingAction.description || "This action needs confirmation before Alfred continues.");
  const buttonLabel = String(pendingAction.confirm_label || "Confirm");
  return {
    type: toolName,
    confirmationId,
    toolName,
    toolArguments: {},
    target,
    displayTarget: target,
    command: `confirm ${target}`,
    title,
    description,
    buttonLabel,
    pendingLabel: "Confirmed",
    statusLabel: `${buttonLabel} ${target}...`,
    userEcho: `Confirmed: ${target}`
  };
}

export function chatConfirmationAction(toolResults: unknown): ChatConfirmationAction | null {
  if (!Array.isArray(toolResults)) return null;
  const result = [...toolResults].reverse().find((item) => isRecord(item) && isRecord(item.output));
  if (!isRecord(result) || !isRecord(result.output) || result.output.requires_confirmation !== true) return null;
  const args = isRecord(result.arguments) ? result.arguments : {};
  const toolName = String(result.name || "").trim();
  const confirmationField = String(result.output.confirmation_field || (toolName === "test_notification_workflow" ? "confirm_send" : "confirm"));
  const toolArguments = { ...args, [confirmationField]: true };
  if (result.name === "open_device") {
    const target = String(result.output.target || args.target || args.entity_id || "").trim();
    if (!target) return null;
    const displayTarget = formatDeviceTargetName(target);
    return {
      type: "open_device",
      toolName,
      toolArguments,
      target,
      displayTarget,
      command: `confirm open ${target}`,
      title: `Open ${displayTarget}?`,
      description: "This will be logged as an Alfred action.",
      buttonLabel: "Confirm",
      pendingLabel: "Confirmed",
      statusLabel: `Opening ${displayTarget}...`,
      userEcho: `Confirmed: open ${displayTarget}`
    };
  }
  if (result.name === "update_schedule") {
    const target = String(result.output.schedule_name || args.schedule_name || args.name || "").trim();
    if (!target) return null;
    const summary = typeof result.output.summary === "string" ? result.output.summary : "the requested times";
    return {
      type: "update_schedule",
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `confirm update ${target} schedule`,
      title: `Update ${target}?`,
      description: `Replace the existing allowed times with ${summary}.`,
      buttonLabel: "Update schedule",
      pendingLabel: "Update confirmed",
      statusLabel: `Updating ${target}...`,
      userEcho: `Confirmed: update ${target}`
    };
  }
  if (result.name === "delete_schedule") {
    const schedule = isRecord(result.output.schedule) ? result.output.schedule : {};
    const target = String(result.output.schedule_name || schedule.name || args.schedule_name || args.name || "").trim();
    if (!target) return null;
    return {
      type: "delete_schedule",
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `confirm delete ${target} schedule`,
      title: `Delete ${target}?`,
      description: String(result.output.detail || "This schedule will be permanently deleted."),
      buttonLabel: "Delete schedule",
      pendingLabel: "Delete confirmed",
      statusLabel: `Deleting ${target}...`,
      userEcho: `Confirmed: delete ${target}`
    };
  }
  if ([
    "create_notification_workflow",
    "update_notification_workflow",
    "delete_notification_workflow",
    "test_notification_workflow"
  ].includes(toolName)) {
    const target = String(result.output.workflow_name || args.rule_name || args.name || "notification workflow").trim();
    const actionVerb = toolName === "create_notification_workflow"
      ? "Create"
      : toolName === "update_notification_workflow"
        ? "Update"
        : toolName === "delete_notification_workflow"
          ? "Delete"
          : "Send test for";
    return {
      type: toolName,
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `${actionVerb.toLowerCase()} ${target}`,
      title: `${actionVerb} ${target}?`,
      description: String(result.output.detail || "This changes notification workflow behaviour."),
      buttonLabel: toolName === "test_notification_workflow" ? "Send test" : actionVerb,
      pendingLabel: "Confirmed",
      statusLabel: `${actionVerb} ${target}...`,
      userEcho: `Confirmed: ${actionVerb.toLowerCase()} ${target}`
    };
  }
  if (toolName) {
    const target = String(result.output.target || result.output.schedule_name || result.output.workflow_name || args.target || args.schedule_name || args.name || toolName.replace(/_/g, " ")).trim();
    return {
      type: toolName,
      toolName,
      toolArguments,
      target,
      displayTarget: target,
      command: `confirm ${toolName}`,
      title: `Confirm ${target}?`,
      description: String(result.output.detail || "This action needs confirmation before Alfred continues."),
      buttonLabel: "Confirm",
      pendingLabel: "Confirmed",
      statusLabel: `Confirming ${target}...`,
      userEcho: `Confirmed: ${target}`
    };
  }
  return null;
}

export const chatMessageVariants = {
  hidden: { opacity: 0, y: 14, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: 8, scale: 0.98 }
};

export function ChatWidget({ currentUser, maintenanceStatus }: { currentUser: UserAccount; maintenanceStatus: MaintenanceStatus | null }) {
  const llmSettings = useSettings("llm");
  const [open, setOpen] = React.useState(false);
  const teaserStorageKey = `iacs-chat-teaser-dismissed:${currentUser.id}`;
  const [showTeaser, setShowTeaser] = React.useState(() => sessionStorage.getItem(teaserStorageKey) !== "true");
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<ChatMessageItem[]>([]);
  const [draft, setDraft] = React.useState("");
  const [llmPickerOpen, setLlmPickerOpen] = React.useState(false);
  const [llmSaving, setLlmSaving] = React.useState(false);
  const [llmFeedback, setLlmFeedback] = React.useState("");
  const [pendingAttachments, setPendingAttachments] = React.useState<ChatAttachmentDraft[]>([]);
  const [connected, setConnected] = React.useState(false);
  const [connectionNonce, setConnectionNonce] = React.useState(0);
  const [thinking, setThinking] = React.useState(false);
  const [slowResponse, setSlowResponse] = React.useState(false);
  const [toolStatus, setToolStatus] = React.useState("");
  const [toolActivities, setToolActivities] = React.useState<ChatToolActivity[]>([]);
  const [runActivity, setRunActivity] = React.useState<ChatRunActivity | null>(null);
  const [agentStatus, setAgentStatus] = React.useState<AlfredAgentStatus | null>(null);
  const [agentStatusError, setAgentStatusError] = React.useState("");
  const [dragActive, setDragActive] = React.useState(false);
  const [copyMenu, setCopyMenu] = React.useState<ChatCopyMenuState | null>(null);
  const [copiedMessageId, setCopiedMessageId] = React.useState<string | null>(null);
  const [feedbackDraft, setFeedbackDraft] = React.useState<ChatFeedbackDraft | null>(null);
  const [viewportHeight, setViewportHeight] = React.useState(() => window.visualViewport?.height ?? window.innerHeight);
  const [viewportTop, setViewportTop] = React.useState(() => window.visualViewport?.offsetTop ?? 0);
  const socketRef = React.useRef<WebSocket | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  const composerRef = React.useRef<HTMLTextAreaElement | null>(null);
  const feedRef = React.useRef<HTMLDivElement | null>(null);
  const activeAssistantMessageRef = React.useRef<string | null>(null);
  const awaitingResponseRef = React.useRef(false);
  const activeTurnStartedAtRef = React.useRef<number | null>(null);
  const activeTurnPhaseRef = React.useRef<string>("idle");
  const lastUserRequestRef = React.useRef<ChatRetryAction | null>(null);
  const pendingAttachmentsRef = React.useRef<ChatAttachmentDraft[]>([]);
  const firstName = currentUser.first_name || displayUserName(currentUser).split(" ")[0] || "there";
  const activeLlmProvider = normalizeLlmProvider(llmSettings.values.llm_provider);
  const headerStatusLabel = chatProviderStatusLabel(agentStatus, connected, activeLlmProvider);
  const maintenanceActive = maintenanceStatus?.is_active === true;
  const uploading = pendingAttachments.some((attachment) => attachment.uploadState === "uploading");
  const readyAttachments = pendingAttachments.filter((attachment) => attachment.uploadState === "ready");
  const canSend = Boolean((draft.trim() || readyAttachments.length) && connected && !uploading && !thinking);
  const widgetStyle = {
    "--chat-vvh": `${Math.round(viewportHeight)}px`,
    "--chat-vv-top": `${Math.round(viewportTop)}px`
  } as React.CSSProperties;

  const markTurnStarted = React.useCallback(() => {
    activeTurnStartedAtRef.current = performance.now();
  }, []);

  const finishTurnDuration = React.useCallback(() => {
    const startedAt = activeTurnStartedAtRef.current;
    activeTurnStartedAtRef.current = null;
    return startedAt === null ? null : Math.max(0, performance.now() - startedAt);
  }, []);

  const applyRunActivityPayload = React.useCallback((payload: Record<string, unknown>) => {
    setRunActivity((current) => {
      const next = chatRunActivityFromPayload(payload, current);
      activeTurnPhaseRef.current = next.phase;
      return next;
    });
  }, []);

  const markAssistantDisplayed = React.useCallback((messageId: string) => {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId && message.role === "assistant" && !message.localSeenAt
          ? { ...message, localSeenAt: Date.now() }
          : message
      )
    );
  }, []);

  React.useEffect(() => {
    setShowTeaser(sessionStorage.getItem(teaserStorageKey) !== "true");
  }, [teaserStorageKey]);

  React.useEffect(() => {
    pendingAttachmentsRef.current = pendingAttachments;
  }, [pendingAttachments]);

  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setAgentStatusError("");
    api.get<AlfredAgentStatus>("/api/v1/ai/agent/status")
      .then((status) => {
        if (!cancelled) setAgentStatus(status);
      })
      .catch((error: unknown) => {
        if (!cancelled) setAgentStatusError(error instanceof Error ? error.message : "Unable to load Alfred status.");
      });
    return () => {
      cancelled = true;
    };
  }, [open, connectionNonce, llmSettings.values.llm_provider]);

  React.useEffect(() => {
    if (!thinking) {
      setSlowResponse(false);
      return undefined;
    }
    setSlowResponse(false);
    const timer = window.setTimeout(() => setSlowResponse(true), 12000);
    return () => window.clearTimeout(timer);
  }, [thinking]);

  React.useEffect(() => {
    return () => {
      pendingAttachmentsRef.current.forEach((attachment) => {
        if (attachment.preview_url) URL.revokeObjectURL(attachment.preview_url);
      });
    };
  }, []);

  React.useEffect(() => {
    if (!open) return;
    const focusTimer = window.setTimeout(() => {
      composerRef.current?.focus({ preventScroll: true });
    }, 120);
    return () => window.clearTimeout(focusTimer);
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const updateViewportHeight = () => {
      const viewport = window.visualViewport;
      setViewportHeight(viewport?.height ?? window.innerHeight);
      setViewportTop(viewport?.offsetTop ?? 0);
    };
    updateViewportHeight();
    window.visualViewport?.addEventListener("resize", updateViewportHeight);
    window.visualViewport?.addEventListener("scroll", updateViewportHeight);
    window.addEventListener("resize", updateViewportHeight);
    return () => {
      window.visualViewport?.removeEventListener("resize", updateViewportHeight);
      window.visualViewport?.removeEventListener("scroll", updateViewportHeight);
      window.removeEventListener("resize", updateViewportHeight);
    };
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    if (!window.matchMedia("(max-width: 720px)").matches) return;
    const scrollY = window.scrollY;
    const originalOverflow = document.body.style.overflow;
    const originalHtmlOverflow = document.documentElement.style.overflow;
    document.body.classList.add("alfred-chat-open");
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.body.classList.remove("alfred-chat-open");
      document.body.style.overflow = originalOverflow;
      document.documentElement.style.overflow = originalHtmlOverflow;
      window.scrollTo(0, scrollY);
    };
  }, [open]);

  React.useEffect(() => {
    if (!copyMenu) return undefined;
    const close = () => setCopyMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [copyMenu]);

  React.useEffect(() => {
    if (!open) setCopyMenu(null);
  }, [open]);

  React.useEffect(() => {
    if (!open) {
      setLlmPickerOpen(false);
      setLlmFeedback("");
    }
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let connectionTimeoutId: number | null = null;
    let reconnectTimerId: number | null = null;
    const clearConnectionTimeout = () => {
      if (connectionTimeoutId) window.clearTimeout(connectionTimeoutId);
      connectionTimeoutId = null;
    };
    const clearReconnectTimer = () => {
      if (reconnectTimerId) window.clearTimeout(reconnectTimerId);
      reconnectTimerId = null;
    };
    const socket = new WebSocket(wsUrl("/api/v1/ai/chat/ws"));
    setConnected(false);
    connectionTimeoutId = window.setTimeout(() => {
      if (socket.readyState !== WebSocket.OPEN) socket.close();
    }, 10000);
    socket.onopen = () => {
      clearConnectionTimeout();
      setConnected(true);
    };
    socket.onmessage = (event) => {
      let data: { type: string; payload?: Record<string, unknown> };
      try {
        data = JSON.parse(event.data) as { type: string; payload?: Record<string, unknown> };
      } catch {
        return;
      }
      const payload = data.payload ?? {};
      if (data.type === "connection.ready") {
        setConnected(true);
        return;
      }
      if (data.type === "chat.thinking") {
        awaitingResponseRef.current = true;
        if (activeTurnStartedAtRef.current === null) markTurnStarted();
        setThinking(true);
        setToolStatus("Starting...");
        setToolActivities([]);
        applyRunActivityPayload(payload);
        return;
      }
      if (data.type === "chat.agent_state") {
        awaitingResponseRef.current = true;
        setThinking(true);
        const label = typeof payload.label === "string" ? payload.label : "Working...";
        const detail = typeof payload.detail === "string" ? payload.detail : "";
        setToolStatus(detail ? `${label} - ${detail}` : label);
        applyRunActivityPayload(payload);
        return;
      }
      if (data.type === "chat.tool_batch") {
        const batchId = typeof payload.batch_id === "string" ? payload.batch_id : clientId("tool-batch");
        const status = typeof payload.status === "string" ? payload.status : "";
        const tools = Array.isArray(payload.tools) ? payload.tools : [];
        applyRunActivityPayload(payload);
        if (status === "completed") {
          setToolActivities((current) => current.filter((item) => item.batchId !== batchId));
        } else {
          setToolActivities((current) => {
            const next = current.filter((item) => item.batchId !== batchId);
            tools.forEach((tool) => {
              if (!isRecord(tool)) return;
              const callId = String(tool.call_id || tool.tool || clientId("tool"));
              next.push({
                id: callId,
                batchId,
                tool: String(tool.tool || "tool"),
                label: String(tool.label || "Running system tool..."),
                status: "queued"
              });
            });
            return next;
          });
        }
        return;
      }
      if (data.type === "chat.tool_status") {
        awaitingResponseRef.current = true;
        setThinking(true);
        const label = typeof payload.label === "string" ? payload.label : "Running system tool...";
        setToolStatus(label);
        const tool = typeof payload.tool === "string" ? payload.tool : "tool";
        const status = ["queued", "running", "succeeded", "failed", "requires_confirmation"].includes(String(payload.status))
          ? String(payload.status) as ChatToolActivity["status"]
          : "running";
        const id = typeof payload.call_id === "string" ? payload.call_id : `${tool}:${String(payload.batch_id || "single")}`;
        const terminalToolStatus = ["succeeded", "failed", "requires_confirmation"].includes(status);
        setRunActivity((current) => {
          const base = chatRunActivityFromPayload(payload, current);
          const next = {
            ...base,
            completedToolSteps: terminalToolStatus
              ? (current?.completedToolSteps ?? 0) + 1
              : base.completedToolSteps,
            awaitingConfirmation: base.awaitingConfirmation || status === "requires_confirmation",
            activeToolCalls: terminalToolStatus
              ? Math.max(0, (current?.activeToolCalls ?? base.activeToolCalls) - 1)
              : base.activeToolCalls
          };
          activeTurnPhaseRef.current = next.phase;
          return next;
        });
        setToolActivities((current) => {
          const existing = current.find((item) => item.id === id);
          if (status === "succeeded") return current.filter((item) => item.id !== id);
          if (existing) {
            return current.map((item) => item.id === id ? { ...item, label, status } : item);
          }
          return [
            ...current,
            {
              id,
              batchId: typeof payload.batch_id === "string" ? payload.batch_id : undefined,
              tool,
              label,
              status
            }
          ];
        });
        return;
      }
      if (data.type === "chat.confirmation_required") {
        awaitingResponseRef.current = true;
        setThinking(true);
        setToolStatus("Waiting for confirmation...");
        applyRunActivityPayload(payload);
        return;
      }
      if (data.type === "chat.response.delta") {
        awaitingResponseRef.current = true;
        const chunk = typeof payload.chunk === "string" ? payload.chunk : "";
        if (!chunk) return;
        setThinking(true);
        setMessages((current) => {
          const activeId = activeAssistantMessageRef.current ?? clientId("alfred-stream");
          activeAssistantMessageRef.current = activeId;
          const existing = current.find((message) => message.id === activeId);
          if (existing) {
            return current.map((message) => message.id === activeId ? { ...message, text: message.text + chunk, streaming: true, status: "streaming" } : message);
          }
          return [...current, { id: activeId, role: "assistant", text: chunk, createdAt: Date.now(), streaming: true, status: "streaming" }];
        });
        return;
      }
      if (data.type === "chat.response") {
        awaitingResponseRef.current = false;
        const responseDurationMs = finishTurnDuration();
        const text = typeof payload.text === "string" ? payload.text : "";
        const responseAttachments = Array.isArray(payload.attachments) ? payload.attachments as ChatAttachment[] : [];
        const confirmationAction = chatPendingAction(payload.pending_action) ?? chatConfirmationAction(payload.tool_results);
        const userMessageId = typeof payload.user_message_id === "string" ? payload.user_message_id : null;
        const assistantMessageId = typeof payload.assistant_message_id === "string" ? payload.assistant_message_id : null;
        const turnPhase = activeTurnPhaseRef.current;
        const responseStatus: ChatMessageStatus = confirmationAction
          ? "awaiting_confirmation"
          : turnPhase === "provider_error"
            ? "failed"
            : "completed";
        const retryAction = responseStatus === "failed" && lastUserRequestRef.current
          ? { ...lastUserRequestRef.current, attachments: [...lastUserRequestRef.current.attachments] }
          : null;
        if (typeof payload.session_id === "string") setSessionId(payload.session_id);
        setMessages((current) => {
          const activeId = activeAssistantMessageRef.current ?? clientId("alfred");
          activeAssistantMessageRef.current = null;
          const existing = current.find((message) => message.id === activeId);
          if (existing) {
            return current.map((message) =>
              message.id === activeId
                ? {
                  ...message,
                  text,
                  createdAt: message.createdAt ?? Date.now(),
                  attachments: responseAttachments,
                  confirmationAction,
                  streaming: false,
                  status: responseStatus,
                  retryAction,
                    userMessageId,
                    assistantMessageId,
                    responseDurationMs
                }
                : message
            );
          }
          return [
            ...current,
            {
              id: activeId,
              role: "assistant",
              text,
              createdAt: Date.now(),
              attachments: responseAttachments,
              confirmationAction,
              status: responseStatus,
              retryAction,
                userMessageId,
                assistantMessageId,
                responseDurationMs
            }
          ];
        });
        activeTurnPhaseRef.current = "idle";
        lastUserRequestRef.current = null;
        setThinking(false);
        setToolStatus("");
        setToolActivities([]);
        setRunActivity(null);
        return;
      }
      if (data.type === "chat.error") {
        awaitingResponseRef.current = false;
        const responseDurationMs = finishTurnDuration();
        const retryAction = lastUserRequestRef.current
          ? { ...lastUserRequestRef.current, attachments: [...lastUserRequestRef.current.attachments] }
          : null;
        setMessages((current) => [
          ...current,
          {
            id: clientId("alfred-error"),
            role: "assistant",
            text: typeof payload.message === "string" ? payload.message : "Alfred could not complete that request.",
            createdAt: Date.now(),
            status: "failed",
            retryAction,
            responseDurationMs
          }
        ]);
        activeTurnPhaseRef.current = "idle";
        lastUserRequestRef.current = null;
        setThinking(false);
        setToolStatus("");
        setToolActivities([]);
        setRunActivity(null);
      }
    };
    socket.onerror = () => {
      socket.close();
    };
    socket.onclose = () => {
      clearConnectionTimeout();
      if (socketRef.current === socket) socketRef.current = null;
      const interruptedTurn = awaitingResponseRef.current;
      awaitingResponseRef.current = false;
      activeTurnStartedAtRef.current = null;
      setConnected(false);
      setThinking(false);
      setToolStatus("");
      setToolActivities([]);
      setRunActivity(null);
      if (!cancelled && interruptedTurn) {
        const retryAction = lastUserRequestRef.current
          ? { ...lastUserRequestRef.current, attachments: [...lastUserRequestRef.current.attachments] }
          : null;
        activeAssistantMessageRef.current = null;
        setMessages((current) => [
          ...current,
          {
            id: clientId("alfred-disconnect"),
            role: "assistant",
            text: "Alfred disconnected while answering. I logged the failure for review; please try again.",
            createdAt: Date.now(),
            status: "failed",
            retryAction
          }
        ]);
      }
      activeTurnPhaseRef.current = "idle";
      lastUserRequestRef.current = null;
      if (!cancelled) {
        const delay = Math.min(8000, 700 + connectionNonce * 600);
        reconnectTimerId = window.setTimeout(() => {
          setConnectionNonce((current) => current + 1);
        }, delay);
      }
    };
    socketRef.current = socket;
    return () => {
      cancelled = true;
      clearReconnectTimer();
      clearConnectionTimeout();
      socket.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [applyRunActivityPayload, connectionNonce, finishTurnDuration, markTurnStarted, open]);

  React.useEffect(() => {
    if (!feedRef.current) return;
    window.requestAnimationFrame(() => {
      if (feedRef.current) {
        feedRef.current.scrollTop = feedRef.current.scrollHeight;
      }
    });
  }, [messages, thinking, toolStatus, toolActivities, runActivity]);

  const dismissTeaser = React.useCallback(() => {
    sessionStorage.setItem(teaserStorageKey, "true");
    setShowTeaser(false);
  }, [teaserStorageKey]);

  const removeAttachment = React.useCallback((id: string) => {
    setPendingAttachments((current) => {
      const removed = current.find((attachment) => attachment.id === id);
      if (removed?.preview_url) URL.revokeObjectURL(removed.preview_url);
      return current.filter((attachment) => attachment.id !== id);
    });
  }, []);

  const addFiles = React.useCallback((fileList: FileList | File[]) => {
    const files = Array.from(fileList).slice(0, 6);
    if (!files.length) return;
    const drafts: ChatAttachmentDraft[] = files.map((file) => ({
      id: clientId("upload"),
      filename: file.name || "Attachment",
      content_type: file.type || "application/octet-stream",
      size_bytes: file.size,
      kind: file.type.startsWith("image/") ? "image" : "document",
      url: "",
      uploadState: "uploading",
      preview_url: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined
    }));
    setPendingAttachments((current) => [...current, ...drafts]);
    drafts.forEach((draftAttachment, index) => {
      uploadChatAttachment(files[index], sessionId)
        .then((uploaded) => {
          if (draftAttachment.preview_url) URL.revokeObjectURL(draftAttachment.preview_url);
          setPendingAttachments((current) =>
            current.map((attachment) =>
              attachment.id === draftAttachment.id
                ? { ...uploaded, uploadState: "ready" }
                : attachment
            )
          );
        })
        .catch((error: unknown) => {
          setPendingAttachments((current) =>
            current.map((attachment) =>
              attachment.id === draftAttachment.id
                ? {
                  ...attachment,
                  uploadState: "error",
                  error: error instanceof Error ? error.message : "Upload failed"
                }
                : attachment
            )
          );
        });
    });
  }, [sessionId]);

  const sendConfirmationAction = React.useCallback((messageId: string, action: ChatConfirmationAction, decision: "confirm" | "cancel" = "confirm") => {
    const socket = socketRef.current;
    if (!connected || thinking || !socket || socket.readyState !== WebSocket.OPEN || action.sent) return;
    const userEcho = decision === "confirm" ? action.userEcho : `Cancelled: ${action.displayTarget}`;
    setMessages((current) => [
      ...current.map((message) =>
        message.id === messageId && message.confirmationAction
          ? {
            ...message,
            status: decision === "cancel" ? "cancelled" : message.status,
            confirmationAction: { ...message.confirmationAction, sent: true, decision }
          }
          : message
      ),
      {
        id: clientId("user"),
        role: "user",
        text: userEcho,
        createdAt: Date.now(),
        status: "sent_local"
      }
    ]);
    socket.send(JSON.stringify({
      message: userEcho,
      session_id: sessionId,
      attachments: [],
      client_context: chatClientContext(),
      tool_confirmation: {
        id: action.confirmationId,
        confirmation_id: action.confirmationId,
        decision
      }
    }));
    awaitingResponseRef.current = true;
    activeTurnPhaseRef.current = decision === "confirm" ? "using_tools" : "cancelled";
    lastUserRequestRef.current = null;
    markTurnStarted();
    setThinking(true);
    setToolStatus(decision === "confirm" ? action.statusLabel : "Cancelling action...");
    setRunActivity(null);
  }, [connected, markTurnStarted, sessionId, thinking]);

  const submitFeedback = React.useCallback(async (
    message: ChatMessageItem,
    rating: "up" | "down",
    reason = "",
    idealAnswer = ""
  ) => {
    const assistantMessageId = message.assistantMessageId;
    if (!assistantMessageId) return;
    setMessages((current) =>
      current.map((item) =>
        item.id === message.id
          ? { ...item, feedback: { rating, status: "saving" } }
          : item
      )
    );
    try {
      const result = await api.post<AlfredFeedbackResponse>("/api/v1/ai/feedback", {
        assistant_message_id: assistantMessageId,
        rating,
        reason,
        ideal_answer: idealAnswer,
        source_channel: "dashboard"
      });
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? { ...item, feedback: { rating, status: "saved" } }
            : item
        )
      );
      const corrected = String(result.corrected_answer || "").trim();
      if (rating === "down" && corrected) {
        setMessages((current) => [
          ...current,
          {
            id: clientId("alfred-repair"),
            role: "assistant",
            text: corrected
          }
        ]);
      }
      setFeedbackDraft(null);
    } catch (error) {
      const messageText = error instanceof Error ? error.message : "Unable to submit feedback.";
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? { ...item, feedback: { rating, status: "error", error: messageText } }
            : item
        )
      );
      setFeedbackDraft((current) => current?.messageId === message.id ? { ...current, saving: false, error: messageText } : current);
    }
  }, []);

  const openNegativeFeedback = React.useCallback((message: ChatMessageItem) => {
    if (!message.assistantMessageId) return;
    setFeedbackDraft({
      messageId: message.id,
      assistantMessageId: message.assistantMessageId,
      reason: "",
      idealAnswer: "",
      saving: false,
      error: ""
    });
  }, []);

  const submitFeedbackDraft = React.useCallback(async (message: ChatMessageItem) => {
    if (!feedbackDraft || feedbackDraft.messageId !== message.id) return;
    if (!feedbackDraft.reason.trim()) {
      setFeedbackDraft({ ...feedbackDraft, error: "Tell Alfred what was wrong so he can learn the right lesson." });
      return;
    }
    setFeedbackDraft({ ...feedbackDraft, saving: true, error: "" });
    await submitFeedback(message, "down", feedbackDraft.reason.trim(), feedbackDraft.idealAnswer.trim());
  }, [feedbackDraft, submitFeedback]);

  const selectLlmProvider = React.useCallback(async (provider: LlmProviderKey) => {
    if (llmSaving || llmSettings.loading) return;
    const definition = llmProviderDefinitions.find((item) => item.key === provider);
    const label = definition?.label ?? provider;
    if (!isLlmProviderConfigured(provider, llmSettings.values)) {
      setLlmFeedback(`${label} is not configured yet.`);
      return;
    }
    setLlmSaving(true);
    setLlmFeedback("");
    try {
      await llmSettings.save({ llm_provider: provider });
      setLlmPickerOpen(false);
      setMessages((current) => [
        ...current,
        {
          id: clientId("alfred-llm"),
          role: "assistant",
          text: `System LLM set to ${label}.`
        }
      ]);
      window.setTimeout(() => composerRef.current?.focus({ preventScroll: true }), 20);
    } catch (error) {
      setLlmFeedback(error instanceof Error ? error.message : "Unable to update the system LLM.");
    } finally {
      setLlmSaving(false);
    }
  }, [llmSaving, llmSettings]);

  const updateDraft = React.useCallback((value: string) => {
    if (value.trim().toLowerCase() === "/llm") {
      setDraft("");
      setLlmFeedback("");
      setLlmPickerOpen(true);
      return;
    }
    setDraft(value);
  }, []);

  const sendMessage = React.useCallback((overrideText?: string, overrideAttachments?: ChatAttachment[]) => {
    const socket = socketRef.current;
    const sourceText = typeof overrideText === "string" ? overrideText : draft;
    if (typeof overrideText !== "string" && sourceText.trim().toLowerCase() === "/llm") {
      setDraft("");
      setLlmFeedback("");
      setLlmPickerOpen(true);
      return;
    }
    const attachments = overrideAttachments ?? readyAttachments.map(publicChatAttachment);
    if ((!sourceText.trim() && !attachments.length) || !connected || uploading || thinking || !socket || socket.readyState !== WebSocket.OPEN) return;
    const text = sourceText.trim() || "Please inspect the attached file.";
    setMessages((current) => [
      ...current,
      { id: clientId("user"), role: "user", text, createdAt: Date.now(), attachments, status: "sent_local" }
    ]);
    lastUserRequestRef.current = { text, attachments: attachments.map((attachment) => ({ ...attachment })) };
    activeTurnPhaseRef.current = "starting";
    markTurnStarted();
    socket.send(JSON.stringify({ message: text, session_id: sessionId, attachments, client_context: chatClientContext() }));
    awaitingResponseRef.current = true;
    setDraft("");
    setLlmPickerOpen(false);
    setLlmFeedback("");
    if (!overrideAttachments) {
      setPendingAttachments((current) => current.filter((attachment) => attachment.uploadState === "error"));
    }
    setThinking(true);
    setToolStatus("Starting...");
    setRunActivity(null);
  }, [connected, draft, markTurnStarted, readyAttachments, sessionId, thinking, uploading]);

  const handleComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Escape" && llmPickerOpen) {
      event.preventDefault();
      setLlmPickerOpen(false);
      setLlmFeedback("");
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  };

  const handleDrop = React.useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    addFiles(event.dataTransfer.files);
  }, [addFiles]);

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (!nextTarget || !event.currentTarget.contains(nextTarget as Node)) {
      setDragActive(false);
    }
  };

  const retryMessage = React.useCallback((retryAction: ChatRetryAction) => {
    sendMessage(retryAction.text, retryAction.attachments);
  }, [sendMessage]);

  return (
    <div className={open ? "chat-widget open" : "chat-widget"} style={widgetStyle}>
      <AnimatePresence>
        {open ? (
          <motion.div
            className={dragActive ? "chat-panel drag-active" : "chat-panel"}
            initial={{ opacity: 0, scale: 0.86, y: 28 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 24 }}
            transition={{ type: "spring", stiffness: 360, damping: 32 }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <div className="chat-header">
              <div className="alfred-identity">
                <span className="alfred-avatar">
                  <Bot size={18} aria-hidden="true" />
                  {maintenanceActive ? <HardHat className="alfred-maintenance-icon" size={14} strokeWidth={1} aria-hidden="true" /> : null}
                </span>
                <span>
                  <strong>Alfred {maintenanceActive ? <HardHat className="alfred-header-maintenance" size={15} strokeWidth={1} aria-label="Maintenance Mode active" /> : null}</strong>
                  <small title={agentStatusError || headerStatusLabel}>
                    <span className={connected ? agentStatus && !agentStatus.v3_ready ? "alfred-status issue" : "alfred-status online" : "alfred-status"} />
                    {agentStatusError ? "Status unavailable" : headerStatusLabel}
                  </small>
                </span>
              </div>
              <button className="icon-button chat-close" onClick={() => setOpen(false)} type="button" aria-label="Close Alfred">
                <X size={16} />
              </button>
            </div>

            <div className="chat-feed" ref={feedRef}>
              {messages.length === 0 ? (
                <ChatEmptyState
                  disabled={!connected || thinking || uploading}
                  firstName={firstName}
                  maintenanceActive={maintenanceActive}
                  onPrompt={(prompt) => sendMessage(prompt, [])}
                />
              ) : null}
              <AnimatePresence initial={false}>
                {messages.map((message, index) => (
                  <ChatMessageBubble
                    index={index}
                    key={message.id}
                    message={message}
                    onConfirm={sendConfirmationAction}
                    onFeedback={(targetMessage, rating) => {
                      if (rating === "up") {
                        void submitFeedback(targetMessage, "up");
                      } else {
                        openNegativeFeedback(targetMessage);
                      }
                    }}
                    feedbackDraft={feedbackDraft?.messageId === message.id ? feedbackDraft : null}
                    onFeedbackDraftChange={setFeedbackDraft}
                    onFeedbackDraftClose={() => setFeedbackDraft(null)}
                    onFeedbackDraftSubmit={submitFeedbackDraft}
                    onDisplayed={markAssistantDisplayed}
                    onOpenCopyMenu={setCopyMenu}
                    onRetry={retryMessage}
                    retryDisabled={!connected || thinking || uploading}
                    senderName={message.role === "assistant" ? "Alfred" : firstName}
                  />
                ))}
              </AnimatePresence>
              {thinking ? <TypingIndicator activities={toolActivities} runActivity={runActivity} slow={slowResponse} status={toolStatus} /> : null}
            </div>

            <div className="chat-composer">
              <AnimatePresence>
                {llmPickerOpen ? (
                  <ChatLlmProviderPopover
                    activeProvider={activeLlmProvider}
                    error={llmSettings.error || llmFeedback}
                    loading={llmSettings.loading}
                    saving={llmSaving}
                    values={llmSettings.values}
                    onClose={() => {
                      setLlmPickerOpen(false);
                      setLlmFeedback("");
                      composerRef.current?.focus({ preventScroll: true });
                    }}
                    onSelect={selectLlmProvider}
                  />
                ) : null}
              </AnimatePresence>
              {pendingAttachments.length ? (
                <div className="chat-composer-attachments" aria-label="Pending attachments">
                  {pendingAttachments.map((attachment) => (
                    <ChatAttachmentPreview attachment={attachment} key={attachment.id} onRemove={removeAttachment} />
                  ))}
                </div>
              ) : null}
              <div className="chat-input">
                <input
                  className="chat-file-input"
                  multiple
                  onChange={(event) => {
                    if (event.currentTarget.files) addFiles(event.currentTarget.files);
                    event.currentTarget.value = "";
                  }}
                  ref={fileInputRef}
                  type="file"
                />
                <button className="icon-button attach" onClick={() => fileInputRef.current?.click()} type="button" aria-label="Attach files">
                  <Paperclip size={17} />
                </button>
                <textarea
                  value={draft}
                  onChange={(event) => updateDraft(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder="Ask Alfred..."
                  ref={composerRef}
                  rows={1}
                />
                <button className="icon-button send" disabled={!canSend} onClick={() => sendMessage()} type="button" aria-label="Send message">
                  <Send size={17} />
                </button>
              </div>
              {dragActive ? (
                <div className="chat-drop-overlay">
                  <Sparkles size={18} />
                  <span>Drop files for Alfred</span>
                </div>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {!open && showTeaser ? (
          <motion.div
            className="chat-teaser"
            initial={{ opacity: 0, y: 10, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.96 }}
          >
            <button className="teaser-close" onClick={dismissTeaser} type="button" aria-label="Dismiss chat prompt">
              <X size={16} />
            </button>
            <strong>Alfred is ready</strong>
            <p>Hi {firstName}, how can I help?</p>
          </motion.div>
        ) : null}
      </AnimatePresence>
      {!open ? (
        <motion.button
          className="chat-pill"
          onClick={() => setOpen(true)}
          type="button"
          aria-label="Open Alfred"
          whileTap={{ scale: 0.97 }}
        >
          <MessageCircle size={18} />
          <span>Alfred</span>
        </motion.button>
      ) : null}
      {copyMenu ? (
        <ChatCopyMenu
          copied={copiedMessageId === copyMenu.messageId}
          menu={copyMenu}
          onCopy={async () => {
            await copyToClipboard(copyMenu.text);
            setCopiedMessageId(copyMenu.messageId);
            window.setTimeout(() => setCopyMenu(null), 450);
          }}
        />
      ) : null}
    </div>
  );
}

export function ChatEmptyState({
  disabled,
  firstName,
  maintenanceActive,
  onPrompt
}: {
  disabled: boolean;
  firstName: string;
  maintenanceActive: boolean;
  onPrompt: (prompt: string) => void;
}) {
  const prompts = maintenanceActive
    ? ["What is maintenance mode blocking?", "Show recent gate activity", "Check top gate status", "Review open anomalies"]
    : ["Who is currently on site?", "Show recent gate anomalies", "Check top gate status", "Any outside-schedule access today?"];
  return (
    <div className="chat-empty-state">
      <span className="chat-empty-mark">
        <Bot size={18} />
      </span>
      <div className="chat-empty-copy">
        <strong>{chatDaypart()}, {firstName}</strong>
        <small>{maintenanceActive ? "Maintenance Mode active" : "Operational chat ready"}</small>
      </div>
      <div className="chat-empty-prompts" aria-label="Alfred starter prompts">
        {prompts.map((prompt) => (
          <button disabled={disabled} key={prompt} onClick={() => onPrompt(prompt)} type="button">
            <Sparkles size={13} />
            <span>{prompt}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function ChatLlmProviderPopover({
  activeProvider,
  error,
  loading,
  saving,
  values,
  onClose,
  onSelect
}: {
  activeProvider: LlmProviderKey;
  error: string;
  loading: boolean;
  saving: boolean;
  values: SettingsMap;
  onClose: () => void;
  onSelect: (provider: LlmProviderKey) => Promise<void>;
}) {
  return (
    <motion.div
      className="chat-llm-popover"
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.98 }}
      transition={{ type: "spring", stiffness: 420, damping: 34 }}
    >
      <div className="chat-llm-popover-head">
        <span>
          <Bot size={15} />
          <strong>System LLM</strong>
        </span>
        <button className="icon-button" onClick={onClose} type="button" aria-label="Close LLM selector">
          <X size={14} />
        </button>
      </div>
      <div className="chat-llm-provider-grid">
        {llmProviderDefinitions.map((provider) => {
          const configured = isLlmProviderConfigured(provider.key, values);
          const active = provider.key === activeProvider;
          const disabled = saving || loading || (!configured && !active);
          const Icon = provider.key === "gemini"
            ? CircleDot
            : provider.key === "anthropic"
              ? MessageCircle
              : provider.key === "ollama"
                ? Terminal
                : Bot;
          return (
            <button
              className={active ? "chat-llm-provider active" : "chat-llm-provider"}
              disabled={disabled}
              key={provider.key}
              onClick={() => onSelect(provider.key)}
              type="button"
            >
              <Icon size={16} />
              <span>
                <strong>{provider.label}</strong>
                <small>{provider.agentCapable ? active ? "Active" : configured ? "Ready" : "Not configured" : "Diagnostic only"}</small>
              </span>
              {saving && active ? <Loader2 className="spin" size={14} /> : null}
            </button>
          );
        })}
      </div>
      {error ? <p className="chat-llm-feedback" role="status">{error}</p> : null}
    </motion.div>
  );
}

export function ChatMessageBubble({
  message,
  index,
  senderName,
  feedbackDraft,
  onFeedback,
  onFeedbackDraftChange,
  onFeedbackDraftClose,
  onFeedbackDraftSubmit,
  onDisplayed,
  onOpenCopyMenu,
  onRetry,
  retryDisabled,
  onConfirm
}: {
  message: ChatMessageItem;
  index: number;
  senderName: string;
  feedbackDraft: ChatFeedbackDraft | null;
  onFeedback: (message: ChatMessageItem, rating: "up" | "down") => void;
  onFeedbackDraftChange: (draft: ChatFeedbackDraft | null) => void;
  onFeedbackDraftClose: () => void;
  onFeedbackDraftSubmit: (message: ChatMessageItem) => void;
  onDisplayed: (messageId: string) => void;
  onOpenCopyMenu: (menu: ChatCopyMenuState) => void;
  onRetry: (retryAction: ChatRetryAction) => void;
  retryDisabled: boolean;
  onConfirm: (messageId: string, action: ChatConfirmationAction, decision?: "confirm" | "cancel") => void;
}) {
  const longPressTimerRef = React.useRef<number | null>(null);
  const messageRef = React.useRef<HTMLDivElement | null>(null);
  const displayText = cleanChatText(message.text, message.attachments ?? []);
  const createdTime = formatChatMessageTime(message.createdAt);
  const displayedTime = formatChatMessageTime(message.localSeenAt);
  const stateLabel = chatMessageStateLabel(message);
  const isAssistant = message.role === "assistant";
  const isFailed = message.status === "failed";
  const clearLongPress = React.useCallback(() => {
    if (!longPressTimerRef.current) return;
    window.clearTimeout(longPressTimerRef.current);
    longPressTimerRef.current = null;
  }, []);
  const openCopyMenu = React.useCallback((x: number, y: number) => {
    if (!displayText) return;
    onOpenCopyMenu({ messageId: message.id, text: displayText, x, y });
  }, [displayText, message.id, onOpenCopyMenu]);

  React.useEffect(() => {
    if (!isAssistant || message.localSeenAt) return undefined;
    const node = messageRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      onDisplayed(message.id);
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting && entry.intersectionRatio >= 0.6)) {
        onDisplayed(message.id);
        observer.disconnect();
      }
    }, { threshold: [0.6] });
    observer.observe(node);
    return () => observer.disconnect();
  }, [isAssistant, message.id, message.localSeenAt, onDisplayed]);

  return (
    <motion.div
      className={`chat-message ${message.role} ${message.status || ""}`}
      ref={messageRef}
      variants={chatMessageVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      transition={{ type: "spring", stiffness: 420, damping: 34, delay: Math.min(index * 0.025, 0.18) }}
      layout
    >
      {isAssistant ? (
        <span className={isFailed ? "chat-message-avatar failed" : "chat-message-avatar assistant"}>
          {isFailed ? <AlertTriangle size={15} /> : <Bot size={15} />}
        </span>
      ) : null}
      <div className="chat-message-stack">
        <span className="chat-message-head">
          <span className="chat-sender-label">{senderName}</span>
          {createdTime ? <time>{createdTime}</time> : null}
        </span>
        {displayText ? (
          <div
            className={`chat-bubble ${message.role}`}
            onContextMenu={(event) => {
              event.preventDefault();
              openCopyMenu(event.clientX, event.clientY);
            }}
            onPointerCancel={clearLongPress}
            onPointerDown={(event) => {
              if (event.pointerType !== "touch" && event.pointerType !== "pen") return;
              clearLongPress();
              const { clientX, clientY } = event;
              longPressTimerRef.current = window.setTimeout(() => openCopyMenu(clientX, clientY), 560);
            }}
            onPointerLeave={clearLongPress}
            onPointerMove={clearLongPress}
            onPointerUp={clearLongPress}
          >
            <p>{displayText}</p>
          </div>
        ) : null}
        {message.attachments?.length ? (
          <div className="chat-bubble-attachments">
            {message.attachments.map((attachment) => (
              <ChatAttachmentCard attachment={attachment} key={attachment.id} />
            ))}
          </div>
        ) : null}
        {message.confirmationAction ? (
          <ChatConfirmationCard
            action={message.confirmationAction}
            onConfirm={() => onConfirm(message.id, message.confirmationAction as ChatConfirmationAction)}
            onCancel={() => onConfirm(message.id, message.confirmationAction as ChatConfirmationAction, "cancel")}
          />
        ) : null}
        <div className="chat-message-footer">
          <span className={`chat-message-state ${message.status || "completed"}`}>
            {message.streaming || message.status === "streaming" ? <Loader2 className="spin" size={12} /> : isFailed ? <WifiOff size={12} /> : <CheckCircle2 size={12} />}
            <span>{stateLabel}</span>
          </span>
          {isAssistant && displayedTime ? (
            <span className="chat-local-seen" title="Rendered in this browser viewport">
              <Eye size={12} />
              <span>Displayed locally {displayedTime}</span>
            </span>
          ) : null}
          {message.responseDurationMs !== null && message.responseDurationMs !== undefined ? (
            <span className="chat-response-duration" title="Response time">
              <Clock3 size={12} />
              <span>{formatChatResponseDuration(message.responseDurationMs)}</span>
            </span>
          ) : null}
          {message.retryAction ? (
            <button className="chat-retry-button" disabled={retryDisabled} onClick={() => message.retryAction ? onRetry(message.retryAction) : undefined} type="button">
              <RefreshCcw size={12} />
              <span>Retry</span>
            </button>
          ) : null}
        </div>
        {message.role === "assistant" && message.assistantMessageId && !message.streaming ? (
          <div className="chat-feedback-row" aria-label="Rate Alfred response">
            <button
              className={message.feedback?.rating === "up" ? "chat-feedback-button active" : "chat-feedback-button"}
              disabled={message.feedback?.status === "saving"}
              onClick={() => onFeedback(message, "up")}
              title="Good response"
              type="button"
            >
              <ThumbsUp size={13} />
            </button>
            <button
              className={message.feedback?.rating === "down" ? "chat-feedback-button active" : "chat-feedback-button"}
              disabled={message.feedback?.status === "saving"}
              onClick={() => onFeedback(message, "down")}
              title="Needs improvement"
              type="button"
            >
              <ThumbsDown size={13} />
            </button>
            {message.feedback?.status === "saved" ? <small>Saved</small> : null}
            {message.feedback?.status === "error" ? <small className="error">{message.feedback.error}</small> : null}
          </div>
        ) : null}
        {feedbackDraft ? (
          <ChatFeedbackPanel
            draft={feedbackDraft}
            onChange={onFeedbackDraftChange}
            onClose={onFeedbackDraftClose}
            onSubmit={() => onFeedbackDraftSubmit(message)}
          />
        ) : null}
      </div>
      {!isAssistant ? (
        <span className="chat-message-avatar user" aria-label={senderName}>
          {senderName ? userInitials({ first_name: senderName, last_name: "", full_name: senderName }) : <UserRound size={15} />}
        </span>
      ) : null}
    </motion.div>
  );
}

export function ChatFeedbackPanel({
  draft,
  onChange,
  onClose,
  onSubmit
}: {
  draft: ChatFeedbackDraft;
  onChange: (draft: ChatFeedbackDraft | null) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  return (
    <div className="chat-feedback-panel">
      <label>
        <span>What was wrong?</span>
        <textarea
          value={draft.reason}
          onChange={(event) => onChange({ ...draft, reason: event.target.value, error: "" })}
          placeholder="Tell Alfred what made this answer unhelpful."
          rows={3}
        />
      </label>
      <label>
        <span>What should Alfred have said?</span>
        <textarea
          value={draft.idealAnswer}
          onChange={(event) => onChange({ ...draft, idealAnswer: event.target.value })}
          placeholder="Optional corrected answer."
          rows={2}
        />
      </label>
      {draft.error ? <small className="chat-feedback-error">{draft.error}</small> : null}
      <div className="chat-feedback-actions">
        <button className="chat-feedback-submit secondary" onClick={onClose} type="button">Cancel</button>
        <button className="chat-feedback-submit" disabled={draft.saving} onClick={onSubmit} type="button">
          {draft.saving ? <Loader2 className="spin" size={13} /> : <Check size={13} />}
          <span>{draft.saving ? "Sending" : "Send feedback"}</span>
        </button>
      </div>
    </div>
  );
}

export function ChatCopyMenu({
  copied,
  menu,
  onCopy
}: {
  copied: boolean;
  menu: ChatCopyMenuState;
  onCopy: () => void;
}) {
  const left = Math.max(8, Math.min(menu.x, window.innerWidth - 112));
  const top = Math.max(8, Math.min(menu.y, window.innerHeight - 48));
  return (
    <div
      className="chat-copy-menu"
      onClick={(event) => event.stopPropagation()}
      role="menu"
      style={{ left, top }}
    >
      <button onClick={onCopy} role="menuitem" type="button">
        <Copy size={14} />
        <span>{copied ? "Copied" : "Copy"}</span>
      </button>
    </div>
  );
}

export function ChatConfirmationCard({
  action,
  onCancel,
  onConfirm
}: {
  action: ChatConfirmationAction;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const actionSent = action.sent === true;
  const sentLabel = action.decision === "cancel" ? "Cancellation sent" : action.pendingLabel;
  return (
    <div className={actionSent ? "chat-confirm-card sent" : "chat-confirm-card"} role="group" aria-label={action.title}>
      <span className="chat-confirm-icon">
        {action.type === "update_schedule" ? <Clock3 size={17} /> : action.type === "delete_schedule" ? <Trash2 size={17} /> : <ShieldCheck size={17} />}
      </span>
      <span>
        <small className="chat-confirm-kicker">Confirmation Required</small>
        <strong>{action.title}</strong>
        <small>{action.description}</small>
      </span>
      <span className="chat-confirm-actions">
        <button className="chat-confirm-button secondary" disabled={actionSent} onClick={onCancel} type="button">
          <X size={14} />
          <span>Cancel</span>
        </button>
        <button className="chat-confirm-button" disabled={actionSent} onClick={onConfirm} type="button">
          <ShieldCheck size={14} />
          <span>{actionSent ? sentLabel : action.buttonLabel}</span>
        </button>
      </span>
    </div>
  );
}

export function ChatAttachmentCard({ attachment }: { attachment: ChatAttachment }) {
  const url = attachment.url || attachment.download_url || "#";
  if (attachment.kind === "image") {
    return (
      <a className="chat-image-attachment" href={url} target="_blank" rel="noreferrer">
        <img alt={attachment.filename} src={url} />
      </a>
    );
  }
  return (
    <div className="chat-download-card">
      <span className="chat-file-icon"><FileText size={18} /></span>
      <span>
        <strong>{attachment.filename}</strong>
        <small>{formatFileSize(attachment.size_bytes)} · {attachment.content_type}</small>
      </span>
      <a className="chat-download-button" href={attachment.download_url || url} download>
        <Download size={14} />
        <span>Download</span>
      </a>
    </div>
  );
}

export function ChatAttachmentPreview({
  attachment,
  onRemove
}: {
  attachment: ChatAttachmentDraft;
  onRemove: (id: string) => void;
}) {
  const isImage = attachment.kind === "image";
  const Icon = isImage ? FileImage : FileIcon;
  return (
    <div className={`chat-attachment-pill ${attachment.uploadState}`}>
      {isImage && (attachment.preview_url || attachment.url) ? (
        <img alt="" src={attachment.preview_url || attachment.url} />
      ) : (
        <Icon size={15} />
      )}
      <span>
        <strong>{attachment.filename}</strong>
        <small>{attachment.uploadState === "error" ? attachment.error : formatFileSize(attachment.size_bytes)}</small>
      </span>
      {attachment.uploadState === "uploading" ? <Loader2 className="spin" size={14} /> : null}
      <button onClick={() => onRemove(attachment.id)} type="button" aria-label={`Remove ${attachment.filename}`}>
        <X size={13} />
      </button>
    </div>
  );
}

export function TypingIndicator({
  activities,
  runActivity,
  slow,
  status
}: {
  activities: ChatToolActivity[];
  runActivity: ChatRunActivity | null;
  slow: boolean;
  status: string;
}) {
  const activeActivity = activities.find((activity) => activity.status === "running" || activity.status === "requires_confirmation")
    ?? activities[0];
  const extraActivityCount = activeActivity ? Math.max(0, activities.length - 1) : 0;
  const agentLabel = runActivity ? chatAgentRunningLabel(runActivity.agentsRunning) : "";
  const phaseLabel = runActivity?.providerError
    ? "Provider error"
    : runActivity?.awaitingConfirmation
      ? "Awaiting confirmation"
      : runActivity
        ? chatPhaseLabel(runActivity.phase)
        : "";
  const toolCountLabel = runActivity?.activeToolCalls
    ? `${runActivity.activeToolCalls} tool call${runActivity.activeToolCalls === 1 ? "" : "s"} active`
    : chatToolStepsLabel(runActivity?.completedToolSteps ?? 0);
  const liveActivityClass = [
    "typing-live-activity",
    runActivity?.providerError ? "error" : "",
    runActivity?.awaitingConfirmation ? "warning" : ""
  ].filter(Boolean).join(" ");
  return (
    <motion.div
      className="typing-row"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
    >
      {runActivity ? (
        <span className={liveActivityClass}>
          <span className="typing-live-icon">
            {runActivity.providerError ? <AlertTriangle size={14} /> : runActivity.awaitingConfirmation ? <ShieldCheck size={14} /> : <Loader2 className="spin" size={14} />}
          </span>
          <span className="typing-live-copy">
            {phaseLabel ? <strong>{phaseLabel}</strong> : null}
            {runActivity.detail ? <small>{runActivity.detail}</small> : slow ? <small>Still waiting on Alfred's provider or IACS tools.</small> : null}
          </span>
          {agentLabel || toolCountLabel ? (
            <span className="typing-live-metrics">
              {agentLabel ? <span>{agentLabel}</span> : null}
              {toolCountLabel ? <span>{toolCountLabel}</span> : null}
            </span>
          ) : null}
        </span>
      ) : null}
      {activeActivity ? (
        <span className="typing-activities">
          <Activity size={12} />
          <span className={`typing-activity ${activeActivity.status}`}>
            {activeActivity.label}
          </span>
          {extraActivityCount ? <span className="typing-activity-count">+{extraActivityCount}</span> : null}
        </span>
      ) : status ? <span className="typing-status">{status}</span> : null}
      {slow && !runActivity ? <span className="typing-slow-note">Still waiting on Alfred's provider or IACS tools.</span> : null}
      <span className="typing-bubble" aria-label="Alfred is typing">
        <i />
        <i />
        <i />
      </span>
    </motion.div>
  );
}

export function publicChatAttachment(attachment: ChatAttachmentDraft): ChatAttachment {
  return {
    id: attachment.id,
    filename: attachment.filename,
    content_type: attachment.content_type,
    size_bytes: attachment.size_bytes,
    kind: attachment.kind,
    url: attachment.url,
    download_url: attachment.download_url,
    source: attachment.source,
    created_at: attachment.created_at
  };
}

export function chatClientContext() {
  return {
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    locale: navigator.language
  };
}
