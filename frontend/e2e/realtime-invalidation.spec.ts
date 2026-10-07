import { expect, test, type Page } from "playwright/test";

const now = "2026-10-07T12:00:00Z";
const vehicle = { id: "vehicle", registration_number: "AB12 CDE", make: "Ford", model: "Original model", color: "Blue", description: "", is_active: true, person_id: null, person_ids: [], owners: [], owner: null, schedule_id: null, schedule: null, vehicle_photo_url: null };
const person = { id: "person", first_name: "Ada", last_name: "Resident", display_name: "Ada Resident", group_id: null, group: null, category: null, schedule_id: null, schedule: null, vehicles: [], is_active: true, profile_photo_url: null, garage_door_entity_ids: [], home_assistant_presence_input_boolean_entity_ids: [] };
const event = { id: "event", registration_number: "AB12 CDE", direction: "entry", decision: "granted", confidence: .98, occurred_at: now, anomaly_count: 0, visitor_name: null, snapshot_url: null, movement_saga: null };

async function fixtures(page: Page) {
  const unexpected: string[] = [];
  let changed = false;
  let connected = false;
  let send: (type: string, payload: Record<string, unknown>) => void = () => { throw new Error("Fixture stream has not connected"); };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") { unexpected.push(`${request.method()} ${url.pathname}`); await route.abort("blockedbyclient"); return; }
    if (changed && url.pathname === "/api/v1/groups") { await route.fulfill({ status: 503, json: { detail: "Synthetic unrelated groups outage" } }); return; }
    let body: unknown;
    switch (url.pathname) {
      case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", username: "fixture-admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
      case "/api/v1/people": body = { items: [{ ...person, display_name: changed ? "Ada Updated" : person.display_name }], total: 1, next_cursor: null }; break;
      case "/api/v1/vehicles": body = { items: [{ ...vehicle, model: changed ? "Updated model" : vehicle.model }], total: 1, next_cursor: null }; break;
      case "/api/v1/events": body = [event]; break;
      case "/api/v1/events/history": body = { items: [{ ...event, id: url.searchParams.has("cursor") ? "older" : "event", registration_number: url.searchParams.has("cursor") ? "OLDER" : changed ? "LATEST" : "ORIGINAL" }], next_cursor: url.searchParams.has("cursor") ? null : "older", as_of: now }; break;
      case "/api/v1/maintenance/status": body = { is_active: false, enabled_by: null, enabled_at: null, source: null, reason: null, duration_seconds: 0, duration_label: null }; break;
      case "/api/v1/integrations/gate/status": body = { configured: false, connected: false, gate_entity_id: null, default_media_player: null, last_gate_state: "unknown", garage_door_entities: [] }; break;
      case "/api/v1/groups": case "/api/v1/schedules": case "/api/v1/alerts": case "/api/v1/settings": body = []; break;
      default: unexpected.push(`GET ${url.pathname}`); await route.abort("blockedbyclient"); return;
    }
    await route.fulfill({ json: body });
  });
  // This intercepted socket has no server connection; every event is synthetic.
  await page.routeWebSocket("**/*", (socket) => {
    connected = true;
    send = (type, payload) => socket.send(JSON.stringify({ type, payload, created_at: now }));
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw));
      if (request.type === "client.ping") send("connection.pong", { id: request.payload.id });
    });
  });
  return {
    unexpected,
    async update(type: string, payload: Record<string, unknown> = {}) {
      await expect.poll(() => connected).toBe(true);
      changed = true;
      send(type, payload);
    }
  };
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`people refresh from committed realtime updates despite another read failing at ${viewport.width}px`, async ({ page }, testInfo) => {
    const fixture = await fixtures(page);
    await page.setViewportSize(viewport);
    await page.goto("/people");
    const group = page.locator(".directory-group-header").first();
    await expect(group).toBeVisible();
    if (await group.getAttribute("aria-expanded") === "false") await group.click();
    await expect(page.getByRole("button", { name: "Edit person Ada Resident" })).toBeVisible();
    await fixture.update("audit.log.created", { log: { action: "person.update" } });
    await expect(page.getByRole("button", { name: "Edit person Ada Updated" })).toBeVisible({ timeout: 10_000 });
    await page.screenshot({ path: testInfo.outputPath(`people-realtime-${viewport.width}.png`), fullPage: true });
    expect(fixture.unexpected).toEqual([]);
  });

  test(`vehicle information refresh preserves the open dirty editor at ${viewport.width}px`, async ({ page }, testInfo) => {
    const fixture = await fixtures(page);
    await page.setViewportSize(viewport);
    await page.goto("/vehicles");
    const group = page.locator(".directory-group-header").first();
    await expect(group).toBeVisible();
    if (await group.getAttribute("aria-expanded") === "false") await group.click();
    const row = page.getByRole("button", { name: "Edit vehicle AB12 CDE" });
    await expect(row).toContainText("Original model");
    await row.click();
    await page.getByLabel("Friendly description").fill("My retained draft");
    await fixture.update("vehicle_information.updated", { vehicle_id: vehicle.id });
    await expect(row).toContainText("Updated model", { timeout: 10_000 });
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByLabel("Friendly description")).toHaveValue("My retained draft");
    await expect(page.getByLabel("Vehicle Model")).toHaveValue("Original model");
    await page.screenshot({ path: testInfo.outputPath(`vehicle-draft-realtime-${viewport.width}.png`), fullPage: true });
    expect(fixture.unexpected).toEqual([]);
  });

  test(`events retain page two and offer the latest filtered activity at ${viewport.width}px`, async ({ page }, testInfo) => {
    const fixture = await fixtures(page);
    await page.setViewportSize(viewport);
    await page.goto("/events");
    await page.getByRole("combobox", { name: "Decision", exact: true }).selectOption("granted");
    await expect(page.locator(".events-table-card").getByText("ORIGINAL", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect(page.locator(".events-table-card").getByText("OLDER", { exact: true })).toBeVisible();
    await fixture.update("access_event.finalized", { event_id: "latest" });
    const activity = page.getByRole("button", { name: "New activity available. Return to latest." });
    await expect(activity).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".events-table-card").getByText("OLDER", { exact: true })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Decision", exact: true })).toHaveValue("granted");
    await activity.click();
    await expect(page.locator(".events-table-card").getByText("LATEST", { exact: true })).toBeVisible();
    await expect(activity).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`events-realtime-${viewport.width}.png`), fullPage: true });
    expect(fixture.unexpected).toEqual([]);
  });
}
