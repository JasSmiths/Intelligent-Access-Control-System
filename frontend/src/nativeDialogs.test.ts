import { expect, it } from "vitest";

// Native browser dialogs ignore the app theme, block the page and cannot be tested;
// use ui/useConfirm (and the toast stack for errors) instead.
const sources = import.meta.glob<string>(["./**/*.{ts,tsx}", "!./**/*.test.{ts,tsx}", "!./test/**"], { eager: true, query: "?raw", import: "default" });
const nativeDialog = [
  /\b(?:window|globalThis|self|top|parent)\s*(?:\?\.|\.)\s*(?:confirm|alert|prompt)\b/,
  /\b(?:window|globalThis|self|top|parent)\s*\[\s*["'`](?:confirm|alert|prompt)["'`]\s*\]/,
  /(?<![.\w$])(?:confirm|alert|prompt)\s*\(/,
];
const offences = (source: string) => source.split("\n").filter((line) => nativeDialog.some((pattern) => pattern.test(line)));

it("detects every native dialog form it guards against", () => {
  for (const sample of ["window.confirm('x')", "window?.alert(x)", "globalThis.prompt('t', v)", "window['confirm']", "if (!confirm(`Delete?`)) return;", "alert(message)"]) {
    expect(offences(sample), sample).toHaveLength(1);
  }
  for (const sample of ["await askConfirm({ title })", "answerConfirm(\"confirm\")", "useConfirm()", "notification.alert(x)", "role=\"alertdialog\""]) {
    expect(offences(sample), sample).toHaveLength(0);
  }
});

it("never uses native browser confirm, alert or prompt dialogs in application source", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(50);
  const found = Object.entries(sources).flatMap(([file, source]) => offences(source).map((line) => `${file}: ${line.trim()}`));
  expect(found).toEqual([]);
});
