import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { workflowApi, type AutomationCatalogResponse, type AutomationRule, type NotificationCatalogResponse, type NotificationRule } from "../../api/workflows";
import type { UserAccount } from "../../api/types";
import { AutomationsView } from "./AutomationsView";
import { NotificationsView } from "./NotificationsView";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const automation: AutomationRule = { id: "auto-one", name: "Test automation", description: "", is_active: false, triggers: [{ id: "t", type: "time.every_x", config: { interval: 5, unit: "minutes" } }], trigger_keys: ["time.every_x"], conditions: [], actions: [{ id: "a", type: "notification.run", config: { notification_rule_id: "notify-one" } }], run_count: 0 };
const automationCatalog: AutomationCatalogResponse = { triggers: [], actions: [], conditions: [], variables: [], notification_rules: [], garage_doors: [], mock_context: {} };
const notification: NotificationRule = { id: "notify-one", name: "Test notification", is_active: false, trigger_event: "gate_malfunction", conditions: [], actions: [{ id: "a", type: "in_app", target_mode: "all", target_ids: [], title_template: "Title", message_template: "Body", gate_malfunction_stages: [], actionable: { enabled: false, action: "" }, media: { attach_camera_snapshot: false, camera_id: "" } }] };
const notificationCatalog: NotificationCatalogResponse = { triggers: [], variables: [], integrations: [], mock_context: {} };
const currentUser = { id: "test-user", role: "admin" } as UserAccount;

it("retains notification edits during refresh and saves through the canonical API owner", async () => {
  vi.spyOn(workflowApi, "getNotificationData").mockResolvedValue({ rules: [notification], catalog: notificationCatalog });
  const cameras = vi.spyOn(workflowApi, "getNotificationCameras").mockResolvedValue([]);
  const save = vi.spyOn(workflowApi, "saveNotificationRule").mockResolvedValue(notification);
  const { rerender } = render(<NotificationsView currentUser={currentUser} people={[]} schedules={[]} refreshToken={0} />);
  fireEvent.click(await screen.findByRole("button", { name: notification.name }));
  fireEvent.change(screen.getByLabelText("Workflow name"), { target: { value: "Keep this draft" } });
  rerender(<NotificationsView currentUser={currentUser} people={[]} schedules={[]} refreshToken={1} />);
  await waitFor(() => expect(workflowApi.getNotificationData).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("Workflow name")).toHaveValue("Keep this draft");
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(screen.queryByLabelText("Workflow name")).not.toBeInTheDocument());
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ id: notification.id }), expect.objectContaining({ name: "Keep this draft", is_active: false, trigger_event: "gate_malfunction", actions: notification.actions }));
  expect(cameras).toHaveBeenCalled();
});
it("duplicates notification rules as paused drafts for review", async () => {
  vi.spyOn(workflowApi, "getNotificationData").mockResolvedValue({ rules: [notification], catalog: notificationCatalog });
  vi.spyOn(workflowApi, "getNotificationCameras").mockResolvedValue([]);
  const duplicate = vi.spyOn(workflowApi, "duplicateNotificationRule").mockResolvedValue({ ...notification, id: "copy", name: "Test notification Copy" });
  await act(async () => { render(<NotificationsView currentUser={currentUser} people={[]} schedules={[]} refreshToken={0} />); });
  fireEvent.click(await screen.findByRole("button", { name: `Options for ${notification.name}` }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }));
  await waitFor(() => expect(duplicate).toHaveBeenCalledWith(expect.objectContaining({ name: "Test notification Copy", is_active: false })));
  await screen.findByText("Notification workflow duplicated and paused for review.");
});
it("saves automation edits and reports a subsequent read failure independently", async () => {
  const read = vi.spyOn(workflowApi, "getAutomationData").mockResolvedValueOnce({ rules: [automation], catalog: automationCatalog, users: [] }).mockRejectedValueOnce(new Error("List offline"));
  const save = vi.spyOn(workflowApi, "saveAutomationRule").mockResolvedValue(automation);
  render(<AutomationsView currentUser={currentUser} people={[]} vehicles={[]} refreshToken={0} />);
  fireEvent.click(await screen.findByRole("button", { name: automation.name }));
  fireEvent.change(screen.getByLabelText("Automation name"), { target: { value: "Updated automation" } });
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await screen.findByText("List offline");
  expect(read).toHaveBeenCalledTimes(2);
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ id: automation.id }), expect.objectContaining({ name: "Updated automation", triggers: automation.triggers, actions: automation.actions }));
  expect(screen.getByText("Automation saved. It will run when its trigger fires.")).toBeInTheDocument();
});
