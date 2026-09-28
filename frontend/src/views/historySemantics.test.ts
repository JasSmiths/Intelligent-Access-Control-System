import { expect, it } from "vitest";

import { movementSagaDisplay } from "../lib/format";

it("never calls an unknown movement confirmed from a presence flag alone", () => {
  expect(movementSagaDisplay({ state: "legacy_unknown", presence_committed: true }))
    .toEqual({ label: "Legacy Unknown", tone: "gray" });
  expect(movementSagaDisplay({ state: "completed", presence_committed: false }))
    .toEqual({ label: "Confirmed", tone: "green" });
});
