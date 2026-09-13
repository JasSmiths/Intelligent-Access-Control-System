import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Vehicle } from "../api/types";
import { VehicleModal, type DvlaLookupResponse } from "./DirectoryViews";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

function modal(vehicle: Vehicle | null = null) {
  return render(<VehicleModal defaultPolicyOptionLabel="Default" groups={[]} mode={vehicle ? "edit" : "create"}
    onClose={vi.fn()} onSaved={vi.fn().mockResolvedValue(undefined)} people={[]}
    refreshVehicles={vi.fn().mockResolvedValue(undefined)} schedules={[]} setPageError={vi.fn()} vehicle={vehicle} />);
}

function result(registration: string, make = "Lookup Make"): DvlaLookupResponse {
  return { registration_number: registration, vehicle: { make } };
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it.each(["", "X"])("invalidates an in-flight lookup when the plate becomes %j", async (nextPlate) => {
  const lookup = deferred<DvlaLookupResponse>();
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
  const lookup = deferred<DvlaLookupResponse>();
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
  const old = deferred<DvlaLookupResponse>();
  const current = deferred<DvlaLookupResponse>();
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
  const lookup = deferred<DvlaLookupResponse>();
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
