import { expect, test, type Locator, type Page } from "playwright/test";

const groupName = "Residents and authorised contractors for the west courtyard and service entrance";
const personName = "Alexandra Montgomery-Wellington and household";
const scheduleName = "Weekday morning arrivals and evening departures — authorised maintenance visits";
const group = { id: "directory-group", name: groupName, category: "family", subtype: "Residents", description: "Shared household and service access", people_count: 1 };
const vehicle = { id: "directory-vehicle", registration_number: "AB12 CDE", make: "Land Rover", model: "Range Rover Sport Plug-in Hybrid", color: "Midnight blue", description: "Long wheelbase family vehicle with shared authorised drivers", is_active: true, person_id: "directory-person", person_ids: ["directory-person"], owners: [personName], owner: personName, schedule_id: "directory-schedule", schedule: scheduleName, vehicle_photo_data_url: null, vehicle_photo_url: null };
const person = { id: "directory-person", first_name: "Alexandra", last_name: "Montgomery-Wellington", display_name: personName, group_id: group.id, group: groupName, category: "family", schedule_id: vehicle.schedule_id, schedule: scheduleName, vehicles: [vehicle], is_active: true, profile_photo_data_url: null, profile_photo_url: null, garage_door_entity_ids: ["cover.west_courtyard_service_garage_door"], home_assistant_presence_input_boolean_entity_ids: [] };
const schedule = { id: "directory-schedule", name: scheduleName, description: "Synthetic directory layout fixture", time_blocks: {} };

async function installDirectoryFixtures(page: Page) {
  const unexpected: string[] = [];
  const fixtures: Record<string, unknown> = {
    "/api/v1/auth/status": { setup_required: false, authenticated: true, user: { id: "directory-admin", username: "layout-admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } },
    "/api/v1/people": { items: [person], total: 1, next_cursor: null },
    "/api/v1/groups": [group],
    "/api/v1/vehicles": { items: [vehicle], total: 1, next_cursor: null },
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
    if (request.method() !== "GET" || !(path in fixtures)) {
      unexpected.push(`${request.method()} ${path}`);
      await route.abort("blockedbyclient");
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixtures[path]) });
  });
  await page.routeWebSocket("**/*", (socket) => socket.close());
  return unexpected;
}

async function increaseDirectoryText(page: Page, factor: number) {
  await page.evaluate((scale) => {
    const elements = Array.from(document.querySelectorAll<HTMLElement>(".users-card, .users-card *"));
    const sizes = elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize));
    elements.forEach((element, index) => { element.style.fontSize = `${sizes[index] * scale}px`; });
  }, factor);
}

async function expectContentFits(locator: Locator) {
  const dimensions = await locator.evaluate((element) => ({
    scrollWidth: element.scrollWidth, width: element.clientWidth,
    scrollHeight: element.scrollHeight, height: element.clientHeight,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.width + 1);
  expect(dimensions.scrollHeight).toBeLessThanOrEqual(dimensions.height + 1);
}

async function expectNoOverlap(first: Locator, second: Locator) {
  const a = await first.boundingBox();
  const b = await second.boundingBox();
  expect(a).not.toBeNull();
  expect(b).not.toBeNull();
  const overlapWidth = Math.min(a!.x + a!.width, b!.x + b!.width) - Math.max(a!.x, b!.x);
  const overlapHeight = Math.min(a!.y + a!.height, b!.y + b!.height) - Math.max(a!.y, b!.y);
  expect(overlapWidth <= 1 || overlapHeight <= 1, "directory fields and controls must not overlap").toBe(true);
}

for (const width of [320, 390, 768, 1440]) {
  test(`directory content and controls fit at ${width}px with long labels and enlarged text`, async ({ page }, testInfo) => {
    const unexpected = await installDirectoryFixtures(page);
    await page.setViewportSize({ width, height: 900 });
    for (const path of ["/vehicles", "/people", "/groups"]) {
      await page.goto(path);
      await expect(page.locator("main .loading-state")).toHaveCount(0);
      if (path !== "/groups") {
        const groupToggle = page.locator(".directory-group-header").first();
        await expect(groupToggle).toBeVisible();
        if (await groupToggle.getAttribute("aria-expanded") === "false") await groupToggle.click();
      }
      const row = page.locator(path === "/vehicles" ? ".vehicle-row" : path === "/people" ? ".person-row" : ".group-row").first();
      await expect(row).toBeVisible();
      for (const factor of [1, 1.5]) {
        if (factor > 1) await increaseDirectoryText(page, factor);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${path} at ${width}px with ${factor}× text`).toBeLessThanOrEqual(1);
        await expectContentFits(row);
        await expectContentFits(row.locator(".directory-row-copy"));
        const badge = row.locator(".badge");
        await expectNoOverlap(row.locator(".directory-row-copy"), badge);
        if (path === "/vehicles") {
          const edit = row.getByRole("button", { name: "Edit vehicle AB12 CDE" });
          const remove = row.getByRole("button", { name: "Delete AB12 CDE" });
          const chip = row.locator(".schedule-chip");
          await expect(chip).toHaveText(scheduleName);
          await expectContentFits(chip);
          await expectContentFits(row.locator(".vehicle-owner"));
          await expectNoOverlap(chip, remove);
          await expectNoOverlap(row.locator(".vehicle-owner"), remove);
          await expectNoOverlap(edit, remove);
          const bounds = await row.boundingBox();
          const action = await remove.boundingBox();
          expect(action!.x + action!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
          expect(action!.y + action!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height + 1);
        } else if (path === "/people") {
          await expectContentFits(row.locator(".schedule-chip"));
          await expectContentFits(row.locator(".vehicle-chip-list"));
        }
      }
      await page.screenshot({ path: testInfo.outputPath(`${path.slice(1)}-${width}-large-text.png`), fullPage: true, animations: "disabled" });
    }
    expect(unexpected).toEqual([]);
  });
}

test("directory row editing uses native keyboard activation without nesting delete controls", async ({ page }) => {
  const unexpected = await installDirectoryFixtures(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/vehicles");
  const groupToggle = page.locator(".directory-group-header").first();
  await expect(groupToggle).toBeVisible();
  if (await groupToggle.getAttribute("aria-expanded") === "false") await groupToggle.click();
  const edit = page.getByRole("button", { name: "Edit vehicle AB12 CDE" });
  await expect(edit).toBeVisible();
  await expect(edit.getByRole("button")).toHaveCount(0);
  await edit.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel(/^Vehicle Registration/)).toHaveValue("AB12 CDE");
  expect(unexpected).toEqual([]);
});
