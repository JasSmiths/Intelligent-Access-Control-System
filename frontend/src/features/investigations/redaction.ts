const SENSITIVE_KEY = /(authorization|cookie|password|passphrase|secret|token|api[_-]?key|webhook[_-]?key|credential|private[_-]?key)/i;
const SENSITIVE_VALUE = /(bearer\s+)[a-z0-9._~+\/-]+/gi;

export function redactEvidenceForDisplay(value: unknown, seen = new WeakSet<object>()): unknown {
  if (typeof value === "string") return value.replace(SENSITIVE_VALUE, "$1[REDACTED]");
  if (value == null || typeof value !== "object") return value;
  if (seen.has(value)) return "[CIRCULAR]";
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => redactEvidenceForDisplay(item, seen));
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [
    key,
    SENSITIVE_KEY.test(key) ? "[REDACTED]" : redactEvidenceForDisplay(item, seen)
  ]));
}

export function evidenceJson(value: unknown) {
  return JSON.stringify(redactEvidenceForDisplay(value), null, 2);
}
