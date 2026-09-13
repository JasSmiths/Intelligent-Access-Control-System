import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as chatApi from "../../api/chat";
import type { ChatApprovalInspection } from "../../api/chat";
import contract from "../../api/fixtures/approvalInspection.generated.json";
import { approvalStorageKey, useApprovalRecovery, type RetainedApproval } from "./useApprovalRecovery";

const first: RetainedApproval = { sessionId: "session-synthetic", confirmationId: "confirm-first", decision: "confirm" };
const second: RetainedApproval = { sessionId: "session-synthetic", confirmationId: "confirm-second" };
const pending = contract.pending as ChatApprovalInspection;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

it("retains only requester-scoped opaque receipts across reload and keeps earlier unresolved actions", async () => {
  const inspect = vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValue(pending);
  const view = renderHook(() => useApprovalRecovery("synthetic:admin", true, 0));
  act(() => { view.result.current.remember(first); });
  await act(async () => {});
  act(() => { view.result.current.remember(second); });
  await act(async () => {});
  expect(view.result.current.approvals).toEqual([first, second]);
  expect(JSON.parse(sessionStorage.getItem(approvalStorageKey("synthetic:admin"))!)).toEqual([first, second]);
  view.unmount();
  const restored = renderHook(() => useApprovalRecovery("synthetic:admin", true, 0));
  await act(async () => {});
  expect(inspect.mock.calls.at(-1)!.slice(0, 2)).toEqual([second.sessionId, second.confirmationId]);
  act(() => restored.result.current.forget(second.confirmationId));
  await act(async () => {});
  expect(restored.result.current.approval).toEqual(first);
  expect(inspect.mock.calls.at(-1)!.slice(0, 2)).toEqual([first.sessionId, first.confirmationId]);
});

it("aborts an earlier receipt inspection and rejects its late result after selection changes", async () => {
  const old = deferred<ChatApprovalInspection>();
  const current = deferred<ChatApprovalInspection>();
  const inspect = vi.spyOn(chatApi, "inspectChatApproval").mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  const view = renderHook(() => useApprovalRecovery("synthetic:admin", true, 0));
  act(() => view.result.current.remember(first));
  const signal = inspect.mock.calls[0][2]!.signal!;
  act(() => view.result.current.remember(second));
  expect(signal.aborted).toBe(true);
  await act(async () => current.resolve(contract.in_progress as ChatApprovalInspection));
  expect(view.result.current.inspection?.status).toBe("in_progress");
  await act(async () => old.resolve(contract.completed as ChatApprovalInspection));
  expect(view.result.current.inspection?.status).toBe("in_progress");
});

it("isolates account changes, aborts disabled or unmounted reads, and rechecks only with a fresh GET", async () => {
  const old = deferred<ChatApprovalInspection>();
  const inspect = vi.spyOn(chatApi, "inspectChatApproval").mockReturnValueOnce(old.promise).mockResolvedValue(pending);
  const view = renderHook(({ requester, enabled, nonce }) => useApprovalRecovery(requester, enabled, nonce), {
    initialProps: { requester: "synthetic:admin", enabled: true, nonce: 0 }
  });
  act(() => view.result.current.remember(first));
  const signal = inspect.mock.calls[0][2]!.signal!;
  view.rerender({ requester: "other:admin", enabled: true, nonce: 1 });
  expect(signal.aborted).toBe(true);
  expect(view.result.current.approval).toBeNull();
  await act(async () => old.resolve(contract.completed as ChatApprovalInspection));
  expect(view.result.current.inspection).toBeNull();
  act(() => view.result.current.remember(second));
  await act(async () => {});
  act(() => view.result.current.recheck());
  await act(async () => {});
  expect(inspect).toHaveBeenCalledTimes(3);
  const currentSignal = inspect.mock.calls[2][2]!.signal!;
  view.unmount();
  expect(currentSignal.aborted).toBe(true);
});

it("ignores malformed stored records and cannot reconstruct an executable action", () => {
  sessionStorage.setItem(approvalStorageKey("synthetic:admin"), JSON.stringify([{ confirmationId: "confirm", toolArguments: { confirm: true } }]));
  const inspect = vi.spyOn(chatApi, "inspectChatApproval");
  const view = renderHook(() => useApprovalRecovery("synthetic:admin", true, 0));
  expect(view.result.current.approvals).toEqual([]);
  expect(inspect).not.toHaveBeenCalled();
});

it("selects an already retained action when it is explicitly rediscovered", async () => {
  vi.spyOn(chatApi, "inspectChatApproval").mockResolvedValue(pending);
  const view = renderHook(() => useApprovalRecovery("synthetic:admin", true, 0));
  await act(async () => view.result.current.remember(first));
  await act(async () => view.result.current.remember(second));
  await act(async () => view.result.current.remember(first));
  expect(view.result.current.approval).toEqual(first);
  expect(view.result.current.approvals).toEqual([first, second]);
});
