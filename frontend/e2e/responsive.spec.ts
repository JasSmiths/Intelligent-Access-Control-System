import { expect, test, type Locator, type Page } from "playwright/test";

const viewportMatrix = [
  [280, 900], [320, 480], [640, 320], [768, 1024], [1024, 640],
  [1440, 1000], [1440, 400], [560, 800], [900, 500]
] as const;

const visitorEvent = {
  id: "responsive-snapshot-event",
  registration_number: "DUO 123",
  direction: "entry",
  decision: "granted",
  confidence: 0.99,
  source: "lpr",
  occurred_at: "2026-09-23T10:30:00Z",
  timing_classification: "on_time",
  anomaly_count: 0,
  visitor_pass_id: "visitor-1",
  visitor_name: "Snapshot Guest",
  visitor_pass_mode: "single_use",
  external_admission_mode: null,
  external_admission_source: null,
  snapshot_url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='48'%3E%3Crect width='80' height='48' fill='%2394a3b8'/%3E%3C/svg%3E",
  snapshot_captured_at: "2026-09-23T10:30:00Z",
  snapshot_bytes: 128,
  snapshot_width: 80,
  snapshot_height: 48,
  snapshot_camera: "Entry",
  movement_saga: null
};

const account = {
  id: "responsive-admin",
  username: "responsive-admin",
  first_name: "Alex",
  last_name: "Operator",
  full_name: "Alex Operator",
  profile_photo_data_url: null,
  email: null,
  mobile_phone_number: null,
  role: "admin",
  is_active: true,
  last_login_at: null,
  person_id: null,
  preferences: { sidebarCollapsed: false },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

type NetworkMock = { unexpected: string[] };

async function mockOperationalNetwork(page: Page): Promise<NetworkMock> {
  const unexpected: string[] = [];
  const allowedReads = new Set([
    "/api/v1/auth/status",
    "/api/v1/presence",
    "/api/v1/presence/expected-today",
    "/api/v1/events",
    "/api/v1/alerts",
    "/api/v1/people",
    "/api/v1/vehicles",
    "/api/v1/groups",
    "/api/v1/schedules",
    "/api/v1/integrations/gate/status",
    "/api/v1/maintenance/status",
    "/api/v1/reports/context",
    "/api/v1/settings"
  ]);

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET" || !allowedReads.has(url.pathname)) {
      unexpected.push(`${request.method()} ${url.pathname}`);
      await route.abort("blockedbyclient");
      return;
    }
    let body: unknown = {};
    switch (url.pathname) {
      case "/api/v1/auth/status":
        body = { setup_required: false, authenticated: true, user: account };
        break;
      case "/api/v1/events":
        body = [visitorEvent];
        break;
      case "/api/v1/presence/expected-today":
        body = {
          date: "2026-09-23", timezone: "Europe/London", generated_at: "2026-09-23T08:00:00Z",
          count: 0, learning: false,
          coverage: { regular_candidates: 0, learned_candidates: 0, learning_population: 0, ratio: 0 },
          people: []
        };
        break;
      case "/api/v1/integrations/gate/status":
        body = { configured: false, gate_entity_id: null, default_media_player: null, last_gate_state: "unknown" };
        break;
      case "/api/v1/maintenance/status":
        body = { is_active: false, enabled_by: null, enabled_at: null, source: null, reason: null, duration_seconds: 0, duration_label: null };
        break;
      case "/api/v1/reports/context":
        body = { site_timezone: "Europe/London", now: "2026-09-23T10:30:00Z" };
        break;
      default:
        body = [];
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  // The real-time transport is local and inert. No live server or commands can be reached.
  page.routeWebSocket("**/*", (socket) => socket.close());
  return { unexpected };
}

async function openDashboard(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Recent Events" })).toBeVisible();
}

async function expectNoPageOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    innerWidth: window.innerWidth,
    root: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
    offenders: Array.from(document.querySelectorAll<HTMLElement>("*"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          selector: `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ""}${typeof element.className === "string" && element.className ? `.${element.className.trim().replace(/\s+/g, ".")}` : ""}`,
          left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width),
          scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, overflowX: style.overflowX
        };
      })
      .filter((element) => element.right > document.documentElement.clientWidth + 1 || element.left < -1)
      .sort((a, b) => b.right - a.right)
      .slice(0, 8)
  }));
  expect(dimensions.root, `root overflow at ${dimensions.viewport}px: ${JSON.stringify(dimensions.offenders)}`).toBeLessThanOrEqual(dimensions.viewport + 1);
  expect(dimensions.body, `body overflow at ${dimensions.viewport}px`).toBeLessThanOrEqual(dimensions.viewport + 1);
}

