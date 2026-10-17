import { expect, test, type Page } from "playwright/test";
const now = "2026-10-07T12:00:00Z";
const initialSettings: Record<string, unknown> = { home_assistant_url: "http://inert.local", unifi_protect_host: "inert-protect", openai_api_key: true };
const providers = [
  { title: "Home Assistant", key: "home_assistant", field: /^URL$/, draft: "http://retained.local" },
  { title: "UniFi Protect", key: "unifi_protect", field: /^Console host/, draft: "retained-protect" },
  { title: "OpenAI", key: "openai", field: /^API key/, draft: "inert-retained-key" },
  { title: "Apprise", key: "apprise", field: /^Apprise URL/, draft: "inert://retained-draft" },
  { title: "ESPHome", key: "esphome", field: /^Name$/, draft: "Retained controller" },
];
async function fixtures(page: Page, blocked = false) {
  const unexpected: string[] = []; let settingsReads = 0; let mutations = 0; let confirmations = 0; let connected = false; let changed = false;
  let release!: () => void; const pending = new Promise<void>((resolve) => { release = resolve; });
  let send: () => void = () => {};
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && path === "/api/v1/action-confirmations") { confirmations += 1; if (blocked) await pending; await route.fulfill({ json: { confirmation_token: "inert" } }); return; }
    if (request.method() === "PATCH" && path === "/api/v1/settings") { mutations += 1; await route.fulfill({ json: { saved: true, settings: [], changed_keys: [], activation: { state: "not_required", services: {} } } }); return; }
    if (request.method() === "POST" && path === "/api/v1/integrations/apprise/urls") { mutations += 1; await route.fulfill({ json: { urls: [{ index: 0, destination_id: "d".repeat(64), revision: "e".repeat(64), type: "Inert", scheme: "inert", preview: "Safe preview" }] } }); return; }
    if (request.method() === "POST" && path === "/api/v1/integrations/esphome/devices") { mutations += 1; await route.fulfill({ json: { devices: [], activation: { state: "activated", services: { access_devices: "activated" } } } }); return; }
    if (request.method() !== "GET") { unexpected.push(`${request.method()} ${path}`); await route.abort("blockedbyclient"); return; }
    let body: unknown;
    switch (path) {
      case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
      case "/api/v1/settings": settingsReads += 1; body = Object.entries(changed ? { ...initialSettings, home_assistant_url: "http://server-refresh.local", unifi_protect_host: "server-refresh" } : initialSettings).map(([key, value]) => ({ key, value })); break;
      case "/api/v1/alerts": body = []; break;
      case "/api/v1/maintenance/status": body = { is_active: false }; break;
      case "/api/v1/integrations/gate/status": case "/api/v1/integrations/home-assistant/status": body = { configured: false, connected: false }; break;
      case "/api/v1/integrations/home-assistant/entities": body = { cover_entities: [], media_player_entities: [] }; break;
      case "/api/v1/integrations/unifi-protect/status": body = { configured: false, connected: false, camera_count: 0 }; break;
      case "/api/v1/integrations/apprise/urls": body = { urls: [] }; break;
      case "/api/v1/integrations/esphome/devices": body = { devices: [] }; break;
      default: unexpected.push(`GET ${path}`); await route.abort("blockedbyclient"); return;
    }
    await route.fulfill({ json: body });
  });
  await page.routeWebSocket("**/*", (socket) => {
    connected = true;
    send = () => socket.send(JSON.stringify({ type: "audit.log.created", payload: { log: { action: "settings.update" } }, created_at: now }));
    socket.onMessage((raw) => { const request = JSON.parse(String(raw)); if (request.type === "client.ping") socket.send(JSON.stringify({ type: "connection.pong", payload: { id: request.payload.id }, created_at: now })); });
  });
  return { unexpected, release, get mutations() { return mutations; }, get confirmations() { return confirmations; }, get settingsReads() { return settingsReads; }, async refresh() { await expect.poll(() => connected).toBe(true); changed = true; send(); } };
}
async function openDraft(page: Page, provider: typeof providers[number]) {
  await page.goto("/integrations"); await page.getByRole("button", { name: new RegExp(`^${provider.title}`) }).click();
  const modal = page.getByRole("dialog", { name: provider.title, exact: true });
  if (provider.key === "apprise") await modal.getByRole("button", { name: "Add New Apprise URL" }).click();
  if (provider.key === "esphome") await modal.getByRole("button", { name: "Add New ESPHome Device" }).click();
  await modal.getByLabel(provider.field).fill(provider.draft);
  if (provider.key === "esphome") await modal.getByLabel("Host or IP", { exact: true }).fill("inert.local");
  return modal;
}
for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 844, height: 390 }, { width: 900, height: 1000 }]) {
  for (const provider of providers) {
    test(`retains ${provider.title} draft through realtime and guarded close at ${viewport.width}x${viewport.height}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport); const fixture = await fixtures(page);
      const modal = await openDraft(page, provider);
      if (viewport.width === 900) await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
      const before = fixture.settingsReads;
      await fixture.refresh(); await expect.poll(() => fixture.settingsReads).toBeGreaterThan(before);
      await expect(modal.getByLabel(provider.field)).toHaveValue(provider.draft);
      if (provider.key === "unifi_protect") { await modal.getByRole("tab", { name: "Exposes" }).click(); await modal.getByRole("tab", { name: "General" }).click(); await expect(modal.getByLabel(provider.field)).toHaveValue(provider.draft); }
      let nativePrompts = 0; page.on("dialog", async (dialog) => { nativePrompts += 1; await dialog.dismiss(); });
      const discard = page.getByRole("alertdialog", { name: "Discard unsaved integration changes?" });
      await page.keyboard.press("Escape"); await expect(discard).toBeVisible(); await expect(discard.getByRole("button", { name: "Keep editing" })).toBeFocused();
      await discard.getByRole("button", { name: "Keep editing" }).click(); await expect(discard).toHaveCount(0);
      await modal.getByRole("button", { name: "Close", exact: true }).click(); await expect(discard).toBeVisible();
      const promptBounds = await discard.boundingBox(); expect(promptBounds!.x).toBeGreaterThanOrEqual(0); expect(promptBounds!.x + promptBounds!.width).toBeLessThanOrEqual(viewport.width + 1);
      if (viewport.width === 900 || viewport.width === 390) await page.screenshot({ path: testInfo.outputPath(`provider-${provider.key}-${viewport.width}-discard-confirm.png`), fullPage: false, animations: "disabled" });
      await page.keyboard.press("Escape"); await expect(discard).toHaveCount(0);
      await expect(modal).toBeVisible(); await expect(modal.getByLabel(provider.field)).toHaveValue(provider.draft); expect(nativePrompts).toBe(0);
      const bounds = await modal.boundingBox(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1); expect(bounds!.height).toBeLessThanOrEqual(viewport.height + 1);
      const action = modal.getByRole("button", { name: /^(Save|Done)$/, exact: true }).last(); await action.scrollIntoViewIfNeeded(); await expect(action).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
      expect(fixture.mutations).toBe(0); expect(fixture.unexpected).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`provider-${provider.key}-${viewport.width}.png`), fullPage: true });
    });
  }
}
for (const provider of providers) {
  test(`protects pending ${provider.title} operations and preserves a truthful result`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 }); const fixture = await fixtures(page, true); const modal = await openDraft(page, provider);
    const action = provider.key === "apprise" ? "Add URL" : provider.key === "esphome" ? "Add Device" : "Save";
    await modal.getByRole("button", { name: action, exact: true }).click();
    await expect.poll(() => fixture.confirmations).toBe(1);
    await expect(modal.getByLabel(provider.field)).toBeDisabled();
    let prompts = 0; page.on("dialog", async (dialog) => { prompts += 1; await dialog.dismiss(); });
    await page.keyboard.press("Escape"); await modal.getByRole("button", { name: "Close", exact: true }).click(); await expect(modal).toBeVisible(); expect(prompts).toBe(0); await expect(page.getByRole("alertdialog")).toHaveCount(0);
    fixture.release();
    if (provider.key === "apprise") await expect(modal.getByText("Apprise URL added", { exact: true })).toBeVisible();
    else if (provider.key === "esphome") await expect(modal.getByText("ESPHome device added. Waiting for native stream...", { exact: true })).toBeVisible();
    else await expect(modal).toHaveCount(0);
    expect(fixture.mutations).toBe(1); expect(fixture.unexpected).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`provider-pending-${provider.key}.png`), fullPage: true });
  });
}
