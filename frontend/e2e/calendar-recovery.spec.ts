import { expect, test, type Page } from "playwright/test";
const original = "00000000-0000-4000-8000-000000000001";
const workflow = "00000000-0000-4000-8000-000000000002";
const key = "iacs-calendar-run:fixture-admin:admin";
const now = "2026-10-08T12:00:00Z";
function receipt(id: string, origin = "ui", complete = false) {
  return { id, started_at: now, finished_at: complete ? now : null, status: complete ? "ok" : "partial", trigger_source: origin,
    triggered_by_user_id: "fixture-admin", account_count: 2, events_scanned: 2, events_matched: 2, passes_created: 1,
    passes_updated: 0, passes_cancelled: 0, passes_skipped: 0, error: null, account_results: [{ account_id: "complete", status: "ok" }],
    recovery: { original_origin: origin, original_actor_user_id: "fixture-admin", workflow_operation_id: origin === "workflow" ? id : null,
      recovery_actor_user_id: complete ? "fixture-admin" : null, account_ids: ["complete", "remaining"],
      completed_account_ids: complete ? ["complete", "remaining"] : ["complete"], remaining_account_ids: complete ? [] : ["remaining"],
      can_resume: !complete, busy: false, coverage: { requested_start: now, requested_end: now, effective_start: now, effective_end: now,
        start_date: "2026-10-08", end_date: "2026-10-23", vendor_timezone: "Europe/London" } } };
}
async function fixtures(page: Page, lostStart = false) {
  const unexpected: string[] = []; const writes: { path: string; body: unknown }[] = []; const reads: string[] = [];
  let admitted = ""; let complete = false; let offline = false;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      writes.push({ path, body: request.postDataJSON() });
      if (path === "/api/v1/action-confirmations") { await route.fulfill({ json: { confirmation_token: "inert" } }); return; }
      if (path === "/api/v1/integrations/icloud-calendar/sync") {
        const identity = request.postDataJSON().run_id;
        if (!admitted) { admitted = identity; complete = true; if (lostStart) { await route.abort("failed"); return; } }
        expect(identity).toBe(admitted); await route.fulfill({ json: receipt(identity, "ui", true) }); return;
      }
      if (path === `/api/v1/integrations/icloud-calendar/sync/${workflow}/resume`) { complete = true; offline = true; await route.fulfill({ json: receipt(workflow, "workflow", true) }); return; }
    }
    if (request.method() !== "GET") { unexpected.push(`${request.method()} ${path}`); await route.abort("blockedbyclient"); return; }
    if (path.startsWith("/api/v1/integrations/icloud-calendar/sync/")) {
      reads.push(path); const id = path.split("/").at(-1)!;
      if (id !== original && id !== workflow && id !== admitted) { await route.fulfill({ status: 404, json: { detail: "Request not admitted yet" } }); return; }
      await route.fulfill({ json: receipt(id, id === workflow ? "workflow" : "ui", complete && (id === workflow || id === admitted)) }); return;
    }
    let body: unknown;
    switch (path) {
      case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", username: "fixture-admin", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
      case "/api/v1/settings": case "/api/v1/alerts": body = []; break;
      case "/api/v1/maintenance/status": body = { is_active: false }; break;
      case "/api/v1/integrations/gate/status": case "/api/v1/integrations/home-assistant/status": case "/api/v1/integrations/unifi-protect/status": body = { configured: false, connected: false }; break;
      case "/api/v1/integrations/icloud-calendar/accounts":
        if (offline) { await route.fulfill({ status: 503, json: { detail: "Calendar metadata offline" } }); return; }
        body = { accounts: [{ id: "account", display_name: "Inert calendar", apple_id: "inert@example.invalid", status: "connected", is_active: true }], recent_sync_runs: [receipt(workflow, "workflow")] }; break;
      default: unexpected.push(`GET ${path}`); await route.abort("blockedbyclient"); return;
    }
    await route.fulfill({ json: body });
  });
  await page.routeWebSocket("**/*", () => {});
  return { writes, reads, unexpected, get admitted() { return admitted; } };
}
for (const viewport of [{ width: 390, height: 844 }, { width: 1440, height: 900 }, { width: 900, height: 1000 }]) {
  test(`explicit workflow recovery retains original request and dirty authentication at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(({ key, original }) => sessionStorage.setItem(key, JSON.stringify({ original_request_id: original, selected_run_id: original })), { key, original });
    const fixture = await fixtures(page);
    await page.goto("/integrations"); await page.getByRole("button", { name: /^iCloud Calendar/ }).click();
    const modal = page.getByRole("dialog", { name: "iCloud Calendar", exact: true });
    if (viewport.width === 900) await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    await modal.getByRole("button", { name: "Add Account", exact: true }).click();
    await modal.getByLabel("Apple ID", { exact: true }).fill("retained@example.invalid");
    await modal.getByLabel("Password", { exact: true }).fill("inert-unsent-password");
    await modal.getByRole("button", { name: `Read run ${workflow}` }).click();
    await expect(modal.getByText(/Original origin: workflow/)).toBeVisible();
    expect(fixture.writes).toEqual([]);
    const prompts: string[] = []; page.on("dialog", async (dialog) => { prompts.push(dialog.message()); await dialog.accept(); });
    await modal.getByRole("button", { name: "Resume This Sync" }).click();
    await expect(modal.getByText("Calendar recovery receipt retained", { exact: true })).toBeVisible();
    await expect(modal.getByText(/Calendar metadata offline/)).toBeVisible();
    await expect(modal.getByLabel("Apple ID", { exact: true })).toHaveValue("retained@example.invalid");
    await expect(modal.getByLabel("Password", { exact: true })).toHaveValue("inert-unsent-password");
    expect(prompts).toHaveLength(1); expect(prompts[0]).toContain("1 completed accounts will be skipped"); expect(prompts[0]).toContain("workflow result unchanged");
    expect(fixture.writes.map((item) => item.path)).toEqual(["/api/v1/action-confirmations", `/api/v1/integrations/icloud-calendar/sync/${workflow}/resume`]);
    await modal.getByRole("button", { name: "Read Original Request" }).click();
    await expect(modal.getByText(original, { exact: true })).toBeVisible();
    expect(await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)!), key)).toEqual({ original_request_id: original, selected_run_id: original });
    expect(fixture.reads).toContain(`/api/v1/integrations/icloud-calendar/sync/${workflow}`); expect(fixture.unexpected).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`calendar-recovery-${viewport.width}.png`), fullPage: true });
  });
}
test("lost start response restores exact known UUID and ordinary replay creates no second run", async ({ page }) => {
  const fixture = await fixtures(page, true);
  await page.goto("/integrations"); await page.getByRole("button", { name: /^iCloud Calendar/ }).click();
  await page.getByRole("button", { name: "Sync Calendars Now" }).click();
  await expect(page.getByText("Calendar sync response unavailable", { exact: true })).toBeVisible();
  await expect.poll(() => fixture.admitted).not.toBe(""); const identity = fixture.admitted;
  await page.reload(); await page.getByRole("button", { name: /^iCloud Calendar/ }).click();
  await expect(page.getByText(identity, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Check Original Sync Request" }).click();
  await expect(page.getByText("Calendar sync complete", { exact: true })).toBeVisible();
  const posts = fixture.writes.filter((item) => item.path === "/api/v1/integrations/icloud-calendar/sync");
  expect(posts.map((item) => item.body)).toEqual([{ run_id: identity }, { run_id: identity }]);
  expect(fixture.reads).toContain(`/api/v1/integrations/icloud-calendar/sync/${identity}`); expect(fixture.unexpected).toEqual([]);
});
