import { describe, expect, it } from "vitest";
import { canAccessView, navigationItems, primaryNavItems, settingsNavItems, viewFromPath, viewPaths } from "./navigation";
import type { UserAccount, ViewKey } from "../api/types";

const standard = { role: "standard" } as UserAccount;
const admin = { role: "admin" } as UserAccount;

describe("console navigation registry", () => {
  it("covers every existing route exactly once and keeps its URL", () => {
    const keys = navigationItems.map((item) => item.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(keys)).toEqual(new Set(Object.keys(viewPaths) as ViewKey[]));
    for (const [key, path] of Object.entries(viewPaths)) expect(viewFromPath(path)).toBe(key);
  });

  it("groups operational destinations and hides backend admin readers", () => {
    expect(primaryNavItems.filter((item) => item.group === "Operations").map((item) => item.key)).toEqual(["dashboard", "events", "movements", "alerts"]);
    expect(primaryNavItems.filter((item) => item.group === "Access").map((item) => item.key)).toEqual(["people", "groups", "vehicles", "schedules", "passes"]);
    for (const key of ["logs", "users", "alfred_training", "integrations", "settings_automations", "settings_notifications", "settings_command_history"] as ViewKey[]) {
      expect(canAccessView(key, standard)).toBe(false);
      expect(canAccessView(key, admin)).toBe(true);
    }
    expect(canAccessView("settings_general", standard)).toBe(true);
    expect(canAccessView("settings_lpr", standard)).toBe(true);
    expect(settingsNavItems.some((item) => item.key === "groups")).toBe(false);
  });
});
