import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { AccessEvent } from "../api/types";
import { EventsView } from "./EventsView";

const event = (id: string, registration_number: string): AccessEvent => ({
  id, registration_number, direction: "entry", decision: "granted", confidence: .9,
  source: "synthetic", occurred_at: "2026-09-28T12:00:00Z", timing_classification: "normal",
  anomaly_count: 0, visitor_pass_id: null, visitor_name: null, visitor_pass_mode: null,
  external_admission_mode: null, external_admission_source: null, snapshot_url: null,
  snapshot_captured_at: null, snapshot_bytes: null, snapshot_width: null, snapshot_height: null,
  snapshot_camera: null, movement_saga: null,
});

afterEach(() => vi.unstubAllGlobals());

it("opens older direct results when the target changes within the same route", async () => {
  const fetcher = vi.fn(async (path: string) => {
    if (path.startsWith("/api/v1/events/history?")) return new Response(JSON.stringify({
      items: [], next_cursor: null, as_of: "2026-09-28T12:00:00Z",
    }));
    if (path === "/api/v1/events/id-1") return new Response(JSON.stringify(event("id-1", "FIRST11")));
    if (path === "/api/v1/events/id-2") return new Response(JSON.stringify(event("id-2", "SECOND22")));
    throw new Error(`Unexpected URL: ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const { rerender } = render(<EventsView refreshToken={0} resetToken={0} targetId={null} />);
  await waitFor(() => expect(screen.getByText("No events recorded yet.")).toBeInTheDocument());
  rerender(<EventsView refreshToken={0} resetToken={0} targetId="id-1" />);
  await screen.findByText("FIRST11");
  rerender(<EventsView refreshToken={0} resetToken={0} targetId="id-2" />);
  await screen.findByText("SECOND22");
  expect(screen.queryByText("FIRST11")).not.toBeInTheDocument();
  expect(fetcher.mock.calls.map(([path]) => path)).toContain("/api/v1/events/id-2");
});
