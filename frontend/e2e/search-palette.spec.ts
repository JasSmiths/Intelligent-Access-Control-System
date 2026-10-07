import { expect, test, type Page } from "playwright/test";

const now = "2026-10-04T10:30:00Z";
const person = { id: "person-search", first_name: "Ada", last_name: "Resident", display_name: "Ada Resident", group_id: null, group: null, category: "family", schedule_id: null, schedule: null, vehicles: [], is_active: true, profile_photo_data_url: null, profile_photo_url: null, garage_door_entity_ids: [], home_assistant_presence_input_boolean_entity_ids: [] };
const searchResult = { id: person.id, type: "person", label: "Ada Resident", subtitle: "Resident · People", filter_value: "Ada", target: { view: "people" }, preview: { title: "Ada Resident", body: "Find this resident in the People directory.", badges: ["Person"], facts: [{ label: "Access", value: "Active" }] } };

async function installFixtures(page: Page) {
  const unexpected: string[] = [];
  const reads: Record<string, unknown> = {
    "/api/v1/auth/status": { setup_required: false, authenticated: true, user: { id: "search-admin", username: "admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false }, profile_photo_data_url: null } },
    "/api/v1/alerts": [],
    "/api/v1/events": [],
    "/api/v1/events/history": { items: [], next_cursor: null, as_of: now },
    "/api/v1/people": { items: [person], total: 1, next_cursor: null },
    "/api/v1/vehicles": { items: [], total: 0, next_cursor: null },
    "/api/v1/groups": [],
    "/api/v1/schedules": [],
    "/api/v1/settings": [],
    "/api/v1/access-devices": [],
    "/api/v1/integrations/gate/status": { configured: false, connected: false, gate_entity_id: null, default_media_player: null, last_gate_state: "unknown", garage_door_entities: [] },
    "/api/v1/maintenance/status": { is_active: false, enabled_by: null, enabled_at: null, source: null, reason: null, duration_seconds: 0, duration_label: null },
    "/api/v1/search": [searchResult],
  };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const local = url.origin === "http://127.0.0.1:5174";
    if (local && request.method() === "GET" && Object.hasOwn(reads, url.pathname)) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(reads[url.pathname]) });
    } else if (local && request.method() === "GET" && !url.pathname.startsWith("/api/") && ["document", "script", "stylesheet", "image", "font"].includes(request.resourceType())) {
      await route.continue();
    } else {
      unexpected.push(`${request.method()} ${url.origin}${url.pathname}`);
      await route.abort("blockedbyclient");
    }
  });
  await page.routeWebSocket("**/*", (socket) => socket.close());
  return unexpected;
}

async function setSearchTheme(page: Page, theme: "light" | "dark") {
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  // Wait for the theme paint before retaining screenshots across a media change.
  await expect(page.locator(".search-palette-row-main strong").first()).toHaveCSS("color", theme === "dark" ? "rgb(237, 243, 251)" : "rgb(25, 37, 54)");
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
}

