import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { integrationsApi, type GateCommandReceipt } from "../api/integrations";
import type { UserAccount } from "../api/types";
import { CommandHistoryView } from "./CommandHistoryView";

const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("loads saved gate receipts for an administrator without sending a command", async () => {
  const pages = (await import("../api/fixtures/gateReceiptPages.generated.json")).default;
  const list = vi.spyOn(integrationsApi, "getGateCommands").mockResolvedValue(pages.gates as Awaited<ReturnType<typeof integrationsApi.getGateCommands>>);
  const read = vi.spyOn(integrationsApi, "getGateCommand").mockResolvedValue(pages.gates.items[0] as GateCommandReceipt & { command_id: string });
  const send = vi.spyOn(integrationsApi, "openGate");
  const confirm = vi.spyOn(integrationsApi, "confirmGateOpen");
  render(<CommandHistoryView currentUser={admin} />);
  expect(screen.getByRole("heading", { name: "Command History" })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: `Inspect command ${pages.gates.items[0].command_id}` }));
  expect(await screen.findByText("Request accepted · Physical open verified")).toBeInTheDocument();
  expect(list).toHaveBeenCalledOnce();
  expect(read).toHaveBeenCalledOnce();
  expect(send).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
});

it("hides receipts from a standard account", () => {
  const list = vi.spyOn(integrationsApi, "getGateCommands");
  render(<CommandHistoryView currentUser={{ ...admin, role: "standard" }} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Administrator access required for Command History.");
  expect(list).not.toHaveBeenCalled();
});
