import { expect, test, type Page } from "playwright/test";
const names = ["Alpha", "Zed", "alpha", "Ána", "Éva", "😀 Person"];
async function fixture(page: Page) {
  const now = new Date().toISOString(); const reads: string[] = []; const unexpected: string[] = [];
  const inside = names.map((display_name, index) => ({ person_id: `inside-${index}`, display_name, state: "present", last_changed_at: now }));
  const expected = names.map((name, index) => ({ person_id: `expected-${index}`, display_name: `Expected ${name}`, confidence: .8, evidence_days: 12, observed_weekdays: 6, typical_arrival: "08:00", typical_departure: "17:00" }));
  const directory = [...inside, ...expected].map((person) => ({ id: person.person_id, display_name: person.display_name, first_name: person.display_name, last_name: "", is_active: true, profile_photo_data_url: null, profile_photo_url: null, vehicles: [] }));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (request.method() !== "GET") { unexpected.push(`${request.method()} ${path}`); await route.abort("blockedbyclient"); return; }
    reads.push(`${path}${url.search}`); let body: unknown;
    switch (path) {
      case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", username: "admin", first_name: "Synthetic", full_name: "Synthetic Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
      case "/api/v1/alerts": body = []; break;
      case "/api/v1/maintenance/status": body = { is_active: false }; break;
      case "/api/v1/integrations/gate/status": body = { configured: false }; break;
      case "/api/v1/presence/summary": body = { counts: { total: 90, present: 19, exited: 60, unknown: 11 }, inside: { people: inside, hidden: 13 } }; break;
      case "/api/v1/presence/expected-today/summary": body = { date: now.slice(0, 10), timezone: "Europe/London", generated_at: now, count: 40, hidden: 34, learning: false, coverage: { regular_candidates: 40, learned_candidates: 40, learning_population: 40, ratio: 1 }, people: expected }; break;
      case "/api/v1/events": body = [1, 2].map((index) => ({ id: `exit-${index}`, registration_number: `EXIT${index}`, direction: "exit", decision: "granted", occurred_at: now, source: "synthetic", confidence: .99, timing_classification: "on_time", anomaly_count: 0, visitor_pass_id: `guest-${index}`, visitor_name: `Synthetic Guest ${index}`, visitor_pass_mode: "duration" })); break;
      case "/api/v1/vehicles": body = { items: [], total: url.searchParams.has("active") ? 2000 : 0, next_cursor: null }; break;
      case "/api/v1/people": {
        const ids = url.searchParams.getAll("ids");
        if (!ids.length || ids.length > 12) unexpected.push(`Unbounded people read ${url.search}`);
        body = { items: directory.filter((person) => ids.includes(person.id)), total: ids.length, next_cursor: null }; break;
      }
      default: unexpected.push(`GET ${path}`); await route.fulfill({ status: 501, json: { detail: "Unexpected inert read" } }); return;
    }
    await route.fulfill({ json: body });
  });
  await page.routeWebSocket("**/*", (socket) => socket.close());
  return { reads, unexpected };
}
for (const width of [390, 1440, 900]) {
  test(`bounded presence retains global counts, names and recent exits at ${width}px`, async ({ page }, testInfo) => {
    const network = await fixture(page); await page.setViewportSize({ width, height: 1000 }); await page.goto("/");
    if (width === 900) await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    const inside = page.getByLabel("People inside now", { exact: true });
    await expect(inside.locator(":scope > strong")).toHaveText("19");
    await inside.click(); await expect(inside.getByRole("tooltip")).toBeVisible();
    await expect(inside.locator(".expected-tooltip-person strong")).toHaveText(names);
    await expect(inside.getByText("+13 more inside", { exact: true })).toBeVisible();
    await expect(inside.getByText("19 people", { exact: true })).toBeVisible();
    const forecast = page.getByLabel("Expected arrivals today", { exact: true });
    await forecast.click(); await expect(forecast.locator(":scope > strong")).toHaveText("40");
    await expect(forecast.getByText("+34 more expected", { exact: true })).toBeVisible();
    const exits = page.getByLabel("People who exited today", { exact: true });
    await expect(exits.locator(":scope > strong")).toHaveText("2");
    await expect(page.locator(".presence-legend")).toContainText("60");
    await expect.poll(() => network.reads.filter((path) => path.startsWith("/api/v1/people?")).length).toBeGreaterThan(0);
    expect(network.reads.some((path) => path === "/api/v1/presence" || path === "/api/v1/presence/expected-today")).toBe(false);
    expect(network.unexpected).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await testInfo.attach("bounded-read-contract", { body: JSON.stringify(network, null, 2), contentType: "application/json" });
    await page.screenshot({ path: testInfo.outputPath(`presence-summary-${width}.png`), fullPage: true });
  });
}
