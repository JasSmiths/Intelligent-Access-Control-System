import { expect, test, type Page } from "playwright/test";

const groupName = "Residents and authorised contractors for the west courtyard and service entrance";
const personName = "Alexandra Montgomery-Wellington and household";
const scheduleName = "Weekday morning arrivals and evening departures — authorised maintenance visits";
const group = { id: "directory-group", name: groupName, category: "family", subtype: "Residents", description: "Shared household and service access", people_count: 1 };
const vehicle = { id: "directory-vehicle", registration_number: "AB12 CDE", make: "Land Rover", model: "Range Rover Sport Plug-in Hybrid", color: "Midnight blue", description: "Long wheelbase family vehicle with shared authorised drivers", is_active: true, person_id: "directory-person", person_ids: ["directory-person"], owners: [personName], owner: personName, schedule_id: "directory-schedule", schedule: scheduleName, vehicle_photo_data_url: null, vehicle_photo_url: null };
const motVehicle = { ...vehicle, mot_status: "Valid", mot_expiry: "2027-10-06", mot_source: "dvsa", mot_checked_at: "2026-10-07T10:00:00Z", mot_freshness: "stale", information_checked_at: "2026-10-07T10:00:00Z" };
const person = { id: "directory-person", first_name: "Alexandra", last_name: "Montgomery-Wellington", display_name: personName, group_id: group.id, group: groupName, category: "family", schedule_id: vehicle.schedule_id, schedule: scheduleName, vehicles: [vehicle], is_active: true, profile_photo_data_url: null, profile_photo_url: null, garage_door_entity_ids: ["cover.west_courtyard_service_garage_door"], home_assistant_presence_input_boolean_entity_ids: [] };
const schedule = { id: "directory-schedule", name: scheduleName, description: "Synthetic directory layout fixture", time_blocks: {} };

