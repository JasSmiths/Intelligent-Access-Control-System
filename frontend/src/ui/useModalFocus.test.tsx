import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createPortal } from "react-dom";
import { useModalFocus } from "./useModalFocus";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function Modal({ label, close }: { label: string; close: () => void }) {
  const ref = React.useRef<HTMLDivElement>(null);
  useModalFocus(ref, true, close);
  return createPortal(<div ref={ref} role="dialog" aria-label={label}>
    <button>{label} first</button><button disabled>Disabled</button><button>{label} last</button>
  </div>, document.body);
}

it("contains keyboard focus, isolates the background and restores the opener", () => {
  const close = vi.fn();
  const background = document.createElement("button");
  background.textContent = "Opener";
  document.body.append(background);
  background.focus();
  const view = render(<Modal label="Editor" close={close} />);
  expect(screen.getByText("Editor first")).toHaveFocus();
  expect(background.inert).toBe(true);
  fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey: true });
  expect(screen.getByText("Editor last")).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Tab" });
  expect(screen.getByText("Editor first")).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
  view.unmount();
  expect(background.inert).toBeFalsy();
  expect(background).toHaveFocus();
  background.remove();
});

it("dismisses only the top modal and restores its parent's focus", () => {
  const parentClose = vi.fn();
  const childClose = vi.fn();
  const parent = render(<Modal label="Parent" close={parentClose} />);
  const child = render(<Modal label="Child" close={childClose} />);
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(childClose).toHaveBeenCalledOnce();
  expect(parentClose).not.toHaveBeenCalled();
  child.unmount();
  expect(screen.getByText("Parent first")).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(parentClose).toHaveBeenCalledOnce();
  parent.unmount();
});

it("uses an updated close callback without resetting focus on rerender", () => {
  const initialClose = vi.fn();
  const nextClose = vi.fn();
  const view = render(<Modal label="Editor" close={initialClose} />);
  screen.getByText("Editor last").focus();
  view.rerender(<Modal label="Editor" close={nextClose} />);
  expect(screen.getByText("Editor last")).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(initialClose).not.toHaveBeenCalled();
  expect(nextClose).toHaveBeenCalledOnce();
});

it("defers keyboard handling to a nested native dialog", () => {
  const close = vi.fn();
  render(<Modal label="Editor" close={close} />);
  const nativeDialog = document.createElement("dialog");
  const recipientInput = document.createElement("input");
  nativeDialog.append(recipientInput);
  document.body.append(nativeDialog);
  // jsdom does not implement the browser top layer.
  const querySelector = document.querySelector.bind(document);
  vi.spyOn(document, "querySelector").mockImplementation((selector: string) => selector === "dialog:modal" ? nativeDialog : querySelector(selector));
  recipientInput.focus();
  expect(fireEvent.keyDown(recipientInput, { key: "Tab" })).toBe(true);
  expect(fireEvent.keyDown(recipientInput, { key: "Escape" })).toBe(true);
  expect(close).not.toHaveBeenCalled();
  expect(recipientInput).toHaveFocus();
  nativeDialog.remove();
});

it("tracks the keyboard's visual viewport for a dialog and releases its listeners", () => {
  const viewport = Object.assign(new EventTarget(), { height: 700, offsetTop: 0, scale: 1 });
  vi.stubGlobal("visualViewport", viewport);
  function FormDialog() {
    const ref = React.useRef<HTMLDivElement>(null);
    useModalFocus(ref, true, () => {});
    return <div className="modal-backdrop" data-testid="backdrop"><div ref={ref}><input aria-label="Name" /></div></div>;
  }
  const view = render(<FormDialog />);
  const backdrop = screen.getByTestId("backdrop");
  expect(backdrop.style.getPropertyValue("--overlay-viewport-height")).toBe("700px");
  viewport.height = 280;
  viewport.offsetTop = 40;
  viewport.dispatchEvent(new Event("resize"));
  expect(backdrop.style.getPropertyValue("--overlay-viewport-height")).toBe("280px");
  expect(backdrop.style.getPropertyValue("--overlay-viewport-top")).toBe("40px");
  viewport.height = 700;
  viewport.offsetTop = 0;
  viewport.dispatchEvent(new Event("scroll"));
  expect(backdrop.style.getPropertyValue("--overlay-viewport-height")).toBe("700px");
  view.unmount();
  viewport.dispatchEvent(new Event("resize"));
  expect(backdrop.style.getPropertyValue("--overlay-viewport-height")).toBe("");
});
