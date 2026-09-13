import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { schedulesApi } from "../../api/schedules";
import type { Schedule } from "../../api/types";
import { emptyScheduleBlocks, normalizeScheduleBlocks, scheduleBlocksToSlots, slotsToScheduleBlocks } from "./model";
import { ScheduleEditor } from "./ScheduleEditor";
import { WeeklyScheduleGrid } from "./WeeklyScheduleGrid";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const schedule: Schedule = { id: "schedule-one", name: "Weekday access", description: null, time_blocks: { "0": [{ start: "09:00", end: "17:00" }] }, created_at: "", updated_at: "" };

it("preserves canonical weekly intervals, midnight, and the existing end-of-day convention", () => {
  const blocks = { ...emptyScheduleBlocks(), "0": [{ start: "09:00", end: "12:00" }, { start: "11:30", end: "17:00" }], "6": [{ start: "23:30", end: "23:59" }] };
  expect(normalizeScheduleBlocks(blocks)).toEqual({ ...emptyScheduleBlocks(), "0": [{ start: "09:00", end: "17:00" }], "6": [{ start: "23:30", end: "24:00" }] });
  const all = Object.fromEntries(Array.from({ length: 7 }, (_, day) => [day, [{ start: "00:00", end: "24:00" }]]));
  expect(scheduleBlocksToSlots(all).size).toBe(336);
  expect(slotsToScheduleBlocks(scheduleBlocksToSlots(all))).toEqual(all);
});
it("edits a real weekly selection through presets without changing other form ownership", () => {
  function Grid() { const [value, setValue] = React.useState(emptyScheduleBlocks()); return <><WeeklyScheduleGrid value={value} onChange={setValue} /><output data-testid="value">{JSON.stringify(value)}</output></>; }
  render(<Grid />);
  fireEvent.click(screen.getByRole("button", { name: "24/7" }));
  expect(screen.getByRole("button", { name: "Sunday 23:30-24:00 allowed" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Clear$/ }));
  expect(JSON.parse(screen.getByTestId("value").textContent!)).toEqual(emptyScheduleBlocks());
  fireEvent.click(screen.getByRole("button", { name: "Weekdays" }));
  expect(JSON.parse(screen.getByTestId("value").textContent!)["6"]).toEqual([]);
  expect(screen.getByRole("button", { name: "Monday 09:00-09:30 allowed" })).toBeInTheDocument();
});
it("saves the edited payload and keeps the form available after a failed confirmation", async () => {
  vi.spyOn(schedulesApi, "dependencies").mockResolvedValue({ people: [], vehicles: [], doors: [] });
  const save = vi.spyOn(schedulesApi, "save").mockRejectedValueOnce(new Error("Confirmation denied")).mockResolvedValue(schedule);
  const onSaved = vi.fn().mockResolvedValue(undefined);
  render(<ScheduleEditor mode="edit" schedule={schedule} onClose={() => {}} onSaved={onSaved} setPageError={() => {}} />);
  fireEvent.change(screen.getByLabelText("Schedule name"), { target: { value: "Updated" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Schedule" }));
  await screen.findByText("Confirmation denied");
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Schedule name")).toHaveValue("Updated");
  fireEvent.click(screen.getByRole("button", { name: "Save Schedule" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(save).toHaveBeenLastCalledWith({ name: "Updated", description: null, time_blocks: normalizeScheduleBlocks(schedule.time_blocks) }, schedule);
});
it("aborts dependency reads when the editor closes", () => {
  const read = vi.spyOn(schedulesApi, "dependencies").mockImplementation(() => new Promise(() => {}));
  const { unmount } = render(<ScheduleEditor mode="edit" schedule={schedule} onClose={() => {}} onSaved={async () => {}} setPageError={() => {}} />);
  const signal = read.mock.calls[0][1]?.signal;
  expect(signal?.aborted).toBe(false);
  unmount();
  expect(signal?.aborted).toBe(true);
});

it("refreshes the schedule default policy through the existing settings owner", async () => {
  const { api } = await import("../../api/client");
  const { SchedulesView } = await import("./SchedulesView");
  const read = vi.spyOn(api, "get").mockResolvedValue([]);
  const { rerender } = render(<SchedulesView schedules={[]} query="" refresh={async () => {}} refreshToken={0} />);
  await waitFor(() => expect(read).toHaveBeenCalledOnce());
  rerender(<SchedulesView schedules={[]} query="" refresh={async () => {}} refreshToken={1} />);
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  expect(read.mock.calls.map(([url]) => url)).toEqual(["/api/v1/settings?category=access", "/api/v1/settings?category=access"]);
});
