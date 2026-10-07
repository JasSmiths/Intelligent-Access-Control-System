import reportContract from "../src/api/fixtures/reportPreview.generated.json" with { type: "json" };
import { expect, test, type Page } from "playwright/test";

const now = "2026-10-07T12:00:00Z";
const remotePass = { id: "visitor-after-500", visitor_name: "Remote Visitor", visitor_phone: "07700123999", number_plate: null, pass_type: "one-time", status: "active", updated_at: now };
async function reportFixtures(page: Page) {
  const unexpected: string[] = []; const reads: string[] = []; const previews: Record<string, unknown>[] = [];
  let send = (_type: string) => { throw new Error("Synthetic stream not connected"); };
  let releasePreview!: () => void;
  const firstPreview = new Promise<void>((resolve) => { releasePreview = resolve; });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/v1/reports/person-movements/preview") {
      previews.push(request.postDataJSON());
      if (previews.length === 1) await firstPreview;
      const ready = reportContract.ready;
      await route.fulfill({ json: { ...ready, report: { ...ready.report, subject_type: "visitor_pass", person: { ...ready.report.person, id: remotePass.id, display_name: remotePass.visitor_name }, events: [] } } });
      return;
    }
    if (request.method() !== "GET") { unexpected.push(`${request.method()} ${url.pathname}`); await route.abort("blockedbyclient"); return; }
    reads.push(`${url.pathname}${url.search}`);
    let body: unknown;
    switch (url.pathname) {
      case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", username: "admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
      case "/api/v1/alerts": body = []; break;
      case "/api/v1/maintenance/status": body = { is_active: false }; break;
      case "/api/v1/reports/context": body = { site_timezone: "Europe/London", now }; break;
      case "/api/v1/people": body = { items: [], total: 501, next_cursor: null }; break;
      case "/api/v1/visitor-passes": body = url.searchParams.get("q") === remotePass.visitor_phone ? [remotePass] : []; break;
      case `/api/v1/visitor-passes/${remotePass.id}`: body = remotePass; break;
      default: unexpected.push(`GET ${url.pathname}`); await route.abort("blockedbyclient"); return;
    }
    await route.fulfill({ json: body });
  });
  await page.routeWebSocket("**/*", (socket) => {
    send = (type) => socket.send(JSON.stringify({ type, payload: {}, created_at: now }));
    socket.onMessage((raw) => { const message = JSON.parse(String(raw)); if (message.type === "client.ping") socket.send(JSON.stringify({ type: "connection.pong", payload: { id: message.payload.id } })); });
  });
  return { reads, previews, unexpected, releasePreview, burst: () => { for (let index = 0; index < 100; index++) send("vehicle_information.updated"); } };
}
for (const width of [390, 1440]) {
  test(`Reports uses remote phone search and coalesces actual preview reads at ${width}px`, async ({ page }, testInfo) => {
    const fixture = await reportFixtures(page); await page.setViewportSize({ width, height: 900 });
    await page.goto("/reports");
    const subject = page.getByRole("combobox");
    await expect(subject).toBeVisible();
    expect(fixture.reads.filter((path) => path.startsWith("/api/v1/people") || path.startsWith("/api/v1/visitor-passes"))).toEqual([]);
    await subject.fill(remotePass.visitor_phone);
    await page.getByRole("option", { name: /Remote Visitor/ }).click();
    await expect.poll(() => fixture.previews.length).toBe(1);
    expect(fixture.previews[0]).toMatchObject({ visitor_pass_id: remotePass.id });
    expect(fixture.reads).toContain(`/api/v1/visitor-passes?limit=8&q=${remotePass.visitor_phone}`);
    expect(fixture.reads.some((path) => path.includes("limit=500"))).toBe(false);
    fixture.burst();
    // The first real HTTP read remains in flight; all 100 invalidations share one trailing read.
    expect(fixture.previews).toHaveLength(1);
    fixture.releasePreview();
    await expect.poll(() => fixture.previews.length, { timeout: 10_000 }).toBe(2);
    await expect(page.getByRole("button", { name: "Export PDF", exact: true })).toBeEnabled();
    await expect(subject).toHaveValue(remotePass.visitor_name);
    // Selected-detail hydration returns new objects without changing preview identity.
    expect(fixture.previews).toHaveLength(2);
    expect(fixture.reads.filter((path) => path.includes(`/visitor-passes/${remotePass.id}`))).toHaveLength(2);
    expect(fixture.unexpected).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`reports-feature-read-${width}.png`), fullPage: true });
  });
}
