import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import * as directory from "../../api/directory";
import type { Person, Vehicle } from "../../api/types";
import { VehicleModal, VehiclePeoplePicker } from "./VehicleEditor";
import type { VehicleInformation } from "../../api/vehicleInformation";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

function modal(vehicle: Vehicle | null = null) {
  return render(<VehicleModal canRefreshInformation={true} defaultPolicyOptionLabel="Default" groups={[]} mode={vehicle ? "edit" : "create"}
    onClose={vi.fn()} onSaved={vi.fn().mockResolvedValue(undefined)} people={[]}
    refreshVehicles={vi.fn().mockResolvedValue(undefined)} schedules={[]} setPageError={vi.fn()} vehicle={vehicle} />);
}

function result(registration: string, make = "Lookup Make"): VehicleInformation {
  return { registration_number: registration, make, model: "Focus", colour: "Blue", fuel_type: "Petrol", mot_status: "Valid", mot_expiry: "2027-01-01", tax_status: "Taxed", tax_expiry: null, last_dvla_lookup_date: "2026-10-07", providers: { dvla: { status: "found", checked_at: null, retry_at: null, error: null }, dvsa: { status: "found", checked_at: null, retry_at: null, error: null } } };
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it.each(["", "X"])("invalidates an in-flight lookup when the plate becomes %j", async (nextPlate) => {
  const lookup = deferred<VehicleInformation>();
  const post = vi.spyOn(api, "post").mockReturnValue(lookup.promise);
  modal();
  const registration = screen.getByLabelText(/^Vehicle Registration/);
  const make = screen.getByLabelText("Vehicle Make");
  fireEvent.change(make, { target: { value: "User Draft" } });
  fireEvent.change(registration, { target: { value: "SYNTH01" } });
  await act(async () => { vi.advanceTimersByTime(850); });
  expect(post).toHaveBeenCalledTimes(1);
  const signal = post.mock.calls[0][2]?.signal;
  fireEvent.change(registration, { target: { value: nextPlate } });
  expect(signal?.aborted).toBe(true);
  await act(async () => { lookup.resolve(result("SYNTH01")); });
  expect(registration).toHaveValue(nextPlate);
  expect(make).toHaveValue("User Draft");
  expect(screen.queryByText("DVLA details applied")).not.toBeInTheDocument();
});

it("keeps a restored original plate and draft when an edit's old lookup finishes", async () => {
  const lookup = deferred<VehicleInformation>();
  const post = vi.spyOn(api, "post").mockReturnValue(lookup.promise);
  modal({ id: "synthetic", registration_number: "ORIGINAL", make: "Original Make", model: null, description: null });
  const registration = screen.getByLabelText(/^Vehicle Registration/);
  fireEvent.change(registration, { target: { value: "SYNTH02" } });
  await act(async () => { vi.advanceTimersByTime(850); });
  fireEvent.change(registration, { target: { value: "ORIGINAL" } });
  expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true);
  await act(async () => { lookup.resolve(result("SYNTH02")); });
  expect(registration).toHaveValue("ORIGINAL");
  expect(screen.getByLabelText("Vehicle Make")).toHaveValue("Original Make");
});

it("applies only the newest lookup when responses finish out of order", async () => {
  const old = deferred<VehicleInformation>();
  const current = deferred<VehicleInformation>();
  vi.spyOn(api, "post").mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  modal();
  const registration = screen.getByLabelText(/^Vehicle Registration/);
  fireEvent.change(registration, { target: { value: "SYNTH01" } });
  await act(async () => { vi.advanceTimersByTime(850); });
  fireEvent.change(registration, { target: { value: "SYNTH02" } });
  await act(async () => { vi.advanceTimersByTime(850); });
  await act(async () => { current.resolve(result("SYNTH02", "Current Make")); });
  await act(async () => { old.resolve(result("SYNTH01", "Stale Make")); });
  expect(registration).toHaveValue("SYNTH02");
  expect(screen.getByLabelText("Vehicle Make")).toHaveValue("Current Make");
});

it("aborts the lookup and clears the debounce timer on unmount", async () => {
  const lookup = deferred<VehicleInformation>();
  const post = vi.spyOn(api, "post").mockReturnValue(lookup.promise);
  const view = modal();
  fireEvent.change(screen.getByLabelText(/^Vehicle Registration/), { target: { value: "SYNTH01" } });
  await act(async () => { vi.advanceTimersByTime(850); });
  const signal = post.mock.calls[0][2]?.signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { lookup.resolve(result("SYNTH01")); });
  const pending = modal();
  fireEvent.change(screen.getByLabelText(/^Vehicle Registration/), { target: { value: "SYNTH02" } });
  pending.unmount();
  await act(async () => { vi.advanceTimersByTime(850); });
  expect(post).toHaveBeenCalledTimes(1);
});

it("keeps server search matches visible when a registration matches instead of the person's name", async () => {
  const matching = { id: "matching-owner", display_name: "Synthetic Owner", group: "Residents" } as Person;
  const selected = { id: "selected-owner", display_name: "Previously Selected" } as Person;
  const read = vi.spyOn(directory, "readDirectory").mockResolvedValue({ items: [matching], total: 1, next_cursor: null });
  vi.spyOn(directory, "lookupDirectory").mockResolvedValue([selected]);
  render(<VehiclePeoplePicker groups={[]} people={[]} selectedPersonIds={[selected.id]} onToggle={vi.fn()} />);
  await act(async () => { vi.advanceTimersByTime(0); });
  fireEvent.change(screen.getByRole("textbox", { name: "Search people to assign" }), { target: { value: "SYNTH01" } });
  await act(async () => { vi.advanceTimersByTime(150); });
  expect(read.mock.calls.at(-1)?.[1]?.q).toBe("SYNTH01");
  expect(screen.getByText("Synthetic Owner")).toBeInTheDocument();
  expect(screen.getByText("Previously Selected")).toBeInTheDocument();
  expect(screen.queryByText("No people match this search.")).not.toBeInTheDocument();
});
