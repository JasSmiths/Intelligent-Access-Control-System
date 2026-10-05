import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UserAccount } from "../../api/types";
import { LogsView } from "../../views/LogsView";
import { ActivityTimeline } from "./ActivityTimeline";
import { InvestigationFilters } from "./InvestigationFilters";
import { InvestigationOverview } from "./InvestigationOverview";
import {
  filterCatalog,
  defaultOverview,
  integrationRejectedEpisode,
  scheduleBlockedDetail,
  scheduleBlockedEpisode,
  SITE_TIMEZONE,
  skippedEpisode,
  successfulEpisode,
  unverifiedEpisode
} from "./fixtures";
import { DEFAULT_INVESTIGATION_QUERY } from "./query";

const timelineDefaults = {
  details: {},
  detailErrors: {},
  hasFilters: false,
  items: [scheduleBlockedEpisode],
  loading: false,
  loadingDetailIds: new Set<string>(),
  loadingMore: false,
  nextCursor: null,
  onLoadDetail: vi.fn(),
  onLoadMore: vi.fn(),
  partial: false,
  timezone: SITE_TIMEZONE
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("activity timeline", () => {
  it("renders one readable correlated episode and expands its chronological evidence", () => {
    const onLoadDetail = vi.fn();
    render(<ActivityTimeline {...timelineDefaults} details={{ [scheduleBlockedEpisode.episode_id]: scheduleBlockedDetail }} onLoadDetail={onLoadDetail} />);

    expect(screen.getByText("Open on arrival was blocked")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /Open on arrival was blocked/ }));
    expect(onLoadDetail).toHaveBeenCalledWith(scheduleBlockedEpisode.episode_id);
    expect(screen.getByText("Presence changed to home")).toBeInTheDocument();
    expect(screen.getByText("Garage-door schedule condition failed")).toBeInTheDocument();
    expect(screen.getByText("Command not sent")).toBeInTheDocument();
    expect(screen.getByText(/Allowed window 06:00–22:30/)).toBeInTheDocument();
    expect(screen.getByText("Captured at evaluation")).toBeInTheDocument();
    expect(screen.getByText("22:47")).toBeInTheDocument();
    expect(screen.getAllByRole("time")[2]).toHaveAttribute("title", expect.stringContaining("2026"));
    const firstEvidenceRow = screen.getByText("Presence changed to home").closest("li");
    expect(firstEvidenceRow?.children[0].tagName).toBe("TIME");
    expect(firstEvidenceRow?.children[1]).toHaveClass("investigation-evidence-marker");
    expect(firstEvidenceRow?.children[2]).toHaveClass("investigation-evidence-copy");
  });

  it("keeps blocked, skipped, failed, pending and successful outcomes distinct", () => {
    render(<ActivityTimeline {...timelineDefaults} items={[scheduleBlockedEpisode, skippedEpisode, integrationRejectedEpisode, unverifiedEpisode, successfulEpisode]} />);
    expect(screen.getByText("Blocked")).toHaveAttribute("data-outcome", "blocked");
    expect(screen.getByText("Skipped")).toHaveAttribute("data-outcome", "skipped");
    expect(screen.getByText("Failed")).toHaveAttribute("data-outcome", "failed");
    expect(screen.getByText("Pending")).toHaveAttribute("data-outcome", "pending");
    expect(screen.getByText("Succeeded")).toHaveAttribute("data-outcome", "succeeded");
    expect(screen.getByText(/Standalone event/)).toBeInTheDocument();
  });

  it("redacts raw values defensively and exposes captured configuration values", () => {
    render(<ActivityTimeline {...timelineDefaults} details={{ [scheduleBlockedEpisode.episode_id]: scheduleBlockedDetail }} />);
    fireEvent.click(screen.getByRole("button", { name: /Open on arrival was blocked/ }));
    for (const summary of screen.getAllByText("Sanitised raw evidence")) {
      const details = summary.closest("details") as HTMLDetailsElement;
      details.open = true;
      fireEvent(details, new Event("toggle"));
    }
    expect(document.body).toHaveTextContent("[REDACTED]");
    expect(document.body).toHaveTextContent("safe");
    expect(document.body).not.toHaveTextContent("must-never-render");
    expect(document.body).not.toHaveTextContent("redact-me");
  });

  it("supports incremental loading and differentiated loading, empty, no-results and partial states", () => {
    const onLoadMore = vi.fn();
    const { rerender } = render(<ActivityTimeline {...timelineDefaults} items={[]} loading />);
    expect(screen.getByText("Building the activity timeline")).toBeInTheDocument();

    rerender(<ActivityTimeline {...timelineDefaults} items={[]} />);
    expect(screen.getByText("No activity was recorded in this period")).toBeInTheDocument();

    rerender(<ActivityTimeline {...timelineDefaults} hasFilters items={[]} />);
    expect(screen.getByText("No activity matched these filters")).toBeInTheDocument();

    rerender(<ActivityTimeline {...timelineDefaults} nextCursor="cursor-2" onLoadMore={onLoadMore} partial />);
    expect(screen.getByText(/evidence sources were unavailable/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load older activity" }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("shows a specific failure state when expanded evidence cannot be loaded", () => {
    render(<ActivityTimeline {...timelineDefaults} detailErrors={{ [scheduleBlockedEpisode.episode_id]: "Evidence service unavailable" }} />);
    fireEvent.click(screen.getByRole("button", { name: /Open on arrival was blocked/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Evidence service unavailable");
  });

  it("links retained event evidence and identifies an older selected investigation", () => {
    const selected = { ...scheduleBlockedDetail, timeline: [
      { ...scheduleBlockedDetail.timeline[0], event_id: "11111111-1111-4111-8111-111111111111" },
      ...scheduleBlockedDetail.timeline.slice(1)
    ] };
    render(<ActivityTimeline {...timelineDefaults} items={[]} details={{ [scheduleBlockedEpisode.episode_id]: selected }} requestedEpisodeId={scheduleBlockedEpisode.episode_id} />);
    expect(screen.getByText(/outside the current results/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View event 11111111/ })).toHaveAttribute("href", "/events?event=11111111-1111-4111-8111-111111111111");
  });
});

describe("default investigation overview", () => {
  it("prioritises recent, incomplete and repeated problems before routine activity", () => {
    render(<InvestigationOverview onSelect={vi.fn()} overview={defaultOverview} />);
    expect(screen.getByText("Problems and blocked actions")).toBeInTheDocument();
    expect(screen.getByText("Repeated problems")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant command rejection")).toBeInTheDocument();
    expect(screen.getByText("Important recent activity")).toBeInTheDocument();
  });

  it("collapses an entirely quiet overview into one concise state", () => {
    render(<InvestigationOverview onSelect={vi.fn()} overview={{ ...defaultOverview,
      recent_problems: [], incomplete_runs: [], repeated_problems: [], important_activity: [] }} />);
    expect(screen.getByText(/No recent problems or repeated failures/)).toBeInTheDocument();
    expect(screen.queryByText("Problems and blocked actions")).not.toBeInTheDocument();
  });
});
