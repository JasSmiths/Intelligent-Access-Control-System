import { useCallback, useEffect, useRef, type RefObject } from "react";

/** Animate an accepted close. Draft/pending guards must run before calling this. */
export function useModalClose<Args extends unknown[]>(
  ref: RefObject<HTMLElement | null>,
  onClose: (...args: Args) => void | Promise<void>
): (...args: Args) => void | Promise<void> {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const cleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanupRef.current?.(), []);

  return useCallback((...args: Args) => {
    const modal = ref.current;
    if (cleanupRef.current || modal?.closest('[data-closing="true"]')) return;
    const backdrop = modal?.closest<HTMLElement>(".modal-backdrop, .search-palette-backdrop, dialog");
    if (!modal || !modal.matches('[role="dialog"], dialog') || !backdrop || !modal.getAnimations ||
        window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ||
        getComputedStyle(modal).animationName === "none" || !getComputedStyle(modal).animationName) {
      return closeRef.current(...args);
    }

    // Preserve the current pose when dismissal interrupts the entrance.
    const style = getComputedStyle(modal);
    modal.style.setProperty("--modal-exit-opacity", style.opacity);
    modal.style.setProperty("--modal-exit-transform", style.transform);
    backdrop.style.setProperty("--modal-backdrop-opacity", getComputedStyle(backdrop).opacity);
    modal.focus({ preventScroll: true });
    const children = Array.from(modal.children).filter((child): child is HTMLElement => child instanceof HTMLElement);
    const previousInert = children.map((child) => child.inert);
    children.forEach((child) => { child.inert = true; });
    backdrop.dataset.closing = "true";
    const blockBackdrop = (event: Event) => { event.preventDefault(); event.stopImmediatePropagation(); };
    backdrop.addEventListener("mousedown", blockBackdrop, true);
    backdrop.addEventListener("click", blockBackdrop, true);

    let active = true;
    const cleanup = () => {
      active = false;
      children.forEach((child, index) => { child.inert = previousInert[index]; });
      delete backdrop.dataset.closing;
      backdrop.removeEventListener("mousedown", blockBackdrop, true);
      backdrop.removeEventListener("click", blockBackdrop, true);
      modal.style.removeProperty("--modal-exit-opacity");
      modal.style.removeProperty("--modal-exit-transform");
      backdrop.style.removeProperty("--modal-backdrop-opacity");
      cleanupRef.current = null;
    };
    cleanupRef.current = cleanup;
    const animations = [...new Set([...modal.getAnimations(), ...backdrop.getAnimations()])];
    if (!animations.length) {
      cleanup();
      return closeRef.current(...args);
    }
    // Cancellation (including a motion preference change) also completes exit.
    return Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
      if (!active) return;
      const stillMounted = modal.isConnected && ref.current === modal;
      cleanup();
      if (stillMounted) return closeRef.current(...args);
    });
  }, [ref]);
}
