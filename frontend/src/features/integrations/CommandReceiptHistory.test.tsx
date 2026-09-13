import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { integrationsApi, type GateCommandPage, type GateCommandReceipt, type DeviceCommandReceipt } from "../../api/integrations";
import type { UserAccount } from "../../api/types";
import pages from "../../api/fixtures/gateReceiptPages.generated.json";
import { CommandReceiptHistory } from "./CommandReceiptHistory";
const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;
const gates = pages.gates as GateCommandPage;
const gateId = gates.items[0].command_id;
const renderReceipt = vi.fn((receipt: GateCommandReceipt | DeviceCommandReceipt) => <p>Retained outcome: {receipt.delivery}</p>);
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
beforeEach(() => {
  sessionStorage.clear(); renderReceipt.mockClear();
  vi.spyOn(integrationsApi, "openGate").mockImplementation(() => { throw new Error("Unexpected command"); });
  vi.spyOn(integrationsApi, "commandCover").mockImplementation(() => { throw new Error("Unexpected command"); });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("discovers and inspects gate and cover receipts by command ID with an empty browser index", async () => {
  const gateList = vi.spyOn(integrationsApi, "getGateCommands").mockResolvedValue(gates);
  const coverList = vi.spyOn(integrationsApi, "getCoverCommands").mockResolvedValue(pages.covers as Awaited<ReturnType<typeof integrationsApi.getCoverCommands>>);
  const gateRead = vi.spyOn(integrationsApi, "getGateCommand").mockResolvedValue(gates.items[0]);
  const coverRead = vi.spyOn(integrationsApi, "getCoverCommand").mockResolvedValue(pages.covers.items[0] as Awaited<ReturnType<typeof integrationsApi.getCoverCommand>>);
  render(<CommandReceiptHistory currentUser={admin} renderReceipt={renderReceipt} />);
  fireEvent.click(await screen.findByRole("button", { name: `Inspect command ${gateId}` }));
  await screen.findByText("Retained outcome: accepted");
  expect(gateList).toHaveBeenCalledOnce();
  expect(gateRead).toHaveBeenCalledWith(gateId, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  fireEvent.change(screen.getByLabelText("Command history type"), { target: { value: "cover" } });
  fireEvent.click(await screen.findByRole("button", { name: `Inspect command ${pages.covers.items[0].command_id}` }));
  await screen.findByText("Retained outcome: accepted");
  expect(coverList).toHaveBeenCalledOnce();
  expect(coverRead).toHaveBeenCalledWith(pages.covers.items[0].command_id, expect.anything());
  expect(integrationsApi.openGate).not.toHaveBeenCalled();
  expect(integrationsApi.commandCover).not.toHaveBeenCalled();
  expect(sessionStorage.length).toBe(0);
});
it("keeps missing and historical incomplete results uncertain, with GET-only manual refresh", async () => {
  vi.spyOn(integrationsApi, "getGateCommands").mockResolvedValue(gates);
  const read = vi.spyOn(integrationsApi, "getGateCommand").mockRejectedValueOnce(new Error("404 Not found"))
    .mockResolvedValueOnce({ command_id: gateId, accepted: true, state: "open" });
  render(<CommandReceiptHistory currentUser={admin} renderReceipt={renderReceipt} />);
  fireEvent.click(await screen.findByRole("button", { name: `Inspect command ${gateId}` }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Do not repeat an uncertain command");
  fireEvent.click(screen.getByRole("button", { name: "Refresh command result" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no complete command receipt");
  expect(renderReceipt).not.toHaveBeenCalled();
  expect(read).toHaveBeenCalledTimes(2);
  expect(integrationsApi.openGate).not.toHaveBeenCalled();
});
it("aborts older pages on refresh and ignores their late results", async () => {
  const pending = deferred<GateCommandPage>();
  const list = vi.spyOn(integrationsApi, "getGateCommands").mockResolvedValueOnce({ ...gates, next_cursor: gateId })
    .mockReturnValueOnce(pending.promise).mockResolvedValueOnce({ items: [], next_cursor: null });
  render(<CommandReceiptHistory currentUser={admin} renderReceipt={renderReceipt} />);
  fireEvent.click(await screen.findByRole("button", { name: "Load older commands" }));
  const signal = list.mock.calls[1][1]!.signal!;
  fireEvent.click(screen.getByRole("button", { name: "Refresh command history" }));
  await screen.findByText(/No recorded commands are available/);
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve(gates));
  expect(screen.queryByRole("button", { name: /Inspect command/ })).not.toBeInTheDocument();
});
it("deduplicates pages and reports a non-advancing cursor", async () => {
  vi.spyOn(integrationsApi, "getGateCommands").mockResolvedValueOnce({ ...gates, next_cursor: gateId })
    .mockResolvedValueOnce({ ...gates, next_cursor: "next" }).mockResolvedValueOnce({ ...gates, next_cursor: gateId });
  render(<CommandReceiptHistory currentUser={admin} renderReceipt={renderReceipt} />);
  fireEvent.click(await screen.findByRole("button", { name: "Load older commands" }));
  await act(async () => {});
  expect(screen.getAllByRole("button", { name: /Inspect command/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Load older commands" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("did not advance");
});
it("aborts detail reads on kind/account change and clears old results before late promises resolve", async () => {
  vi.spyOn(integrationsApi, "getGateCommands").mockResolvedValue(gates);
  const coverList = vi.spyOn(integrationsApi, "getCoverCommands").mockReturnValue(new Promise(() => {}));
  const pending = deferred<Awaited<ReturnType<typeof integrationsApi.getGateCommand>>>();
  const read = vi.spyOn(integrationsApi, "getGateCommand").mockReturnValue(pending.promise);
  const view = render(<CommandReceiptHistory currentUser={admin} renderReceipt={renderReceipt} />);
  fireEvent.click(await screen.findByRole("button", { name: `Inspect command ${gateId}` }));
  const signal = read.mock.calls[0][1]!.signal!;
  fireEvent.change(screen.getByLabelText("Command history type"), { target: { value: "cover" } });
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve(gates.items[0]));
  expect(renderReceipt).not.toHaveBeenCalled();
  view.rerender(<CommandReceiptHistory currentUser={{ ...admin, role: "standard" }} renderReceipt={renderReceipt} />);
  expect(coverList.mock.calls[0][1]!.signal!.aborted).toBe(true);
  expect(screen.queryByRole("region", { name: "Command history" })).not.toBeInTheDocument();
});
it("aborts an in-flight list on unmount", () => {
  const list = vi.spyOn(integrationsApi, "getGateCommands").mockReturnValue(new Promise(() => {}));
  const view = render(<CommandReceiptHistory currentUser={admin} renderReceipt={renderReceipt} />);
  view.unmount();
  expect(list.mock.calls[0][1]!.signal!.aborted).toBe(true);
});
