import type { VariableRecipientRestriction } from "../api/workflows";

export function templateOccurrences(value: string) {
  return Array.from(value.matchAll(/@([A-Za-z][A-Za-z0-9_]*)/g));
}

export function restrictionsAfterReplacement(value: string, restrictions: VariableRecipientRestriction[], from: number, to: number, replacement: string) {
  const before = templateOccurrences(value);
  const after = templateOccurrences(value.slice(0, from) + replacement + value.slice(to));
  return restrictions.flatMap((rule) => {
    const match = before[rule.occurrence];
    if (!match || match[1] !== rule.name) return [];
    const start = match.index!;
    const end = start + match[0].length;
    if (from < end && to > start) return [];
    if (from === to && from > start && from < end) return [];
    const nextStart = start >= to ? start + replacement.length - (to - from) : start;
    const occurrence = after.findIndex((item) => item.index === nextStart && item[1] === rule.name);
    return occurrence < 0 ? [] : [{ ...rule, occurrence }];
  });
}

export function renderRecipientTemplate(value: string, context: Record<string, string>, restrictions: VariableRecipientRestriction[] = [], recipient?: string) {
  let occurrence = -1;
  let omitted = false;
  const rules = new Map(restrictions.map((rule) => [rule.occurrence, rule]));
  const names = new Map(Object.entries(context).map(([key, text]) => [key.replace(/[^a-z0-9]/gi, "").toLowerCase(), text]));
  const template = value.replace(/(@[A-Za-z][A-Za-z0-9_]*)[ \t]+(?=[,.;:!?])/g, "$1");
  let result = template.replace(/@([A-Za-z][A-Za-z0-9_]*)/g, (_, name: string) => {
    const rule = rules.get(++occurrence);
    if (rule && (!recipient || !rule.target_ids.includes(recipient))) {
      omitted = true;
      return "";
    }
    return names.get(name.replace(/[^a-z0-9]/gi, "").toLowerCase()) ?? "";
  }).trim();
  if (omitted) result = result.replace(/[ \t]{2,}/g, " ").replace(/[ \t]+([,.;:!?])/g, "$1");
  return result;
}

export function normalizeVariableRecipients(value: unknown): Record<string, VariableRecipientRestriction[]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result: Record<string, VariableRecipientRestriction[]> = {};
  for (const field of ["title_template", "message_template"]) {
    const entries = (value as Record<string, unknown>)[field];
    if (Array.isArray(entries) && entries.length) result[field] = entries.map((entry) => ({
      occurrence: entry.occurrence, name: entry.name, target_ids: Array.isArray(entry.target_ids) ? [...entry.target_ids] : [],
    }));
  }
  return result;
}
