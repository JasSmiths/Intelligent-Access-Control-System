import React from "react";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { workflowEventMatches, notificationCameraEventMatches } from "./invalidation";
import type { UnifiProtectCamera } from "../../api/types";
import { workflowApi } from "../../api/workflows";
import type { NotificationFilterCounts, NotificationStatusFilter, WorkflowRuleStatusFeedback } from "./model";

const EMPTY_RULES: never[] = [];

// One request owner for both workflow lists. A superseded response cannot replace
// current data, and background refresh leaves an open editor mounted.
export function useWorkflowData<Data extends { rules: { id: string }[] }>(
  fetchData: (options: { signal: AbortSignal }) => Promise<Data>
) {
  const [data, setData] = React.useState<Data | null>(null);
  const rules: Data["rules"] = data?.rules ?? EMPTY_RULES;
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const request = React.useRef<AbortController | null>(null);
  const read = React.useCallback(async (signal: AbortSignal) => {
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal.addEventListener("abort", abort, { once: true });
    request.current = controller;
    setError("");
    try {
      const result = await fetchData({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setData(result);
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(failure instanceof Error ? failure.message : "Unable to load workflows.");
      throw failure; // Read failure stays distinct from an accepted mutation.
    } finally {
      signal.removeEventListener("abort", abort);
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [fetchData]);
  const refresh = useCoordinatedRead(read, workflowEventMatches);
  // Refresh errors stay in this read owner; accepted mutation receipts remain accepted.
  const load = React.useCallback(async () => { await refresh().catch(() => undefined); }, [refresh]);
  const setRules = React.useCallback((update: React.SetStateAction<Data["rules"]>) => {
    // A completed mutation is newer than a previously started list read.
    request.current?.abort();
    setData((current) => current ? { ...current, rules: typeof update === "function" ? update(current.rules) : update } : current);
    setLoading(false);
  }, []);
  return { data, rules, setRules, loading, error, load };
}

export function useNotificationCameras(enabled: boolean) {
  const [cameras, setCameras] = React.useState<UnifiProtectCamera[]>([]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    try { const result = await workflowApi.getNotificationCameras({ signal }); if (!signal.aborted) setCameras(result); }
    catch (error) { if (!signal.aborted) setCameras([]); throw error; }
  }, []);
  useCoordinatedRead(read, notificationCameraEventMatches, enabled);
  React.useEffect(() => { if (!enabled) setCameras([]); }, [enabled]);
  return cameras;
}

export function useWorkflowRuleFilters<Rule extends { is_active: boolean }>(rules: Rule[]) {
  const [statusFilter, setStatusFilter] = React.useState<NotificationStatusFilter>("all");
  const filterCounts = React.useMemo<NotificationFilterCounts>(() => {
    return rules.reduce<NotificationFilterCounts>((counts, rule) => {
      counts.all += 1;
      if (rule.is_active) counts.active += 1;
      else counts.inactive += 1;
      return counts;
    }, { all: 0, active: 0, inactive: 0 });
  }, [rules]);
  const filteredRules = React.useMemo(() => {
    if (statusFilter === "active") return rules.filter((rule) => rule.is_active);
    if (statusFilter === "inactive") return rules.filter((rule) => !rule.is_active);
    return rules;
  }, [rules, statusFilter]);
  return { filterCounts, filteredRules, setStatusFilter, statusFilter };
}

export function useTransientRuleStatusFeedback() {
  const [ruleStatusFeedback, setRuleStatusFeedback] = React.useState<WorkflowRuleStatusFeedback | null>(null);

  React.useEffect(() => {
    if (!ruleStatusFeedback) return undefined;
    const timeout = window.setTimeout(() => {
      setRuleStatusFeedback((current) => current?.nonce === ruleStatusFeedback.nonce ? null : current);
    }, 3600);
    return () => window.clearTimeout(timeout);
  }, [ruleStatusFeedback]);

  return [ruleStatusFeedback, setRuleStatusFeedback] as const;
}

export function usePendingWorkflowIds() {
  const [pendingIds, setPendingIds] = React.useState<Set<string>>(() => new Set());
  const setPending = React.useCallback((id: string, pending: boolean) => {
    setPendingIds((current) => {
      const next = new Set(current);
      if (pending) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);
  return [pendingIds, setPending] as const;
}
