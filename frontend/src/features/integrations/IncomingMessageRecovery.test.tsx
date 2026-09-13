import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MessageCircle } from "lucide-react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { incomingMessagesApi, type IncomingMessage, type IncomingMessagePage, type IncomingMessageProvider } from "../../api/incomingMessages";
import type { UserAccount } from "../../api/types";
import contract from "../../api/fixtures/incomingRecovery.generated.json";
import { IncomingMessageDetails, IncomingMessageRecovery } from "./IncomingMessageRecovery";
import { IntegrationModal } from "./providerPanels";
import type { IntegrationDefinition } from "./catalog";

const admin = { id: "synthetic-admin", role: "admin", is_active: true } as UserAccount;
const page: IncomingMessagePage = contract.whatsapp;
const message = page.items[2];
const props = { currentUser: admin, provider: "whatsapp" as const };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
function openHistory() {
  fireEvent.click(screen.getByRole("button", { name: "Inspect incoming messages" }));
}

beforeEach(() => {
  vi.spyOn(incomingMessagesApi, "getPage").mockResolvedValue(page);
  vi.spyOn(incomingMessagesApi, "getDetail").mockResolvedValue(message);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each<IncomingMessageProvider>(["whatsapp", "discord"])("mounts incoming recovery in the existing %s settings modal without sending or saving", async (provider) => {
  vi.mocked(incomingMessagesApi.getPage).mockResolvedValue(contract[provider]);
  const definition: IntegrationDefinition = { key: provider, title: provider, description: "Synthetic integration", category: "notifications",
    icon: MessageCircle, fields: [], statusLabel: "Not configured", statusTone: "gray" };
  const onSaved = vi.fn();
  render(<IntegrationModal definition={definition} currentUser={admin} initialTab="general" values={{}} loading={false}
    dependencyPackages={[]} dependencyStorage={null} people={[]} onClose={vi.fn()} onSettingsChanged={vi.fn()} onSaved={onSaved} />);
  expect(incomingMessagesApi.getPage).not.toHaveBeenCalled();
  openHistory();
  await screen.findByRole("button", { name: `Inspect incoming message ${contract[provider].items[0].id}` });
  expect(onSaved).not.toHaveBeenCalled();
});

it.each<IncomingMessageProvider>(["whatsapp", "discord"])("only reads %s history after the operator opens it", async (provider) => {
  vi.mocked(incomingMessagesApi.getPage).mockResolvedValue(contract[provider]);
  render(<IncomingMessageRecovery {...props} provider={provider} />);
  expect(incomingMessagesApi.getPage).not.toHaveBeenCalled();
  openHistory();
  await screen.findByRole("button", { name: `Inspect incoming message ${contract[provider].items[0].id}` });
  expect(incomingMessagesApi.getPage).toHaveBeenCalledExactlyOnceWith(provider, undefined, { signal: expect.any(AbortSignal) });
});

it.each([{ ...admin, role: "standard" as const }, { ...admin, is_active: false }])("never reads incoming history for an unauthorized account", (currentUser) => {
  render(<IncomingMessageRecovery {...props} currentUser={currentUser} />);
  expect(screen.queryByRole("button", { name: "Inspect incoming messages" })).not.toBeInTheDocument();
  expect(incomingMessagesApi.getPage).not.toHaveBeenCalled();
});

it("distinguishes processing and delivery, and shows the bound opaque approval without actions", () => {
  const view = render(<IncomingMessageDetails message={message} />);
  expect(screen.getByText("Review required")).toBeInTheDocument();
  expect(screen.getByText("Delivery unknown — review required")).toBeInTheDocument();
  expect(screen.getByText("confirm-00000000000000000000000000000022")).toBeInTheDocument();
  expect(view.container.querySelector(".badge.green")).toBeNull();
  expect(screen.queryByRole("button", { name: /retry|resend|confirm|approve/i })).not.toBeInTheDocument();
  view.rerender(<IncomingMessageDetails message={contract.discord.items[0]} />);
  expect(screen.getByText("Processing finished", { selector: ".badge-label" })).toBeInTheDocument();
  expect(screen.getByText("Accepted by provider")).toBeInTheDocument();
  expect(screen.getByText("Provider acceptance does not confirm that a recipient read the reply.")).toBeInTheDocument();
});

it("shows an expired processing claim or unfamiliar delivery as requiring review", () => {
  const view = render(<IncomingMessageDetails message={{ ...page.items[1], lease_expired: true }} />);
  expect(screen.getByText(/Processing stopped before a final outcome/)).toBeInTheDocument();
  expect(screen.getByText("Review required")).toBeInTheDocument();
  view.rerender(<IncomingMessageDetails message={{ ...message, requires_review: false, review_reason: null, state: "handled",
    replies: [{ ...message.replies[0], delivery: "future_value" }] }} />);
  expect(screen.getByText("Delivery unavailable — review required")).toBeInTheDocument();
  expect(view.container.querySelector(".badge.green")).toBeNull();
});

it("keeps unknown status and label values explicit instead of inferring success", () => {
  render(<IncomingMessageDetails message={{ ...page.items[0], state: "future_value", origin_kind: "toString", review_reason: "toString" }} />);
  expect(screen.getByText("Status unavailable — review required")).toBeInTheDocument();
  expect(screen.getByText("Unavailable")).toBeInTheDocument();
  expect(screen.getByText("The recorded outcome requires review.")).toBeInTheDocument();
});

it("does not render unrecognized private fields, arbitrary reasons, or unsafe identifiers", () => {
  const unexpected = { ...message, review_reason: "synthetic-private-content", envelope: { body: "synthetic-private-body" },
    result_ids: { ...message.result_ids, secret: "synthetic-secret", confirmation_id: "javascript:synthetic" },
    replies: [{ ...message.replies[0], operation_id: "javascript:synthetic" }] };
  const view = render(<IncomingMessageDetails message={unexpected} />);
  expect(view.container.textContent).not.toMatch(/synthetic-private|synthetic-secret|javascript:/);
  expect(screen.getByText("The recorded outcome requires review.")).toBeInTheDocument();
  expect(screen.getByText("Operation identity unavailable.")).toBeInTheDocument();
});

it("loads detail separately and recovers a failed read without repeating the message", async () => {
  vi.mocked(incomingMessagesApi.getDetail).mockRejectedValueOnce(new Error("Record temporarily unavailable")).mockResolvedValueOnce(message);
  render(<IncomingMessageRecovery {...props} />);
  openHistory();
  fireEvent.click(await screen.findByRole("button", { name: `Inspect incoming message ${message.id}` }));
  await screen.findByText(/A failed read does not establish the delivery outcome/);
  fireEvent.click(screen.getByRole("button", { name: "Refresh message result" }));
  const detail = await screen.findByRole("region", { name: "Selected incoming message" });
  await within(detail).findByText("Delivery unknown — review required");
  expect(incomingMessagesApi.getDetail).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: /retry|resend|confirm|approve/i })).not.toBeInTheDocument();
});

