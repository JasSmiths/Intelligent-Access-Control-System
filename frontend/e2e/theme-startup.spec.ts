import { expect, test, type Page } from "playwright/test";

const backgrounds = { light: "rgb(243, 245, 248)", dark: "rgb(16, 21, 30)" };

async function dashboardFixture(page: Page) {
  const unexpected: string[] = [];
  const now = "2026-10-13T05:00:00Z";
  const responses: Record<string, unknown> = {
    "/api/v1/auth/status": { setup_required: false, authenticated: true, user: { id: "theme-admin", username: "admin", first_name: "Synthetic", full_name: "Synthetic Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } },
    "/api/v1/alerts": [],
    "/api/v1/maintenance/status": { is_active: false },
    "/api/v1/integrations/gate/status": { configured: false },
    "/api/v1/presence/summary": { counts: { total: 0, present: 0, exited: 0, unknown: 0 }, inside: { people: [], hidden: 0 } },
    "/api/v1/presence/expected-today/summary": { date: "2026-10-13", timezone: "Europe/London", generated_at: now, count: 0, hidden: 0, learning: false, coverage: { regular_candidates: 0, learned_candidates: 0, learning_population: 0, ratio: 0 }, people: [] },
    "/api/v1/events": [],
    "/api/v1/vehicles": { items: [], total: 0, next_cursor: null }
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET" || !(path in responses)) {
      unexpected.push(`${request.method()} ${path}`);
      await route.abort("blockedbyclient");
      return;
    }
    await route.fulfill({ json: responses[path] });
  });
  await page.routeWebSocket("**/*", (socket) => socket.close());
  return unexpected;
}

const cases = [
  { saved: "dark", system: "light", resolved: "dark" },
  { saved: "dark", system: "dark", resolved: "dark" },
  { saved: "light", system: "dark", resolved: "light" },
  { saved: "system", system: "dark", resolved: "dark" },
  { saved: "system", system: "light", resolved: "light" },
  { saved: null, system: "dark", resolved: "dark" },
  { saved: null, system: "light", resolved: "light" },
  { saved: "invalid", system: "dark", resolved: "dark" }
] as const;

for (const appearance of cases) {
  test(`saved ${appearance.saved ?? "missing"} on ${appearance.system} system paints ${appearance.resolved} before app scripts`, async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: appearance.system });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript((saved) => {
      if (saved !== null) localStorage.setItem("iacs-theme", saved);
    }, appearance.saved);
    let withholdApp = true;
    await page.route("**/*", async (route) => {
      if (withholdApp && route.request().resourceType() === "script") {
        // A completed inert response lets WebKit capture the initial canvas.
        await route.fulfill({ contentType: "application/javascript", body: "" });
      } else {
        await route.continue();
      }
    });
    const unexpected = await dashboardFixture(page);
    await page.goto("/");
    await expect(page.locator("html")).toHaveCSS("background-color", backgrounds[appearance.resolved]);
    await expect(page.locator("html")).toHaveCSS("color-scheme", appearance.resolved);
    await expect(page.locator("#root")).toBeEmpty();
    await page.screenshot({ path: testInfo.outputPath("before-app.png") });
    withholdApp = false;
    await page.reload();
    await expect(page.getByRole("heading", { name: /, Synthetic$/ })).toBeVisible();
    await expect(page.locator("body")).toHaveCSS("background-color", backgrounds[appearance.resolved]);
    await page.reload();
    await expect(page.locator("body")).toHaveCSS("background-color", backgrounds[appearance.resolved]);
    expect(unexpected).toEqual([]);
  });
}

test("theme controls persist across reload and system appearance changes", async ({ page }, testInfo) => {
  const unexpected = await dashboardFixture(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  const theme = page.getByRole("button", { name: "Theme", exact: true });
  await expect(theme).toHaveText("system");
  await theme.click();
  await expect(page.locator("html")).toHaveCSS("background-color", backgrounds.light);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.screenshot({ path: testInfo.outputPath(`dashboard-light-${width}.png`), fullPage: true, animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await theme.click();
  await expect(theme).toHaveText("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await page.reload();
  await expect(theme).toHaveText("dark");
  await expect(page.locator("html")).toHaveCSS("background-color", backgrounds.dark);
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 900, height: 1000 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(viewport);
    await expect(page.getByRole("heading", { name: /, Synthetic$/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    await page.screenshot({ path: testInfo.outputPath(`dashboard-dark-${viewport.width}.png`), fullPage: true, animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await theme.click();
  await expect(theme).toHaveText("system");
  await expect(page.locator("html")).toHaveCSS("background-color", backgrounds.light);
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveCSS("background-color", backgrounds.dark);
  expect(unexpected).toEqual([]);
});

test("unavailable theme storage falls back to system and controls still work", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => {
    for (const method of ["getItem", "setItem"] as const) {
      Object.defineProperty(Storage.prototype, method, { value: () => { throw new DOMException("Storage unavailable", "SecurityError"); } });
    }
  });
  const unexpected = await dashboardFixture(page);
  await page.goto("/");
  const theme = page.getByRole("button", { name: "Theme", exact: true });
  await expect(theme).toHaveText("system");
  await expect(page.locator("html")).toHaveCSS("background-color", backgrounds.dark);
  await theme.click();
  await expect(page.locator("html")).toHaveCSS("background-color", backgrounds.light);
  expect(errors).toEqual([]);
  expect(unexpected).toEqual([]);
});
