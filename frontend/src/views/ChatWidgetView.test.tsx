import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import * as chatApi from "../api/chat";
import type { ChatApprovalInspection } from "../api/chat";
import type { UserAccount } from "../api/types";
import contract from "../api/fixtures/approvalInspection.generated.json";
import { approvalStorageKey } from "../features/alfred/useApprovalRecovery";
import { chatPendingAction, ChatWidget } from "./ChatWidgetView";

vi.mock("../lib/settings", () => ({ useSettings: () => ({ values: { llm_provider: "local" }, loading: false, error: "", save: vi.fn() }) }));

class FakeSocket {
  static CONNECTING = 0; static OPEN = 1; static CLOSED = 3;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn(() => { this.readyState = FakeSocket.CLOSED; this.onclose?.(new Event("close")); });
  constructor(readonly url: string) { FakeSocket.instances.push(this); }
  open() { this.readyState = FakeSocket.OPEN; this.onopen?.(new Event("open")); }
  message(payload: unknown) { this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) })); }
}
const admin = { id: "synthetic-admin", role: "admin", first_name: "Synthetic", last_name: "Admin", full_name: "Synthetic Admin" } as UserAccount;
const pendingResponse = { type: "chat.response", payload: { session_id: contract.pending.pending_action.session_id,
  text: "Please review this action.", pending_action: contract.pending.pending_action, tool_results: [], attachments: [] } };

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
  sessionStorage.clear();
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.spyOn(api, "get").mockReturnValue(new Promise(() => {}));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); });

it("requires server-issued approval/session identifiers and removes tool-result reconstruction", async () => {
  expect(chatPendingAction({ ...contract.pending.pending_action, confirmation_id: undefined })).toBeNull();
  expect(chatPendingAction({ ...contract.pending.pending_action, session_id: undefined })).toBeNull();
  render(<ChatWidget currentUser={admin} initialOpen maintenanceStatus={null} />);
  const socket = FakeSocket.instances[0];
  act(() => { socket.open(); socket.message({ type: "chat.response", payload: { text: "Needs approval", tool_results: [{
    name: "open_device", arguments: { target: "Synthetic Gate" }, output: { requires_confirmation: true, target: "Synthetic Gate" }
  }] } }); });
  expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  expect(socket.send).not.toHaveBeenCalled();
});

it("sends a single explicit approval then recovers its completed result over GET after disconnect", async () => {
  const inspect = vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValueOnce(contract.pending as ChatApprovalInspection)
    .mockResolvedValue(contract.completed as ChatApprovalInspection);
  render(<ChatWidget currentUser={admin} initialOpen maintenanceStatus={null} />);
  const socket = FakeSocket.instances[0];
  await act(async () => { socket.open(); socket.message(pendingResponse); });
  fireEvent.click(screen.getByRole("button", { name: "Open Gate" }));
  expect(socket.send).toHaveBeenCalledTimes(1);
  const sent = JSON.parse(socket.send.mock.calls[0][0]);
  expect(sent.tool_confirmation).toEqual({ confirmation_id: "confirm-synthetic", decision: "confirm" });
  expect(sent.session_id).toBe(contract.pending.pending_action.session_id);
  expect(sessionStorage.getItem(approvalStorageKey(`${admin.id}:${admin.role}`))).toContain("confirm-synthetic");
  act(() => socket.close());
  await act(async () => { vi.advanceTimersByTime(700); });
  const reconnected = FakeSocket.instances[1];
  await act(async () => reconnected.open());
  expect(inspect).toHaveBeenLastCalledWith(contract.pending.pending_action.session_id, "confirm-synthetic", expect.anything());
  expect(screen.getByText("The controller accepted the request.")).toBeInTheDocument();
  expect(reconnected.send).not.toHaveBeenCalled();
  expect(socket.send).toHaveBeenCalledTimes(1);
  expect(sessionStorage.getItem(approvalStorageKey(`${admin.id}:${admin.role}`))).toBeNull();
});

