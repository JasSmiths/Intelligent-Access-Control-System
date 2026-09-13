import { afterEach, expect, it, vi } from "vitest";
import contract from "./fixtures/approvalInspection.generated.json";
import { inspectChatApproval } from "./chat";

afterEach(() => vi.unstubAllGlobals());
it.each(Object.entries(contract))("reads the paired %s approval state without confirming", async (_status, response) => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response)));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  expect(await inspectChatApproval("session synthetic", "confirm/synthetic", { signal: controller.signal })).toEqual(response);
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/ai/chat/approvals/confirm%2Fsynthetic?session_id=session+synthetic", {
    credentials: "include", signal: controller.signal
  });
});

it("discovers requester approvals with an opaque encoded cursor using GET only", async () => {
  const { listChatApprovals } = await import("./chat");
  const pages = await import("./fixtures/approvalPages.generated.json");
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(pages.default.first_page)));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  expect(await listChatApprovals("confirm/synthetic cursor", { signal: controller.signal })).toEqual(pages.default.first_page);
  expect(fetchMock).toHaveBeenCalledExactlyOnceWith("/api/v1/ai/chat/approvals?limit=25&before_id=confirm%2Fsynthetic+cursor", {
    credentials: "include", signal: controller.signal
  });
});
