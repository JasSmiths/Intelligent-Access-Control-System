import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  REALTIME_CLIENT_PING_INTERVAL_MS,
  REALTIME_DEFER_PARSE_BYTES,
  REALTIME_PROBE_TIMEOUT_MS,
  REALTIME_RECONNECT_DELAY_MS,
  REALTIME_RESUME_RECONNECT_AFTER_MS
} from "./realtimeEvents";
import { useRealtimeConnection } from "./useRealtimeConnection";

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  send = vi.fn<(message: string) => void>();
  close = vi.fn(() => {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.(new Event("close"));
  });
  constructor(readonly url: string) { FakeSocket.instances.push(this); }
  open() { this.readyState = FakeSocket.OPEN; this.onopen?.(new Event("open")); }
  message(data: unknown) { this.onmessage?.(new MessageEvent("message", { data: typeof data === "string" ? data : JSON.stringify(data) })); }
  verify() {
    const probe = JSON.parse(this.send.mock.calls.at(-1)![0]);
    this.message({ type: "connection.pong", payload: { id: probe.payload.id } });
  }
}

function options(sessionKey: string | null = "synthetic-admin:admin") {
  return { sessionKey, onMessage: vi.fn(), onRefresh: vi.fn(), onStatus: vi.fn() };
}
const message = { type: "visitor_pass.updated", payload: { id: "synthetic-pass" } };
let visibility: DocumentVisibilityState;

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("keeps one socket across route consumers and dispatches to the latest committed callbacks", () => {
  const first = options();
  const { rerender } = renderHook((props) => useRealtimeConnection(props), { initialProps: first });
  const socket = FakeSocket.instances[0];
  act(() => { socket.open(); socket.verify(); });
  expect(first.onRefresh).not.toHaveBeenCalled();
  const next = options();
  rerender(next);
  expect(FakeSocket.instances).toHaveLength(1);
  expect(socket.close).not.toHaveBeenCalled();
  act(() => { socket.message(message); });
  expect(first.onMessage).not.toHaveBeenCalled();
  expect(next.onMessage).toHaveBeenCalledWith(message);
  expect(socket.url).toMatch(/\/api\/v1\/realtime\/ws$/);
});

it.each(["other-admin:admin", "synthetic-admin:standard", null])("replaces or closes the transport for session %s and discards old queued frames", (sessionKey) => {
  const initial = options();
  const { rerender } = renderHook((props) => useRealtimeConnection(props), { initialProps: initial });
  const old = FakeSocket.instances[0];
  act(() => { old.open(); old.verify(); });
  const queuedCallback = old.onmessage!;
  act(() => { old.message({ type: "large.event", payload: { padding: "x".repeat(REALTIME_DEFER_PARSE_BYTES) } }); });
  const next = options(sessionKey);
  rerender(next);
  expect(old.close).toHaveBeenCalledOnce();
  expect(old.onmessage).toBeNull();
  act(() => {
    queuedCallback(new MessageEvent("message", { data: JSON.stringify(message) }));
    vi.advanceTimersByTime(0);
  });
  expect(initial.onMessage).not.toHaveBeenCalled();
  expect(next.onMessage).not.toHaveBeenCalled();
  expect(FakeSocket.instances).toHaveLength(sessionKey ? 2 : 1);
});

it("refreshes the current route exactly once after a replacement socket is verified", () => {
  const first = options();
  const { rerender } = renderHook((props) => useRealtimeConnection(props), { initialProps: first });
  const original = FakeSocket.instances[0];
  act(() => { original.open(); original.verify(); original.close(); });
  const current = options();
  rerender(current);
  act(() => { vi.advanceTimersByTime(REALTIME_RECONNECT_DELAY_MS); });
  const replacement = FakeSocket.instances[1];
  act(() => { replacement.open(); replacement.verify(); replacement.verify(); });
  expect(first.onRefresh).not.toHaveBeenCalled();
  expect(current.onRefresh).toHaveBeenCalledOnce();
  expect(FakeSocket.instances).toHaveLength(2);
});

