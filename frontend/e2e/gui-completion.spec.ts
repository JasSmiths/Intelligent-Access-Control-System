import { expect, test, type Page } from "playwright/test";

const now = "2026-09-23T10:30:00Z";
const account = (role: "admin" | "standard") => ({ id: `fixture-${role}`, username: role, first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role, is_active: true, preferences: { sidebarCollapsed: false }, profile_photo_data_url: null });
const group = { id: "group-1", name: "Residents", category: "family", subtype: null, description: "Site residents", person_count: 1 };
const vehicle = { id: "vehicle-1", registration_number: "AB12 CDE", make: "Ford", model: "Focus", color: "blue", description: "Family car", is_active: true, person_id: "person-1", person_ids: ["person-1"], owners: ["Ada Resident"], owner: "Ada Resident", schedule_id: "schedule-1", schedule: "Weekday access", vehicle_photo_data_url: null, vehicle_photo_url: null };
const person = { id: "person-1", first_name: "Ada", last_name: "Resident", display_name: "Ada Resident", group_id: "group-1", group: "Residents", category: "family", schedule_id: "schedule-1", schedule: "Weekday access", vehicles: [vehicle], is_active: true, profile_photo_data_url: null, profile_photo_url: null, garage_door_entity_ids: [], home_assistant_presence_input_boolean_entity_ids: [] };
const blocks = Object.fromEntries(Array.from({ length: 7 }, (_, day) => [String(day), day < 5 ? [{ start: "08:00", end: "18:00" }] : []]));
const schedule = { id: "schedule-1", name: "Weekday access", description: "Work hours", time_blocks: blocks };
const event = { id: "event-1", registration_number: "AB12 CDE", direction: "entry", decision: "granted", confidence: .98, source: "lpr", occurred_at: now, timing_classification: "on_time", anomaly_count: 0, visitor_pass_id: null, visitor_name: null, snapshot_url: null, movement_saga: null };
const movement = { id: "movement-1", source: "lpr", state: "pending", access_event_id: "event-1", registration_number: "AB12 CDE", direction: "entry", decision: "granted", occurred_at: now, gate_command_required: true, presence_committed: true, reconciliation_required: false, failure_detail: null, updated_at: now, gate_commands: [], intent_payload: { source: "synthetic fixture" }, decision_payload: { decision: "granted" }, state_history: [] };
const pass = { id: "pass-1", visitor_name: "Sam Visitor", pass_type: "one-time", visitor_phone: null, expected_time: now, window_minutes: 30, valid_from: null, valid_until: null, window_start: now, window_end: "2026-09-23T11:00:00Z", status: "scheduled", creation_source: "manual", source_reference: null, source_metadata: null, whatsapp_status: null, whatsapp_status_label: null, whatsapp_status_detail: null, created_by_user_id: null, created_by: null, arrival_time: null, departure_time: null, number_plate: null, vehicle_make: null, vehicle_colour: null, duration_on_site_seconds: null, duration_human: null, arrival_event_id: null, departure_event_id: null, telemetry_trace_id: null, created_at: now, updated_at: now };

function history(items: unknown[]) { return { items, next_cursor: null, as_of: now }; }

