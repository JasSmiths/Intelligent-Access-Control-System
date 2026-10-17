import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";
import { type Confirm, type ConfirmOptions, useConfirm } from "./useConfirm";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function deferred() {
  let resolve!: () => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function Owner({ expose }: { expose: (confirm: Confirm) => void }) {
  const askConfirm = useConfirm();
  React.useLayoutEffect(() => { expose(askConfirm); }, [askConfirm, expose]);
  return <button type="button">Opener</button>;
}

function mountOwner() {
  let askConfirm!: Confirm;
  const view = render(<Owner expose={(next) => { askConfirm = next; }} />);
  const ask = (options: ConfirmOptions) => { let answer!: Promise<boolean>; act(() => { answer = askConfirm(options); }); return answer; };
  return { ...view, ask };
}

const remove: ConfirmOptions = { title: "Delete TEST123?", message: "This vehicle will be permanently removed.", confirmLabel: "Delete", tone: "danger" };

it("renders an accessible, labelled danger alert dialog and resolves true when confirmed", async () => {
  const { ask } = mountOwner();
  const answer = ask(remove);
  const dialog = screen.getByRole("alertdialog", { name: "Delete TEST123?" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveAccessibleDescription("This vehicle will be permanently removed.");
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  const accept = within(dialog).getByRole("button", { name: "Delete" });
  expect(accept).toHaveClass("danger-button");
  expect(screen.getByRole("button", { name: "Opener", hidden: true }).parentElement!.inert).toBe(true);
  fireEvent.click(accept);
  await expect(answer).resolves.toBe(true);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

it("uses the primary style by default and resolves false from Cancel", async () => {
  const { ask } = mountOwner();
  const answer = ask({ title: "Continue?" });
  const dialog = screen.getByRole("alertdialog", { name: "Continue?" });
  expect(dialog).not.toHaveAttribute("aria-describedby");
  expect(within(dialog).getByRole("button", { name: "Confirm" })).toHaveClass("primary-button");
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await expect(answer).resolves.toBe(false);
});

it("cancels on Escape, traps Tab focus and restores focus to the opener", async () => {
  const { ask } = mountOwner();
  const opener = screen.getByRole("button", { name: "Opener" }); opener.focus();
  const answer = ask(remove);
  const dialog = screen.getByRole("alertdialog");
  const [cancel, accept] = within(dialog).getAllByRole("button");
  expect(cancel).toHaveFocus();
  accept.focus(); fireEvent.keyDown(document, { key: "Tab" }); expect(cancel).toHaveFocus();
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true }); expect(accept).toHaveFocus();
  fireEvent.keyDown(document, { key: "Escape" });
  await expect(answer).resolves.toBe(false);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
});

it("keeps the dialog open and busy while its action runs, then confirms", async () => {
  const { ask } = mountOwner(); const work = deferred(); const action = vi.fn(() => work.promise);
  const answer = ask({ ...remove, busyLabel: "Deleting...", action });
  const dialog = screen.getByRole("alertdialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Deleting..." }));
  expect(action).toHaveBeenCalledOnce();
  expect(dialog).toHaveAttribute("aria-busy", "true");
  within(dialog).getAllByRole("button").forEach((button) => expect(button).toBeDisabled());
  fireEvent.keyDown(document, { key: "Escape" });
  expect(dialog).toBeInTheDocument();
  await act(async () => { work.resolve(); });
  await expect(answer).resolves.toBe(true);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

it("closes and rejects when its action fails", async () => {
  const { ask } = mountOwner(); const work = deferred();
  const answer = ask({ ...remove, action: () => work.promise });
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
  const rejected = expect(answer).rejects.toThrow("Delete rejected");
  await act(async () => { work.reject(new Error("Delete rejected")); });
  await rejected;
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

it("shows queued requests one at a time and cancels an owner's prompts when it unmounts", async () => {
  const { ask, unmount } = mountOwner();
  const first = ask({ title: "First?" }); const second = ask({ title: "Second?" });
  expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await expect(first).resolves.toBe(true);
  await waitFor(() => expect(screen.getByRole("alertdialog", { name: "Second?" })).toBeInTheDocument());
  act(() => unmount());
  await expect(second).resolves.toBe(false);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

it("disables both actions and Escape when the owner marks the dialog busy", () => {
  const onResult = vi.fn();
  render(<ConfirmDialog title="Remove gate?" confirmLabel="Remove" tone="danger" busy busyLabel="Removing..." onResult={onResult} />);
  const dialog = screen.getByRole("alertdialog", { name: "Remove gate?" });
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Removing..." })).toBeDisabled();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onResult).not.toHaveBeenCalled();
});
