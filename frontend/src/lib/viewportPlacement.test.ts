import { afterEach, describe, expect, it, vi } from "vitest";
import { getUsableViewportBounds, observeOverlayPlacement, placeOverlay } from "./viewportPlacement";

const anchor = { left: 120, right: 160, top: 100, bottom: 130 };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("viewport placement", () => {
  it("constrains offscreen anchors and allows a clipped overlay to grow after resizing", () => {
    const bounds = { left: 20, top: 10, right: 220, bottom: 170 };
    const offscreen = placeOverlay({ left: 500, right: 520, top: 900, bottom: 920 }, { width: 400, height: 600 }, bounds);
    expect(offscreen).toMatchObject({ left: 20, top: 10, maxWidth: 200, maxHeight: 160 });
    const small = placeOverlay(anchor, { width: 100, height: 200 }, { ...bounds, bottom: 150 });
    const expanded = placeOverlay(anchor, { width: 100, height: 200 }, { ...bounds, bottom: 500 });
    expect(expanded.maxHeight).toBeGreaterThan(small.maxHeight);
  });
  it("bounds a large overlay on all sides and reports the usable size", () => {
    const bounds = { left: 24, right: 264, top: 36, bottom: 300 };
    expect(placeOverlay(anchor, { width: 400, height: 240 }, bounds, { alignment: "end" })).toEqual({
      left: 24, top: 138, maxWidth: 240, maxHeight: 162, side: "bottom"
    });
    expect(placeOverlay({ left: 230, right: 250, top: 270, bottom: 290 }, { width: 100, height: 80 }, bounds)).toEqual({
      left: 164, top: 182, maxWidth: 240, maxHeight: 226, side: "top"
    });
  });

  it("uses visual viewport offsets and independent edge tokens", () => {
    vi.stubGlobal("innerWidth", 800);
    vi.stubGlobal("innerHeight", 600);
    vi.stubGlobal("visualViewport", { offsetLeft: 30, offsetTop: 50, width: 280, height: 200 });
    // CSS env()/max() resolution belongs to the browser; supply its computed result here.
    vi.spyOn(window, "getComputedStyle").mockReturnValue({ paddingLeft: "20px", paddingRight: "10px", paddingTop: "30px", paddingBottom: "15px" } as CSSStyleDeclaration);
    expect(getUsableViewportBounds()).toEqual({ left: 50, right: 300, top: 80, bottom: 235 });
  });

  it("updates for visual viewport resize and scroll, then removes listeners", () => {
    const viewport = new EventTarget();
    vi.stubGlobal("visualViewport", viewport);
    const callbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => { callbacks.push(callback); return callbacks.length; });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const update = vi.fn();
    const cleanup = observeOverlayPlacement(null, null, update);
    expect(update).toHaveBeenCalledTimes(1);
    viewport.dispatchEvent(new Event("resize"));
    viewport.dispatchEvent(new Event("scroll"));
    expect(callbacks).toHaveLength(1);
    callbacks.shift()?.(0);
    expect(update).toHaveBeenCalledTimes(2);
    cleanup();
    viewport.dispatchEvent(new Event("scroll"));
    expect(callbacks).toHaveLength(0);
  });
});
