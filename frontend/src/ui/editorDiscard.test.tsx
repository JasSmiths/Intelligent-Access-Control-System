import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { EditorDialog } from "./EditorDialog";
import { type Confirm, useConfirm } from "./useConfirm";

afterEach(() => { cleanup(); });

/** Browsers ignore clicks inside an inert subtree; jsdom does not, so fail the click the way a browser would. */
function browserClick(element: HTMLElement) {
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    expect(node.inert || node.hasAttribute("inert"), `${element.textContent} is inside an inert subtree`).toBeFalsy();
  }
  fireEvent.click(element);
}

function Page({ expose }: { expose: (confirm: Confirm) => void }) {
  const askConfirm = useConfirm();
  React.useLayoutEffect(() => { expose(askConfirm); }, [askConfirm, expose]);
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("Saved");
  return <>
    <button type="button" onClick={() => { setName("Saved"); setOpen(true); }}>Edit</button>
    {open ? <EditorDialog title="Edit workflow" description="Synthetic editor" dirty={name !== "Saved"} busy={false} onClose={() => setOpen(false)}>
      {() => <label>Workflow name<input value={name} onChange={(event) => setName(event.target.value)} /></label>}
    </EditorDialog> : null}
  </>;
}

async function openDirtyEditor() {
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const editor = screen.getByRole("dialog", { name: "Edit workflow" });
  fireEvent.change(within(editor).getByLabelText("Workflow name"), { target: { value: "Changed" } });
  return editor;
}

async function discardFromClose(editor: HTMLElement) {
  browserClick(within(editor).getByRole("button", { name: "Close" }));
  const prompt = await screen.findByRole("alertdialog", { name: "Discard unsaved integration changes?" });
  browserClick(within(prompt).getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Edit workflow" })).not.toBeInTheDocument());
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
}

it("closes a dirty editor from Discard on every prompt in the same page session", async () => {
  render(<Page expose={() => undefined} />);
  // The first prompt creates the shared confirm host after the editor opened.
  await discardFromClose(await openDirtyEditor());
  expect(document.querySelector("[data-confirm-host]")).toBeNull();
  // Regression: the second editor used to inert the leftover host, so Discard ignored clicks.
  const editor = await openDirtyEditor();
  browserClick(within(editor).getByRole("button", { name: "Close" }));
  let prompt = await screen.findByRole("alertdialog");
  browserClick(within(prompt).getByRole("button", { name: "Keep editing" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(within(editor).getByLabelText("Workflow name")).toHaveValue("Changed");
  fireEvent.keyDown(document, { key: "Escape" });
  prompt = await screen.findByRole("alertdialog");
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(editor).toBeInTheDocument();
  await discardFromClose(editor);
});

it("closes an editor from Discard after an unrelated confirmation earlier on the page", async () => {
  let askConfirm!: Confirm;
  render(<Page expose={(next) => { askConfirm = next; }} />);
  let answer!: Promise<boolean>;
  act(() => { answer = askConfirm({ title: "Delete vehicle?", confirmLabel: "Delete", tone: "danger" }); });
  browserClick(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  await expect(answer).resolves.toBe(false);
  await discardFromClose(await openDirtyEditor());
});