async function openSearch(page: Page, activation: "pointer" | "keyboard" = "pointer") {
  const opener = page.getByRole("button", { name: "Search anything", exact: true });
  if (activation === "keyboard") {
    await opener.focus();
    await opener.press("Enter");
  } else {
    await opener.click();
  }
  const dialog = page.getByRole("dialog", { name: "Global search" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("combobox", { name: "Search anything" })).toBeFocused();
  await expect(dialog.getByRole("combobox")).toHaveAttribute("aria-expanded", "true");
  return dialog;
}

async function setKeyboardViewport(page: Page, height: number, offsetTop: number) {
  await page.evaluate(({ height, offsetTop }) => {
    Object.assign(window.visualViewport!, { height, offsetTop });
    window.visualViewport!.dispatchEvent(new Event("resize"));
    window.visualViewport!.dispatchEvent(new Event("scroll"));
  }, { height, offsetTop });
}

test.describe("touch phone search", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("phone search gives results the available height with and without the keyboard", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    // Browser emulation cannot display an OS keyboard. Exercise the same visual
    // viewport resize/scroll contract used by Safari while keeping fixtures inert.
    const viewport = Object.assign(new EventTarget(), { height: window.innerHeight, width: window.innerWidth, offsetTop: 0, offsetLeft: 0, pageTop: 0, pageLeft: 0, scale: 1 });
    Object.defineProperty(window, "visualViewport", { value: viewport, configurable: true });
  });
  const unexpected = await installFixtures(page);
  await page.goto("/events");
  const dialog = await openSearch(page);
  const results = dialog.getByRole("listbox");
  await expect(dialog.locator(".search-palette-preview")).toBeHidden();
  for (const theme of ["light", "dark"] as const) {
    await setSearchTheme(page, theme);
    await setKeyboardViewport(page, 844, 0);
    await expect(page.locator(".search-palette-backdrop")).toHaveCSS("height", "844px");
    await expect.poll(() => results.evaluate((element) => element.clientHeight)).toBeGreaterThan(650);
    await page.screenshot({ path: testInfo.outputPath(`search-phone-${theme}.png`), animations: "disabled" });

    await setKeyboardViewport(page, 320, 36);
    await expect(page.locator(".search-palette-backdrop")).toHaveCSS("height", "320px");
    await expect(page.locator(".search-palette-backdrop")).toHaveCSS("top", "36px");
    await expect.poll(() => results.evaluate((element) => element.clientHeight)).toBeGreaterThan(195);
    // Read layout coordinates after the synthetic keyboard size has applied.
    const bounds = await dialog.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom };
    });
    expect(bounds.top).toBeGreaterThanOrEqual(36);
    expect(bounds.bottom).toBeLessThanOrEqual(356);
    expect(await results.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
    const last = results.getByRole("option").last();
    await last.scrollIntoViewIfNeeded();
    const lastBounds = await last.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom };
    });
    const listBounds = await results.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom };
    });
    expect(lastBounds.top).toBeGreaterThanOrEqual(listBounds.top - 1);
    expect(lastBounds.bottom).toBeLessThanOrEqual(listBounds.bottom + 1);
    await results.evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: testInfo.outputPath(`search-phone-keyboard-${theme}.png`), animations: "disabled" });
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(unexpected).toEqual([]);
});

test("phone shortcuts and search results open directly by tap or Enter", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const unexpected = await installFixtures(page);
  await page.goto("/events");
  let dialog = await openSearch(page);
  await dialog.getByRole("option", { name: /^People\s+Access/ }).tap();
  await expect(page).toHaveURL(/\/people$/);
  await expect(dialog).toHaveCount(0);

  dialog = await openSearch(page);
  await dialog.getByRole("combobox").fill("Ada");
  await dialog.getByRole("option", { name: /Ada Resident/ }).tap();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Page filter" })).toHaveValue("Ada");

  await page.goto("/events");
  dialog = await openSearch(page);
  await dialog.getByRole("combobox").fill("Ada");
  await expect(dialog.getByRole("option", { name: /Ada Resident/ })).toBeVisible();
  await dialog.getByRole("combobox").press("Enter");
  await expect(page).toHaveURL(/\/people$/);
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Page filter" })).toHaveValue("Ada");
  expect(unexpected).toEqual([]);
});

});

test("desktop keeps a useful preview and explicit Open action", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const unexpected = await installFixtures(page);
  await page.goto("/events");
  const dialog = await openSearch(page);
  await dialog.getByRole("combobox").fill("Ada");
  await dialog.getByRole("option", { name: /Ada Resident/ }).click();
  await expect(page).toHaveURL(/\/events$/);
  await expect(dialog.locator(".search-palette-preview")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Ada Resident" })).toBeVisible();
  for (const theme of ["light", "dark"] as const) {
    await setSearchTheme(page, theme);
    await page.screenshot({ path: testInfo.outputPath(`search-desktop-${theme}.png`), animations: "disabled" });
  }
  await dialog.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page).toHaveURL(/\/people$/);
  await expect(page.getByRole("textbox", { name: "Page filter" })).toHaveValue("Ada");
  expect(unexpected).toEqual([]);
});

