import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouteErrorBoundary } from "./RouteErrorBoundary";

function BrokenView() {
  throw new Error("render failed");
  return null;
}

describe("RouteErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a route-local fallback and resets when the route changes", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = render(
      <RouteErrorBoundary view="events">
        <BrokenView />
      </RouteErrorBoundary>
    );

    expect(screen.getByText("This view could not be loaded.")).toBeInTheDocument();

    rerender(
      <RouteErrorBoundary view="alerts">
        <div>Recovered alerts view</div>
      </RouteErrorBoundary>
    );

    await waitFor(() => {
      expect(screen.getByText("Recovered alerts view")).toBeInTheDocument();
    });
  });
});
