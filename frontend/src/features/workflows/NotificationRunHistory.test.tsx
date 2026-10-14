import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { workflowApi } from "../../api/workflows";
import { NotificationRunHistory } from "./NotificationRunHistory";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("shows preserved acceptance and each uncertain or unsent destination without offering replay", async () => {
  const read = vi.spyOn(workflowApi, "getNotificationRuns").mockResolvedValue([{
    id: "synthetic", status: "review_required", trigger_event: "vehicle.known_plate", review_reason: "provider_outcome_unknown",
    actions: [{ index: 0, accepted_any: true, partial_failure: true, review_required: true, destination_outcomes: [
      { target: "media_player.accepted", delivery: "accepted" }, { target: "media_player.uncertain", delivery: "unknown" },
      { target: "media_player.later", delivery: "not_sent" },
    ] }],
  }]);
  render(<NotificationRunHistory accountKey="synthetic-admin" />);
  expect(read).not.toHaveBeenCalled();
  const details = screen.getByText("Recent notification delivery history").parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle"));
  await screen.findByText(/At least one provider accepted; partial failure; review required/);
  expect(screen.getByText("media_player.accepted: Accepted")).toBeInTheDocument();
  expect(screen.getByText("media_player.uncertain: Unknown")).toBeInTheDocument();
  expect(screen.getByText("media_player.later: Not Sent")).toBeInTheDocument();
  expect(screen.getByText(/Provider acceptance does not prove audible playback/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /send|retry|replay/i })).not.toBeInTheDocument();
});