test("keeps the dashboard within the viewport matrix without reloading", async ({ page }, testInfo) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);

  for (const [width, height] of viewportMatrix) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(350);
    const refresh = page.getByRole("button", { name: "Refresh", exact: true });
    await refresh.focus();
    await expect(refresh.locator(".refresh-status-dot")).toBeVisible();
    const statusTooltip = refresh.locator(".refresh-status-tooltip");
    await expect(statusTooltip).toBeVisible();
    await expect(refresh).toHaveAccessibleDescription(/.+: .+/);
    const tooltipBounds = await statusTooltip.boundingBox();
    expect(tooltipBounds!.x).toBeGreaterThanOrEqual(0);
    expect(tooltipBounds!.x + tooltipBounds!.width).toBeLessThanOrEqual(width);
    await expect(page.locator(".sidebar-footer > *")).toHaveCount(1);
    await expectNoPageOverflow(page);
    if (width === 320 && height === 480) {
      await page.screenshot({ path: testInfo.outputPath("dashboard-320x480.png"), fullPage: false });
    }
    if (width === 1440 && height === 400) {
      await page.screenshot({ path: testInfo.outputPath("dashboard-1440x400.png"), fullPage: false });
    }
  }
  await page.setViewportSize({ width: 280, height: 480 });
  const presence = page.locator(".presence-stat.has-tooltip").first();
  await presence.focus();
  const roster = presence.locator(".expected-presence-tooltip");
  await expect(roster).toBeVisible();
  const rosterBounds = await roster.boundingBox();
  expect(rosterBounds).not.toBeNull();
  expect(rosterBounds!.x).toBeGreaterThanOrEqual(0);
  expect(rosterBounds!.x + rosterBounds!.width).toBeLessThanOrEqual(280);
  await expectNoPageOverflow(page);
  expect(network.unexpected).toEqual([]);
});

test("keeps navigation available across the 720px content and 980px drawer boundaries", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);

  for (const width of [719, 720, 721, 979, 980]) {
    await page.setViewportSize({ width, height: 640 });
    const navToggle = page.getByRole("button", { name: /navigation sidebar/i });
    await expect(navToggle).toBeVisible();
    await expect(navToggle).toBeInViewport();
    await navToggle.click();
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Dashboard" })).toBeVisible();
    await page.getByRole("button", { name: "Close navigation" }).click();
  }
  await page.setViewportSize({ width: 981, height: 640 });
  await expect(page.getByRole("button", { name: /navigation sidebar/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reports" })).toBeVisible();
  await page.setViewportSize({ width: 1100, height: 640 });
  await expect(page.getByRole("button", { name: "Reports" })).toBeVisible();
  expect(network.unexpected).toEqual([]);
});

test("restores the desktop sidebar preference after visiting drawer width", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  const preferenceUpdates: boolean[] = [];
  await page.route("**/api/v1/auth/me/preferences", async (route) => {
    const body = route.request().postDataJSON() as { sidebarCollapsed: boolean };
    preferenceUpdates.push(body.sidebarCollapsed);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...account, preferences: body })
    });
  });
  await page.setViewportSize({ width: 1100, height: 640 });
  await openDashboard(page);

  await page.getByRole("button", { name: "Collapse navigation sidebar" }).click();
  await expect(page.locator(".app-shell")).toHaveClass(/sidebar-collapsed/);
  await expect.poll(() => preferenceUpdates).toEqual([true]);

  await page.setViewportSize({ width: 956, height: 440 });
  await expect(page.locator(".app-shell")).not.toHaveClass(/sidebar-collapsed/);
  await expect(page.getByRole("dialog", { name: "Site navigation" })).toHaveCount(0);
  await page.getByRole("button", { name: "Expand navigation sidebar" }).click();
  await expect(page.getByRole("dialog", { name: "Site navigation" })).toBeVisible();
  await page.getByRole("button", { name: "Close navigation" }).click();

  await page.setViewportSize({ width: 981, height: 640 });
  await expect(page.locator(".app-shell")).toHaveClass(/sidebar-collapsed/);
  expect(preferenceUpdates).toEqual([true]);
  expect(network.unexpected).toEqual([]);
});

