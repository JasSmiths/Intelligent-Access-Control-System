import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { AlfredTrainingView } from "./AlfredTrainingView";

afterEach(() => vi.unstubAllGlobals());

it("keeps a committed lesson review locked when the follow-up read fails", async () => {
  let reviewed = false;
  const lesson = { id: "lesson-1", scope: "site", title: "Check gate receipt", lesson: "Verify outcome",
    tags: [], source_feedback_ids: [], confidence: .8, status: "pending",
    created_at: "2026-09-28T12:00:00Z", updated_at: "2026-09-28T12:00:00Z" };
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.includes("/settings?")) return new Response("[]");
    if (path.includes("/feedback?")) return reviewed
      ? new Response(JSON.stringify({ detail: "read unavailable" }), { status: 503 })
      : new Response(JSON.stringify({ feedback: [] }));
    if (path.includes("/lessons?")) return new Response(JSON.stringify({ lessons: [lesson] }));
    if (path.includes("/eval-examples?")) return new Response(JSON.stringify({ examples: [] }));
    if (path.endsWith("/lessons/lesson-1/review") && options?.method === "POST") {
      reviewed = true;
      return new Response("{}");
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AlfredTrainingView refreshToken={0} />);
  const approve = await screen.findByRole("button", { name: "Approve" });
  fireEvent.click(approve);
  await waitFor(() => expect(screen.getByText("Lesson approved.")).toBeInTheDocument());
  expect(screen.getByText(/Lesson reviewed, but the latest training data is unavailable/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reviewed" })).toBeDisabled();
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith("/lessons/lesson-1/review"))).toHaveLength(1);
});
