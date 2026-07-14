import { afterEach, describe, expect, it } from "vitest";
import { evidenceJson, redactEvidenceForDisplay } from "./redaction";
import {
  DEFAULT_INVESTIGATION_QUERY,
  queryToSearchParams,
  readInvestigationQuery,
  writeInvestigationQuery,
  zonedWallTimeToIso
} from "./query";

describe("investigation query state", () => {
  afterEach(() => window.history.replaceState({}, "", "/"));

  it("round-trips time, device, automation, outcome and text filters through the URL", () => {
    const query = {
      ...DEFAULT_INVESTIGATION_QUERY,
      range: "yesterday" as const,
      device: "main-garage",
      automation: "open-on-arrival",
      outcome: "blocked",
      q: "schedule condition",
      includeRoutine: true
    };
    const params = queryToSearchParams(query);
    expect(params.get("device")).toBe("main-garage");
    expect(params.get("automation")).toBe("open-on-arrival");
    expect(params.get("outcome")).toBe("blocked");

    window.history.replaceState({}, "", `/logs?${params}`);
    expect(readInvestigationQuery()).toEqual(query);

    writeInvestigationQuery({ ...query, trace: "trace-123" });
    expect(new URLSearchParams(window.location.search).get("trace")).toBe("trace-123");
  });

  it("converts custom wall time using the site timezone instead of the browser timezone", () => {
    expect(zonedWallTimeToIso("2026-07-14T22:47", "Europe/London")).toBe("2026-07-14T21:47:00.000Z");
    expect(zonedWallTimeToIso("2026-01-14T22:47", "Europe/London")).toBe("2026-01-14T22:47:00.000Z");
  });
});

describe("investigation evidence redaction", () => {
  it("redacts sensitive keys and bearer values before rendering", () => {
    const redacted = redactEvidenceForDisplay({
      safe: "visible",
      password: "never-render",
      nested: { authorization: "Bearer abc.def", note: "Bearer secret-token" }
    });
    const rendered = evidenceJson(redacted);
    expect(rendered).toContain("visible");
    expect(rendered).toContain("[REDACTED]");
    expect(rendered).not.toContain("never-render");
    expect(rendered).not.toContain("abc.def");
    expect(rendered).not.toContain("secret-token");
  });
});