test("preserves snapshot state and keyboard activation across a resize", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);
  await page.setViewportSize({ width: 560, height: 800 });

  const snapshot = page.getByRole("button", { name: "Toggle Snapshot for Snapshot Guest" });
  await expect(snapshot).toBeVisible();
  await snapshot.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".dashboard-event-snapshot-preview img")).toBeVisible();
  await page.setViewportSize({ width: 900, height: 500 });
  await expect(page.locator(".dashboard-event-snapshot-preview img")).toBeVisible();

  await page.setViewportSize({ width: 560, height: 800 });
  await snapshot.focus();
  await page.keyboard.press("Space");
  await expect(page.locator(".dashboard-event-snapshot-preview img")).toBeHidden();
  expect(network.unexpected).toEqual([]);
});

async function sampleSnapshotTransition(preview: Locator, property: string, finish = true) {
  return preview.evaluate((element, options) => {
    const transition = element.getAnimations().find((animation) =>
      "transitionProperty" in animation && animation.transitionProperty === options.property);
    if (!transition?.effect) return null;
    const duration = Number(transition.effect.getTiming().duration);
    transition.pause();
    transition.currentTime = duration / 2;
    const style = getComputedStyle(element);
    const sample = { duration, opacity: Number(style.opacity), visibility: style.visibility, height: element.getBoundingClientRect().height };
    if (options.finish) transition.finish();
    return sample;
  }, { property, finish });
}

test("animates snapshot hover entry and interrupted exit while Escape closes under the pointer", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openDashboard(page);
  const row = page.getByRole("button", { name: "Toggle Snapshot for Snapshot Guest" });
  const preview = row.locator(".dashboard-event-snapshot-preview");
  await expect(preview).toHaveAttribute("aria-hidden", "true");
  await expect(preview.locator("img")).toHaveCount(0);

  await row.hover();
  const entry = await sampleSnapshotTransition(preview, "opacity");
  expect(entry).not.toBeNull();
  expect(entry!.duration).toBeGreaterThan(0);
  expect(entry!.opacity).toBeGreaterThan(0);
  expect(entry!.opacity).toBeLessThan(1);
  await expect(preview).toHaveCSS("opacity", "1");
  const image = await preview.locator("img").elementHandle();

  await page.mouse.move(0, 0);
  const exit = await sampleSnapshotTransition(preview, "opacity", false);
  expect(exit).not.toBeNull();
  expect(exit!.visibility).toBe("visible");
  expect(exit!.opacity).toBeGreaterThan(0);
  expect(exit!.opacity).toBeLessThan(1);
  await row.hover();
  await expect(preview).toHaveCSS("opacity", "1");
  expect(await image!.evaluate((element) => element.isConnected)).toBe(true);

  await row.focus();
  await page.keyboard.press("Escape");
  await expect(row).toHaveAttribute("aria-expanded", "false");
  await expect(preview).toHaveCSS("visibility", "hidden");
  await expect(preview.locator("img")).toBeHidden();
  expect(await row.evaluate((element) => element.matches(":hover"))).toBe(true);
  expect(network.unexpected).toEqual([]);
});

