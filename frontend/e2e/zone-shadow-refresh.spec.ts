import { expect, test, type Page } from "playwright/test";

const now = "2026-10-20T12:00:00Z";
const vehicle = { id: "vehicle", registration_number: "AB12 CDE", make: "Ford", model: "Original model", color: "Blue", description: "", is_active: true, person_id: null, person_ids: [], owners: [], owner: null, schedule_id: null, schedule: null, vehicle_photo_url: null };

async function fixtures(page: Page) {
  let revision = 1;
  let connected = false;
  let send: () => void = () => { throw new Error("Synthetic stream not connected"); };
  const reads: string[] = [];
  const unexpected: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") {
      unexpected.push(`${request.method()} ${url.pathname}`);
      await route.abort("blockedbyclient");
      return;
    }
    reads.push(`${url.pathname}${url.search}`);
    let body: unknown;
    switch (url.pathname) {
      case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", username: "admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
      case "/api/v1/settings": case "/api/v1/alerts": case "/api/v1/groups": case "/api/v1/schedules": body = []; break;
      case "/api/v1/maintenance/status": body = { is_active: false }; break;
      case "/api/v1/integrations/gate/status": body = { configured: false, connected: false, gate_entity_id: null, last_gate_state: "unknown", garage_door_entities: [] }; break;
      case "/api/v1/integrations/home-assistant/entities": body = { configured: false, connected: false, covers: [], input_booleans: [], mobile_app_notify_services: [] }; break;
      case "/api/v1/people": body = { items: [], total: 0, next_cursor: null }; break;
      case "/api/v1/vehicles": body = { items: [vehicle], total: 1, next_cursor: null }; break;
      case "/api/v1/diagnostics/lpr-zone-shadow":
        expect(url.searchParams.get("limit")).toBe("300");
        body = { observations: [{ id: `observation-${revision}`, access_event_id: null, registration_number: `SYN000${revision}`, detected_registration_number: null, source: "synthetic", observed_at: now, time_of_day: "day", time_of_day_source: "synthetic", zone_id: "driveway", zone_name: "Driveway", zone_status: "enter", zone_level: 95, actual_decision: null, actual_direction: null, shadow_decision: "allow", shadow_reason: `Synthetic revision ${revision}`, would_suppress: false, details: {}, created_at: now }] };
        break;
      default: unexpected.push(`GET ${url.pathname}`); await route.abort("blockedbyclient"); return;
    }
    await route.fulfill({ json: body });
  });
  await page.routeWebSocket("**/*", (socket) => {
    connected = true;
    send = () => socket.send(JSON.stringify({ type: "lpr_zone_shadow.observed", payload: {}, created_at: now }));
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "client.ping") socket.send(JSON.stringify({ type: "connection.pong", payload: { id: message.payload.id } }));
    });
  });
  const observationReads = () => reads.filter((path) => path.startsWith("/api/v1/diagnostics/lpr-zone-shadow")).length;
  const settingsReads = () => reads.filter((path) => path.startsWith("/api/v1/settings")).length;
  return { reads, unexpected, observationReads, settingsReads,
    connected: () => connected,
    resetConnection: () => { connected = false; },
    revise: (next: number) => { revision = next; },
    send: () => send() };
}

test("zone observations refresh from committed hints and visible repair while unrelated drafts survive", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.clock.install({ time: new Date(now) });
  const fixture = await fixtures(page);
  const { reads, unexpected, observationReads, settingsReads } = fixture;
  const pause = async () => { await page.clock.pauseAt(new Date(await page.evaluate(() => Date.now()) + 1000)); };
  const advance = async (milliseconds: number) => {
    // Give routed socket pongs a browser/driver turn before the next virtual
    // second; protocol timeout races are covered by the transport's own suite.
    for (let remaining = milliseconds; remaining > 0; remaining -= 1000) {
      await page.clock.runFor(Math.min(remaining, 1000));
      await page.evaluate(() => Promise.resolve());
    }
  };
  await page.goto("/settings/zones");
  await expect(page.getByText("SYN0001", { exact: true })).toBeVisible();
  await expect.poll(fixture.connected).toBe(true);
  await pause();
  const initialObservations = observationReads();
  const initialSettings = settingsReads();
  await advance(15_000);
  expect(observationReads()).toBe(initialObservations);
  fixture.revise(2);
  fixture.send();
  await expect(page.getByText("SYN0002", { exact: true })).toBeVisible();
  expect(observationReads()).toBe(initialObservations + 1);
  expect(settingsReads()).toBe(initialSettings);
  await expect(page.getByRole("button", { name: "Shadow", exact: true })).toHaveClass("active");
  // No hint is sent for revision three: the sixty-second visible repair must recover it.
  fixture.revise(3);
  await advance(46_000);
  await expect(page.getByText("SYN0003", { exact: true })).toBeVisible();
  expect(observationReads()).toBe(initialObservations + 2);
  expect(settingsReads()).toBe(initialSettings);
  await page.clock.resume();
  // Zones has no editable draft; its mode buttons persist immediately. Verify
  // cross-feature hints leave a real dirty vehicle editor and its fields intact.
  fixture.resetConnection();
  await page.goto("/vehicles");
  await expect.poll(fixture.connected).toBe(true);
  const group = page.locator(".directory-group-header").first();
  await expect(group).toBeVisible();
  if (await group.getAttribute("aria-expanded") === "false") await group.click();
  await page.getByRole("button", { name: "Edit vehicle AB12 CDE" }).click();
  await page.getByLabel("Friendly description").fill("Retained zone-hint draft");
  await pause();
  const beforeUnmountedHint = observationReads();
  fixture.send();
  await advance(65_000);
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("Friendly description")).toHaveValue("Retained zone-hint draft");
  await expect(page.getByLabel("Vehicle Model")).toHaveValue("Original model");
  expect(observationReads()).toBe(beforeUnmountedHint);
  expect(unexpected).toEqual([]);
  await page.clock.resume();
  await testInfo.attach("zone-shadow-request-evidence", { body: JSON.stringify({ reads, unexpected, limit: 300, repairMilliseconds: 60_000 }, null, 2), contentType: "application/json" });
});


test("zone observations render with the real browser clock", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const fixture = await fixtures(page);
  await page.goto("/settings/zones");
  await expect(page.getByText("SYN0001", { exact: true })).toBeVisible();
  await expect.poll(fixture.connected).toBe(true);
  await expect(page.getByRole("button", { name: "Shadow", exact: true })).toHaveClass("active");
  await page.screenshot({ path: testInfo.outputPath("zone-shadow-real-clock.png"), fullPage: true, timeout: 10_000 });
  expect(fixture.unexpected).toEqual([]);
});
