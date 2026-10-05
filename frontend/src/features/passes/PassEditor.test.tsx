import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, createActionConfirmation } from "../../api/client";
import { VisitorPassModal } from "./PassEditor";
import type { VisitorPass } from "./types";

vi.mock("../../api/client", () => ({
  api: { post: vi.fn(), patch: vi.fn() },
  createActionConfirmation: vi.fn()
}));

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(createActionConfirmation).mockResolvedValue({ confirmation_token: "preview-confirmation" } as never);
  vi.mocked(api.post).mockResolvedValue({});
  vi.mocked(api.patch).mockResolvedValue({});
});

function legacyPass(): VisitorPass {
  return {
    id: "legacy-duration", visitor_name: "Visitor", pass_type: "duration", visitor_phone: null,
    expected_time: "2026-10-05T10:00:00Z", window_minutes: 30, valid_from: "2026-10-05T10:00:00Z",
    valid_until: "2026-10-05T12:00:00Z", window_start: "2026-10-05T10:00:00Z", window_end: "2026-10-05T12:00:00Z",
    status: "active", creation_source: "manual", source_reference: null, created_by_user_id: null, created_by: null,
    arrival_time: null, departure_time: null, number_plate: null, vehicle_make: null, vehicle_colour: null,
    duration_on_site_seconds: null, duration_human: null, arrival_event_id: null, departure_event_id: null,
    telemetry_trace_id: null, created_at: "2026-10-05T10:00:00Z", updated_at: "2026-10-05T10:00:00Z"
  };
}

function editor(pass: VisitorPass | null = null) {
  return render(<VisitorPassModal mode={pass ? "edit" : "create"} visitorPass={pass}
    onClose={vi.fn()} onSaved={vi.fn().mockResolvedValue(undefined)} />);
}

it("requires a usable plate for a new duration pass before requesting confirmation", async () => {
  editor();
  fireEvent.change(screen.getByLabelText("Visitor name"), { target: { value: "Visitor" } });
  fireEvent.click(screen.getByRole("button", { name: "Duration" }));
  fireEvent.change(screen.getByLabelText("Number plate"), { target: { value: "---" } });
  fireEvent.submit(screen.getByRole("dialog", { name: "Visitor pass" }));
  expect(await screen.findByText("Duration passes need a number plate.")).toBeInTheDocument();
  expect(createActionConfirmation).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
});

it("creates a duration pass with a normalized plate and optional phone through confirmation", async () => {
  editor();
  fireEvent.change(screen.getByLabelText("Visitor name"), { target: { value: "Visitor" } });
  fireEvent.click(screen.getByRole("button", { name: "Duration" }));
  fireEvent.change(screen.getByLabelText("Number plate"), { target: { value: "ab 12-cde" } });
  fireEvent.submit(screen.getByRole("dialog", { name: "Visitor pass" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/visitor-passes", expect.objectContaining({
    pass_type: "duration", number_plate: "AB12CDE", visitor_phone: null, confirmation_token: "preview-confirmation"
  })));
  expect(createActionConfirmation).toHaveBeenCalledWith("visitor_pass.create", expect.objectContaining({ number_plate: "AB12CDE" }), expect.any(Object));
});

it("keeps one-time pass creation available without a plate", async () => {
  editor();
  fireEvent.change(screen.getByLabelText("Visitor name"), { target: { value: "Visitor" } });
  fireEvent.submit(screen.getByRole("dialog", { name: "Visitor pass" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/visitor-passes", expect.objectContaining({ pass_type: "one-time", number_plate: null })));
});

it("allows ordinary edits to a legacy duration pass without clearing or inventing its plate", async () => {
  editor(legacyPass());
  fireEvent.change(screen.getByLabelText("Visitor name"), { target: { value: "Renamed Visitor" } });
  fireEvent.submit(screen.getByRole("dialog", { name: "Visitor pass" }));
  await waitFor(() => expect(api.patch).toHaveBeenCalled());
  expect(vi.mocked(api.patch).mock.calls[0][1]).not.toHaveProperty("number_plate");
  expect(vi.mocked(api.patch).mock.calls[0][1]).toMatchObject({ visitor_name: "Renamed Visitor", confirmation_token: "preview-confirmation" });
});

it("sends a supplied plate change with the audited update confirmation", async () => {
  editor(legacyPass());
  fireEvent.change(screen.getByLabelText("Number plate"), { target: { value: "xy 34 zzz" } });
  fireEvent.submit(screen.getByRole("dialog", { name: "Visitor pass" }));
  await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/api/v1/visitor-passes/legacy-duration", expect.objectContaining({ number_plate: "XY34ZZZ", confirmation_token: "preview-confirmation" })));
  expect(createActionConfirmation).toHaveBeenCalledWith("visitor_pass.update", expect.objectContaining({ pass_id: "legacy-duration", number_plate: "XY34ZZZ" }), expect.any(Object));
});
