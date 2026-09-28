import { useLayoutEffect, useRef, type RefObject } from "react";

const modalStack: HTMLElement[] = [];
const inertOwners = new Map<HTMLElement, { count: number; previous: boolean }>();
const focusableSelector = 'button, [href], input, select, textarea, [tabindex], [contenteditable="true"]';

function isVisible(element: HTMLElement) {
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (node.hidden || node.inert || style.display === "none" || style.visibility === "hidden") return false;
  }
  return true;
}

function hasNativeModalOutside(modal: HTMLElement) {
  // Native dialog owns focus and Escape while it is in the top layer.
  try {
    const nativeModal = document.querySelector("dialog:modal");
    return nativeModal !== null && !modal.contains(nativeModal);
  } catch {
    return false;
  }
}

/** Keep an existing modal's focus and background behavior without remounting its content. */
export function useModalFocus<T extends HTMLElement>(
  ref: RefObject<T | null>,
  open: boolean,
  onClose: () => void,
) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useLayoutEffect(() => {
    const modal = ref.current;
    if (!open || !modal) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousTabIndex = modal.getAttribute("tabindex");
    modal.tabIndex = -1;
    modalStack.push(modal);

    // Safari's keyboard can reduce the visual viewport without changing dvh.
    const backdrop = modal.closest<HTMLElement>(".modal-backdrop, .search-palette-backdrop");
    const viewport = window.visualViewport;
    const updateViewport = () => {
      if (!backdrop || !viewport || viewport.scale !== 1) return;
      backdrop.style.setProperty("--overlay-viewport-height", `${viewport.height}px`);
      backdrop.style.setProperty("--overlay-viewport-top", `${viewport.offsetTop}px`);
    };
    updateViewport();
    viewport?.addEventListener("resize", updateViewport);
    viewport?.addEventListener("scroll", updateViewport);

    // Isolate siblings at each ancestor, including siblings of a portal's root.
    const isolated: HTMLElement[] = [];
    let branch: HTMLElement = modal;
    while (branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === branch || sibling.matches("script, style, link")) continue;
        const owner = inertOwners.get(sibling) ?? { count: 0, previous: sibling.inert };
        owner.count += 1;
        inertOwners.set(sibling, owner);
        sibling.inert = true;
        isolated.push(sibling);
      }
      if (branch.parentElement === document.body) break;
      branch = branch.parentElement;
    }
    const controls = () => Array.from(modal.querySelectorAll<HTMLElement>(focusableSelector)).filter((element) =>
      (element.tabIndex >= 0 || element.contentEditable === "true") && !element.matches(":disabled") &&
      !element.closest('[aria-hidden="true"]') && isVisible(element),
    );
    (controls()[0] ?? modal).focus({ preventScroll: true });
    const onKeyDown = (event: KeyboardEvent) => {
      if (modalStack.at(-1) !== modal || event.defaultPrevented || hasNativeModalOutside(modal)) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
      } else if (event.key === "Tab") {
        const elements = controls();
        const first = elements[0] ?? modal;
        const last = elements.at(-1) ?? modal;
        if (!modal.contains(document.activeElement) || !elements.length) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && (document.activeElement === first || document.activeElement === modal)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      viewport?.removeEventListener("resize", updateViewport);
      viewport?.removeEventListener("scroll", updateViewport);
      backdrop?.style.removeProperty("--overlay-viewport-height");
      backdrop?.style.removeProperty("--overlay-viewport-top");
      document.removeEventListener("keydown", onKeyDown);
      modalStack.splice(modalStack.indexOf(modal), 1);
      for (const sibling of isolated) {
        const owner = inertOwners.get(sibling)!;
        owner.count -= 1;
        if (!owner.count) {
          sibling.inert = owner.previous;
          inertOwners.delete(sibling);
        }
      }
      if (previousTabIndex === null) modal.removeAttribute("tabindex");
      else modal.setAttribute("tabindex", previousTabIndex);
      if (previousFocus?.isConnected && isVisible(previousFocus)) previousFocus.focus({ preventScroll: true });
    };
  }, [open, ref]);
}
