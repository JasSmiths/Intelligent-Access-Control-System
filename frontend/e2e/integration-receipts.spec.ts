import { expect, test } from "playwright/test";

for (const scenario of ["settings", "apprise", "calendar", "rejected-settings"] as const) {
  test(`keeps ${scenario} mutation receipts separate from provider read errors`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: scenario === "settings" ? 390 : 1440, height: 900 });
    let accepted = false; let mutations = 0; const unexpected: string[] = [];
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request(); const path = new URL(request.url()).pathname;
      if (request.method() === "POST" && path === "/api/v1/action-confirmations") { await route.fulfill({ json: { confirmation_token: "inert" } }); return; }
      if (request.method() === "PATCH" && path === "/api/v1/settings") {
        mutations += 1;
        if (scenario === "rejected-settings") { await route.fulfill({ status: 409, json: { detail: "Settings rejected" } }); return; }
        accepted = true; await route.fulfill({ json: [] }); return;
      }
      if (request.method() === "DELETE" && path === "/api/v1/integrations/apprise/urls/0") { mutations += 1; accepted = true; await route.fulfill({ json: { urls: [] } }); return; }
      if (request.method() === "POST" && path === "/api/v1/integrations/icloud-calendar/sync") { mutations += 1; accepted = true; await route.fulfill({ json: { status: "ok", account_count: 1, events_scanned: 0, passes_created: 0, passes_updated: 0, passes_cancelled: 0, passes_skipped: 0 } }); return; }
      if (request.method() !== "GET") { unexpected.push(`${request.method()} ${path}`); await route.abort("blockedbyclient"); return; }
      let body: unknown;
      switch (path) {
        case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
        case "/api/v1/settings": case "/api/v1/alerts": body = []; break;
        case "/api/v1/maintenance/status": body = { is_active: false }; break;
        case "/api/v1/integrations/gate/status": case "/api/v1/integrations/home-assistant/status": body = { configured: false, connected: false }; break;
        case "/api/v1/integrations/unifi-protect/status":
          if (accepted) { await route.fulfill({ status: 503, json: { detail: "Protect read offline" } }); return; }
          body = { configured: false, connected: false }; break;
        case "/api/v1/integrations/icloud-calendar/accounts":
          if (accepted) { await route.fulfill({ status: 503, json: { detail: "Calendar read offline" } }); return; }
          body = { accounts: [{ id: "cloud", apple_id: "synthetic@example.invalid", display_name: "Synthetic calendar", status: "connected", is_active: true }], recent_sync_runs: [] }; break;
        case "/api/v1/integrations/apprise/urls": body = { urls: accepted ? [] : [{ index: 0, type: "Pushover", scheme: "pover", preview: "Synthetic preview" }] }; break;
        default: unexpected.push(`GET ${path}`); await route.abort("blockedbyclient"); return;
      }
      await route.fulfill({ json: body });
    });
    await page.routeWebSocket("**/*", () => {});
    await page.goto("/integrations");
    if (scenario === "settings" || scenario === "rejected-settings") {
      await page.getByRole("button", { name: /DVLA Lookup/ }).click();
      const modal = page.getByRole("dialog", { name: "DVLA Lookup" });
      await modal.getByRole("textbox", { name: /Test VRN/ }).fill("SYN123");
      await modal.getByRole("button", { name: "Save", exact: true }).click();
      if (scenario === "rejected-settings") { await expect(modal.getByText("Unable to save settings", { exact: true })).toBeVisible(); await expect(modal.getByText(/Settings rejected/)).toBeVisible(); }
      else { await expect(modal).toHaveCount(0); await expect(page.getByText(/Protect read offline/)).toBeVisible(); }
    } else if (scenario === "apprise") {
      await page.getByRole("button", { name: /Apprise/ }).click();
      await page.getByRole("button", { name: "Remove Pushover URL" }).click();
      await expect(page.getByText("No notification URLs configured", { exact: true })).toBeVisible();
      await expect(page.getByText("Apprise URL update failed", { exact: true })).toHaveCount(0);
      await expect(page.getByText(/Protect read offline/)).toBeVisible();
    } else {
      await page.getByRole("button", { name: /iCloud Calendar/ }).click();
      await page.getByRole("button", { name: "Sync Calendars Now" }).click();
      await expect(page.getByText("Calendar sync complete", { exact: true })).toBeVisible();
      await expect(page.getByText(/Calendar read offline/)).toBeVisible();
      await expect(page.getByText("Calendar sync failed", { exact: true })).toHaveCount(0);
    }
    expect(mutations).toBe(1); expect(unexpected).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`integration-${scenario}.png`), fullPage: true });
  });
}
