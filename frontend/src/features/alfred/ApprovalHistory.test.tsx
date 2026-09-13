import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as chat from "../../api/chat";
import type { ChatApprovalPage } from "../../api/chat";
import type { UserAccount } from "../../api/types";
import pages from "../../api/fixtures/approvalPages.generated.json";
import { ApprovalHistory } from "./ApprovalHistory";
const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;
const first = pages.first_page as ChatApprovalPage;
const last = pages.last_page as ChatApprovalPage;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("discovers without browser IDs, waits for explicit selection, and preserves the original conversation", async () => {
  const list = vi.spyOn(chat, "listChatApprovals").mockResolvedValue(first);
  const inspect = vi.fn();
  render(<ApprovalHistory currentUser={admin} busy={false} onInspect={inspect} />);
  await screen.findByRole("button", { name: `Inspect action ${first.items[0].confirmation_id}` });
  expect(list).toHaveBeenCalledWith(undefined, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(inspect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: `Inspect action ${first.items[2].confirmation_id}` }));
  expect(inspect).toHaveBeenCalledExactlyOnceWith({ sessionId: first.items[2].session_id, confirmationId: first.items[2].confirmation_id });
  expect(sessionStorage.length).toBe(0);
});
it("never fabricates a session for historical records and does not inspect while busy", async () => {
  vi.spyOn(chat, "listChatApprovals").mockResolvedValue({ items: [{ ...first.items[0], session_id: null }, first.items[1]], next_cursor: null });
  const inspect = vi.fn();
  render(<ApprovalHistory currentUser={admin} busy onInspect={inspect} />);
  expect(await screen.findByRole("button", { name: `Inspect action ${first.items[0].confirmation_id}` })).toBeDisabled();
  expect(screen.getByRole("button", { name: `Inspect action ${first.items[1].confirmation_id}` })).toBeDisabled();
  expect(screen.getByText(/original conversation is unavailable/)).toBeInTheDocument();
  expect(inspect).not.toHaveBeenCalled();
});
it("paginates and deduplicates, then rejects a repeated cursor without losing displayed rows", async () => {
  const list = vi.spyOn(chat, "listChatApprovals").mockResolvedValueOnce(first)
    .mockResolvedValueOnce({ items: [first.items[0], ...last.items], next_cursor: "next-page" })
    .mockResolvedValueOnce({ items: last.items, next_cursor: first.next_cursor });
  render(<ApprovalHistory currentUser={admin} busy={false} onInspect={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Load older actions" }));
  await screen.findByText(/Outcome unknown/);
  expect(screen.getAllByRole("button", { name: /Inspect action/ })).toHaveLength(6);
  fireEvent.click(screen.getByRole("button", { name: "Load older actions" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("did not advance");
  expect(list).toHaveBeenCalledTimes(3);
});
it("aborts superseded pagination and allows a fresh read to win even if the old promise resolves", async () => {
  const old = deferred<ChatApprovalPage>();
  const list = vi.spyOn(chat, "listChatApprovals").mockResolvedValueOnce(first).mockReturnValueOnce(old.promise).mockResolvedValueOnce(pages.unavailable);
  render(<ApprovalHistory currentUser={admin} busy={false} onInspect={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Load older actions" }));
  const signal = list.mock.calls[1][1]!.signal!;
  fireEvent.click(screen.getByRole("button", { name: "Refresh approval history" }));
  await screen.findByText(/No saved actions are available/);
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve(last));
  expect(screen.queryByText(/Outcome unknown/)).not.toBeInTheDocument();
});
it("clears account results and aborts account/unmount lifetimes", async () => {
  const old = deferred<ChatApprovalPage>();
  const second = deferred<ChatApprovalPage>();
  const list = vi.spyOn(chat, "listChatApprovals").mockReturnValueOnce(old.promise).mockReturnValueOnce(second.promise);
  const view = render(<ApprovalHistory currentUser={admin} busy={false} onInspect={vi.fn()} />);
  view.rerender(<ApprovalHistory currentUser={{ ...admin, id: "other-admin" }} busy={false} onInspect={vi.fn()} />);
  expect(list.mock.calls[0][1]!.signal!.aborted).toBe(true);
  await act(async () => old.resolve(first));
  expect(screen.queryByRole("button", { name: /Inspect action/ })).not.toBeInTheDocument();
  view.rerender(<ApprovalHistory currentUser={{ ...admin, role: "standard" }} busy={false} onInspect={vi.fn()} />);
  expect(list.mock.calls[1][1]!.signal!.aborted).toBe(true);
  await act(async () => second.resolve(last));
  expect(screen.queryByRole("region", { name: "Approval history" })).not.toBeInTheDocument();
});
it("failed reads remain visible and a manual refresh reads again without selecting anything", async () => {
  const list = vi.spyOn(chat, "listChatApprovals").mockRejectedValueOnce(new Error("Read unavailable")).mockResolvedValueOnce(first);
  const inspect = vi.fn();
  render(<ApprovalHistory currentUser={admin} busy={false} onInspect={inspect} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Read unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Refresh approval history" }));
  await screen.findByRole("button", { name: `Inspect action ${first.items[0].confirmation_id}` });
  expect(list).toHaveBeenCalledTimes(2);
  expect(inspect).not.toHaveBeenCalled();
});
