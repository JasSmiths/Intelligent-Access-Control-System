import { describe, expect, it } from "vitest";

import { csvCell } from "./utils";

describe("log export CSV cells", () => {
  it("neutralizes spreadsheet formulas while preserving ordinary text", () => {
    expect(csvCell("=SUM(A1:A2)")).toBe("\"'=SUM(A1:A2)\"");
    expect(csvCell("+cmd")).toBe("\"'+cmd\"");
    expect(csvCell("-cmd")).toBe("\"'-cmd\"");
    expect(csvCell("@cmd")).toBe("\"'@cmd\"");
    expect(csvCell("normal value")).toBe("\"normal value\"");
  });

  it("quotes embedded double quotes", () => {
    expect(csvCell('He said "open"')).toBe('"He said ""open"""');
  });
});
