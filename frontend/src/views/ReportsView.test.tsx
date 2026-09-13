import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Person } from "../api/types";
import type { VisitorPass } from "./PassesView";
import { reportsApi, type ReportPreviewResponse, type ReportSnapshotEvent, type ReportExportResponse, type ReportSnapshotTimelineEvent } from "../api/reports";
// Generated fixture copy: backend tests enforce equality with the canonical report contract.
import contract from "../api/fixtures/reportPreview.generated.json";
import { ReportsView } from "./ReportsView";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const person: Person = {
  id: "synthetic-person", display_name: "Synthetic Resident", first_name: "Synthetic", last_name: "Resident",
  pronouns: null, profile_photo_data_url: null, group_id: null, group: null, category: null,
  schedule_id: null, schedule: null, is_active: true, notes: null, garage_door_entity_ids: [],
  home_assistant_mobile_app_notify_service: null, home_assistant_presence_input_boolean_entity_ids: [],
  home_assistant_presence_input_boolean_entry_action: "turn_on", home_assistant_presence_input_boolean_exit_action: "turn_off",
  vehicles: [{ id: "synthetic-vehicle", registration_number: "SYNTH01", description: null, make: null, model: null }]
};

function visitor(name: string): VisitorPass {
  return { id: name, visitor_name: name, number_plate: null, pass_type: "one-time", status: "active" } as VisitorPass;
}

const ready = contract.ready as ReportPreviewResponse & { status: "ready" };
const ambiguous = contract.ambiguous as ReportPreviewResponse;
const context = { site_timezone: "Europe/London", now: "2026-07-14T09:00:00Z" };
function previewWithSource(source: string): typeof ready {
  const event: ReportSnapshotEvent = {
    id: source, registration_number: "SYNTH01", direction: "exit", decision: "granted", confidence: 0.94,
    confidence_percent: 94, source, source_label: source, occurred_at: "2026-07-14T08:30:00Z", occurred_label: "14 Jul at 09:30",
    timing_classification: "unknown", anomaly_count: 0, visitor_pass_id: null, visitor_name: null, visitor_pass_mode: null,
    snapshot_url: null, snapshot_captured_at: null, snapshot_bytes: null, snapshot_width: null, snapshot_height: null,
    snapshot_camera: null, duration: { label: "6hrs 0m" }, tone: "blue", type_label: "Departure", detail: "Access granted"
  };
  return { ...ready, report: { ...ready.report, events: [event], summary: { ...ready.report.summary, total: 1, departures: 1 } } };
}
function setupReports() {
  vi.spyOn(reportsApi, "context").mockResolvedValue(context);
  vi.spyOn(api, "get").mockResolvedValue([]);
}
async function selectResident() {
  await act(async () => {});
  fireEvent.focus(screen.getByRole("combobox", { name: "Subject" }));
  fireEvent.click(screen.getByRole("option", { name: /Synthetic Resident/ }));
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); });

it("retries a canceled visitor list read and ignores the late canceled response", async () => {
  vi.spyOn(reportsApi, "context").mockResolvedValue(context);
  const old = deferred<VisitorPass[]>();
  const current = deferred<VisitorPass[]>();
  const read = vi.spyOn(api, "get").mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  render(<ReportsView events={[]} people={[]} presence={[]} />);
  const subject = screen.getByRole("combobox", { name: "Subject" });
  fireEvent.focus(subject);
  expect(read).toHaveBeenCalledTimes(1);
  const canceledSignal = read.mock.calls[0][1]?.signal;
  fireEvent.change(subject, { target: { value: "Visitor" } });
  expect(canceledSignal?.aborted).toBe(true);
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => { current.resolve([visitor("Visitor Current")]); });
  expect(screen.getByRole("option", { name: /Visitor Current/ })).toBeInTheDocument();
  await act(async () => { old.resolve([visitor("Visitor Stale")]); });
  expect(screen.queryByRole("option", { name: /Visitor Stale/ })).not.toBeInTheDocument();
  expect(screen.getByRole("option", { name: /Visitor Current/ })).toBeInTheDocument();
});

