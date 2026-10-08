import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { TopChartsView, type LeaderboardResponse } from "./TopChartsView";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("renders the public latest_snapshot image and full-size preview for an unknown plate", async () => {
  const snapshotUrl = "/api/v1/events/00000000-0000-0000-0000-00000000012d/snapshot";
  const response: LeaderboardResponse = {
    known: [], top_known: null, generated_at: "2026-10-08T12:00:00+00:00",
    unknown: [{
      rank: 1, registration_number: "PUBLIC", read_count: 1,
      first_seen_at: "2026-10-08T12:00:00+00:00", last_seen_at: "2026-10-08T12:00:00+00:00",
      latest_snapshot: {
        event_id: "00000000-0000-0000-0000-00000000012d", url: snapshotUrl,
        captured_at: "2026-10-08T12:00:00+00:00", bytes: null,
        width: 320, height: 180, camera: "camera.synthetic",
      },
      vehicle_information: { status: "skipped", information: null, label: "" },
    }],
  };
  const get = vi.spyOn(api, "get").mockResolvedValue(response);

  render(<TopChartsView query="" />);

  const thumbnail = await screen.findByRole("button", { name: "Latest snapshot for PUBLIC" });
  expect(thumbnail.querySelector("img")?.getAttribute("src")).toBe(`${snapshotUrl}?variant=thumb`);
  expect(screen.queryByLabelText("No stored snapshot for PUBLIC")).toBeNull();
  fireEvent.focus(thumbnail);
  const preview = screen.getByRole("tooltip");
  expect(preview.querySelector("img")?.getAttribute("src")).toBe(snapshotUrl);
  expect(within(preview).getByText("PUBLIC")).toBeTruthy();
  expect(get).toHaveBeenCalledOnce();
  expect(get.mock.calls[0][0]).toBe("/api/v1/leaderboard");
});

it("keeps unavailable charts distinct from an empty result and recovers on retry", async () => {
  const fetcher = vi.fn()
    .mockRejectedValueOnce(new Error("Connection interrupted"))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      known: [], unknown: [], top_known: null, generated_at: "2026-10-04T12:00:00Z",
    })));
  vi.stubGlobal("fetch", fetcher);

  render(<TopChartsView query="" />);
  expect(screen.getByRole("status", { name: "Loading Top Charts" })).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection interrupted");
  expect(screen.queryByText("No known vehicle entries recorded")).not.toBeInTheDocument();
  expect(screen.queryByText("No unknown denied plates recorded")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("No known vehicle entries recorded")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
});