test("animates inline snapshot expansion and collapse on touch and disables motion when requested", async ({ browser }) => {
  const context = await browser.newContext({ isMobile: true, hasTouch: true, viewport: { width: 390, height: 844 }, reducedMotion: "no-preference" });
  const page = await context.newPage();
  const network = await mockOperationalNetwork(page);
  try {
    await openDashboard(page);
    const row = page.getByRole("button", { name: "Toggle Snapshot for Snapshot Guest" });
    const preview = row.locator(".dashboard-event-snapshot-preview");
    await expect.poll(async () => (await preview.boundingBox())?.height ?? 0).toBe(0);
    await row.tap();
    const entry = await sampleSnapshotTransition(preview, "grid-template-rows");
    expect(entry).not.toBeNull();
    expect(entry!.height).toBeGreaterThan(0);
    await expect(preview).toHaveCSS("opacity", "1");
    const fullHeight = (await preview.boundingBox())!.height;
    expect(entry!.height).toBeLessThan(fullHeight);
    const image = await preview.locator("img").elementHandle();

    await row.tap();
    const exit = await sampleSnapshotTransition(preview, "grid-template-rows", false);
    expect(exit).not.toBeNull();
    expect(exit!.height).toBeGreaterThan(0);
    expect(exit!.height).toBeLessThan(fullHeight);
    await row.tap();
    await expect(row).toHaveAttribute("aria-expanded", "true");
    await expect.poll(async () => (await preview.boundingBox())?.height ?? 0).toBeCloseTo(fullHeight, 1);
    expect(await image!.evaluate((element) => element.isConnected)).toBe(true);
    await row.tap();
    await expect(preview.locator("img")).toBeHidden();
    await expect.poll(async () => (await preview.boundingBox())?.height ?? 0).toBe(0);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await row.tap();
    await expect(preview.locator("img")).toBeVisible();
    const duration = await preview.evaluate((element) => Math.max(...getComputedStyle(element).transitionDuration.split(",").map(parseFloat)));
    expect(duration).toBeLessThanOrEqual(.001);
    await row.tap();
    await expect(preview.locator("img")).toBeHidden();
    await expect.poll(async () => (await preview.boundingBox())?.height ?? 0).toBe(0);
    expect(network.unexpected).toEqual([]);
  } finally { await context.close(); }
});

async function sampleDialogMotion(dialog: Locator) {
  return dialog.evaluate((element) => {
    const container = element.closest(".modal-backdrop, .search-palette-backdrop") ?? element;
    for (const animation of container.getAnimations({ subtree: true })) {
      animation.pause();
      animation.currentTime = Number(animation.effect!.getTiming().duration) / 2;
    }
    const animation = element.getAnimations()[0];
    const style = getComputedStyle(element);
    return { duration: Number(animation?.effect?.getTiming().duration ?? 0), opacity: Number(style.opacity), transform: style.transform };
  });
}

async function finishDialogMotion(dialog: Locator) {
  await dialog.evaluate((element) => {
    const container = element.closest(".modal-backdrop, .search-palette-backdrop") ?? element;
    for (const animation of container.getAnimations({ subtree: true })) animation.finish();
  });
}

async function expectClosingDialog(page: Page, dialog: Locator) {
  await expect(dialog).toBeFocused();
  const controls = dialog.locator("button, input, select, textarea");
  expect(await controls.evaluateAll((elements) => elements.every((element) => Boolean(element.closest("[inert]"))))).toBe(true);
  await controls.first().evaluate((element) => (element as HTMLElement).focus());
  await expect(dialog).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog).toBeFocused();
}