async function installDirectoryFixtures(page: Page, role = "admin") {
  const unexpected: string[] = [];
  const historyRequests: string[] = [];
  const fixtures: Record<string, unknown> = {
    "/api/v1/auth/status": { setup_required: false, authenticated: true, user: { id: "directory-admin", username: "layout-admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role, is_active: true, preferences: { sidebarCollapsed: false } } },
    "/api/v1/people": { items: [person], total: 1, next_cursor: null },
    "/api/v1/groups": [group],
    "/api/v1/vehicles": { items: [motVehicle], total: 1, next_cursor: null },
    "/api/v1/schedules": [schedule],
    "/api/v1/presence": [],
    "/api/v1/presence/expected-today": { date: "2026-10-04", timezone: "Europe/London", generated_at: "2026-10-04T09:00:00Z", count: 0, learning: false, coverage: { regular_candidates: 0, learned_candidates: 0, learning_population: 0, ratio: 0 }, people: [] },
    "/api/v1/events": [],
    "/api/v1/alerts": [],
    "/api/v1/integrations/gate/status": { configured: false, connected: false, gate_entity_id: null, default_media_player: null, last_gate_state: "unknown", garage_door_entities: [] },
    "/api/v1/maintenance/status": { is_active: false, enabled_by: null, enabled_at: null, source: null, reason: null, duration_seconds: 0, duration_label: null },
    "/api/v1/settings": [],
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/vehicles/directory-vehicle/mot-history") {
      historyRequests.push(request.url());
      const next = new URL(request.url()).searchParams.has("cursor");
      await route.fulfill({ json: { registration_number: vehicle.registration_number, checked_at: "2026-10-07T10:00:00Z", freshness: "stale", outcome: "found", total: 11, next_cursor: next ? null : "fixture-cursor", items: [{ number: next ? "OLDER-TEST" : "LATEST-TEST", completed_at: next ? "2025-10-06T09:00:00Z" : "2026-10-06T09:00:00Z", result: next ? "PASSED" : "FAILED", expiry: next ? "2026-10-05" : null, mileage: next ? null : "52000", mileage_unit: "mi", mileage_read: next ? "NO_ODOMETER" : "READ", source: "dvsa", defects: next ? null : [{ type: "ADVISORY", dangerous: false, text: "Synthetic advisory with a long description to verify wrapping on narrow screens." }] }] } });
      return;
    }
    if (request.method() !== "GET" || !(path in fixtures)) {
      unexpected.push(`${request.method()} ${path}`);
      await route.abort("blockedbyclient");
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixtures[path]) });
  });
  await page.routeWebSocket("**/*", (socket) => socket.close());
  return { unexpected, historyRequests };
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 820, height: 1180 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
  test(`registration refresh fills vehicle details and preserves drafts at ${viewport.width}×${viewport.height}`, async ({ page }, testInfo) => {
    const { unexpected } = await installDirectoryFixtures(page);
    const lookups: unknown[] = [];
    let finishRefresh!: () => void;
    const pendingRefresh = new Promise<void>((resolve) => { finishRefresh = resolve; });
    const provider = { status: "found", checked_at: "2026-10-07T11:00:00Z", retry_at: null, error: null };
    await page.route("**/api/v1/integrations/vehicles/lookup", async (route) => {
      lookups.push(route.request().postDataJSON());
      await pendingRefresh;
      await route.fulfill({ json: { registration_number: "AB12CDE", make: "Toyota", model: "Corolla", colour: "Blue", fuel_type: "Petrol", mot_status: "Not valid", mot_expiry: "2025-10-06", tax_status: "Untaxed", tax_expiry: null, last_dvla_lookup_date: "2026-10-07", providers: { dvla: provider, dvsa: provider } } });
    });
    await page.setViewportSize(viewport);
    await page.goto("/vehicles");
    if (viewport.width === 820) await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    const group = page.locator(".directory-group-header").first();
    if (await group.getAttribute("aria-expanded") === "false") await group.click();
    await page.getByRole("button", { name: "Edit vehicle AB12 CDE" }).click();
    const refresh = page.getByRole("button", { name: "Refresh vehicle info", exact: true });
    await expect(refresh).toBeVisible();
    await expect(page.locator(".vehicle-registration-field")).toContainText("Refresh vehicle info");
    await page.getByLabel("Vehicle Model").fill("Replace this model");
    await page.getByLabel("Friendly description").fill("My retained description");
    const savedCompliance = await page.locator(".vehicle-compliance-card").textContent() ?? "";
    if (viewport.height < 500 || viewport.width < 500) await refresh.click();
    else { await refresh.focus(); await refresh.press("Enter"); }
    await expect(refresh).toBeDisabled();
    await expect(refresh).toHaveText("Refreshing...");
    await expect(page.getByRole("button", { name: "Save Changes" })).toBeDisabled();
    finishRefresh();
    await expect(page.getByLabel("Vehicle Make")).toHaveValue("Toyota");
    await expect(page.getByLabel("Colour")).toHaveValue("Blue");
    await expect(page.getByLabel("Vehicle Model")).toHaveValue("Corolla");
    await expect(page.getByLabel("Friendly description")).toHaveValue("My retained description");
    await expect(page.getByLabel(/^Vehicle Registration/)).toHaveValue(vehicle.registration_number);
    await expect(page.getByLabel("Access Schedule")).toHaveValue(schedule.id);
    await expect(refresh).toBeEnabled();
    await expect(page.locator(".vehicle-compliance-card")).toContainText("Valid");
    await expect(page.locator(".vehicle-compliance-card")).not.toContainText("Untaxed");
    await expect(page.locator(".vehicle-compliance-card")).toHaveText(savedCompliance);
    await expect(page.getByRole("button", { name: "Refresh vehicle information", exact: true })).toBeEnabled();
    await expect(page.getByRole("dialog").getByRole("status")).toContainText("Save changes to keep them");
    expect(lookups).toEqual([{ registration_number: "AB12CDE" }]);
    await refresh.evaluate((element) => element.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: testInfo.outputPath(`vehicle-info-refresh-${viewport.width}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    expect(unexpected).toEqual([]);
  });

  test(`MOT history is lazy, paged and preserves drafts at ${viewport.width}×${viewport.height}`, async ({ page }, testInfo) => {
    const { unexpected, historyRequests } = await installDirectoryFixtures(page);
    await page.setViewportSize(viewport);
    await page.goto("/vehicles");
    const group = page.locator(".directory-group-header").first();
    if (await group.getAttribute("aria-expanded") === "false") await group.click();
    await page.getByRole("button", { name: "Edit vehicle AB12 CDE" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByLabel("Vehicle Model").fill("My retained model");
    expect(historyRequests).toHaveLength(0);
    await page.getByText("MOT test history", { exact: true }).click();
    await expect(page.getByText("Saved information is stale.", { exact: false })).toBeVisible();
    await expect(page.getByText("Failed", { exact: true })).toBeVisible();
    await page.getByText("Test details (1 defects or advisories)").click();
    await expect(page.getByText("Synthetic advisory", { exact: false })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`mot-history-${viewport.width}.png`), fullPage: true });
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByText("Mileage unavailable", { exact: false })).toBeVisible();
    expect(historyRequests).toHaveLength(2);
    expect(historyRequests[1]).toContain("cursor=fixture-cursor");
    await expect(page.getByLabel("Vehicle Model")).toHaveValue("My retained model");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await page.getByRole("button", { name: "Previous", exact: true }).click();
    await expect(page.getByText("Failed", { exact: true })).toBeVisible();
    expect(unexpected).toEqual([]);
  });
}


test("history errors recover without provider calls in the dark editor", async ({ page }) => {
  const { unexpected } = await installDirectoryFixtures(page);
  let failed = false;
  await page.route("**/api/v1/vehicles/directory-vehicle/mot-history?*", async (route) => {
    if (!failed) { failed = true; await route.fulfill({ status: 503, json: { detail: "Stored history temporarily unavailable" } }); }
    else await route.fallback();
  });
  await page.goto("/vehicles");
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
  const group = page.locator(".directory-group-header").first();
  if (await group.getAttribute("aria-expanded") === "false") await group.click();
  await page.getByRole("button", { name: "Edit vehicle AB12 CDE" }).click();
  await page.getByText("MOT test history", { exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Stored history temporarily unavailable");
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Failed", { exact: true })).toBeVisible();
  expect(unexpected).toEqual([]);
});

test("standard users can read history without starting provider lookups or refreshes", async ({ page }) => {
  const { unexpected, historyRequests } = await installDirectoryFixtures(page, "standard");
  await page.goto("/vehicles");
  await expect(page.getByRole("heading", { name: "Vehicles", exact: true })).toBeVisible();
  const group = page.locator(".directory-group-header").first();
  if (await group.getAttribute("aria-expanded") === "false") await group.click();
  await expect(page.getByText("AB12 CDE", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Edit vehicle AB12 CDE" }).click();
  await expect(page.getByRole("button", { name: "Refresh vehicle information", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh vehicle info", exact: true })).toHaveCount(0);
  await page.getByText("MOT test history", { exact: true }).click();
  await expect(page.getByText("Failed", { exact: true })).toBeVisible();
  expect(historyRequests).toHaveLength(1);
  await page.getByLabel(/^Vehicle Registration/).fill("ZZSYN99");
  await expect(page.getByText("MOT test history", { exact: true })).toHaveCount(0);
  await page.waitForTimeout(1000); // Pass the lookup debounce and prove the Admin-only call stays absent.
  expect(unexpected).toEqual([]);
});
