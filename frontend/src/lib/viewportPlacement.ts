/** Coordinates are CSS pixels in the layout viewport, like getBoundingClientRect(). */
export type ViewportBounds = { left: number; top: number; right: number; bottom: number };
export type OverlaySize = { width: number; height: number };
export type OverlayAnchor = Pick<DOMRect, "left" | "right" | "top" | "bottom">;
export type OverlayPlacement = { left: number; top: number; maxWidth: number; maxHeight: number; side: "top" | "bottom" };

function pixelValue(raw: string, fallback: number): number {
  const value = parseFloat(raw);
  return Number.isFinite(value) ? Math.max(0, value) : fallback;
}

export function getUsableViewportBounds(): ViewportBounds {
  const viewport = window.visualViewport;
  // Custom properties can contain max()/env() expressions. Resolve them through
  // ordinary padding so the browser computes the actual pixel insets for us.
  const probe = document.createElement("div");
  probe.style.cssText = "position:fixed;visibility:hidden;pointer-events:none;padding:var(--edge-top, 12px) var(--edge-right, 12px) var(--edge-bottom, 12px) var(--edge-left, 12px)";
  document.documentElement.appendChild(probe);
  const style = getComputedStyle(probe);
  const leftInset = pixelValue(style.paddingLeft, 12);
  const rightInset = pixelValue(style.paddingRight, 12);
  const topInset = pixelValue(style.paddingTop, 12);
  const bottomInset = pixelValue(style.paddingBottom, 12);
  probe.remove();
  const left = viewport?.offsetLeft ?? 0;
  const top = viewport?.offsetTop ?? 0;
  const width = viewport?.width ?? window.innerWidth;
  const height = viewport?.height ?? window.innerHeight;
  return {
    left: left + leftInset,
    top: top + topInset,
    right: Math.max(left + leftInset, left + width - rightInset),
    bottom: Math.max(top + topInset, top + height - bottomInset)
  };
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

export function placeOverlay(
  anchor: OverlayAnchor,
  overlay: OverlaySize,
  bounds: ViewportBounds,
  options: { gap?: number; alignment?: "start" | "end" | "center"; preferredSide?: "top" | "bottom" } = {}
): OverlayPlacement {
  const gap = options.gap ?? 8;
  const maxWidth = Math.max(0, bounds.right - bounds.left);
  const width = Math.min(overlay.width, maxWidth);
  const below = Math.max(0, bounds.bottom - anchor.bottom - gap);
  const above = Math.max(0, anchor.top - bounds.top - gap);
  const preferred = options.preferredSide ?? "bottom";
  const side = (preferred === "bottom" ? below >= overlay.height || below >= above : above < overlay.height && below > above) ? "bottom" : "top";
  const available = side === "bottom" ? below : above;
  const maxHeight = Math.min(available, Math.max(0, bounds.bottom - bounds.top));
  const renderedHeight = Math.min(overlay.height, maxHeight);
  const aligned = options.alignment === "end" ? anchor.right - width : options.alignment === "center" ? (anchor.left + anchor.right - width) / 2 : anchor.left;
  return {
    left: clamp(aligned, bounds.left, bounds.right - width),
    top: side === "bottom" ? clamp(anchor.bottom + gap, bounds.top, bounds.bottom - renderedHeight) : clamp(anchor.top - gap - renderedHeight, bounds.top, bounds.bottom - renderedHeight),
    maxWidth,
    maxHeight,
    side
  };
}

/** Reposition an open overlay when its anchor, content, or usable viewport changes. */
export function observeOverlayPlacement(anchor: Element | null, overlay: Element | null, update: () => void): () => void {
  let frame = 0;
  const schedule = () => {
    if (!frame) frame = window.requestAnimationFrame(() => { frame = 0; update(); });
  };
  update();
  window.addEventListener("resize", schedule);
  window.addEventListener("scroll", schedule, true);
  window.visualViewport?.addEventListener("resize", schedule);
  window.visualViewport?.addEventListener("scroll", schedule);
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
  if (anchor) observer?.observe(anchor);
  if (overlay) observer?.observe(overlay);
  return () => {
    window.cancelAnimationFrame(frame);
    window.removeEventListener("resize", schedule);
    window.removeEventListener("scroll", schedule, true);
    window.visualViewport?.removeEventListener("resize", schedule);
    window.visualViewport?.removeEventListener("scroll", schedule);
    observer?.disconnect();
  };
}