async function installFixtures(page: Page, role: "admin" | "standard" = "admin") {
  const unexpected: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") { unexpected.push(`${request.method()} ${path}`); await route.abort("blockedbyclient"); return; }
    let body: unknown;
    switch (path) {
      case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: account(role) }; break;
      case "/api/v1/presence": body = []; break;
      case "/api/v1/presence/expected-today": body = { date: "2026-09-23", timezone: "Europe/London", generated_at: now, count: 0, learning: false, coverage: { regular_candidates: 0, learned_candidates: 0, learning_population: 0, ratio: 0 }, people: [] }; break;
      case "/api/v1/events": body = [event]; break;
      case "/api/v1/events/history": body = history([event]); break;
      case "/api/v1/events/event-1": body = event; break;
      case "/api/v1/access/movements/history": body = history([movement]); break;
      case "/api/v1/access/movements/movement-1": body = movement; break;
      case "/api/v1/alerts": body = []; break;
      case "/api/v1/alerts/history": body = history([]); break;
      case "/api/v1/people": body = [person]; break;
      case "/api/v1/vehicles": body = [vehicle]; break;
      case "/api/v1/groups": body = [group]; break;
      case "/api/v1/schedules": body = [schedule]; break;
      case "/api/v1/schedules/schedule-1/dependencies": body = { people: [person], vehicles: [vehicle], doors: [] }; break;
      case "/api/v1/visitor-passes": body = [pass]; break;
      case "/api/v1/integrations/gate/status": body = { configured: false, connected: false, gate_entity_id: null, default_media_player: null, last_gate_state: "unknown", garage_door_entities: [] }; break;
      case "/api/v1/integrations/gate/commands": body = { items: [], next_cursor: null }; break;
      case "/api/v1/missed-exit-recovery/attempts": body = { items: [], total: 0, offset: 0, limit: 25 }; break;
      case "/api/v1/integrations/home-assistant/status": body = { configured: false, connected: false }; break;
      case "/api/v1/integrations/unifi-protect/status": body = { configured: false, connected: false, realtime_connected: false }; break;
      case "/api/v1/integrations/unifi-protect/cameras": body = { cameras: [] }; break;
      case "/api/v1/integrations/icloud-calendar/accounts": body = { accounts: [], recent_sync_runs: [] }; break;
      case "/api/v1/integrations/discord/status": body = { configured: false, connected: false }; break;
      case "/api/v1/integrations/discord/channels": body = { channels: [] }; break;
      case "/api/v1/integrations/discord/identities": body = { identities: [] }; break;
      case "/api/v1/integrations/whatsapp/status": body = { configured: false, enabled: false }; break;
      case "/api/v1/integrations/home-assistant/entities": body = { cover_entities: [] }; break;
      case "/api/v1/integrations/home-assistant/recovery-trackers": body = { trackers: [], mappings: [], reason: "not_configured" }; break;
      case "/api/v1/integrations/esphome/entities": body = { cover_entities: [] }; break;
      case "/api/v1/access-devices": body = []; break;
      case "/api/v1/maintenance/status": body = { is_active: false, enabled_by: null, enabled_at: null, source: null, reason: null, duration_seconds: 0, duration_label: null }; break;
      case "/api/v1/settings": body = []; break;
      case "/api/v1/settings/security/auth-secret": body = { source: "generated", environment: "test", file_path: "", rotation_required: false, ui_rotation_available: false, detail: "Managed in fixture" }; break;
      case "/api/v1/diagnostics/lpr-zone-shadow": body = { observations: [] }; break;
      case "/api/v1/reports/context": body = { site_timezone: "Europe/London", now }; break;
      case "/api/v1/leaderboard": body = { known: [], unknown: [], top_known: null, generated_at: now }; break;
      case "/api/v1/users": body = [account("admin")]; break;
      case "/api/v1/automations/catalog": body = { triggers: [], conditions: [], actions: [], variables: [], users: [], garage_doors: [], notification_rules: [], mock_context: {} }; break;
      case "/api/v1/automations/rules": body = []; break;
      case "/api/v1/notifications/catalog": body = { triggers: [], conditions: [], actions: [], variables: [], integrations: [], actionable_notifications: [], gate_malfunction_stages: [], mock_context: {} }; break;
      case "/api/v1/notifications/rules": body = []; break;
      case "/api/v1/ai/agent/status": body = { active_mode: "mocked", provider: "local", v3_ready: true }; break;
      case "/api/v1/ai/training/feedback": body = { feedback: [] }; break;
      case "/api/v1/ai/training/lessons": body = { lessons: [] }; break;
      case "/api/v1/ai/training/eval-examples": body = { examples: [] }; break;
      case "/api/v1/telemetry/investigation-filters": body = { site_timezone: "Europe/London", devices: [], automations: [], schedules: [], integrations: [], categories: [], severities: [], outcomes: [], triggers: [], actors: [] }; break;
      case "/api/v1/telemetry/investigation-overview": body = { site_timezone: "Europe/London", resolved_range: {}, recent_problems: [], incomplete_runs: [], repeated_problems: [], important_activity: [] }; break;
      case "/api/v1/telemetry/activity": body = { items: [], next_cursor: null, site_timezone: "Europe/London", resolved_range: {}, partial: false }; break;
      case "/api/v1/search": body = [{ id: "person-1", type: "person", label: "Ada Resident", subtitle: "Person", filter_value: "Ada", target: { view: "people" }, preview: { title: "Ada Resident", body: null, badges: ["Person"], facts: [] } }]; break;
      default: unexpected.push(`GET ${path}`); await route.fulfill({ status: 501, contentType: "application/json", body: JSON.stringify({ detail: "Unexpected fixture request" }) }); return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  page.routeWebSocket("**/*", (socket) => socket.close());
  return unexpected;
}

