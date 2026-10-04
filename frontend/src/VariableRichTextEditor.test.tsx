import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import type { VariableRecipientRestriction } from "./api/workflows";
import VariableRichTextEditor from "./VariableRichTextEditor";

afterEach(cleanup);
const layoutMethods = [
  {prototype: HTMLElement.prototype, name: "scrollIntoView", value: vi.fn()},
  {prototype: Range.prototype, name: "getClientRects", value: () => []},
  {prototype: Range.prototype, name: "getBoundingClientRect", value: () => new DOMRect()},
].map((method) => ({...method, descriptor: Object.getOwnPropertyDescriptor(method.prototype, method.name)}));
beforeAll(() => {
  for (const {prototype, name, value} of layoutMethods) Object.defineProperty(prototype, name, {value, configurable: true});
});
afterAll(() => {
  for (const {prototype, name, descriptor} of layoutMethods) {
    if (descriptor) Object.defineProperty(prototype, name, descriptor);
    else Reflect.deleteProperty(prototype, name);
  }
});
const recipients = [
  {id: "home_assistant_mobile:jason", label: "Jason", provider: "Home Assistant", detail: ""},
  {id: "home_assistant_mobile:steph", label: "Steph", provider: "Home Assistant", detail: ""},
];
const variables = [{name: "VehicleTimeAway", token: "@VehicleTimeAway", label: "Time away", group: "Vehicle"}, {name: "FirstName", token: "@FirstName", label: "First name", group: "Person"}];

function Editor({initialValue = "@VehicleTimeAway then @VehicleTimeAway."}: {initialValue?: string}) {
  const [value, setValue] = React.useState(initialValue);
  const [restrictions, setRestrictions] = React.useState<VariableRecipientRestriction[]>([]);
  return <><VariableRichTextEditor label="Message" multiline value={value} variables={variables} recipients={recipients} variableRecipients={restrictions}
    onChange={(next, rules) => {setValue(next); setRestrictions(rules ?? []);}} /><pre data-testid="rules">{JSON.stringify(restrictions)}</pre><pre data-testid="value">{value}</pre></>;
}

it.each(["keyboard", "mouse"])("inserts a variable before punctuation without adding a space via %s", (method) => {
  render(<Editor initialValue="arrived @Veh, I've let her in." />);
  const editor = screen.getByRole("textbox", {name: "Message"});
  editor.focus();
  const range = document.createRange();
  range.setStart(editor.firstChild!, "arrived @Veh".length);
  range.collapse(true);
  window.getSelection()!.removeAllRanges();
  window.getSelection()!.addRange(range);
  fireEvent.input(editor);

  if (method === "keyboard") fireEvent.keyDown(editor, {key: "Enter"});
  else fireEvent.click(screen.getByRole("option", {name: /@VehicleTimeAway/}));

  expect(screen.getByTestId("value").textContent).toBe("arrived @VehicleTimeAway, I've let her in.");
  expect(window.getSelection()!.focusNode).toBe(editor);
  expect(window.getSelection()!.focusOffset).toBe(2);
});

it.each([" ", ""])("inserts a variable without adding to the existing suffix %j", (suffix) => {
  render(<Editor initialValue={`arrived @Veh${suffix}`} />);
  const editor = screen.getByRole("textbox", {name: "Message"});
  editor.focus();
  const range = document.createRange();
  range.setStart(editor.firstChild!, "arrived @Veh".length);
  range.collapse(true);
  window.getSelection()!.removeAllRanges();
  window.getSelection()!.addRange(range);
  fireEvent.input(editor);
  fireEvent.keyDown(editor, {key: "Tab"});

  expect(screen.getByTestId("value").textContent).toBe(`arrived @VehicleTimeAway${suffix}`);
});

it("restricts just the hovered occurrence and can restore Everyone", () => {
  render(<Editor />);
  fireEvent.mouseOver(screen.getAllByRole("button", {name: /Recipients for @VehicleTimeAway/})[1]);
  fireEvent.click(screen.getByRole("checkbox", {name: "Jason"}));
  expect(JSON.parse(screen.getByTestId("rules").textContent!)).toEqual([{occurrence: 1, name: "VehicleTimeAway", target_ids: [recipients[0].id]}]);
  expect(screen.getByRole("checkbox", {name: "Everyone"})).not.toBeChecked();
  expect(screen.getAllByRole("button", {name: /Recipients for @VehicleTimeAway/})[0]).toHaveAccessibleName("Recipients for @VehicleTimeAway: Everyone");
  fireEvent.click(screen.getByRole("checkbox", {name: "Everyone"}));
  expect(screen.getByTestId("rules")).toHaveTextContent("[]");
});

it("preserves the audience when another variable is inserted before the chip", () => {
  render(<Editor />);
  fireEvent.click(screen.getAllByRole("button", {name: /Recipients for @VehicleTimeAway/})[1]);
  fireEvent.click(screen.getByRole("checkbox", {name: "Jason"}));
  fireEvent.click(screen.getByRole("button", {name: "Done"}));
  const editor = screen.getByRole("textbox", {name: "Message"});
  editor.insertBefore(document.createTextNode("🚙 @FirstName "), editor.firstChild);
  fireEvent.input(editor);
  expect(JSON.parse(screen.getByTestId("rules").textContent!)).toEqual([{occurrence: 2, name: "VehicleTimeAway", target_ids: [recipients[0].id]}]);
});

it("offers the same controls from keyboard activation", () => {
  render(<Editor />);
  const pill = screen.getAllByRole("button", {name: /Recipients for @VehicleTimeAway/})[1];
  pill.focus();
  fireEvent.keyDown(pill, {key: "Enter"});
  expect(screen.getByRole("dialog", {name: "Recipients for @VehicleTimeAway"})).toBeInTheDocument();
  expect(screen.getByRole("checkbox", {name: "Everyone"})).toHaveFocus();
  fireEvent.click(screen.getByRole("checkbox", {name: "Jason"}));
  fireEvent.keyDown(screen.getByRole("checkbox", {name: "Everyone"}), {key: "Escape"});
  expect(screen.getAllByRole("button", {name: /Recipients for @VehicleTimeAway/})[1]).toHaveFocus();
});