it("uses a matching pong and replaces an unanswered health probe", () => {
  const callbacks = options();
  renderHook(() => useRealtimeConnection(callbacks));
  const socket = FakeSocket.instances[0];
  act(() => {
    socket.open();
    socket.message({ type: "connection.ready", payload: {} });
    socket.message({ type: "connection.pong", payload: { id: "wrong-probe" } });
  });
  expect(callbacks.onStatus.mock.calls.at(-1)?.[0]).toBe("checking");
  act(() => { vi.advanceTimersByTime(REALTIME_PROBE_TIMEOUT_MS); });
  expect(socket.close).toHaveBeenCalledOnce();
  expect(FakeSocket.instances).toHaveLength(2);
  act(() => { FakeSocket.instances[1].open(); FakeSocket.instances[1].verify(); });
  expect(callbacks.onStatus.mock.calls.at(-1)?.[0]).toBe("live");
});

it("reconnects after a long hidden interval and performs one refresh when verified", () => {
  const callbacks = options();
  renderHook(() => useRealtimeConnection(callbacks));
  const socket = FakeSocket.instances[0];
  act(() => { socket.open(); socket.verify(); });
  act(() => {
    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(REALTIME_RESUME_RECONNECT_AFTER_MS + 1);
  });
  expect(socket.send).toHaveBeenCalledOnce();
  act(() => {
    visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(FakeSocket.instances).toHaveLength(2);
  expect(callbacks.onRefresh).not.toHaveBeenCalled();
  act(() => { FakeSocket.instances[1].open(); FakeSocket.instances[1].verify(); });
  expect(callbacks.onRefresh).toHaveBeenCalledOnce();
});

it("probes a short focus resume without reconnecting and continues routine pings", () => {
  const callbacks = options();
  renderHook(() => useRealtimeConnection(callbacks));
  const socket = FakeSocket.instances[0];
  act(() => { socket.open(); socket.verify(); window.dispatchEvent(new Event("focus")); socket.verify(); });
  expect(FakeSocket.instances).toHaveLength(1);
  expect(callbacks.onRefresh).toHaveBeenCalledOnce();
  act(() => { vi.advanceTimersByTime(REALTIME_CLIENT_PING_INTERVAL_MS); });
  expect(socket.send).toHaveBeenCalledTimes(3);
});

it("cleans all parse, probe and reconnect timers and event listeners on unmount", () => {
  const callbacks = options();
  const { unmount } = renderHook(() => useRealtimeConnection(callbacks));
  const socket = FakeSocket.instances[0];
  act(() => {
    socket.open();
    socket.message({ type: "large.event", payload: { padding: "x".repeat(REALTIME_DEFER_PARSE_BYTES) } });
    socket.close();
  });
  unmount();
  expect(vi.getTimerCount()).toBe(0);
  act(() => { window.dispatchEvent(new Event("online")); window.dispatchEvent(new Event("focus")); vi.advanceTimersByTime(60_000); });
  expect(FakeSocket.instances).toHaveLength(1);
  expect(callbacks.onMessage).not.toHaveBeenCalled();
});

it("remains usable after the StrictMode setup/cleanup probe", () => {
  const callbacks = options();
  // RTL wraps a custom wrapper component outside its child StrictMode. With
  // this React version that is not a root effect replay; request root mode.
  const { unmount } = renderHook(() => useRealtimeConnection(callbacks),
    { reactStrictMode: true });
  expect(FakeSocket.instances).toHaveLength(2);
  expect(FakeSocket.instances[0].close).toHaveBeenCalledOnce();
  const current = FakeSocket.instances[1];
  act(() => { current.open(); current.verify(); current.message(message); });
  expect(callbacks.onMessage).toHaveBeenCalledOnce();
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it("reports malformed envelopes without exposing them to route consumers", () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const callbacks = options();
  renderHook(() => useRealtimeConnection(callbacks));
  const socket = FakeSocket.instances[0];
  act(() => { socket.open(); socket.message("{"); socket.message({ payload: {} }); socket.message({ type: "event", payload: null }); });
  expect(callbacks.onMessage).not.toHaveBeenCalled();
  expect(callbacks.onStatus.mock.calls.at(-1)?.[0]).toBe("degraded");
});