test("opens all route families with isolated loaded fixtures across viewport sizes", async ({ page }, testInfo) => {
  test.setTimeout(300_000);
  const unexpected = await installFixtures(page);
  const routes = ["/", "/events", "/movements", "/alerts", "/people", "/groups", "/vehicles", "/schedules", "/passes", "/reports", "/top-charts", "/logs", "/settings", "/settings/general", "/settings/gates", "/settings/garage-doors", "/settings/command-history", "/settings/missed-exit-recovery", "/settings/auth-security", "/integrations", "/settings/automations", "/settings/notifications", "/settings/lpr-tuning", "/settings/zones", "/settings/users", "/settings/alfred-training"];
  const viewports = [{ name: "desktop", width: 1440, height: 1000 }, { name: "tablet", width: 900, height: 1000 }, { name: "mobile", width: 390, height: 844 }, { name: "mobile-short", width: 390, height: 600 }];
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    for (const path of routes) {
      await page.goto(path);
      await expect(page.locator("main")).not.toContainText("This view could not be loaded.");
      await expect(page.locator("main .loading-panel, main .loading-state")).toHaveCount(0, { timeout: 4000 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${viewport.name} ${path} horizontal overflow`).toBeLessThanOrEqual(1);
      const clippedPills = await page.locator("main .badge, main .vehicle-chip, main .schedule-dependency-pill, main .visitor-pass-status-pill").evaluateAll((elements) => elements.flatMap((element) => {
        const bounds = element.getBoundingClientRect();
        if (!bounds.width || !bounds.height || getComputedStyle(element).visibility === "hidden") return [];
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let node: Node | null;
        while ((node = walker.nextNode())) {
          if (!node.textContent?.trim()) continue;
          const range = document.createRange();
          range.selectNodeContents(node);
          for (const text of range.getClientRects()) {
            if (text.left < bounds.left - 1 || text.right > bounds.right + 1 || text.top < bounds.top - 1 || text.bottom > bounds.bottom + 1) {
              return [element.textContent?.trim()];
            }
          }
        }
        return [];
      }));
      expect(clippedPills, `${viewport.name} ${path} pill text must fit its container`).toEqual([]);
      if (viewport.name === "desktop" || viewport.name === "mobile") {
        const routeName = path === "/" ? "dashboard" : path.replace(/^\//, "").replaceAll("/", "-");
        await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-${routeName}-after.png`), fullPage: false, animations: "disabled" });
      }
    }
  }
  expect(unexpected).toEqual([]);
});

test("keeps Settings state, search filters, role gates, and mobile dismissal in sync", async ({ page }) => {
  const unexpected = await installFixtures(page);
  await page.goto("/settings/general");
  await expect(page.getByRole("button", { name: "Collapse Settings" })).toBeVisible();
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await expect(page.getByRole("button", { name: "Expand Settings" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("button", { name: "Collapse Settings" })).toBeVisible();
  await page.getByRole("button", { name: "Search anything" }).click();
  await page.getByRole("dialog", { name: "Global search" }).getByRole("combobox").fill("Ada");
  await page.getByRole("option", { name: /Ada Resident/ }).first().click();
  await page.getByRole("dialog", { name: "Global search" }).getByRole("button", { name: "Open" }).click();
  await expect(page.getByRole("textbox", { name: "Page filter" })).toHaveValue("Ada");
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByRole("textbox", { name: "Page filter" })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: /navigation sidebar/i }).click();
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await expect(page.locator(".app-shell")).not.toHaveClass(/mobile-nav-open/);
  expect(unexpected).toEqual([]);
});

