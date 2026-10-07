import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as information from "../../api/vehicleInformation";
import { MotHistory } from "./MotHistory";

const empty: information.MotHistoryPage = { registration_number: "SYNTH01", checked_at: null, freshness: "unknown", outcome: "no_history", items: [], total: 0, next_cursor: null };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
async function openHistory() {
  const details = screen.getByText("MOT test history").closest("details")!;
  await act(async () => { details.open = true; fireEvent(details, new Event("toggle")); });
}

it("loads lazily, retries errors and keeps empty history distinct from not found", async () => {
  const read = vi.spyOn(information, "readMotHistory").mockRejectedValueOnce(new Error("Fixture unavailable"))
    .mockResolvedValueOnce(empty).mockResolvedValueOnce({ ...empty, outcome: "not_found" });
  const view = render(<MotHistory vehicleId="fixture" revision={null} />);
  expect(read).not.toHaveBeenCalled();
  await openHistory();
  expect(await screen.findByRole("alert")).toHaveTextContent("Fixture unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No MOT tests are recorded for this vehicle.")).toBeVisible();
  view.rerender(<MotHistory vehicleId="fixture" revision="updated" />);
  expect(await screen.findByText("DVSA could not find this registration.")).toBeVisible();
});

it("renders missing commercial-vehicle fields and cancels history reads on unmount", async () => {
  const read = vi.spyOn(information, "readMotHistory").mockResolvedValue({ ...empty, total: 1, outcome: "found", freshness: "stale", items: [{ number: null, completed_at: null, result: "FAILED", expiry: null, mileage: null, mileage_unit: null, mileage_read: "NO_ODOMETER", source: "CVS", defects: null }] });
  const view = render(<MotHistory vehicleId="fixture" revision={null} />);
  await openHistory();
  expect(await screen.findByText("Test date unavailable")).toBeVisible();
  expect(screen.getByText("Mileage unavailable")).toBeVisible();
  expect(screen.getByText(/Saved information is stale/)).toBeVisible();
  const signal = read.mock.calls[0][2]?.signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
});
