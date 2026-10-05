import { describe, expect, it } from "vitest";

import { coerceSettingsPayload } from "./lib/settings";

describe("frontend guardrails", () => {
  it("rejects non-finite numeric dynamic settings", () => {
    expect(() => coerceSettingsPayload({ lpr_debounce_quiet_seconds: "" })).toThrow(
      "lpr_debounce_quiet_seconds must be a finite number."
    );
    expect(() => coerceSettingsPayload({ lpr_similarity_threshold: "NaN" })).toThrow(
      "lpr_similarity_threshold must be a finite number."
    );
    expect(coerceSettingsPayload({ lpr_debounce_quiet_seconds: "2.5" })).toEqual({
      lpr_debounce_quiet_seconds: 2.5
    });
  });

});
