import { afterEach, expect, it, vi } from "vitest";
// Generated fixture copy: backend tests enforce equality with the canonical report contract.
import contract from "./fixtures/reportPreview.generated.json";
import { reportsApi, type ReportPreviewRequest } from "./reports";

const request: ReportPreviewRequest = {
  person_id: contract.ready.report.person.id, period_start: "2026-10-25T01:30:00", period_end: "2026-10-25T03:00:00",
  include_denied: false, include_snapshots: true, include_confidence: true
};
afterEach(() => vi.unstubAllGlobals());

it("uses the shared authenticated client for paired preview choices and explicit folds", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(contract.ambiguous)))
    .mockResolvedValueOnce(new Response(JSON.stringify(contract.ready)));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  expect(await reportsApi.preview(request, { signal: controller.signal })).toEqual(contract.ambiguous);
  expect(await reportsApi.preview({ ...request, period_start_fold: 1 })).toEqual(contract.ready);
  expect(fetchMock.mock.calls[0]).toEqual(["/api/v1/reports/person-movements/preview", {
    method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include",
    signal: controller.signal, body: JSON.stringify(request)
  }]);
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ period_start: request.period_start, period_start_fold: 1 });
});

it("surfaces the paired gap error and preserves export and historical lookup URLs", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail: contract.gap.detail }), { status: 400 }))
    .mockImplementation(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", fetchMock);
  await expect(reportsApi.preview({ ...request, period_start: contract.gap.period_start })).rejects.toThrow(contract.gap.detail);
  const { start, end } = contract.ready.report.period;
  await reportsApi.export({ ...request, period_start: start, period_end: end });
  expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/reports/person-movements/export");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ period_start: start, period_end: end });
  await reportsApi.load("123/456");
  expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/reports/123%2F456");
  await reportsApi.context();
  expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/reports/context");
});