test("scrolls a lower active Settings destination into the sidebar viewport", async ({ page }) => {
  const unexpected = await installFixtures(page);
  await page.setViewportSize({ width: 1440, height: 600 });
  await page.goto("/settings/alfred-training");
  await expect(page.getByRole("button", { name: "Alfred Training" })).toBeInViewport();
  expect(unexpected).toEqual([]);
});

test("shows a complete touch navigation drawer at iPhone landscape size", async ({ browser }, testInfo) => {
  const context = await browser.newContext({ baseURL: "http://127.0.0.1:5174", viewport: { width: 956, height: 440 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  try {
    const unexpected = await installFixtures(page);
    await page.goto("/settings/alfred-training");
    await page.evaluate(() => {
      const root = document.documentElement;
      root.style.setProperty("--safe-top", "12px");
      root.style.setProperty("--safe-bottom", "12px");
      root.style.setProperty("--safe-left", "62px");
      root.style.setProperty("--safe-right", "38px");
    });
    const toggle = page.getByRole("button", { name: "Expand navigation sidebar" });
    await expect(toggle).toBeInViewport();
    await toggle.click();
    const sidebar = page.getByRole("dialog", { name: "Site navigation" });
    await expect(sidebar).toBeVisible();
    await expect.poll(async () => Math.round((await sidebar.boundingBox())?.x ?? -1)).toBe(0);
    const active = sidebar.getByRole("button", { name: "Alfred Training" });
    await expect(active).toBeInViewport();
    await expect(sidebar.getByRole("button", { name: "Close navigation" })).toBeInViewport();
    await expect(sidebar.getByRole("button", { name: "Account menu for Alex Operator" })).toBeInViewport();
    const navigationList = sidebar.getByRole("navigation", { name: "Main navigation" });
    expect(await navigationList.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);
    const bounds = await Promise.all([sidebar.boundingBox(), active.locator("svg").boundingBox(), active.locator("span").boundingBox()]);
    expect(bounds.every(Boolean)).toBe(true);
    const [rail, icon, label] = bounds as Array<{ x: number; y: number; width: number; height: number }>;
    for (const item of [icon, label]) {
      expect(item.x).toBeGreaterThanOrEqual(rail.x);
      expect(item.x + item.width).toBeLessThanOrEqual(rail.x + rail.width);
    }
    await page.screenshot({ path: testInfo.outputPath("iphone-landscape-settings-drawer-956x440-after.png"), fullPage: false });
    await navigationList.evaluate((node) => { node.scrollTop = 0; });
    await expect(sidebar.getByText("Operations", { exact: true })).toBeInViewport();
    await active.scrollIntoViewIfNeeded();
    await expect(active).toBeInViewport();
    await page.keyboard.press("Escape");
    await expect(sidebar).toHaveCount(0);
    await expect(toggle).toBeFocused();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => {
      document.documentElement.style.setProperty("--safe-left", "0px");
      document.documentElement.style.setProperty("--safe-right", "0px");
    });
    await toggle.click();
    await expect(sidebar).toBeVisible();
    await expect(active).toBeInViewport();
    await page.setViewportSize({ width: 956, height: 440 });
    await page.evaluate(() => {
      document.documentElement.style.setProperty("--safe-left", "62px");
      document.documentElement.style.setProperty("--safe-right", "38px");
    });
    await expect(active).toBeInViewport();
    await expect(sidebar.getByRole("button", { name: "Account menu for Alex Operator" })).toBeInViewport();
    await sidebar.getByRole("button", { name: "Close navigation" }).click();
    await expect(sidebar).toHaveCount(0);
    expect(unexpected).toEqual([]);
  } finally {
    await context.close();
  }
});

test("keeps long directory labels and missing avatars usable with enlarged text", async ({ page }) => {
  const unexpected = await installFixtures(page);
  const longName = "Residents with extended access for multiple homes and shared deliveries";
  await page.route("**/api/v1/groups", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ ...group, name: longName }]) }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/groups");
  await page.evaluate(() => { document.documentElement.style.fontSize = "22px"; document.body.style.fontSize = "1.15em"; });
  await expect(page.getByText(longName)).toBeVisible();
  await page.getByRole("button", { name: "Expand navigation sidebar" }).click();
  await expect(page.getByRole("button", { name: "Account menu for Alex Operator" })).toContainText("AO");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(unexpected).toEqual([]);
});

