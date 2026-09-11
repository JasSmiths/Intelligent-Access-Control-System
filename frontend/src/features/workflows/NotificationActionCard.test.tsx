import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, expect, it } from "vitest";
import type { NotificationAction, NotificationIntegration } from "../../api/workflows";
import { NotificationActionCard } from "./WorkflowFeature";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(cleanup);
const action: NotificationAction = {
  id: "existing-action", type: "mobile", target_mode: "selected", target_ids: ["home_assistant_mobile:one"],
  title_template: "Original title", message_template: "Original message", gate_malfunction_stages: [],
  media: { attach_camera_snapshot: false, camera_id: "gate" }, actionable: { enabled: false, action: "" },
};
const integration: NotificationIntegration = {
  id: "mobile", name: "Mobile", provider: "Mobile", configured: true,
  endpoints: ["one", "two"].map((id) => ({ id: `home_assistant_mobile:${id}`, label: id, provider: "Home Assistant", detail: "" })),
};
function Editor({ initial = action }: { initial?: NotificationAction }) {
  const [value, setValue] = React.useState(initial);
  return <><NotificationActionCard action={value} integration={integration} cameras={[]} actionableOptions={[]} isGateMalfunctionWorkflow stageOptions={[]} variables={[]} onChange={setValue} onRemove={() => {}} /><output data-testid="draft">{JSON.stringify(value)}</output></>;
}
function addTwo() {
  fireEvent.click(screen.getByRole("button", { name: "Add Recipient" }));
  fireEvent.click(screen.getByRole("button", { name: "two" }));
  fireEvent.click(screen.getByRole("button", { name: "Add recipient" }));
}
it("adds and removes recipients on the same action while retaining its content and options", () => {
  render(<Editor />);
  expect(screen.getByRole("button", { name: "Remove one" })).toBeDisabled();
  addTwo();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove one" }));
  expect(JSON.parse(screen.getByTestId("draft").textContent!)).toEqual({ ...action, target_ids: ["home_assistant_mobile:two"] });
  expect(screen.getByRole("button", { name: "Remove two" })).toBeDisabled();
});
it("converts all-recipient delivery to an explicit selection when a recipient is removed", () => {
  render(<Editor initial={{ ...action, target_mode: "all", target_ids: [] }} />);
  fireEvent.click(screen.getByRole("button", { name: "Remove one" }));
  expect(JSON.parse(screen.getByTestId("draft").textContent!)).toEqual({ ...action, target_ids: ["home_assistant_mobile:two"] });
});
it("preserves unavailable recipients until explicitly removed", () => {
  render(<Editor initial={{ ...action, target_ids: ["home_assistant_mobile:missing"] }} />);
  addTwo();
  expect(JSON.parse(screen.getByTestId("draft").textContent!).target_ids).toEqual(["home_assistant_mobile:missing", "home_assistant_mobile:two"]);
  fireEvent.click(screen.getByRole("button", { name: /Remove.*missing/i }));
  expect(JSON.parse(screen.getByTestId("draft").textContent!).target_ids).toEqual(["home_assistant_mobile:two"]);
});
it("searches available recipients and discards selections on cancel", () => {
  render(<Editor />);
  fireEvent.click(screen.getByRole("button", { name: "Add Recipient" }));
  expect(screen.queryByRole("button", { name: "one" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Search recipients" }), { target: { value: "absent" } });
  expect(screen.getByText("No matching recipients")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Search recipients" }), { target: { value: "two" } });
  fireEvent.click(screen.getByRole("button", { name: "two" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(JSON.parse(screen.getByTestId("draft").textContent!)).toEqual(action);
});
it("expands a provider wildcard before removing an individual recipient", () => {
  render(<Editor initial={{ ...action, target_ids: ["home_assistant_mobile:*"] }} />);
  fireEvent.click(screen.getByRole("button", { name: "Remove one" }));
  expect(JSON.parse(screen.getByTestId("draft").textContent!).target_ids).toEqual(["home_assistant_mobile:two"]);
});
