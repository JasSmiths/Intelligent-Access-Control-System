import React from "react";
import type { UnifiProtectCamera } from "../../api/types";
import { workflowApi } from "../../api/workflows";
import type { NotificationFilterCounts, NotificationStatusFilter, WorkflowRuleStatusFeedback } from "./model";

// One request owner for both workflow lists. A superseded response cannot replace
// current data, and background refresh leaves an open editor mounted.
export function useWorkflowData<Data extends { rules: { id: string }[] }>(
  fetchData: (options: { signal: AbortSignal }) => Promise<Data>, refreshToken: number
) {
  const [data, setData] = React.useState<Data | null>(null);
  const [rules, updateRules] = React.useState<Data["rules"]>([] as Data["rules"]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const request = React.useRef<AbortController | null>(null);
  const load = React.useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setError("");
    try {
      const result = await fetchData({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setData(result);
      updateRules(result.rules as Data["rules"]);
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(failure instanceof Error ? failure.message : "Unable to load workflows.");
      // Read failure stays distinct from a mutation that already succeeded.
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [fetchData]);
  React.useEffect(() => {
    void load().catch(() => undefined);
    return () => request.current?.abort();
  }, [load, refreshToken]);
  const setRules = React.useCallback((update: React.SetStateAction<Data["rules"]>) => {
    // A completed mutation is newer than a previously started list read.
    request.current?.abort();
    updateRules(update);
    setLoading(false);
  }, []);
  return { data, rules, setRules, loading, error, load };
}

export function useNotificationCameras(enabled: boolean, refreshToken: number) {
  const [cameras, setCameras] = React.useState<UnifiProtectCamera[]>([]);
  React.useEffect(() => {
    const controller = new AbortController();
    if (enabled) {
      workflowApi.getNotificationCameras({ signal: controller.signal })
        .then((result) => { if (!controller.signal.aborted) setCameras(result); })
        .catch(() => { if (!controller.signal.aborted) setCameras([]); });
    } else setCameras([]);
    return () => controller.abort();
  }, [enabled, refreshToken]);
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