it("replaces the current page with older records and refuses a repeated cursor", async () => {
  const cursor = "00000000-0000-0000-0000-000000000099";
  const older = { ...message, id: "00000000-0000-0000-0000-000000000098" };
  vi.mocked(incomingMessagesApi.getPage).mockResolvedValueOnce({ ...page, next_cursor: cursor })
    .mockResolvedValueOnce({ items: [older], next_cursor: older.id })
    .mockResolvedValueOnce({ items: [older], next_cursor: older.id });
  render(<IncomingMessageRecovery {...props} />);
  openHistory();
  fireEvent.click(await screen.findByRole("button", { name: "Load older messages" }));
  await screen.findByRole("button", { name: `Inspect incoming message ${older.id}` });
  expect(screen.queryByRole("button", { name: `Inspect incoming message ${message.id}` })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
  await screen.findByText(/Incoming history did not advance/);
  expect(screen.queryByRole("button", { name: "Load older messages" })).not.toBeInTheDocument();
  expect(incomingMessagesApi.getPage).toHaveBeenCalledTimes(3);
});

it("cancels older-page reads on refresh and ignores their late response", async () => {
  const cursor = "00000000-0000-0000-0000-000000000099";
  const older = deferred<IncomingMessagePage>();
  vi.mocked(incomingMessagesApi.getPage).mockResolvedValueOnce({ ...page, next_cursor: cursor })
    .mockReturnValueOnce(older.promise).mockResolvedValueOnce({ items: [], next_cursor: null });
  render(<IncomingMessageRecovery {...props} />);
  openHistory();
  fireEvent.click(await screen.findByRole("button", { name: "Load older messages" }));
  const signal = vi.mocked(incomingMessagesApi.getPage).mock.calls[1][2]!.signal!;
  fireEvent.click(screen.getByRole("button", { name: "Refresh incoming history" }));
  expect(signal.aborted).toBe(true);
  await act(async () => older.resolve(page));
  await screen.findByText("No incoming recovery records are available.");
  expect(screen.queryByRole("button", { name: `Inspect incoming message ${message.id}` })).not.toBeInTheDocument();
});

it("aborts both account-owned list and detail reads on account change", async () => {
  const detail = deferred<IncomingMessage>();
  vi.mocked(incomingMessagesApi.getDetail).mockReturnValueOnce(detail.promise);
  const view = render(<IncomingMessageRecovery {...props} />);
  openHistory();
  fireEvent.click(await screen.findByRole("button", { name: `Inspect incoming message ${message.id}` }));
  const listSignal = vi.mocked(incomingMessagesApi.getPage).mock.calls[0][2]!.signal!;
  const detailSignal = vi.mocked(incomingMessagesApi.getDetail).mock.calls[0][2]!.signal!;
  view.rerender(<IncomingMessageRecovery {...props} currentUser={{ ...admin, id: "second-admin" }} />);
  expect(listSignal.aborted).toBe(true);
  expect(detailSignal.aborted).toBe(true);
  await act(async () => detail.resolve(message));
  expect(screen.queryByRole("region", { name: "Selected incoming message" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Inspect incoming messages" })).toHaveAttribute("aria-expanded", "false");
});

it.each(["close", "unmount", "demote", "deactivate", "provider"])("cancels a pending read when %s ends its scope", async (ending) => {
  const pending = deferred<IncomingMessagePage>();
  vi.mocked(incomingMessagesApi.getPage).mockReturnValueOnce(pending.promise);
  const view = render(<IncomingMessageRecovery {...props} />);
  openHistory();
  const signal = vi.mocked(incomingMessagesApi.getPage).mock.calls[0][2]!.signal!;
  if (ending === "close") fireEvent.click(screen.getByRole("button", { name: "Close incoming history" }));
  if (ending === "unmount") view.unmount();
  if (ending === "demote") view.rerender(<IncomingMessageRecovery {...props} currentUser={{ ...admin, role: "standard" }} />);
  if (ending === "deactivate") view.rerender(<IncomingMessageRecovery {...props} currentUser={{ ...admin, is_active: false }} />);
  if (ending === "provider") view.rerender(<IncomingMessageRecovery {...props} provider="discord" />);
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve(page));
  expect(screen.queryByRole("button", { name: `Inspect incoming message ${message.id}` })).not.toBeInTheDocument();
  expect(incomingMessagesApi.getPage).toHaveBeenCalledOnce();
});

it("rejects a cross-provider result without displaying its records", async () => {
  vi.mocked(incomingMessagesApi.getPage).mockResolvedValue(contract.discord);
  render(<IncomingMessageRecovery {...props} />);
  openHistory();
  await screen.findByText(/Incoming history returned an unexpected page/);
  expect(screen.queryByRole("button", { name: `Inspect incoming message ${contract.discord.items[0].id}` })).not.toBeInTheDocument();
});

it("rejects an oversized response rather than accumulating unbounded records", async () => {
  vi.mocked(incomingMessagesApi.getPage).mockResolvedValue({ items: Array.from({ length: 26 }, (_, index) => ({ ...message, id: `synthetic-${index}` })), next_cursor: null });
  render(<IncomingMessageRecovery {...props} />);
  openHistory();
  await screen.findByText(/Incoming history returned an unexpected page/);
  expect(screen.queryByRole("button", { name: /Inspect incoming message synthetic/ })).not.toBeInTheDocument();
});

it("rejects detail that no longer matches the selected provider or record", async () => {
  vi.mocked(incomingMessagesApi.getDetail).mockResolvedValue(contract.discord.items[0]);
  render(<IncomingMessageRecovery {...props} />);
  openHistory();
  fireEvent.click(await screen.findByRole("button", { name: `Inspect incoming message ${message.id}` }));
  await screen.findByText(/did not match the selected message/);
  expect(screen.queryByText("Accepted by provider")).not.toBeInTheDocument();
});
