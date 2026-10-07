import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { UserAccount } from "../api/types";
import { settingsNavItems } from "../app/navigation";
import { SettingsView } from "./SettingsView";

const fetcher = vi.fn();
const groupNames = ["Access & Detection", "Automation & Connectivity", "Administration"];

beforeEach(() => {
  fetcher.mockReset();
  vi.stubGlobal("fetch", fetcher);
});

afterEach(() => {
  expect(fetcher).not.toHaveBeenCalled();
  cleanup();
  vi.unstubAllGlobals();
});

function renderSettings(role: UserAccount["role"] = "admin") {
  const currentUser = { id: `fixture-${role}`, role } as UserAccount;
  const navigateToView = vi.fn();
  const props = { currentUser, navigateToView };
  return { ...render(<SettingsView {...props} />), props, navigateToView };
}

function findSetting(query: string) {
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a setting" }), { target: { value: query } });
}

function settingsPages() {
  return within(screen.getByRole("region", { name: "Settings pages" }));
}

function visibleSettings() {
  return settingsNavItems.filter((item) => settingsPages().queryByRole("button", { name: item.label }));
}

function expectRestrictedSettingsHidden() {
  for (const item of settingsNavItems.filter((item) => item.adminOnly)) {
    expect(screen.queryByRole("button", { name: item.label })).not.toBeInTheDocument();
  }
}

it("shows each canonical destination once with its own accessible description", () => {
  renderSettings();
  expect(screen.getByText(`${settingsNavItems.length} settings`, { exact: true })).toBeInTheDocument();
  expect(settingsPages().getAllByRole("button")).toHaveLength(settingsNavItems.length);
  for (const item of settingsNavItems) {
    const buttons = settingsPages().getAllByRole("button", { name: item.label });
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleDescription();
    const descriptionIds = buttons[0].getAttribute("aria-describedby")?.split(/\s+/) ?? [];
    expect(descriptionIds.length).toBeGreaterThan(0);
    for (const id of descriptionIds) expect(document.getElementById(id)?.textContent?.trim()).toBeTruthy();
  }
  for (const group of groupNames) expect(screen.getByRole("heading", { name: group })).toBeInTheDocument();
});

it("filters destinations and groups for standard users before applying search", () => {
  renderSettings("standard");
  expect(screen.getByText("6 settings", { exact: true })).toBeInTheDocument();
  expect(settingsPages().getAllByRole("button")).toHaveLength(6);
  expectRestrictedSettingsHidden();
  expect(screen.getByRole("heading", { name: "Access & Detection" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Administration" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Automation & Connectivity" })).not.toBeInTheDocument();

  findSetting("Administration");
  expect(screen.getByText("2 results", { exact: true })).toBeInTheDocument();
  expect(visibleSettings().map((item) => item.label)).toEqual(["General", "Auth & Security"]);
  expectRestrictedSettingsHidden();

  for (const query of ["Missed Exit Recovery", "vehicle departures", "Automation & Connectivity"]) {
    findSetting(query);
    expect(screen.getByText("0 results", { exact: true })).toBeInTheDocument();
    expect(visibleSettings()).toHaveLength(0);
    expectRestrictedSettingsHidden();
  }
});

it("navigates to the canonical route key for every destination", () => {
  const { navigateToView } = renderSettings();
  for (const item of settingsNavItems) {
    fireEvent.click(settingsPages().getByRole("button", { name: item.label }));
    expect(navigateToView).toHaveBeenLastCalledWith(item.key);
  }
  expect(navigateToView).toHaveBeenCalledTimes(settingsNavItems.length);
});

it("matches titles and descriptions regardless of letter case and outer whitespace", () => {
  renderSettings();
  expect(screen.getByRole("searchbox", { name: "Find a setting" })).toHaveAttribute("placeholder", "Find a setting…");
  findSetting("  gArAgE dOoRs  ");
  expect(screen.getByText("1 result", { exact: true })).toBeInTheDocument();
  expect(visibleSettings().map((item) => item.label)).toEqual(["Garage Doors"]);

  findSetting("  NuMbEr PlAtE  ");
  expect(screen.getByText("1 result", { exact: true })).toBeInTheDocument();
  expect(visibleSettings().map((item) => item.label)).toEqual(["LPR Tuning"]);

  findSetting("   ");
  expect(screen.getByText(`${settingsNavItems.length} settings`, { exact: true })).toBeInTheDocument();
  expect(visibleSettings()).toHaveLength(settingsNavItems.length);
});

it("matches group names and hides groups without search results", () => {
  renderSettings();
  findSetting("  aCcEsS & dEtEcTiOn  ");
  expect(screen.getByText("5 results", { exact: true })).toBeInTheDocument();
  expect(visibleSettings().map((item) => item.key)).toEqual([
    "settings_gates", "settings_garage_doors", "settings_missed_exit_recovery", "settings_lpr", "settings_zones"
  ]);
  expect(screen.getByRole("heading", { name: "Access & Detection" })).toBeInTheDocument();
  for (const group of ["Automation & Connectivity", "Administration"]) {
    expect(screen.queryByRole("heading", { name: group })).not.toBeInTheDocument();
  }
});

it("clears a matching search and restores all destinations and group headings", () => {
  renderSettings();
  findSetting("Garage Doors");
  fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
  expect(screen.getByRole("searchbox", { name: "Find a setting" })).toHaveValue("");
  expect(screen.getByText(`${settingsNavItems.length} settings`, { exact: true })).toBeInTheDocument();
  expect(visibleSettings()).toHaveLength(settingsNavItems.length);
  for (const group of groupNames) expect(screen.getByRole("heading", { name: group })).toBeInTheDocument();
});

it("offers a reset when there are no results without rendering empty groups", () => {
  renderSettings();
  findSetting("no-setting-matches-this-query");
  expect(screen.getByText("0 results", { exact: true })).toBeInTheDocument();
  expect(visibleSettings()).toHaveLength(0);
  for (const group of groupNames) expect(screen.queryByRole("heading", { name: group })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show all settings" }));
  expect(screen.getByRole("searchbox", { name: "Find a setting" })).toHaveValue("");
  expect(screen.getByText(`${settingsNavItems.length} settings`, { exact: true })).toBeInTheDocument();
  expect(visibleSettings()).toHaveLength(settingsNavItems.length);
});