test("animates access confirmation entry and safe dismissal on desktop and mobile", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await page.route("**/api/v1/integrations/gate/status", async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return; }
    await route.fulfill({ json: {
      configured: true, connected: true, gate_entity_id: null,
      default_media_player: null, last_gate_state: "closed",
      gate_entities: [{ entity_id: "fixture-gate", name: "Fixture Gate", state: "closed", enabled: true }],
      garage_door_entities: [{ entity_id: "fixture-garage", name: "Fixture Garage", state: "closed", enabled: true }]
    } });
  });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await openDashboard(page);

  for (const scenario of [
    { label: "Fixture Gate", width: 1440, dismiss: "cancel" },
    { label: "Fixture Garage", width: 390, dismiss: "escape" }
  ]) {
    await page.setViewportSize({ width: scenario.width, height: 900 });
    const trigger = page.locator(".gate-row").filter({ hasText: scenario.label }).getByRole("button", { name: "Closed", exact: true });
    // Explicit focus also models keyboard activation on Safari, where pointer clicks need not focus buttons.
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: `Open ${scenario.label}?` });
    const entry = await sampleDialogMotion(dialog);
    expect(entry.duration).toBeGreaterThan(0);
    expect(entry.opacity).toBeGreaterThan(0);
    expect(entry.opacity).toBeLessThan(1);
    expect(entry.transform).not.toBe("none");
    await finishDialogMotion(dialog);
    const cancel = dialog.getByRole("button", { name: "Cancel", exact: true });
    const confirm = dialog.getByRole("button", { name: `Open ${scenario.label}`, exact: true });
    await expect(cancel).toBeFocused();
    await expect(confirm).toBeEnabled();
    if (scenario.dismiss === "cancel") await cancel.click();
    else await page.keyboard.press("Escape");

    const exit = await sampleDialogMotion(dialog);
    expect(exit.duration).toBeGreaterThan(0);
    expect(exit.opacity).toBeGreaterThan(0);
    expect(exit.opacity).toBeLessThan(1);
    await expect(page.locator(".modal-backdrop")).toHaveAttribute("data-closing", "true");
    await expectClosingDialog(page, dialog);
    expect(await trigger.evaluate((element) => Boolean(element.closest("[inert]")))).toBe(true);
    await finishDialogMotion(dialog);
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(await trigger.evaluate((element) => Boolean(element.closest("[inert]")))).toBe(false);
  }

  const garage = page.locator(".gate-row").filter({ hasText: "Fixture Garage" }).getByRole("button", { name: "Closed", exact: true });
  const dialog = page.getByRole("dialog", { name: "Open Fixture Garage?" });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await garage.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCSS("animation-name", "none");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(garage).toBeFocused();

  // Changing the preference during dismissal must complete the exit lifecycle too.
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(dialog).toHaveCount(0);
  await expect(garage).toBeFocused();
  expect(network.unexpected).toEqual([]);
});

test("animates global search dismissal through Escape and its backdrop", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await openDashboard(page);
  const trigger = page.getByRole("button", { name: "Search anything", exact: true });
  for (const dismissal of ["escape", "backdrop"]) {
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Global search" });
    const entry = await sampleDialogMotion(dialog);
    expect(entry.duration).toBeGreaterThan(0);
    expect(entry.opacity).toBeGreaterThan(0);
    expect(entry.opacity).toBeLessThan(1);
    await finishDialogMotion(dialog);
    await expect(dialog.getByRole("combobox")).toBeFocused();
    if (dismissal === "escape") await page.keyboard.press("Escape");
    else await page.locator(".search-palette-backdrop").click({ position: { x: 4, y: 4 } });
    const exit = await sampleDialogMotion(dialog);
    expect(exit.opacity).toBeGreaterThan(0);
    expect(exit.opacity).toBeLessThan(1);
    await expect(page.locator(".search-palette-backdrop")).toHaveAttribute("data-closing", "true");
    await expectClosingDialog(page, dialog);
    expect(await trigger.evaluate((element) => Boolean(element.closest("[inert]")))).toBe(true);
    await finishDialogMotion(dialog);
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
  }
  expect(network.unexpected).toEqual([]);
});

test("keeps the floating profile menu reachable through resize and Escape", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);
  await page.setViewportSize({ width: 900, height: 800 });
  await page.getByRole("button", { name: "Expand navigation sidebar" }).click();
  await page.getByRole("button", { name: /Alex Operator/ }).click();
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();

  await page.setViewportSize({ width: 900, height: 500 });
  await expect(menu).toBeVisible();
  const box = await menu.boundingBox();
  expect(box).not.toBeNull();
  if (box) {
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(900);
    expect(box.y + box.height).toBeLessThanOrEqual(500);
  }
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Alex Operator/ })).toBeFocused();
  await expect(page.getByRole("dialog", { name: "Site navigation" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Site navigation" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Expand navigation sidebar" })).toBeFocused();
  expect(network.unexpected).toEqual([]);
});

