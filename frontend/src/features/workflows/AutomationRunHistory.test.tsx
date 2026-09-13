import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { UserAccount } from "../../api/types";
import { workflowApi, type AutomationRun, type AutomationRunPage } from "../../api/workflows";
import contract from "../../api/fixtures/automationRecovery.generated.json";
import { AutomationsView } from "./AutomationsView";
import { AutomationRunDetails, AutomationRunHistory } from "./AutomationRunHistory";

const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;
const run: AutomationRun = contract;
const page: AutomationRunPage = { items: [run], next_cursor: null };
const props = { currentUser: admin, refreshToken: 0, rules: [] };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.spyOn(workflowApi, "getAutomationRuns").mockResolvedValue(page);
  vi.spyOn(workflowApi, "getAutomationRun").mockResolvedValue(run);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("opens run history from the automation owner only when requested", async () => {
  vi.spyOn(workflowApi, "getAutomationData").mockResolvedValue({ rules: [], users: [], catalog: {
    triggers: [], conditions: [], actions: [], variables: [], notification_rules: [], garage_doors: [], mock_context: {},
  } });
  render(<AutomationsView currentUser={admin} people={[]} vehicles={[]} refreshToken={0} />);
  const open = await screen.findByRole("button", { name: "Run history" });
  expect(workflowApi.getAutomationRuns).not.toHaveBeenCalled();
  fireEvent.click(open);
  await screen.findByRole("button", { name: `Inspect Automation run ${run.id}` });
  expect(workflowApi.getAutomationRuns).toHaveBeenCalledOnce();
});

it("shows review state and links the exact gate operation to read-only receipt inspection", () => {
  const view = render(<AutomationRunDetails run={run} />);
  expect(screen.getByText("Review required")).toBeInTheDocument();
  expect(screen.getByText("Outcome unknown — review required")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Inspect gate receipt" })).toHaveAttribute("href",
    `/api/v1/integrations/gate/commands?intent_id=${run.action_states[0].operation_id}`);
  expect(view.container.querySelector(".badge.green")).toBeNull();
  expect(screen.queryByRole("button", { name: /retry|run again|reset/i })).not.toBeInTheDocument();
  expect(view.container.textContent).not.toContain("dispatch");
});

it.each(["claimed", "running", "queued", "processing"])("never renders historical %s as completed", (status) => {
  const view = render(<AutomationRunDetails run={{ ...run, status, recovery_version: null, requires_review: true,
    review_reason: "historical_unfinished", action_states: [] }} />);
  expect(screen.getByText("Review required")).toBeInTheDocument();
  expect(screen.getByText(/Checkpoint details are unavailable/)).toBeInTheDocument();
  expect(view.container.querySelector(".badge.green")).toBeNull();
});

it("shows malformed historical action values as unavailable without inventing a receipt link", () => {
  const view = render(<AutomationRunDetails run={{ ...run, status: "success", requires_review: false, review_reason: null,
    action_states: [{ index: null, id: null, state: ["succeeded"], operation_id: "javascript:synthetic" }] }} />);
  expect(screen.getByText("State unavailable — review required")).toBeInTheDocument();
  expect(screen.getByText("Operation identity unavailable.")).toBeInTheDocument();
  expect(view.container.querySelector(".badge.green")).toBeNull();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

it("distinguishes queued messaging handoff from confirmed delivery", () => {
  const notificationId = "90000000-0000-4000-8000-000000000001";
  const view = render(<AutomationRunDetails run={{ ...run, status: "success", requires_review: false, review_reason: null,
    error: null, action_states: [{ ...run.action_states[0], id: "message", state: "succeeded" }],
    action_results: [{ id: "message", type: "integration.whatsapp.send", status: "queued", delivered_count: 0, notification_run_id: notificationId }] }} />);
  expect(screen.getByText("Delivery queued")).toBeInTheDocument();
  expect(screen.getByText(/Queued for notification delivery — delivery is not confirmed/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Inspect notification delivery" })).toHaveAttribute("href", `/api/v1/notifications/runs/${notificationId}`);
  expect(view.container.querySelector(".badge.green")).toBeNull();
  expect(screen.queryByRole("link", { name: "Inspect gate receipt" })).not.toBeInTheDocument();
});

it("reads selected run details separately and can refresh a failed read without a mutation", async () => {
  vi.mocked(workflowApi.getAutomationRun).mockRejectedValueOnce(new Error("Run not found")).mockResolvedValueOnce(run);
  render(<AutomationRunHistory {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: `Inspect Automation run ${run.id}` }));
  await screen.findByText(/This does not establish whether any action was sent/);
  fireEvent.click(screen.getByRole("button", { name: "Refresh run result" }));
  await screen.findByRole("link", { name: "Inspect gate receipt" });
  expect(workflowApi.getAutomationRun).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: /retry|run again|reset/i })).not.toBeInTheDocument();
});

it("aborts pagination on refresh and ignores a late older-page result", async () => {
  const cursor = "10000000-0000-4000-8000-000000000001";
  const older = deferred<AutomationRunPage>();
  const staleId = "20000000-0000-4000-8000-000000000002";
  vi.mocked(workflowApi.getAutomationRuns).mockResolvedValueOnce({ ...page, next_cursor: cursor })
    .mockReturnValueOnce(older.promise).mockResolvedValueOnce(page);
  const view = render(<AutomationRunHistory {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Load older runs" }));
  const signal = vi.mocked(workflowApi.getAutomationRuns).mock.calls[1][1]!.signal!;
  expect(workflowApi.getAutomationRuns).toHaveBeenLastCalledWith(cursor, { signal });
  view.rerender(<AutomationRunHistory {...props} refreshToken={1} />);
  expect(signal.aborted).toBe(true);
  await act(async () => older.resolve({ items: [{ ...run, id: staleId }], next_cursor: null }));
  await waitFor(() => expect(screen.queryByText("Loading run history…")).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: `Inspect Automation run ${staleId}` })).not.toBeInTheDocument();
});

it("clears and aborts account-owned reads on account or role change", async () => {
  const old = deferred<AutomationRunPage>();
  vi.mocked(workflowApi.getAutomationRuns).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ items: [], next_cursor: null });
  const view = render(<AutomationRunHistory {...props} />);
  const signal = vi.mocked(workflowApi.getAutomationRuns).mock.calls[0][1]!.signal!;
  view.rerender(<AutomationRunHistory {...props} currentUser={{ ...admin, id: "second-admin" }} />);
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve(page));
  expect(screen.queryByRole("button", { name: `Inspect Automation run ${run.id}` })).not.toBeInTheDocument();
  view.rerender(<AutomationRunHistory {...props} currentUser={{ ...admin, role: "standard" }} />);
  expect(screen.queryByRole("region", { name: "Automation run history" })).not.toBeInTheDocument();
  expect(workflowApi.getAutomationRuns).toHaveBeenCalledTimes(2);
});

it("aborts selected detail reads on unmount and rejects their late response", async () => {
  const detail = deferred<AutomationRun>();
  vi.mocked(workflowApi.getAutomationRun).mockReturnValueOnce(detail.promise);
  const view = render(<AutomationRunHistory {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: `Inspect Automation run ${run.id}` }));
  const signal = vi.mocked(workflowApi.getAutomationRun).mock.calls[0][1]!.signal!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => detail.resolve(run));
  expect(screen.queryByRole("link", { name: "Inspect gate receipt" })).not.toBeInTheDocument();
});