test("search retains its query and reachable actions across tablet and landscape resizing", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const unexpected = await installFixtures(page);
  await page.goto("/events");
  const dialog = await openSearch(page);
  const input = dialog.getByRole("combobox");
  await input.fill("Ada");
  const result = dialog.getByRole("option", { name: /Ada Resident/ });
  await expect(result).toBeVisible();
  for (const viewport of [{ width: 768, height: 1024 }, { width: 667, height: 375 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(viewport);
    await expect(input).toHaveValue("Ada");
    await expect(input).toBeFocused();
    if (viewport.width <= 720) await expect(dialog.locator(".search-palette-preview")).toBeHidden();
    else await expect(dialog.getByRole("button", { name: "Open", exact: true })).toBeInViewport();
    await expect(result).toBeInViewport();
    await expect(dialog.getByRole("button", { name: "Close search" })).toBeInViewport();
    await expect.poll(() => dialog.evaluate((element) => element.getBoundingClientRect().bottom)).toBeLessThanOrEqual(viewport.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    for (const theme of ["light", "dark"] as const) {
      await setSearchTheme(page, theme);
      await page.screenshot({ path: testInfo.outputPath(`search-${viewport.width}x${viewport.height}-${theme}.png`), animations: "disabled" });
    }
  }
  await dialog.getByRole("button", { name: "Close search" }).click();
  await expect(dialog).toHaveCount(0);
  expect(unexpected).toEqual([]);
});

test("closing retains modal ownership for its exit and restores focus afterward", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const unexpected = await installFixtures(page);
  await page.goto("/events");
  const opener = page.getByRole("button", { name: "Search anything", exact: true });
  const dialog = await openSearch(page, "keyboard");
  await dialog.evaluate(async (element) => { await Promise.all(element.getAnimations({ subtree: true }).map((animation) => animation.finished)); });
  const duringExit = await page.evaluate(async () => {
    (document.querySelector('[aria-label="Close search"]') as HTMLButtonElement).click();
    await new Promise(requestAnimationFrame);
    return {
      state: document.querySelector(".search-palette-backdrop")?.getAttribute("data-closing"),
      locked: document.body.classList.contains("iacs-modal-open"),
      backgroundInert: document.querySelector("#root")?.hasAttribute("inert"),
      focusedInside: document.querySelector(".search-palette")?.contains(document.activeElement),
    };
  });
  expect(duringExit).toEqual({ state: "true", locked: true, backgroundInert: true, focusedInside: true });
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await expect(page.locator("body")).not.toHaveClass(/iacs-modal-open/);
  await expect(page.locator("#root")).not.toHaveAttribute("inert", "");

  await openSearch(page, "keyboard");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  expect(unexpected).toEqual([]);
});

test("search reopens after animated dismissal and reduced motion closes immediately", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const unexpected = await installFixtures(page);
  await page.goto("/events");
  const dialog = await openSearch(page, "keyboard");
  await dialog.getByRole("button", { name: "Close search" }).click();
  await expect(dialog).toHaveCount(0);
  await page.keyboard.press("Control+k");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("combobox")).toBeFocused();
  await expect(page.locator(".search-palette-backdrop")).not.toHaveAttribute("data-closing", "true");
  await page.emulateMedia({ reducedMotion: "reduce" });
  const removedAtNextFrame = await page.evaluate(async () => {
    (document.querySelector('[aria-label="Close search"]') as HTMLButtonElement).click();
    await new Promise(requestAnimationFrame);
    return document.querySelector(".search-palette-backdrop") === null;
  });
  expect(removedAtNextFrame).toBe(true);
  await expect(page.getByRole("button", { name: "Search anything", exact: true })).toBeFocused();
  expect(unexpected).toEqual([]);
});
