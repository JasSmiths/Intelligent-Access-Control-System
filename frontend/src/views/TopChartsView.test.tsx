import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { TopChartsView } from "./TopChartsView";

afterEach(() => vi.unstubAllGlobals());

it("keeps unavailable charts distinct from an empty result and recovers on retry", async () => {
  const fetcher = vi.fn()
    .mockRejectedValueOnce(new Error("Connection interrupted"))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      known: [], unknown: [], top_known: null, generated_at: "2026-10-04T12:00:00Z",
    })));
  vi.stubGlobal("fetch", fetcher);

  render(<TopChartsView query="" latestRealtime={null} refreshToken={0} />);
  expect(screen.getByRole("status", { name: "Loading Top Charts" })).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection interrupted");
  expect(screen.queryByText("No known vehicle entries recorded")).not.toBeInTheDocument();
  expect(screen.queryByText("No unknown denied plates recorded")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("No known vehicle entries recorded")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
});