test("preserves a dirty group draft and keeps actions visible on a short phone", async ({ page }, testInfo) => {
  const unexpected = await installFixtures(page);
  await page.setViewportSize({ width: 390, height: 600 });
  await page.goto("/groups");
  await page.getByRole("button", { name: "Add Group" }).click();
  const editor = page.getByRole("dialog", { name: "Group" });
  await editor.getByLabel("Group name").fill("Draft group");
  await expect(page.locator(".chat-widget")).toBeHidden();
  await expect(editor.getByRole("button", { name: "Cancel" })).toBeInViewport();
  await expect(editor.getByRole("button", { name: "Save Group" })).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath("group-editor-mobile-short-after.png"), fullPage: false });
  page.once("dialog", (dialog) => dialog.dismiss());
  await editor.getByRole("button", { name: "Cancel" }).click();
  await expect(editor.getByLabel("Group name")).toHaveValue("Draft group");
  page.once("dialog", (dialog) => dialog.accept());
  await page.keyboard.press("Escape");
  await expect(editor).toHaveCount(0);
  expect(unexpected).toEqual([]);
});

test("keeps Schedule actions reachable on a short phone", async ({ page }, testInfo) => {
  const unexpected = await installFixtures(page);
  await page.setViewportSize({ width: 390, height: 600 });
  await page.goto("/schedules");
  await page.getByRole("button", { name: "New Schedule" }).click();
  const editor = page.getByRole("dialog", { name: "Schedule" });
  await editor.getByLabel("Schedule name").fill("Early deliveries");
  await expect(editor.getByRole("button", { name: "Cancel" })).toBeInViewport();
  await expect(editor.getByRole("button", { name: "Create Schedule" })).toBeInViewport();
  await expect(page.locator(".chat-widget")).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath("schedule-editor-mobile-short-after.png"), fullPage: false });
  page.once("dialog", (dialog) => dialog.accept());
  await editor.getByRole("button", { name: "Cancel" }).click();
  expect(unexpected).toEqual([]);
});

test("keeps Notification Save, Test, Cancel and preview reachable on mobile", async ({ page }, testInfo) => {
  const unexpected = await installFixtures(page);
  await page.setViewportSize({ width: 390, height: 600 });
  await page.goto("/settings/notifications");
  await page.getByRole("button", { name: "Add Notification" }).click();
  const editor = page.getByRole("dialog", { name: "Add Notification" });
  for (const name of ["Save", "Send Test", "Cancel"]) await expect(editor.getByRole("button", { name, exact: true })).toBeInViewport();
  await expect(page.locator(".chat-widget")).toBeHidden();
  await expect(editor.getByLabel("Live notification preview")).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath("notification-editor-mobile-short-after.png"), fullPage: false });
  await editor.getByRole("button", { name: "Preview" }).click();
  await expect(editor.getByLabel("Live notification preview")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("notification-preview-mobile-short-after.png"), fullPage: false });
  await editor.getByRole("button", { name: "Preview" }).click();
  await expect(editor.getByRole("button", { name: "Save", exact: true })).toBeInViewport();
  await editor.getByRole("button", { name: "Cancel" }).click();
  expect(unexpected).toEqual([]);
});