it("allows another visitor read after a failure instead of treating a started request as loaded", async () => {
  vi.spyOn(reportsApi, "context").mockResolvedValue(context);
  const failed = deferred<VisitorPass[]>();
  const retried = deferred<VisitorPass[]>();
  const read = vi.spyOn(api, "get").mockReturnValueOnce(failed.promise).mockReturnValueOnce(retried.promise);
  render(<ReportsView events={[]} people={[]} presence={[]} />);
  const subject = screen.getByRole("combobox", { name: "Subject" });
  fireEvent.focus(subject);
  await act(async () => { failed.reject(new Error("Synthetic offline")); });
  fireEvent.change(subject, { target: { value: "Visitor" } });
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => { retried.resolve([visitor("Visitor Retry")]); });
  expect(screen.getByRole("option", { name: /Visitor Retry/ })).toBeInTheDocument();
});

it("retries a canceled complete preview on shell invalidation and ignores late data", async () => {
  setupReports();
  const old = deferred<ReportPreviewResponse>();
  const current = deferred<ReportPreviewResponse>();
  const preview = vi.spyOn(reportsApi, "preview").mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  const view = render(<ReportsView events={[]} people={[person]} presence={[]} />);
  await selectResident();
  expect(preview).toHaveBeenCalledTimes(1);
  const oldSignal = preview.mock.calls[0][1]!.signal!;
  view.rerender(<ReportsView events={[]} people={[person]} presence={[]} />);
  expect(oldSignal.aborted).toBe(true);
  expect(preview).toHaveBeenCalledTimes(2);
  await act(async () => { current.resolve(previewWithSource("Current camera")); });
  expect(screen.getByText("Current camera")).toBeInTheDocument();
  await act(async () => { old.resolve(previewWithSource("Stale camera")); });
  expect(screen.queryByText("Stale camera")).not.toBeInTheDocument();
  expect(api.get).not.toHaveBeenCalledWith("/api/v1/events?limit=250", expect.anything());
});

it("renders all supplied events and authoritative duration and site labels, then exports accepted UTC bounds", async () => {
  setupReports();
  vi.stubEnv("TZ", "America/Los_Angeles");
  const response = previewWithSource("Complete camera");
  response.report.events = Array.from({ length: 251 }, (_, index) => ({ ...response.report.events[0], id: `synthetic-${index}` }));
  response.report.summary.total = 251;
  const timeline: ReportSnapshotTimelineEvent = { ...contract.timeline_event, direction: "entry", decision: "granted", tone: "green" };
  response.report.timeline = { all: [timeline], selected: [timeline] };
  vi.spyOn(reportsApi, "preview").mockResolvedValue(response);
  const exported = { report_id: "123456", report: { ...response.report, report_id: "123456" }, download_url: "/api/v1/reports/123456/pdf" } as ReportExportResponse;
  const exportReport = vi.spyOn(reportsApi, "export").mockResolvedValue(exported);
  const download = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<ReportsView events={[]} people={[person]} presence={[]} />);
  await selectResident();
  await act(async () => {});
  expect(screen.getByText("Showing 251 of 251 events")).toBeInTheDocument();
  expect(screen.getAllByText("6hrs 0m")).toHaveLength(251);
  expect(screen.getAllByText("14 Jul at 09:30")).toHaveLength(251);
  expect(screen.getByRole("button", { name: "To" })).toHaveTextContent("10:00");
  expect(screen.getByTitle(/All vehicles · Arrival/)).toHaveAttribute("title", expect.stringContaining("09:30"));
  fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));
  await act(async () => {});
  expect(exportReport).toHaveBeenCalledWith({ person_id: person.id, visitor_pass_id: undefined,
    period_start: response.report.period.start, period_end: response.report.period.end, ...response.report.options
  }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(download).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Download PDF" })).toBeEnabled();
});