test("keeps a group form draft through resize and contains/restores modal focus", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 900, height: 700 });
  await page.goto("/groups");
  await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible();
  const addGroup = page.getByRole("button", { name: "Add Group" });
  await addGroup.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog", { name: "Group" });
  const entry = await sampleDialogMotion(dialog);
  expect(entry.opacity).toBeGreaterThan(0);
  expect(entry.opacity).toBeLessThan(1);
  await finishDialogMotion(dialog);
  const groupName = dialog.getByLabel("Group name");
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(groupName).toBeFocused();
  await groupName.fill("A long access group name for responsive checks");
  await page.setViewportSize({ width: 320, height: 480 });
  await expect(groupName).toHaveValue("A long access group name for responsive checks");
  await groupName.press("Tab");
  await expect(dialog).toContainText("Category");

  await page.evaluate(() => {
    const nativeDialog = document.createElement("dialog");
    nativeDialog.setAttribute("aria-label", "Native overlay test");
    nativeDialog.innerHTML = "<label>First native action<input aria-label='First native action' /></label><label>Second native action<input aria-label='Second native action' /></label>";
    document.body.append(nativeDialog);
    nativeDialog.showModal();
    nativeDialog.querySelector("input")?.focus();
  });
  const nativeDialog = page.getByRole("dialog", { name: "Native overlay test" });
  await expect(nativeDialog).toBeVisible();
  const firstNativeAction = nativeDialog.getByRole("textbox", { name: "First native action" });
  const secondNativeAction = nativeDialog.getByRole("textbox", { name: "Second native action" });
  await expect(firstNativeAction).toBeFocused();
  await page.keyboard.press("Tab");
  const nativeTabState = await page.evaluate(() => {
    const native = document.querySelector<HTMLDialogElement>("dialog[aria-label='Native overlay test']");
    let modalMatches = false;
    let modalCount = 0;
    let supportsModalPseudo = false;
    try {
      modalMatches = native?.matches(":modal") ?? false;
      modalCount = document.querySelectorAll("dialog:modal").length;
      supportsModalPseudo = CSS.supports("selector(dialog:modal)");
    } catch {
      supportsModalPseudo = false;
    }
    const active = document.activeElement;
    return {
      modalMatches,
      modalCount,
      supportsModalPseudo,
      activeLabel: active instanceof HTMLInputElement ? active.getAttribute("aria-label") : active?.tagName
    };
  });
  await expect(secondNativeAction, `native modal Tab state: ${JSON.stringify(nativeTabState)}`).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(firstNativeAction).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(nativeDialog).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await expect(groupName).toHaveValue("A long access group name for responsive checks");

  page.once("dialog", (confirmation) => confirmation.dismiss());
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await expect(groupName).toHaveValue("A long access group name for responsive checks");
  await expect(page.locator(".modal-backdrop")).not.toHaveAttribute("data-closing", "true");
  expect(await groupName.evaluate((element) => Boolean(element.closest("[inert]")))).toBe(false);
  page.once("dialog", (confirmation) => confirmation.accept());
  await page.keyboard.press("Escape");
  const exit = await sampleDialogMotion(dialog);
  expect(exit.opacity).toBeGreaterThan(0);
  expect(exit.opacity).toBeLessThan(1);
  await expectClosingDialog(page, dialog);
  expect(await addGroup.evaluate((element) => Boolean(element.closest("[inert]")))).toBe(true);
  await finishDialogMotion(dialog);
  await expect(dialog).toHaveCount(0);
  await expect(addGroup).toBeFocused();
  expect(network.unexpected).toEqual([]);
});

test("repositions a JavaScript date popover within the visual viewport", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await page.setViewportSize({ width: 900, height: 700 });
  await page.goto("/reports");
  await expect(page.getByRole("heading", { name: "Reports" })).toBeVisible();
  await page.setViewportSize({ width: 280, height: 480 });
  const fromDate = page.getByRole("button", { name: "From" });
  await expect(fromDate).toBeEnabled();
  await fromDate.click();
  const popover = page.locator(".report-date-time-popover");
  await expect(popover).toBeVisible();
  const bounds = await popover.boundingBox();
  expect(bounds).not.toBeNull();
  if (bounds) {
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(280);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(480);
  }
  await page.setViewportSize({ width: 320, height: 480 });
  await expect(popover).toBeVisible();
  const resizedBounds = await popover.boundingBox();
  expect(resizedBounds).not.toBeNull();
  if (resizedBounds) expect(resizedBounds.x + resizedBounds.width).toBeLessThanOrEqual(320);
  expect(network.unexpected).toEqual([]);
});