test("does not send hidden General edits when saving Auth settings", async ({ page }) => {
  const unexpected = await installFixtures(page);
  const submitted: Record<string, unknown>[] = [];
  const general = [{ key: "app_name", category: "general", value: "IACS", is_secret: false, description: null }];
  const auth = [
    { key: "auth_cookie_name", category: "auth", value: "iacs_session", is_secret: false, description: null },
    { key: "auth_access_token_minutes", category: "auth", value: 30, is_secret: false, description: null },
    { key: "auth_remember_days", category: "auth", value: 14, is_secret: false, description: null },
    { key: "auth_cookie_secure", category: "auth", value: true, is_secret: false, description: null },
  ];
  await page.route("**/api/v1/settings?*", async (route) => {
    const category = new URL(route.request().url()).searchParams.get("category");
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(category === "general" ? general : auth) });
  });
  await page.route("**/api/v1/action-confirmations", async (route) => {
    submitted.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ confirmation_id: "test-intent", confirmation_token: "test-token", action: "settings.update", expires_at: now }) });
  });
  await page.route("**/api/v1/settings", async (route) => {
    expect(route.request().method()).toBe("PATCH");
    submitted.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(auth) });
  });
  await page.goto("/settings/general");
  await page.getByLabel("App name").fill("Unsaved name");
  await page.getByRole("button", { name: "Auth & Security" }).first().click();
  await expect(page.getByLabel("Cookie name")).toHaveValue("iacs_session");
  await page.getByLabel("Cookie name").fill("new_session");
  await page.getByRole("button", { name: "Save Settings" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  expect(JSON.stringify(submitted)).not.toContain("app_name");
  expect(JSON.stringify(submitted)).toContain("auth_cookie_name");
  expect(unexpected).toEqual([]);
});

test("blocks dependent editors after an initial read failure and recovers on retry", async ({ page }) => {
  const unexpected = await installFixtures(page);
  let unavailable = true;
  await page.route("**/api/v1/schedules", async (route) => {
    if (unavailable) await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic dependency outage" }) });
    else await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([schedule]) });
  });
  await page.goto("/schedules");
  await expect(page.getByRole("heading", { name: "Site data unavailable" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New Schedule" })).toHaveCount(0);
  unavailable = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("button", { name: "New Schedule" })).toBeVisible();
  await expect(page.getByText("Weekday access").first()).toBeVisible();
  expect(unexpected).toEqual([]);
});

test("keeps a dirty editor open during a pending save and sends one mutation", async ({ page }) => {
  const unexpected = await installFixtures(page);
  let releaseConfirmation: (() => void) | undefined;
  let confirmations = 0;
  let mutations = 0;
  await page.route("**/api/v1/action-confirmations", async (route) => {
    confirmations += 1;
    await new Promise<void>((resolve) => { releaseConfirmation = resolve; });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ confirmation_id: "test-intent", confirmation_token: "test-token", action: "group.create", expires_at: now }) });
  });
  await page.route("**/api/v1/groups", async (route) => {
    if (route.request().method() === "GET") await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([group]) });
    else { mutations += 1; await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...group, name: "Pending draft" }) }); }
  });
  await page.goto("/groups");
  await page.getByRole("button", { name: "Add Group" }).click();
  const editor = page.getByRole("dialog", { name: "Group" });
  await editor.getByLabel("Group name").fill("Pending draft");
  await editor.getByRole("button", { name: "Save Group" }).click();
  await expect.poll(() => confirmations).toBe(1);
  await editor.dispatchEvent("submit");
  await page.keyboard.press("Escape");
  await editor.getByRole("button", { name: "Cancel" }).click();
  await page.locator(".modal-backdrop").dispatchEvent("mousedown");
  await expect(editor).toBeVisible();
  expect(confirmations).toBe(1);
  releaseConfirmation?.();
  await expect(editor).toHaveCount(0);
  expect(mutations).toBe(1);
  expect(unexpected).toEqual([]);
});

test("clears search and account state across logout and another login", async ({ page }) => {
  const unexpected = await installFixtures(page);
  await page.route("**/api/v1/auth/logout", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "logged_out" }) }));
  await page.route("**/api/v1/auth/login", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(account("standard")) }));
  await page.goto("/people");
  await page.getByRole("button", { name: "Search anything" }).click();
  await page.getByRole("dialog", { name: "Global search" }).getByRole("combobox").fill("Ada");
  await page.getByRole("option", { name: /Ada Resident/ }).first().click();
  await page.getByRole("dialog", { name: "Global search" }).getByRole("button", { name: "Open" }).click();
  await expect(page.getByRole("textbox", { name: "Page filter" })).toHaveValue("Ada");
  await page.getByRole("button", { name: "Account menu for Alex Operator" }).click();
  await page.getByRole("menuitem", { name: "Logout" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.getByLabel("Username").fill("standard");
  await page.getByLabel("Password").fill("synthetic-only");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Dashboard", exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Page filter" })).toHaveCount(0);
  expect(unexpected).toEqual([]);
});

