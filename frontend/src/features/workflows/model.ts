import React from "react";
import { formatDate } from "../../lib/format";

export type NotificationStatusFilter = "all" | "active" | "inactive";

export type NotificationFilterCounts = Record<NotificationStatusFilter, number>;

export type WorkflowRuleMenuState = {
  id: string;
  left: number;
  top: number;
};

export type WorkflowRuleStatusFeedback = {
  nonce: number;
  ruleId: string;
  status: "paused" | "resumed" | "saved";
};

export type WorkflowFeedback = { tone: "success" | "error" | "info"; text: string };

export type WorkflowTriggerCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  order: number;
};

export type TwoPaneCategory = {
  id: string;
  label: string;
  count: number;
  icon?: React.ElementType;
  disabled?: boolean;
};

export type WorkflowRuleBase = { id: string; name: string; is_active: boolean; last_fired_at?: string | null };

export type WorkflowListCategory<Rule extends WorkflowRuleBase> = { id: string; label: string; icon: React.ElementType; rules: Rule[] };

export type WorkflowRuleListKind = "automation" | "notification";

export function groupRulesByTrigger<Rule>(
  rules: Rule[],
  categoryByTrigger: Map<string, WorkflowTriggerCategory>,
  triggerForRule: (rule: Rule) => string,
  uncategorizedIcon: React.ElementType
): Array<{ id: string; label: string; icon: React.ElementType; rules: Rule[] }> {
  const uncategorizedCategory: WorkflowTriggerCategory = {
    id: "other",
    label: "Other",
    icon: uncategorizedIcon,
    order: Number.MAX_SAFE_INTEGER,
  };
  const grouped = new Map<string, { id: string; label: string; icon: React.ElementType; order: number; rules: Rule[] }>();
  rules.forEach((rule) => {
    const category = categoryByTrigger.get(triggerForRule(rule)) ?? uncategorizedCategory;
    const current = grouped.get(category.id);
    if (current) {
      current.rules.push(rule);
      return;
    }
    grouped.set(category.id, { ...category, rules: [rule] });
  });
  return Array.from(grouped.values())
    .sort((left, right) => left.order - right.order || left.label.localeCompare(right.label))
    .map(({ order: _order, ...category }) => category);
}

export function toggleStringList(value: unknown, item: string) {
  const current = Array.isArray(value) ? value.map(String) : [];
  return current.includes(item) ? current.filter((entry) => entry !== item) : [...current, item];
}

export function normalizeIdentityName(value: string) {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

export function matchesSearchText(value: string, query: string) {
  if (!query) return true;
  return value.toLowerCase().includes(query);
}

export function stringifyTemplateValue(value: unknown) {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

export function pluralize(word: string, count: number) {
  return count === 1 ? word : `${word}s`;
}

export function renderWorkflowTemplate(template: string, context: Record<string, string>) {
  return template.replace(/@([A-Za-z][A-Za-z0-9_]*)/g, (_, token: string) => context[token] ?? "").trim();
}

export function draftId(prefix: string) {
  return `draft-${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function formatCompactLastFired(value?: string | null) {
  if (!value) return "never";
  return formatRelativeTime(value);
}

function formatRelativeTime(value: string) {
  const date = new Date(value);
  const timestamp = date.getTime();
  if (Number.isNaN(timestamp)) return formatDate(value);
  const diffSeconds = Math.round((timestamp - Date.now()) / 1000);
  const absSeconds = Math.abs(diffSeconds);
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["week", 60 * 60 * 24 * 7],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  for (const [unit, seconds] of units) {
    if (absSeconds >= seconds) {
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(diffSeconds / seconds), unit);
    }
  }
  return "just now";
}