it("keeps unknown outcomes available for read-only checks and preserves them through ordinary chat", async () => {
  sessionStorage.setItem(approvalStorageKey(`${admin.id}:${admin.role}`), JSON.stringify([{
    sessionId: contract.pending.pending_action.session_id, confirmationId: "confirm-synthetic", decision: "confirm"
  }]));
  const inspect = vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValue(contract.unknown as ChatApprovalInspection);
  render(<ChatWidget currentUser={admin} initialOpen maintenanceStatus={null} />);
  const socket = FakeSocket.instances[0];
  await act(async () => socket.open());
  expect(screen.getByText(/outcome is uncertain and needs review/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Check action result" }));
  await act(async () => {});
  expect(inspect).toHaveBeenCalledTimes(2);
  expect(socket.send).not.toHaveBeenCalled();
  await act(async () => socket.message({ type: "chat.response", payload: { text: "Ordinary answer", tool_results: [], attachments: [] } }));
  expect(sessionStorage.getItem(approvalStorageKey(`${admin.id}:${admin.role}`))).toContain("confirm-synthetic");
});

it("closes transport and clears visible old-account state on role change, rejecting captured late frames", async () => {
  vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValue(contract.pending as ChatApprovalInspection);
  const view = render(<ChatWidget currentUser={admin} initialOpen maintenanceStatus={null} />);
  const old = FakeSocket.instances[0];
  await act(async () => { old.open(); old.message(pendingResponse); });
  const late = old.onmessage!;
  view.rerender(<ChatWidget currentUser={{ ...admin, role: "standard" }} initialOpen maintenanceStatus={null} />);
  expect(old.close).toHaveBeenCalledOnce();
  act(() => late(new MessageEvent("message", { data: JSON.stringify(pendingResponse) })));
  expect(screen.queryByText("Please review this action.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Open Gate" })).not.toBeInTheDocument();
  expect(FakeSocket.instances).toHaveLength(2);
});


it("keeps the receipt after a confirmation response until the durable GET outcome is known", async () => {
  const inspect = vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValueOnce(contract.pending as ChatApprovalInspection)
    .mockResolvedValue(contract.unknown as ChatApprovalInspection);
  render(<ChatWidget currentUser={admin} initialOpen maintenanceStatus={null} />);
  const socket = FakeSocket.instances[0];
  await act(async () => { socket.open(); socket.message(pendingResponse); });
  fireEvent.click(screen.getByRole("button", { name: "Open Gate" }));
  await act(async () => socket.message({ type: "chat.response", payload: {
    ...contract.completed.result, text: "The action result needs review.",
    tool_results: [{ name: "open_gate", output: { status: "unknown", requires_review: true } }]
  } }));
  expect(inspect).toHaveBeenCalledTimes(2);
  expect(sessionStorage.getItem(approvalStorageKey(`${admin.id}:${admin.role}`))).toContain("confirm-synthetic");
  expect(screen.getByText(/outcome is uncertain and needs review/)).toBeInTheDocument();
  expect(socket.send).toHaveBeenCalledTimes(1);
});

it("finds completed approvals after browser loss and recovers over GET without a connected socket or resend", async () => {
  const pages = (await import("../api/fixtures/approvalPages.generated.json")).default;
  const list = vi.spyOn(chatApi, "listChatApprovals").mockResolvedValue(pages.first_page as Awaited<ReturnType<typeof chatApi.listChatApprovals>>);
  const inspect = vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValue(contract.completed as ChatApprovalInspection);
  render(<ChatWidget currentUser={admin} initialOpen maintenanceStatus={null} />);
  const socket = FakeSocket.instances[0]; // Deliberately disconnected: result inspection is HTTP-only.
  expect(list).not.toHaveBeenCalled();
  expect(sessionStorage.length).toBe(0);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Find saved actions" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Inspect action confirm-synthetic-completed" })));
  expect(inspect).toHaveBeenCalledWith(pages.first_page.items[2].session_id, "confirm-synthetic-completed", expect.anything());
  expect(screen.getByText("The controller accepted the request.")).toBeInTheDocument();
  expect(socket.send).not.toHaveBeenCalled();
});
it("inspects a discovered pending approval without confirming until its explicit action button is chosen", async () => {
  const pending = contract.pending.pending_action;
  vi.spyOn(chatApi, "listChatApprovals").mockResolvedValue({ items: [{ confirmation_id: pending.confirmation_id, operation_id: "synthetic-operation", session_id: pending.session_id,
    status: "pending", created_at: "2026-09-12T12:00:00Z", expires_at: "2026-09-12T12:10:00Z" }], next_cursor: null });
  vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValue(contract.pending as ChatApprovalInspection);
  render(<ChatWidget currentUser={admin} initialOpen maintenanceStatus={null} />);
  const socket = FakeSocket.instances[0];
  await act(async () => socket.open());
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Find saved actions" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: `Inspect action ${pending.confirmation_id}` })));
  expect(socket.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Open Gate" }));
  expect(socket.send).toHaveBeenCalledOnce();
  expect(JSON.parse(socket.send.mock.calls[0][0])).toMatchObject({ session_id: pending.session_id,
    tool_confirmation: { confirmation_id: pending.confirmation_id, decision: "confirm" } });
});