test("retains Alfred's draft through a failed synthetic attachment upload and removal", async ({ page }) => {
  const unexpected = await installFixtures(page);
  await page.route("**/api/v1/ai/chat/upload*", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic upload unavailable" }) }));
  await page.goto("/");
  await page.getByRole("button", { name: "Open Alfred" }).click();
  const composer = page.getByPlaceholder("Ask Alfred...");
  await composer.fill("Keep this investigation draft");
  await page.locator("input.chat-file-input").setInputFiles({ name: "synthetic.txt", mimeType: "text/plain", buffer: Buffer.from("synthetic attachment") });
  await expect(page.getByLabel("Pending attachments")).toContainText("Synthetic upload unavailable");
  await expect(composer).toHaveValue("Keep this investigation draft");
  await page.getByRole("button", { name: "Remove synthetic.txt" }).click();
  await expect(page.getByLabel("Pending attachments")).toHaveCount(0);
  await expect(composer).toHaveValue("Keep this investigation draft");
  expect(unexpected).toEqual([]);
});

test("shows movement detail without clipping on desktop and mobile", async ({ page }, testInfo) => {
  const unexpected = await installFixtures(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/movements");
  await page.getByRole("button", { name: /View movement AB12 CDE/ }).click();
  await expect(page.getByText("A gate command was required, but no command record is available.").first()).toBeVisible();
  const detail = page.locator(".movement-detail-panel");
  const bounds = await detail.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(1441);
  await page.screenshot({ path: testInfo.outputPath("movement-detail-desktop-after.png"), fullPage: false });
  await detail.locator(".movement-detail-section").last().scrollIntoViewIfNeeded();
  await expect(detail.locator(".movement-detail-section").last()).toBeInViewport({ ratio: 0.1 });
  await page.screenshot({ path: testInfo.outputPath("movement-detail-desktop-lower-after.png"), fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileBounds = await detail.boundingBox();
  expect(mobileBounds).not.toBeNull();
  expect(mobileBounds!.x).toBeGreaterThanOrEqual(0);
  expect(mobileBounds!.x + mobileBounds!.width).toBeLessThanOrEqual(391);
  await expect(page.locator(".chat-widget")).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath("movement-detail-mobile-after.png"), fullPage: false });
  await detail.locator(".movement-detail-section").last().scrollIntoViewIfNeeded();
  await expect(detail.locator(".movement-detail-section").last()).toBeInViewport({ ratio: 0.1 });
  await page.screenshot({ path: testInfo.outputPath("movement-detail-mobile-lower-after.png"), fullPage: false });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(unexpected).toEqual([]);
});

test("Settings hub adapts across screens and themes using the console theme control", async ({ page }, testInfo) => {
  const unexpected = await installFixtures(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Dismiss chat prompt" }).click();
  const hub = page.getByRole("region", { name: "Settings pages" });
  await expect(hub.getByRole("button")).toHaveCount(13);
  await expect(page.getByRole("heading", { name: "Appearance" })).toHaveCount(0);
  await expect(page.getByRole("radio")).toHaveCount(0);
  const themeControl = page.getByRole("button", { name: "Theme", exact: true });

  for (const [width, height, columns] of [[1440, 1000, 3], [1024, 900, 2], [390, 844, 1]]) {
    await page.setViewportSize({ width, height });
    let lightSurface = "";
    let darkSurface = "";
    for (const mode of ["Light", "Dark", "System"]) {
      await themeControl.press("Enter");
      await expect(page.locator("html")).toHaveAttribute("data-theme", mode.toLowerCase());
      await expect.poll(() => hub.evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").length)).toBe(columns);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
      const surface = await page.locator(".settings-home-group").first().evaluate((node) => getComputedStyle(node).backgroundColor);
      if (mode === "Light") lightSurface = surface;
      if (mode === "Dark") { darkSurface = surface; expect(darkSurface).not.toBe(lightSurface); }
      if (mode === "System") await expect(page.locator(".settings-home-group").first()).toHaveCSS("background-color", darkSurface);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: testInfo.outputPath(`settings-hub-${width}-${mode.toLowerCase()}.png`), fullPage: true, animations: "disabled" });
    }
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const destinationBottom = await hub.getByRole("button").evaluateAll((nodes) => Math.max(...nodes.map((node) => node.getBoundingClientRect().bottom)));
    const launcher = await page.getByRole("button", { name: "Open Alfred" }).boundingBox();
    expect(launcher).not.toBeNull();
    expect(destinationBottom).toBeGreaterThan(0);
    if (width > 768) expect(destinationBottom).toBeLessThanOrEqual(launcher!.y);
    else await expect(page.locator(".chat-widget")).toHaveCSS("position", "static");
  }

  await themeControl.press("Enter");
  await themeControl.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(unexpected).toEqual([]);
});

test("Settings hub searches titles, descriptions and groups and navigates to a destination", async ({ page }) => {
  const unexpected = await installFixtures(page);
  await page.goto("/settings");
  const hub = page.getByRole("region", { name: "Settings pages" });
  const search = page.getByRole("searchbox", { name: "Find a setting" });
  await search.fill("  gAtEs  ");
  await expect(hub.getByRole("button", { name: "Gates", exact: true })).toBeVisible();
  await expect(hub.getByRole("button")).toHaveCount(1);
  await expect(page.locator(".settings-home-count")).toHaveText("1 result");
  await search.fill("fine-tune");
  await expect(hub.getByRole("button", { name: "LPR Tuning" })).toBeVisible();
  await search.fill("Access & Detection");
  await expect(hub.getByRole("button")).toHaveCount(5);
  await expect(hub.getByRole("heading", { name: "Administration" })).toHaveCount(0);
  await page.getByRole("button", { name: "Clear search" }).click();
  await expect(search).toBeFocused();
  await expect(hub.getByRole("button")).toHaveCount(13);
  await search.fill("no matching destination");
  await expect(page.getByRole("heading", { name: "No settings found" })).toBeVisible();
  await expect(page.locator(".settings-home-count")).toHaveText("0 results");
  await page.getByRole("button", { name: "Show all settings" }).click();
  await expect(search).toHaveValue("");
  await expect(search).toBeFocused();
  await hub.getByRole("button", { name: "General", exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/general$/);
  expect(unexpected).toEqual([]);
});

test("Settings hub filters restricted destinations before searching and counting", async ({ page }) => {
  const unexpected = await installFixtures(page, "standard");
  await page.goto("/settings");
  const hub = page.getByRole("region", { name: "Settings pages" });
  await expect(hub.getByRole("button")).toHaveCount(6);
  await expect(page.locator(".settings-home-count")).toHaveText("6 settings");
  await expect(hub.getByRole("heading", { name: "Automation & Connectivity" })).toHaveCount(0);
  await page.getByRole("searchbox", { name: "Find a setting" }).fill("Missed Exit Recovery");
  await expect(page.locator(".settings-home-count")).toHaveText("0 results");
  await expect(hub.getByRole("button", { name: "Missed Exit Recovery" })).toHaveCount(0);
  expect(unexpected).toEqual([]);
});

test("explains restricted direct URLs without admin reads", async ({ page }) => {
  const unexpected = await installFixtures(page, "standard");
  await page.goto("/settings/automations");
  await expect(page.getByRole("heading", { name: "Administrator access required" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Automations" })).toHaveCount(0);
  await page.goto("/settings/users");
  await expect(page.getByRole("heading", { name: "Administrator access required" })).toBeVisible();
  await page.goto("/settings/command-history");
  await expect(page.getByRole("heading", { name: "Administrator access required" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Command History" })).toHaveCount(0);
  expect(unexpected).toEqual([]);
});
