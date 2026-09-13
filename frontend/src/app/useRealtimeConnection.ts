import React from "react";
import { wsUrl } from "../api/client";
import type { RealtimeMessage } from "../api/types";
import { isRecord } from "../lib/format";
import {
  REALTIME_CLIENT_PING_INTERVAL_MS,
  REALTIME_DEFER_PARSE_BYTES,
  REALTIME_PROBE_TIMEOUT_MS,
  REALTIME_RECONNECT_DELAY_MS,
  REALTIME_RESUME_RECONNECT_AFTER_MS,
  realtimeProbeDetail,
  type RealtimeConnectionStatus
} from "./realtimeEvents";

type RealtimeConnectionOptions = {
  sessionKey: string | null;
  onMessage: (message: RealtimeMessage) => void;
  onRefresh: () => void;
  onStatus: (status: RealtimeConnectionStatus, detail: string) => void;
};

// Session identity owns the transport. Route consumers are current committed
// callbacks, so navigation never tears down a healthy authenticated socket.
export function useRealtimeConnection({ sessionKey, onMessage, onRefresh, onStatus }: RealtimeConnectionOptions) {
  const callbacks = React.useRef({ sessionKey, onMessage, onRefresh, onStatus });
  React.useLayoutEffect(() => {
    callbacks.current = { sessionKey, onMessage, onRefresh, onStatus };
  }, [sessionKey, onMessage, onRefresh, onStatus]);
  React.useEffect(() => {
    if (!sessionKey) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let pingTimer: number | null = null;
    let probeTimer: number | null = null;
    let pendingProbeId: string | null = null;
    let verifiedSocket: WebSocket | null = null;
    let stopped = false;
    let refreshOnVerification = false;
    const parseTimers = new Set<number>();
    const isCurrent = () => !stopped && callbacks.current.sessionKey === sessionKey;
    const setRealtimeStatus = (status: RealtimeConnectionStatus, detail: string) => {
      if (isCurrent()) callbacks.current.onStatus(status, detail);
    };
    let backgroundedAt: number | null = document.visibilityState === "hidden" ? Date.now() : null;
    let unfocusedAt: number | null = document.hasFocus() ? null : Date.now();
    let lastSocketActivityAt = Date.now();
    let hasVerifiedSocket = false;
    const handleMessageData = (data: unknown, sourceSocket: WebSocket) => {
      if (!isCurrent() || socket !== sourceSocket) return;
      lastSocketActivityAt = Date.now();
      let parsed: RealtimeMessage;
      try {
        const candidate: unknown = JSON.parse(String(data));
        if (!isRecord(candidate) || typeof candidate.type !== "string" || !isRecord(candidate.payload)) {
          throw new Error("Invalid realtime envelope");
        }
        parsed = candidate as RealtimeMessage;
      } catch (parseError) {
        console.warn("Ignored malformed realtime stream message", {
          error: parseError instanceof Error ? parseError.message : String(parseError),
          bytes: typeof data === "string" ? data.length : undefined
        });
        setRealtimeStatus("degraded", "Ignored malformed stream data; waiting for next event");
        return;
      }
      if (parsed.type === "connection.pong") {
        markSocketVerified(sourceSocket, parsed);
        return;
      }
      if (parsed.type === "connection.ready") {
        if (pendingProbeId) {
          setRealtimeStatus("checking", "Server accepted stream; waiting for health reply");
        } else {
          markSocketVerified(sourceSocket, parsed);
        }
        return;
      }
      callbacks.current.onMessage(parsed);
    };
    const handleMessage = (event: MessageEvent, sourceSocket: WebSocket) => {
      if (!isCurrent() || socket !== sourceSocket) return;
      lastSocketActivityAt = Date.now();
      if (typeof event.data === "string" && event.data.length >= REALTIME_DEFER_PARSE_BYTES) {
        const timer = window.setTimeout(() => {
          parseTimers.delete(timer);
          handleMessageData(event.data, sourceSocket);
        }, 0);
        parseTimers.add(timer);
        return;
      }
      handleMessageData(event.data, sourceSocket);
    };
    const clearProbeTimer = () => {
      if (probeTimer === null) return;
      window.clearTimeout(probeTimer);
      probeTimer = null;
    };
    const clearReconnectTimer = () => {
      if (reconnectTimer === null) return;
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };
    const markSocketVerified = (target: WebSocket, message: RealtimeMessage) => {
      if (!isCurrent() || socket !== target) return;
      const payload = isRecord(message.payload) ? message.payload : {};
      const messageProbeId = typeof payload.id === "string" ? payload.id : null;
      if (message.type === "connection.pong" && pendingProbeId && messageProbeId !== pendingProbeId) return;
      const firstVerificationForSocket = verifiedSocket !== target;
      verifiedSocket = target;
      pendingProbeId = null;
      lastSocketActivityAt = Date.now();
      clearProbeTimer();
      setRealtimeStatus(
        "live",
        message.type === "connection.pong" ? "Stream verified just now" : "Server accepted stream"
      );
      if (firstVerificationForSocket) {
        if (hasVerifiedSocket || refreshOnVerification) {
          callbacks.current.onRefresh();
        }
        refreshOnVerification = false;
        hasVerifiedSocket = true;
      }
    };
    const nextProbeId = () => {
      if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
      }
      return `${Date.now()}:${Math.random().toString(36).slice(2)}`;
    };
    const sendSocketProbe = (target: WebSocket, reason: string) => {
      if (target.readyState !== WebSocket.OPEN) return false;
      const probeId = nextProbeId();
      clearProbeTimer();
      pendingProbeId = probeId;
      setRealtimeStatus("checking", realtimeProbeDetail(reason));
      try {
        target.send(JSON.stringify({
          type: "client.ping",
          payload: {
            id: probeId,
            reason,
            at: new Date().toISOString()
          }
        }));
        probeTimer = window.setTimeout(() => {
          if (!isCurrent() || socket !== target || pendingProbeId !== probeId) return;
          pendingProbeId = null;
          reconnectNow(`probe_timeout:${reason}`);
        }, REALTIME_PROBE_TIMEOUT_MS);
        return true;
      } catch {
        console.warn("Realtime probe send failed; reconnecting stream", { reason });
        try {
          target.close();
        } catch {
          // The resume handler will replace sockets that cannot be probed.
        }
        return false;
      }
    };
    const scheduleReconnect = (closedSocket: WebSocket | null) => {
      if (closedSocket && socket !== closedSocket) return;
      if (closedSocket) {
        socket = null;
      }
      if (!isCurrent() || reconnectTimer !== null) return;
      pendingProbeId = null;
      verifiedSocket = null;
      clearProbeTimer();
      setRealtimeStatus("reconnecting", "Opening a fresh stream shortly");
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        openSocket();
      }, REALTIME_RECONNECT_DELAY_MS);
    };
    const reconnectNow = (reason: string) => {
      if (!isCurrent()) return;
      clearReconnectTimer();
      clearProbeTimer();
      const currentSocket = socket;
      socket = null;
      pendingProbeId = null;
      verifiedSocket = null;
      setRealtimeStatus("reconnecting", realtimeProbeDetail(reason));
      if (currentSocket && currentSocket.readyState !== WebSocket.CLOSED) {
        try {
          currentSocket.close(4000, reason.slice(0, 100));
        } catch {
          try {
            currentSocket.close();
          } catch {
            // The follow-up open below replaces the failed socket either way.
          }
        }
      }
      openSocket();
    };
    const handleResume = (reason: string) => {
      if (!isCurrent()) return;
      const now = Date.now();
      const inactiveFor = Math.max(
        backgroundedAt === null ? 0 : now - backgroundedAt,
        unfocusedAt === null ? 0 : now - unfocusedAt
      );
      backgroundedAt = null;
      unfocusedAt = null;
      const currentSocket = socket;
      const connectingTimedOut =
        currentSocket?.readyState === WebSocket.CONNECTING &&
        now - lastSocketActivityAt >= REALTIME_RESUME_RECONNECT_AFTER_MS;
      const shouldReconnect =
        !currentSocket ||
        currentSocket.readyState === WebSocket.CLOSED ||
        currentSocket.readyState === WebSocket.CLOSING ||
        connectingTimedOut ||
        inactiveFor >= REALTIME_RESUME_RECONNECT_AFTER_MS;
      if (shouldReconnect) {
        refreshOnVerification = true;
        reconnectNow(reason);
      } else if (currentSocket.readyState === WebSocket.OPEN) {
        if (sendSocketProbe(currentSocket, reason)) {
          callbacks.current.onRefresh();
        } else {
          refreshOnVerification = true;
          reconnectNow(`${reason}:probe_failed`);
        }
      } else if (currentSocket.readyState === WebSocket.CONNECTING) {
        refreshOnVerification = true;
        setRealtimeStatus("connecting", "Connection is still opening");
      }
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        backgroundedAt = backgroundedAt ?? Date.now();
        return;
      }
      handleResume("visibilitychange");
    };
    const handleWindowBlur = () => {
      unfocusedAt = unfocusedAt ?? Date.now();
    };
    const handleWindowFocus = () => handleResume("focus");
    const handlePageHide = () => {
      backgroundedAt = backgroundedAt ?? Date.now();
      unfocusedAt = unfocusedAt ?? Date.now();
    };
    const handlePageShow = () => handleResume("pageshow");
    const handleOnline = () => handleResume("online");
    const handleOffline = () => {
      setRealtimeStatus("offline", "Waiting for network to return");
      backgroundedAt = backgroundedAt ?? Date.now();
    };
    function openSocket() {
      if (!isCurrent()) return;
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
      clearReconnectTimer();
      const nextSocket = new WebSocket(wsUrl("/api/v1/realtime/ws"));
      socket = nextSocket;
      lastSocketActivityAt = Date.now();
      setRealtimeStatus("connecting", "Opening /api/v1/realtime/ws");
      nextSocket.onopen = () => {
        if (!isCurrent() || socket !== nextSocket) return;
        lastSocketActivityAt = Date.now();
        sendSocketProbe(nextSocket, "open");
      };
      nextSocket.onmessage = (event) => handleMessage(event, nextSocket);
      nextSocket.onclose = () => scheduleReconnect(nextSocket);
      nextSocket.onerror = () => {
        console.warn("Realtime stream socket error; reconnecting");
        if (socket === nextSocket) {
          nextSocket.close();
        }
      };
    }
    openSocket();
    pingTimer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      const currentSocket = socket;
      if (!currentSocket) {
        scheduleReconnect(null);
        return;
      }
      if (currentSocket.readyState === WebSocket.OPEN) {
        sendSocketProbe(currentSocket, "interval");
        return;
      }
      if (
        currentSocket.readyState === WebSocket.CONNECTING &&
        Date.now() - lastSocketActivityAt >= REALTIME_RESUME_RECONNECT_AFTER_MS
      ) {
        reconnectNow("connect_timeout");
        return;
      }
      if (currentSocket.readyState === WebSocket.CLOSED || currentSocket.readyState === WebSocket.CLOSING) {
        scheduleReconnect(currentSocket);
      }
    }, REALTIME_CLIENT_PING_INTERVAL_MS);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleWindowFocus);
    window.addEventListener("blur", handleWindowBlur);
    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("pageshow", handlePageShow);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      stopped = true;
      clearReconnectTimer();
      clearProbeTimer();
      parseTimers.forEach((timer) => window.clearTimeout(timer));
      parseTimers.clear();
      if (pingTimer !== null) {
        window.clearInterval(pingTimer);
      }
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleWindowFocus);
      window.removeEventListener("blur", handleWindowBlur);
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("pageshow", handlePageShow);
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
        socket.close();
      }
      socket = null;
      verifiedSocket = null;
    };
  }, [sessionKey]);
}
