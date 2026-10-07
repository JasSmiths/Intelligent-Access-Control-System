import { expect, test } from "playwright/test";

// Equivalent visible output on the approved P1 baseline and the final P3 source.
// These are fixture request/payload measurements, not production latency claims.
for (const population of [12, 2000]) {
  test(`retains request and payload evidence for a ${population}-record directory workflow`, async ({ page }, testInfo) => {
    const now = "2026-10-07T12:00:00Z";
    let phase = "initial"; let saved = false; let updated = false; let blockVehicles = false;
    let releaseVehicles!: () => void;
    const blocked = new Promise<void>((resolve) => { releaseVehicles = resolve; });
    const reads: Array<{ phase: string; path: string; bytes: number }> = []; const unexpected: string[] = [];
    let send = () => { throw new Error("Synthetic stream not connected"); };
    const person = (index: number) => ({ id: `person-${index}`, first_name: index === 0 && saved ? "Updated" : "Ada", last_name: `Person${index}`, display_name: `${index === 0 && saved ? "Updated" : "Ada"} Person${index}`, group_id: null, group: null, category: null, schedule_id: null, schedule: null, vehicles: [], is_active: true, profile_photo_url: null, garage_door_entity_ids: [], home_assistant_presence_input_boolean_entity_ids: [] });
    const vehicle = (index: number) => ({ id: `vehicle-${index}`, registration_number: `SYN${String(index).padStart(4, "0")}`, make: "Ford", model: updated ? "Updated model" : "Original model", color: "Blue", description: "", is_active: true, person_id: `person-${index}`, person_ids: [`person-${index}`], owner: person(index).display_name, owners: [person(index).display_name], schedule_id: null, schedule: null, vehicle_photo_url: null });
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request(); const url = new URL(request.url());
      if (request.method() === "POST" && url.pathname === "/api/v1/action-confirmations") { await route.fulfill({ json: { confirmation_token: "inert-fixture-confirmation" } }); return; }
      if (request.method() === "PATCH" && url.pathname === "/api/v1/people/person-0") { saved = true; await route.fulfill({ json: person(0) }); return; }
      if (request.method() !== "GET") { unexpected.push(`${request.method()} ${url.pathname}`); await route.abort("blockedbyclient"); return; }
      let body: unknown;
      switch (url.pathname) {
        case "/api/v1/auth/status": body = { setup_required: false, authenticated: true, user: { id: "fixture-admin", username: "admin", first_name: "Alex", last_name: "Operator", full_name: "Alex Operator", role: "admin", is_active: true, preferences: { sidebarCollapsed: false } } }; break;
        case "/api/v1/people": case "/api/v1/vehicles": {
          const ids = url.searchParams.getAll("ids");
          const indices = ids.length ? ids.map((id) => Number(id.split("-").at(-1))) : Array.from({ length: Math.min(Number(url.searchParams.get("limit") ?? 100), population) }, (_, index) => index);
          body = { items: indices.map(url.pathname.endsWith("people") ? person : vehicle), total: ids.length || population, next_cursor: !ids.length && population > indices.length ? "next-page" : null };
          break;
        }
        case "/api/v1/groups": case "/api/v1/schedules": case "/api/v1/alerts": case "/api/v1/settings": body = []; break;
        case "/api/v1/maintenance/status": body = { is_active: false }; break;
        case "/api/v1/integrations/gate/status": body = { configured: false, connected: false, gate_entity_id: null, last_gate_state: "unknown", garage_door_entities: [] }; break;
        case "/api/v1/integrations/home-assistant/entities": body = { configured: false, connected: false, covers: [], input_booleans: [], mobile_app_notify_services: [] }; break;
        default: unexpected.push(`GET ${url.pathname}`); await route.abort("blockedbyclient"); return;
      }
      reads.push({ phase, path: `${url.pathname}${url.search}`, bytes: new TextEncoder().encode(JSON.stringify(body)).byteLength });
      if (blockVehicles && url.pathname === "/api/v1/vehicles") await blocked;
      await route.fulfill({ json: body });
    });
    await page.routeWebSocket("**/*", (socket) => {
      send = () => socket.send(JSON.stringify({ type: "vehicle_information.updated", payload: {}, created_at: now }));
      socket.onMessage((raw) => { const message = JSON.parse(String(raw)); if (message.type === "client.ping") socket.send(JSON.stringify({ type: "connection.pong", payload: { id: message.payload.id } })); });
    });
    async function stableReads() {
      let previous = -1; let stable = 0;
      await expect.poll(() => { if (previous === reads.length) stable++; else stable = 0; previous = reads.length; return stable; }, { intervals: [100], timeout: 5000 }).toBeGreaterThanOrEqual(3);
    }
    async function expandGroup() { const group = page.locator(".directory-group-header").first(); await expect(group).toBeVisible(); if (await group.getAttribute("aria-expanded") === "false") await group.click(); }
    await page.goto("/people"); await expandGroup();
    await expect(page.getByRole("button", { name: "Edit person Ada Person0", exact: true })).toBeVisible(); await stableReads();
    phase = "mutation";
    await page.getByRole("button", { name: "Edit person Ada Person0", exact: true }).click();
    await page.getByLabel("First name", { exact: true }).fill("Updated");
    await page.getByRole("button", { name: "Save Changes", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit person Updated Person0", exact: true })).toBeVisible(); await stableReads();
    phase = "navigation";
    await page.getByRole("button", { name: "Vehicles", exact: true }).click(); await expandGroup();
    const row = page.getByRole("button", { name: "Edit vehicle SYN0000", exact: true });
    await expect(row).toContainText("Original model"); await stableReads();
    phase = "realtime-burst"; blockVehicles = true; updated = true;
    send();
    await expect.poll(() => reads.filter((read) => read.phase === phase && read.path.startsWith("/api/v1/vehicles")).length, { timeout: 10_000 }).toBeGreaterThanOrEqual(1);
    for (let index = 1; index < 100; index++) send();
    blockVehicles = false; releaseVehicles();
    await expect(row).toContainText("Updated model", { timeout: 10_000 });
    // Include a possible five-second trailing read for both source versions.
    await page.waitForTimeout(5500); await stableReads();
    expect(unexpected).toEqual([]);
    const phases = ["initial", "mutation", "navigation", "realtime-burst"].map((phase) => ({ phase, requests: reads.filter((read) => read.phase === phase).length, bytes: reads.filter((read) => read.phase === phase).reduce((sum, read) => sum + read.bytes, 0) }));
    await testInfo.attach(`directory-${population}-requests`, { body: JSON.stringify({ population, phases, reads, unexpected }, null, 2), contentType: "application/json" });
  });
}
