import { expect, test, type Page } from "playwright/test";

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
    "/api/v1/ai/agent/status",
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
      case "/api/v1/ai/agent/status":
        body = { active_mode: "mocked", provider: "local", v3_ready: true };
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

test("keeps navigation available as width crosses 719, 720 and 721 pixels", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);

  for (const width of [719, 720]) {
    await page.setViewportSize({ width, height: 640 });
    const navToggle = page.getByRole("button", { name: /navigation sidebar/i });
    await expect(navToggle).toBeVisible();
    await expect(navToggle).toBeInViewport();
    await navToggle.click();
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Dashboard" })).toBeVisible();
    if (await page.getByRole("button", { name: "Close navigation" }).count()) {
      await page.getByRole("button", { name: "Close navigation" }).click();
    }
  }
  await page.setViewportSize({ width: 721, height: 640 });
  await expect(page.getByRole("button", { name: /navigation sidebar/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reports" })).toBeVisible();
  await page.setViewportSize({ width: 900, height: 640 });
  await expect(page.getByRole("button", { name: "Reports" })).toBeVisible();
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
  await expect(page.locator(".dashboard-event-snapshot-preview img")).toHaveCount(0);
  expect(network.unexpected).toEqual([]);
});

test("keeps the floating profile menu reachable through resize and Escape", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);
  await page.setViewportSize({ width: 900, height: 800 });
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
  expect(network.unexpected).toEqual([]);
});

test("keeps a group form draft through resize and contains/restores modal focus", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await page.setViewportSize({ width: 900, height: 700 });
  await page.goto("/groups");
  await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible();
  const addGroup = page.getByRole("button", { name: "Add Group" });
  await addGroup.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog", { name: "Group" });
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

  await page.keyboard.press("Escape");
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

test("retains the Alfred composer draft and clears scroll lock at each width", async ({ page }) => {
  const network = await mockOperationalNetwork(page);
  await openDashboard(page);
  await page.setViewportSize({ width: 560, height: 800 });
  await page.getByRole("button", { name: "Open Alfred" }).click();
  const composer = page.getByPlaceholder("Ask Alfred...");
  await expect(composer).toBeVisible();
  await composer.fill("Keep this draft while I rotate the device");
  await expect.poll(() => page.evaluate(() => document.documentElement.style.overflow)).toBe("hidden");

  await page.setViewportSize({ width: 900, height: 500 });
  await expect.poll(() => page.evaluate(() => document.documentElement.style.overflow)).toBe("");
  await expect(composer).toHaveValue("Keep this draft while I rotate the device");

  await page.setViewportSize({ width: 560, height: 800 });
  await expect.poll(() => page.evaluate(() => document.documentElement.style.overflow)).toBe("hidden");
  await expect(composer).toHaveValue("Keep this draft while I rotate the device");
  await page.getByRole("button", { name: "Close Alfred" }).click();
  await expect.poll(() => page.evaluate(() => document.documentElement.style.overflow)).toBe("");
  await expect.poll(() => page.evaluate(() => document.body.classList.contains("alfred-chat-open"))).toBe(false);
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
        launcher: rect(".chat-pill")
      };
    });
    expect(measured.top).toBe(`${edges.top}px`);
    expect(measured.right).toBe(`${edges.right}px`);
    expect(measured.bottom).toBe(`${edges.bottom}px`);
    expect(measured.left).toBe(`${edges.left}px`);
    expect(measured.navigation).not.toBeNull();
    expect(measured.alerts).not.toBeNull();
    expect(measured.launcher).not.toBeNull();
    if (measured.navigation && measured.alerts && measured.launcher) {
      expect(measured.navigation.left).toBeGreaterThanOrEqual(edges.left);
      expect(measured.navigation.left).toBeLessThanOrEqual(edges.left + 32);
      expect(measured.navigation.top).toBeGreaterThanOrEqual(edges.top);
      expect(measured.navigation.top).toBeLessThanOrEqual(edges.top + 32);
      expect(measured.alerts.right).toBeLessThanOrEqual(measured.width - edges.right);
      expect(measured.alerts.top).toBeGreaterThanOrEqual(edges.top);
      expect(measured.launcher.right).toBeLessThanOrEqual(measured.width - edges.right);
      expect(measured.launcher.bottom).toBeLessThanOrEqual(measured.height - edges.bottom);
      expect(measured.width - measured.launcher.right).toBeLessThanOrEqual(edges.right + 32);
      expect(measured.height - measured.launcher.bottom).toBeLessThanOrEqual(edges.bottom + 32);
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
    expect(addGroupBounds?.height ?? 0).toBeGreaterThanOrEqual(44);
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
