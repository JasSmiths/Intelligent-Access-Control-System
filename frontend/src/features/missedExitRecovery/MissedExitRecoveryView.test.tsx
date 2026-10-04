import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { missedExitRecoveryApi, type RecoveryAttempt } from "../../api/missedExitRecovery";
import * as client from "../../api/client";
import type { Person, UserAccount } from "../../api/types";
import { MissedExitRecoveryView, RecoveryAttemptDetails } from "./MissedExitRecoveryView";
import { safeDiagnosticRows } from "./model";
const settings = vi.hoisted(() => ({ values: { missed_exit_recovery_enabled: false, missed_exit_recovery_gate_latitude: null, missed_exit_recovery_gate_longitude: null }, save: vi.fn() }));
vi.mock("../../lib/settings", async () => ({ ...await vi.importActual("../../lib/settings"), useSettings: () => ({ ...settings, loading: false, error: "" }) }));
const person = { id: "owner-1", display_name: "Synthetic Resident", missed_exit_recovery_enabled: false, missed_exit_recovery_tracker_entity_id: null } as Person;
const attempt: RecoveryAttempt = { id: "attempt-1", occurred_at: "2026-10-02T10:00:00Z", owner_id: person.id, owner_name: person.display_name, registration_number: "SYNTH01", vehicle_id: "vehicle-1", event_id: "denied-event", recovery_event_id: "recovery-event", saga_id: "saga-1", command_id: "command-1", method: "phone_automatic", outcome: "command_pending", reason: "Entry accepted, physical verification pending", checks: { phone: { sample_age_seconds: 12, accuracy_m: 15, distance_m: 42, max_distance_m: 100, passed: true, latitude: 51.5, longitude: -0.1 }, provider_payload: { secret: "sensitive" } }, timeline: [{ at: "2026-10-02T10:00:01Z", stage: "access_decision", status: "committed", reason: "Entry committed" }, { at: "2026-10-02T10:00:02Z", stage: "command_verification", status: "pending", reason: "Physical outcome not yet verified" }], notification: { status: "not_requested", expires_at: null, action_at: null, delivery_id: null }, duration_ms: null, policy_version: "1" };
const props = { currentUser: { id: "admin-1", role: "admin" } as UserAccount, people: [person], refreshToken: 0, refresh: vi.fn().mockResolvedValue(undefined) };
beforeEach(() => {
  settings.save.mockReset();
  vi.spyOn(missedExitRecoveryApi, "attempts").mockResolvedValue({ items: [attempt], total: 26, offset: 0, limit: 25 });
  vi.spyOn(missedExitRecoveryApi, "attempt").mockResolvedValue(attempt);
  vi.spyOn(missedExitRecoveryApi, "saveOwner").mockResolvedValue(person);
  vi.spyOn(client, "createActionConfirmation").mockResolvedValue({ confirmation_id: "intent-1", confirmation_token: "token-1", action: "settings.update", expires_at: "" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("protects the entire page and performs no recovery reads for non administrators", async () => {
  await act(async () => render(<MissedExitRecoveryView {...props} currentUser={{ ...props.currentUser, role: "standard" }} />));
  expect(screen.getByRole("alert")).toHaveTextContent("Administrator access required");
  expect(missedExitRecoveryApi.attempts).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Save global configuration" })).not.toBeInTheDocument();
});
it("starts disabled and blocks enabling without gate coordinates before confirmation", async () => {
  await act(async () => render(<MissedExitRecoveryView {...props} />));
  expect(screen.getByRole("combobox", { name: "Global recovery" })).toHaveValue("false");
  fireEvent.change(screen.getByRole("combobox", { name: "Global recovery" }), { target: { value: "true" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save global configuration" })));
  expect(screen.getByRole("alert")).toHaveTextContent("Gate latitude and longitude are required");
  expect(client.createActionConfirmation).not.toHaveBeenCalled();
  expect(settings.save).not.toHaveBeenCalled();
});
it("saves global coordinates and enabled state through the existing confirmation boundary", async () => {
  await act(async () => render(<MissedExitRecoveryView {...props} />));
  fireEvent.change(screen.getByRole("spinbutton", { name: "Gate latitude" }), { target: { value: "51.5" } });
  fireEvent.change(screen.getByRole("spinbutton", { name: "Gate longitude" }), { target: { value: "-0.1" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Global recovery" }), { target: { value: "true" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save global configuration" })));
  const values = { missed_exit_recovery_enabled: true, missed_exit_recovery_gate_latitude: 51.5, missed_exit_recovery_gate_longitude: -0.1 };
  expect(client.createActionConfirmation).toHaveBeenCalledWith("settings.update", { values }, expect.objectContaining({ target_entity: "SystemSetting" }));
  expect(settings.save).toHaveBeenCalledWith(values, { confirmationToken: "token-1" });
});
it("reports rejected settings without displaying a success", async () => {
  settings.save.mockRejectedValue(new Error("Configuration rejected"));
  await act(async () => render(<MissedExitRecoveryView {...props} />));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save global configuration" })));
  expect(screen.getByRole("alert")).toHaveTextContent("Configuration rejected");
  expect(screen.queryByText("Global recovery settings saved.")).not.toBeInTheDocument();
});
it("saves the explicit owner and tracker association", async () => {
  await act(async () => render(<MissedExitRecoveryView {...props} />));
  fireEvent.change(screen.getByRole("combobox", { name: "Configure owner" }), { target: { value: person.id } });
  fireEvent.change(screen.getByRole("combobox", { name: "Owner recovery opt-in" }), { target: { value: "true" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Home Assistant phone tracker entity" }), { target: { value: "device_tracker.synthetic" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save owner configuration" })));
  expect(missedExitRecoveryApi.saveOwner).toHaveBeenCalledWith(person, { missed_exit_recovery_enabled: true, missed_exit_recovery_tracker_entity_id: "device_tracker.synthetic" });
});
it("pages on the server and resets the offset when filters are applied", async () => {
  await act(async () => render(<MissedExitRecoveryView {...props} />));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Next" })));
  expect(missedExitRecoveryApi.attempts).toHaveBeenLastCalledWith({ offset: 25, limit: 25 }, expect.anything());
  fireEvent.change(screen.getByRole("textbox", { name: "Plate filter" }), { target: { value: "SYNTH01" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Method filter" }), { target: { value: "resident_confirmation" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply filters" })));
  expect(missedExitRecoveryApi.attempts).toHaveBeenLastCalledWith(expect.objectContaining({ registration_number: "SYNTH01", method: "resident_confirmation", offset: 0, limit: 25 }), expect.anything());
});
it("loads durable detail on demand and keeps command verification distinct from decision", async () => {
  await act(async () => render(<MissedExitRecoveryView {...props} />));
  expect(missedExitRecoveryApi.attempt).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "View recovery attempt SYNTH01 attempt-1" })));
  expect(missedExitRecoveryApi.attempt).toHaveBeenCalledWith("attempt-1", expect.anything());
  expect(screen.getByText("Access Decision")).toBeInTheDocument();
  expect(screen.getByText("Command Verification")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "denied-event" })).toHaveAttribute("href", "/events?event=denied-event");
  expect(screen.getByRole("link", { name: "command-1" })).toHaveAttribute("href", "/settings/command-history?command=command-1");
  expect(screen.getByText("Not recorded")).toBeInTheDocument();
});
it("renders ages, accuracy, distances and pass checks while withholding raw location and provider payload", () => {
  render(<RecoveryAttemptDetails attempt={attempt} />);
  expect(screen.getByText("Phone · Sample Age Seconds")).toBeInTheDocument();
  expect(screen.getByText("Phone · Accuracy M")).toBeInTheDocument();
  expect(screen.getByText("Phone · Distance M")).toBeInTheDocument();
  expect(screen.getByText("Phone · Passed")).toBeInTheDocument();
  expect(screen.queryByText("sensitive")).not.toBeInTheDocument();
  expect(screen.queryByText("51.5")).not.toBeInTheDocument();
  expect(safeDiagnosticRows({ latitude: 51.5, gps: { secret: "sensitive" }, token: "secret", provider_payload: { ok: true } })).toEqual([]);
});
it("aborts and ignores a late attempt list when the administrator changes filters", async () => {
  let resolve!: (value: { items: RecoveryAttempt[]; total: number; offset: number; limit: number }) => void;
  const pending = new Promise<{ items: RecoveryAttempt[]; total: number; offset: number; limit: number }>((done) => { resolve = done; });
  vi.mocked(missedExitRecoveryApi.attempts).mockReturnValueOnce(pending).mockResolvedValueOnce({ items: [], total: 0, offset: 0, limit: 25 });
  render(<MissedExitRecoveryView {...props} />);
  const signal = vi.mocked(missedExitRecoveryApi.attempts).mock.calls[0][1]!.signal!;
  fireEvent.change(screen.getByRole("combobox", { name: "Outcome filter" }), { target: { value: "denied" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply filters" })));
  expect(signal.aborted).toBe(true);
  await act(async () => resolve({ items: [attempt], total: 1, offset: 0, limit: 25 }));
  expect(screen.queryByText("SYNTH01")).not.toBeInTheDocument();
  expect(screen.getByText("No recovery attempts match these filters.")).toBeInTheDocument();
});
it("shows coalesced canonical authority and keeps notification acceptance separate from the resident action", () => {
  render(<RecoveryAttemptDetails attempt={{ ...attempt, method: "none", outcome: "coalesced", reason: "existing_resident_request", notification: {
    canonical_attempt_id: "canonical-1", canonical_outcome: "expired", canonical_reason: "resident_approval_expired",
    approval_delivery_status: "completed", approval_queued_at: "2026-10-02T10:00:00Z",
    approval_delivered_count: 1, approval_failed_count: 0, approval_skipped_count: 0,
    apology_delivery_status: "pending", apology_delivered_count: 0,
    action_at: null,
  } }} />);
  expect(screen.getByRole("link", { name: "canonical attempt canonical-1" })).toHaveAttribute("href", "/settings/missed-exit-recovery?attempt=canonical-1");
  expect(screen.getByText(/resident_approval_expired/)).toBeInTheDocument();
  expect(screen.getByText("Approval Delivered Count")).toBeInTheDocument();
  expect(screen.getByText("Apology Delivery Status")).toBeInTheDocument();
  expect(screen.getByText(/They do not prove the resident saw the notification/)).toBeInTheDocument();
});
it("renders denied attempts with empty notification metadata", () => {
  render(<RecoveryAttemptDetails attempt={{ ...attempt, outcome: "denied", notification: {} }} />);
  expect(screen.getByText("Not Requested")).toBeInTheDocument();
});
it("loads a linked canonical attempt without requiring a list-row click", async () => {
  await act(async () => render(<MissedExitRecoveryView {...props} targetId="canonical-1" />));
  expect(missedExitRecoveryApi.attempt).toHaveBeenCalledWith("canonical-1", expect.anything());
});
