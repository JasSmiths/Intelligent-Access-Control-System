import React from "react";
import { createPortal } from "react-dom";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useModalClose } from "./useModalClose";
import { useModalFocus } from "./useModalFocus";
import { useEditorDismiss } from "./useEditorDismiss";

type Close = (value?: string) => void | Promise<void>;

beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
});
afterEach(() => {
  cleanup();
  document.querySelectorAll("[data-modal-close-opener]").forEach((element) => element.remove());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function Modal({ label = "Editor", onClose, expose, children }: {
  label?: string;
  onClose: Close;
  expose?: (close: Close) => void;
  children?: React.ReactNode;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const close = useModalClose(ref, onClose);
  useModalFocus(ref, true, close);
  React.useLayoutEffect(() => { expose?.(close); }, [close, expose]);
  return createPortal(
    <div className="modal-backdrop" data-testid={`${label} backdrop`}>
      <div ref={ref} role="dialog" aria-label={label}>
        <div><input aria-label={`${label} draft`} defaultValue="Unsaved draft" />{children}</div>
        <div><button onClick={() => { void close(); }} type="button">Cancel {label}</button></div>
      </div>
    </div>, document.body
  );
}

function deferred() {
  let resolve!: () => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<void>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

function mockMotion(label = "Editor") {
  const modal = screen.getByRole("dialog", { name: label });
  const backdrop = screen.getByTestId(`${label} backdrop`);
  const card = deferred();
  const background = deferred();
  modal.style.animationName = "console-modal-in";
  modal.style.opacity = "0.65";
  modal.style.transform = "translateY(8px)";
  backdrop.style.opacity = "0.8";
  Object.defineProperty(modal, "getAnimations", { configurable: true, value: vi.fn(() => [{ finished: card.promise }]) });
  Object.defineProperty(backdrop, "getAnimations", { configurable: true, value: vi.fn(() => [{ finished: background.promise }]) });
  return { modal, backdrop, card, background };
}

function opener() {
  const button = document.createElement("button");
  button.textContent = "Open editor";
  button.dataset.modalCloseOpener = "true";
  document.body.append(button);
  button.focus();
  return button;
}

it.each(["no CSS", "reduced motion", "no animation API", "no exit animations"])("closes immediately with %s", (condition) => {
  const onClose = vi.fn();
  let close!: Close;
  render(<Modal onClose={onClose} expose={(callback) => { close = callback; }} />);
  const { modal, backdrop } = mockMotion();
  if (condition === "no CSS") modal.style.animationName = "none";
  if (condition === "reduced motion") vi.mocked(window.matchMedia).mockReturnValue({ matches: true } as MediaQueryList);
  if (condition === "no animation API") Object.defineProperty(modal, "getAnimations", { value: undefined });
  if (condition === "no exit animations") {
    Object.defineProperty(modal, "getAnimations", { value: () => [] });
    Object.defineProperty(backdrop, "getAnimations", { value: () => [] });
  }

  act(() => { close("saved-record"); });
  expect(onClose).toHaveBeenCalledExactlyOnceWith("saved-record");
  expect(backdrop.dataset.closing).toBeUndefined();
  expect(Array.from(modal.children).every((element) => !(element as HTMLElement).inert)).toBe(true);
});

it("retains the live draft and focus isolation until both exit animations finish", async () => {
  const trigger = opener();
  const onClose = vi.fn();
  function Editor() {
    const [open, setOpen] = React.useState(true);
    return open ? <Modal onClose={() => { onClose(); setOpen(false); }} /> : null;
  }
  render(<Editor />);
  const { modal, backdrop, card, background } = mockMotion();
  const draft = screen.getByLabelText("Editor draft");
  fireEvent.change(draft, { target: { value: "Draft retained during exit" } });
  fireEvent.keyDown(draft, { key: "Escape" });

  expect(modal).toHaveFocus();
  expect(trigger.inert).toBe(true);
  expect(backdrop.dataset.closing).toBe("true");
  expect(Array.from(modal.children).every((element) => (element as HTMLElement).inert)).toBe(true);
  expect(modal.style.getPropertyValue("--modal-exit-opacity")).toBe("0.65");
  expect(modal.style.getPropertyValue("--modal-exit-transform")).toBe("translateY(8px)");
  expect(backdrop.style.getPropertyValue("--modal-backdrop-opacity")).toBe("0.8");
  fireEvent.keyDown(modal, { key: "Tab" });
  expect(modal).toHaveFocus();
  fireEvent.keyDown(modal, { key: "Escape" });
  fireEvent.keyDown(modal, { key: "Escape" });

  await act(async () => { card.resolve(); });
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Editor draft")).toBe(draft);
  expect(draft).toHaveValue("Draft retained during exit");
  expect(modal).toHaveFocus();
  expect(trigger.inert).toBe(true);

  await act(async () => { background.resolve(); });
  expect(onClose).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger.inert).toBeFalsy();
  expect(trigger).toHaveFocus();
});

it("does not repeat a dirty-discard prompt for Escape or backdrop events during exit", async () => {
  const confirmDiscard = vi.spyOn(window, "confirm").mockReturnValue(true);
  const onClose = vi.fn();
  function GuardedModal() {
    const ref = React.useRef<HTMLDivElement>(null);
    const close = useModalClose(ref, onClose);
    const requestClose = useEditorDismiss(close, true, false, "draft");
    useModalFocus(ref, true, requestClose);
    const dismissBackdrop = (event: React.MouseEvent) => { if (event.target === event.currentTarget) requestClose(); };
    return createPortal(
      <div className="modal-backdrop" data-testid="Editor backdrop" onMouseDown={dismissBackdrop} onClick={dismissBackdrop}>
        <div ref={ref} role="dialog" aria-label="Editor"><input aria-label="Editor draft" /></div>
      </div>, document.body
    );
  }
  render(<GuardedModal />);
  const { modal, backdrop, card, background } = mockMotion();
  fireEvent.keyDown(modal, { key: "Escape" });
  expect(confirmDiscard).toHaveBeenCalledExactlyOnceWith("Discard unsaved draft?");
  fireEvent.keyDown(modal, { key: "Escape" });
  fireEvent.mouseDown(backdrop);
  fireEvent.click(backdrop);
  fireEvent.keyDown(modal, { key: "Escape" });
  expect(confirmDiscard).toHaveBeenCalledOnce();
  expect(onClose).not.toHaveBeenCalled();
  await act(async () => { card.resolve(); background.resolve(); });
  expect(onClose).toHaveBeenCalledOnce();
});

it("completes dismissal after an animation is cancelled and restores prior inert state", async () => {
  const onClose = vi.fn();
  render(<Modal onClose={onClose} />);
  const { modal, backdrop, card, background } = mockMotion();
  const content = modal.children[0] as HTMLElement;
  const actions = modal.children[1] as HTMLElement;
  content.inert = true;
  fireEvent.keyDown(modal, { key: "Escape" });

  await act(async () => { card.reject(new DOMException("Animation cancelled", "AbortError")); });
  expect(onClose).not.toHaveBeenCalled();
  await act(async () => { background.resolve(); });
  expect(onClose).toHaveBeenCalledOnce();
  expect(content.inert).toBe(true);
  expect(actions.inert).toBeFalsy();
  expect(backdrop.dataset.closing).toBeUndefined();
  expect(modal.style.getPropertyValue("--modal-exit-opacity")).toBe("");
  expect(modal.style.getPropertyValue("--modal-exit-transform")).toBe("");
  expect(backdrop.style.getPropertyValue("--modal-backdrop-opacity")).toBe("");
});

it("releases exit state on unmount without calling a stale completion callback", async () => {
  const onClose = vi.fn();
  const view = render(<Modal onClose={onClose} />);
  const { modal, backdrop, card, background } = mockMotion();
  fireEvent.keyDown(modal, { key: "Escape" });
  view.unmount();
  expect(backdrop.dataset.closing).toBeUndefined();
  expect(Array.from(modal.children).every((element) => !(element as HTMLElement).inert)).toBe(true);
  await act(async () => { card.resolve(); background.resolve(); });
  expect(onClose).not.toHaveBeenCalled();
});

it("preserves callback arguments and awaits the latest async save completion", async () => {
  const original = vi.fn();
  const save = deferred();
  const updated = vi.fn(() => save.promise);
  let close!: Close;
  const expose = (callback: Close) => { close = callback; };
  const view = render(<Modal onClose={original} expose={expose} />);
  const { card, background } = mockMotion();
  let completion!: void | Promise<void>;
  act(() => { completion = close("saved-record"); });
  view.rerender(<Modal onClose={updated} expose={expose} />);
  let settled = false;
  const result = Promise.resolve(completion).then(() => { settled = true; });
  await act(async () => { card.resolve(); background.resolve(); });
  expect(original).not.toHaveBeenCalled();
  expect(updated).toHaveBeenCalledExactlyOnceWith("saved-record");
  expect(settled).toBe(false);
  await act(async () => { save.resolve(); await result; });
  expect(settled).toBe(true);
});

it("propagates an async completion failure to the caller", async () => {
  const failure = new Error("Refresh failed");
  const onClose = vi.fn(async () => { throw failure; });
  let close!: Close;
  render(<Modal onClose={onClose} expose={(callback) => { close = callback; }} />);
  const { card, background } = mockMotion();
  let completion!: void | Promise<void>;
  act(() => { completion = close("saved-record"); });
  const assertion = expect(completion).rejects.toBe(failure);
  await act(async () => { card.resolve(); background.resolve(); await assertion; });
  expect(onClose).toHaveBeenCalledExactlyOnceWith("saved-record");
});

it("restores a parent dialog's focus only after the nested dialog finishes exiting", async () => {
  const parentClose = vi.fn();
  const childClose = vi.fn();
  function Nested() {
    const [childOpen, setChildOpen] = React.useState(false);
    return <>
      <Modal label="Parent" onClose={parentClose}>
        <button onClick={() => setChildOpen(true)} type="button">Open child</button>
      </Modal>
      {childOpen ? <Modal label="Child" onClose={() => { childClose(); setChildOpen(false); }} /> : null}
    </>;
  }
  render(<Nested />);
  const openChild = screen.getByRole("button", { name: "Open child" });
  openChild.focus();
  fireEvent.click(openChild);
  const { modal, card, background } = mockMotion("Child");
  const parentBackdrop = screen.getByTestId("Parent backdrop");
  fireEvent.keyDown(screen.getByLabelText("Child draft"), { key: "Escape" });
  expect(modal).toHaveFocus();
  expect(parentBackdrop.inert).toBe(true);
  await act(async () => { background.resolve(); });
  expect(parentClose).not.toHaveBeenCalled();
  expect(childClose).not.toHaveBeenCalled();
  expect(modal).toHaveFocus();
  expect(parentBackdrop.inert).toBe(true);
  await act(async () => { card.resolve(); });
  expect(childClose).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog", { name: "Child" })).not.toBeInTheDocument();
  expect(parentBackdrop.inert).toBeFalsy();
  expect(openChild).toHaveFocus();
  fireEvent.keyDown(openChild, { key: "Escape" });
  expect(parentClose).toHaveBeenCalledOnce();
});
