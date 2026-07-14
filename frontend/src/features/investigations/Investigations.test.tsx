import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UserAccount } from "../../api/types";
import { LogsView } from "../../views/LogsView";
import { ActivityTimeline } from "./ActivityTimeline";
import { InvestigationFilters } from "./InvestigationFilters";
import { InvestigationOverview } from "./InvestigationOverview";
import { QuestionComposer } from "./QuestionComposer";
import {
  filterCatalog,
  defaultOverview,
  groundedAnswer,
  insufficientAnswer,
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

afterEach(cleanup);

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
});

describe("default investigation overview", () => {
  it("prioritises recent, incomplete and repeated problems before routine activity", () => {
    render(<InvestigationOverview onSelect={vi.fn()} overview={defaultOverview} />);
    expect(screen.getByText("Problems and blocked actions")).toBeInTheDocument();
    expect(screen.getByText("Repeated problems")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant command rejection")).toBeInTheDocument();
    expect(screen.getByText("Important recent activity")).toBeInTheDocument();
  });
});

describe("investigation answer", () => {
  it("renders a grounded answer and links each cited event to its exact evidence", () => {
    const onEpisodeSelect = vi.fn();
    render(<QuestionComposer answer={groundedAnswer} error="" loading={false} onClear={vi.fn()} onEpisodeSelect={onEpisodeSelect} onSubmit={vi.fn()} />);
    expect(screen.getByText(/schedule ended at 22:30/)).toBeInTheDocument();
    expect(screen.getByText("The garage-door schedule condition failed.")).toBeInTheDocument();
    const links = screen.getAllByRole("button", { name: "View exact evidence" });
    expect(links.length).toBe(3);
    fireEvent.click(links[1]);
    expect(onEpisodeSelect).toHaveBeenCalledWith(scheduleBlockedEpisode.episode_id, "e-schedule");
  });

  it("states insufficient evidence without inventing a reason", () => {
    render(<QuestionComposer answer={insufficientAnswer} error="" loading={false} onClear={vi.fn()} onEpisodeSelect={vi.fn()} onSubmit={vi.fn()} />);
    expect(screen.getByText(/cannot determine why/)).toBeInTheDocument();
    expect(screen.getByText("Evidence is incomplete")).toBeInTheDocument();
    expect(screen.getByText(/No correlated command/)).toBeInTheDocument();
    expect(screen.queryByText("Most likely reason")).not.toBeInTheDocument();
  });
});

describe("structured filters and permissions", () => {
  it("changes time, device, automation and outcome independently and displays the site timezone", () => {
    const onChange = vi.fn();
    render(<InvestigationFilters catalog={filterCatalog} onChange={onChange} onReset={vi.fn()} query={DEFAULT_INVESTIGATION_QUERY} timezone={SITE_TIMEZONE} />);
    expect(screen.getByText(SITE_TIMEZONE)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("When"), { target: { value: "yesterday" } });
    fireEvent.change(screen.getByLabelText("Device"), { target: { value: "main-garage" } });
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "blocked" } });
    fireEvent.click(screen.getByText("More filters"));
    fireEvent.change(screen.getByLabelText("Automation or rule"), { target: { value: "open-on-arrival" } });
    expect(onChange.mock.calls).toEqual(expect.arrayContaining([
      [{ range: "yesterday" }],
      [{ device: "main-garage" }],
      [{ outcome: "blocked" }],
      [{ automation: "open-on-arrival" }]
    ]));
  });

  it("blocks standard users before any sensitive investigation data is mounted", () => {
    const standardUser = { role: "standard" } as UserAccount;
    render(<LogsView currentUser={standardUser} refreshToken={0} />);
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("Activity investigations require administrator access")).toBeInTheDocument();
    expect(screen.queryByText("Ask what happened")).not.toBeInTheDocument();
    expect(document.body).toHaveClass("investigations-route");
  });
});