it("requires the server's repeated-time choice and forwards its fold without client offset arithmetic", async () => {
  setupReports();
  vi.spyOn(reportsApi, "context").mockResolvedValue({ ...context, now: "2026-11-01T03:00:00Z" });
  const preview = vi.spyOn(reportsApi, "preview").mockResolvedValueOnce(ready).mockResolvedValue(ambiguous);
  render(<ReportsView events={[]} people={[person]} presence={[]} />);
  await selectResident();
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "From" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByRole("spinbutton", { name: "Hour" }), { target: { value: "1" } });
  await act(async () => {});
  fireEvent.change(within(dialog).getByRole("spinbutton", { name: "Min" }), { target: { value: "30" } });
  await act(async () => {});
  expect(preview.mock.calls.at(-1)![0].period_start).toBe("2026-10-25T01:30");
  expect(preview.mock.calls.at(-1)![0].period_start).not.toMatch(/Z|[+]00:00$/);
  expect(screen.getByRole("button", { name: "Export PDF" })).toBeDisabled();
  fireEvent.change(screen.getByRole("combobox", { name: "From occurrence" }), { target: { value: "1" } });
  await act(async () => {});
  expect(preview.mock.calls.at(-1)![0].period_start_fold).toBe(1);
  expect(screen.getByRole("option", { name: "Second occurrence (UTC+00:00)" })).toBeInTheDocument();
});

it("invalidates an old preview immediately when options change and suppresses late errors after unmount", async () => {
  setupReports();
  const next = deferred<ReportPreviewResponse>();
  const preview = vi.spyOn(reportsApi, "preview").mockResolvedValueOnce(ready).mockReturnValueOnce(next.promise);
  const view = render(<ReportsView events={[]} people={[person]} presence={[]} />);
  await selectResident();
  await act(async () => {});
  expect(screen.getByRole("button", { name: "Export PDF" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Denied attempts" }));
  expect(screen.getByRole("button", { name: "Export PDF" })).toBeDisabled();
  expect(screen.queryByRole("article", { name: "Report preview" })).not.toBeInTheDocument();
  const signal = preview.mock.calls[1][1]!.signal!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { next.reject(new Error("Late obsolete preview")); });
});

it("shows a missing-hour error without enabling export and allows a new preview after editing", async () => {
  setupReports();
  const preview = vi.spyOn(reportsApi, "preview").mockRejectedValueOnce(new Error(contract.gap.detail)).mockResolvedValueOnce(ready);
  render(<ReportsView events={[]} people={[person]} presence={[]} />);
  await selectResident();
  await act(async () => {});
  expect(screen.getByRole("alert")).toHaveTextContent(contract.gap.detail);
  expect(screen.getByRole("button", { name: "Export PDF" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Snapshots" }));
  await act(async () => {});
  expect(preview).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Export PDF" })).toBeEnabled();
});

it("cancels a historical lookup when another subject is selected", async () => {
  setupReports();
  vi.spyOn(reportsApi, "preview").mockResolvedValue(ready);
  const lookup = deferred<ReportExportResponse>();
  const read = vi.spyOn(reportsApi, "load").mockReturnValue(lookup.promise);
  render(<ReportsView events={[]} people={[person]} presence={[]} />);
  await act(async () => {});
  const input = screen.getByRole("combobox", { name: "Subject" });
  fireEvent.change(input, { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("option", { name: /Open Report #123456/ }));
  fireEvent.change(input, { target: { value: "Synthetic" } });
  fireEvent.click(screen.getByRole("option", { name: /Synthetic Resident/ }));
  expect(read.mock.calls[0][1]!.signal!.aborted).toBe(true);
  await act(async () => { lookup.resolve({ report_id: "123456", report: ready.report } as ReportExportResponse); });
  expect(screen.queryByText("Exported Report")).not.toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Subject" })).toHaveValue("Synthetic Resident");
});