test("applies unequal safe-area tokens and mirrored edge values without overflow", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);
  await page.setViewportSize({ width: 560, height: 800 });

  for (const edges of [
    { top: 31, right: 17, bottom: 37, left: 29 },
    { top: 17, right: 29, bottom: 31, left: 37 }
  ]) {
    await page.evaluate((safe) => {
      const root = document.documentElement;
      root.style.setProperty("--safe-top", `${safe.top}px`);
      root.style.setProperty("--safe-right", `${safe.right}px`);
      root.style.setProperty("--safe-bottom", `${safe.bottom}px`);
      root.style.setProperty("--safe-left", `${safe.left}px`);
    }, edges);
    await expectNoPageOverflow(page);
    const measured = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      const rect = (selector: string) => {
        const element = document.querySelector<HTMLElement>(selector);
        if (!element) return null;
        const bounds = element.getBoundingClientRect();
        return { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom };
      };
      return {
        top: style.getPropertyValue("--safe-top").trim(),
        right: style.getPropertyValue("--safe-right").trim(),
        bottom: style.getPropertyValue("--safe-bottom").trim(),
        left: style.getPropertyValue("--safe-left").trim(),
        width: document.documentElement.clientWidth,
        height: document.documentElement.clientHeight,
        navigation: rect(".topbar-menu"),
        alerts: rect(".notification-button"),
        topbar: rect(".topbar")
      };
    });
    expect(measured.top).toBe(`${edges.top}px`);
    expect(measured.right).toBe(`${edges.right}px`);
    expect(measured.bottom).toBe(`${edges.bottom}px`);
    expect(measured.left).toBe(`${edges.left}px`);
    expect(measured.navigation).not.toBeNull();
    expect(measured.alerts).not.toBeNull();
    if (measured.navigation && measured.alerts) {
      expect(measured.navigation.left).toBeGreaterThanOrEqual(edges.left);
      expect(measured.navigation.left).toBeLessThanOrEqual(edges.left + 32);
      expect(measured.navigation.top).toBeGreaterThanOrEqual(edges.top);
      expect(measured.navigation.top).toBeLessThanOrEqual(edges.top + 32);
      expect(measured.alerts.right).toBeLessThanOrEqual(measured.width - edges.right);
      expect(measured.alerts.top).toBeGreaterThanOrEqual(edges.top);
    }
  }
  expect(network.unexpected).toEqual([]);
});

test("supports coarse-pointer form controls at 280px and 320px", async ({ browser }) => {
  const context = await browser.newContext({
    isMobile: true,
    hasTouch: true,
    viewport: { width: 320, height: 480 }
  });
  const page = await context.newPage();
  const network = await mockOperationalNetwork(page);
  try {
    await page.goto("/groups");
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible();
    const addGroup = page.getByRole("button", { name: "Add Group" });
    const addGroupBounds = await addGroup.boundingBox();
    // WebKit can report 44 CSS pixels as 43.9999847 after a transform.
    expect(Math.round((addGroupBounds?.height ?? 0) * 100) / 100).toBeGreaterThanOrEqual(44);
    await addGroup.tap();
    const groupName = page.getByRole("dialog", { name: "Group" }).getByLabel("Group name");
    const inputFontSize = await groupName.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
    expect(inputFontSize).toBeGreaterThanOrEqual(16);

    for (const [width, height] of [[280, 900], [320, 480]] as const) {
      await page.setViewportSize({ width, height });
      await page.waitForTimeout(150);
      await expectNoPageOverflow(page);
      await expect(groupName).toBeVisible();
    }
    expect(network.unexpected).toEqual([]);
  } finally {
    await context.close();
  }
});

test("keeps collapsed navigation inaccessible and respects reduced motion", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1200, height: 800 });
  await openDashboard(page);
  const submenu = page.locator("#settings-submenu");
  await expect(submenu).toHaveJSProperty("inert", true);
  await expect(page.getByRole("button", { name: "General", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Expand Settings" }).click();
  await expect(submenu).toHaveJSProperty("inert", false);
  await expect(page.getByRole("button", { name: "General", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Collapse Settings" }).click();
  await expect(submenu).toHaveJSProperty("inert", true);
  await expect.poll(async () => (await submenu.boundingBox())?.height ?? 0).toBeLessThanOrEqual(1);
  const duration = await submenu.evaluate((element) => Math.max(...getComputedStyle(element).transitionDuration.split(",").map(parseFloat)));
  expect(duration).toBeLessThanOrEqual(.001);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(network.unexpected).toEqual([]);
});
