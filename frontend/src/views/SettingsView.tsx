import { ChevronRight, Search, ShieldCheck, SlidersHorizontal, X, Zap } from "lucide-react";
import React from "react";

import type { NavigateToView, UserAccount, ViewKey } from "../api/types";
import { settingsNavItems } from "../app/navigation";

const settingsHomeGroups: Array<{
  key: string;
  title: string;
  description: string;
  icon: React.ElementType;
  items: Array<{ key: ViewKey; description: string }>;
}> = [
  {
    key: "access",
    title: "Access & Detection",
    description: "Control entry and understand movement.",
    icon: ShieldCheck,
    items: [
      { key: "settings_gates", description: "Configure entry gates and access behaviour." },
      { key: "settings_garage_doors", description: "Manage garage doors and their controls." },
      { key: "settings_zones", description: "Define the areas used for presence and access." },
      { key: "settings_lpr", description: "Fine-tune number plate detection and decisions." },
      { key: "settings_missed_exit_recovery", description: "Review and recover missed vehicle departures." }
    ]
  },
  {
    key: "automation",
    title: "Automation & Connectivity",
    description: "Connect your site and make it work for you.",
    icon: Zap,
    items: [
      { key: "integrations", description: "Connect providers, devices, and external services." },
      { key: "settings_automations", description: "Build the rules that keep your site running." },
      { key: "settings_notifications", description: "Manage alerts, delivery channels, and messages." },
    ]
  },
  {
    key: "administration",
    title: "Administration",
    description: "Manage the console with confidence.",
    icon: SlidersHorizontal,
    items: [
      { key: "settings_general", description: "Set site preferences and maintenance options." },
      { key: "settings_auth", description: "Manage sign-in and security settings." },
      { key: "users", description: "Manage console accounts and permissions." },
      { key: "settings_command_history", description: "Review audited gate and garage commands." }
    ]
  }
];

export function SettingsView({
  currentUser,
  navigateToView
}: {
  currentUser: UserAccount;
  navigateToView: NavigateToView;
}) {
  const [search, setSearch] = React.useState("");
  const searchRef = React.useRef<HTMLInputElement>(null);
  const id = React.useId();
  const query = search.trim().toLowerCase();
  const visibleItems = settingsNavItems.filter((item) => !item.adminOnly || currentUser.role === "admin");
  const groups = settingsHomeGroups.map((group) => ({
    ...group,
    items: group.items.flatMap((presentation) => {
      const item = visibleItems.find((candidate) => candidate.key === presentation.key);
      if (!item || !`${item.label} ${presentation.description} ${group.title}`.toLowerCase().includes(query)) return [];
      return [{ ...item, description: presentation.description }];
    })
  })).filter((group) => group.items.length > 0);
  const resultCount = groups.reduce((count, group) => count + group.items.length, 0);
  const clearSearch = () => {
    setSearch("");
    searchRef.current?.focus();
  };

  return (
    <section className="view-stack settings-home" aria-labelledby={`${id}-title`}>
      <header className="settings-home-header">
        <div className="settings-home-intro">
          <div className="settings-home-title"><h1 id={`${id}-title`}>Settings</h1><span className="settings-home-count" role="status" aria-live="polite">{query ? `${resultCount} ${resultCount === 1 ? "result" : "results"}` : `${resultCount} settings`}</span></div>
          <p>Manage access, automation, and console preferences.</p>
        </div>
        <div className="settings-home-search" role="search" aria-label="Settings">
          <Search size={18} aria-hidden="true" />
          <input ref={searchRef} type="search" aria-label="Find a setting" placeholder="Find a setting…" value={search} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape" && search) { event.preventDefault(); clearSearch(); } }} />
          {search && <button type="button" aria-label="Clear search" onClick={clearSearch}><X size={16} aria-hidden="true" /></button>}
        </div>
      </header>

      <div className="settings-home-groups" role="region" aria-label="Settings pages">
        {groups.map((group) => {
          const GroupIcon = group.icon;
          return (
            <section className={`settings-home-group settings-home-group-${group.key}`} key={group.key} aria-labelledby={`${id}-${group.key}`}>
              <header className="settings-home-group-header">
                <span className="settings-home-group-icon"><GroupIcon size={21} aria-hidden="true" /></span>
                <h2 id={`${id}-${group.key}`}>{group.title}</h2>
                <p>{group.description}</p>
              </header>
              <div className="settings-home-group-items">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return <button className="settings-home-destination" key={item.key} type="button" aria-labelledby={`${id}-${item.key}-label`} aria-describedby={`${id}-${item.key}-description`} onClick={() => navigateToView(item.key)}>
                    <span className="settings-home-destination-icon"><Icon size={19} aria-hidden="true" /></span>
                    <span className="settings-home-destination-copy"><span id={`${id}-${item.key}-label`} className="settings-home-destination-title">{item.label}</span><span id={`${id}-${item.key}-description`} className="settings-home-destination-description">{item.description}</span></span>
                    <ChevronRight className="settings-home-destination-arrow" size={16} aria-hidden="true" />
                  </button>;
                })}
              </div>
            </section>
          );
        })}
        {resultCount === 0 && <div className="settings-home-empty">
          <Search size={26} aria-hidden="true" />
          <h2>No settings found</h2>
          <p>No settings match “{search.trim()}”. Try another title or keyword.</p>
          <button type="button" onClick={clearSearch}>Show all settings</button>
        </div>}
      </div>
    </section>
  );
}
