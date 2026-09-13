import { expect, it } from "vitest";

const sources = import.meta.glob<string>(["./features/schedules/*.{ts,tsx}", "./features/workflows/*.{ts,tsx}", "./views/*.tsx", "./app/routes.tsx", "./shared.tsx"], { eager: true, query: "?raw", import: "default" });
const read = (name: string) => sources[`./${name}`];
const files = Object.keys(sources).map((name) => name.slice(2)).filter((name) => name.startsWith("features/") && !name.includes(".test."));
const resolveImport = (from: string, relative: string) => new URL(relative, new URL(from, "https://frontend.test/")).pathname.slice(1);

it("routes directly to feature owners without recreating aggregate facades", () => {
  for (const legacy of ["views/SchedulesView.tsx", "views/WorkflowViews.tsx", "features/workflows/WorkflowFeature.tsx", "shared.tsx"]) expect(read(legacy)).toBeUndefined();
  const routes = read("app/routes.tsx");
  for (const owner of ["schedules/SchedulesView", "workflows/AutomationsView", "workflows/NotificationsView"]) expect(routes).toContain(`import("../features/${owner}")`);
  for (const file of files) expect(read(file)).not.toMatch(/export\s+\*\s+from|\bfetch\s*\(/);
});
it("keeps shared workflow primitives independent of specific editors", () => {
  for (const file of ["model.ts", "components.tsx", "hooks.ts", "TemplateEditor.tsx"]) {
    expect(read(`features/workflows/${file}`)).not.toMatch(/from\s+["'][^"']*(?:AutomationsView|NotificationsView|NotificationEditor|AutomationEditor|notificationModel|automationModel)["']/);
  }
  expect(read("features/schedules/model.ts")).not.toMatch(/from\s+["'][^"']*(?:client|schedules|Editor|View|WeeklyScheduleGrid)["']/);
});
it("has no dependency cycles between the schedule and workflow modules", () => {
  const nodes = new Set(files);
  const graph = new Map<string, string[]>();
  for (const node of nodes) {
    const imports = [...read(node).matchAll(/(?:import|export)[\s\S]*?\bfrom\s+["'](\.[^"']+)["']/g)];
    graph.set(node, imports.flatMap((match) => [".ts", ".tsx"].map((extension) => resolveImport(node, match[1] + extension)).filter((target) => nodes.has(target))));
  }
  const visited = new Set<string>();
  function visit(node: string, stack: Set<string>) {
    if (stack.has(node)) throw new Error(`Ownership cycle at ${node}`);
    if (visited.has(node)) return;
    const next = new Set(stack).add(node);
    for (const dependency of graph.get(node) ?? []) visit(dependency, next);
    visited.add(node);
  }
  nodes.forEach((node) => visit(node, new Set()));
  expect(visited.size).toBe(nodes.size);
});
