import {
AlertTriangle,
ArrowLeft,
Bell,
Bot,
CalendarDays,
Camera,
Car,
Check,
CheckCircle2,
ChevronDown,
ChevronRight,
CircleDot,
Clock3,
Construction,
Copy,
DoorOpen,
GitBranch,
Home,
MessageCircle,
Monitor,
MoreHorizontal,
Pencil,
Play,
PlugZap,
Plus,
Save,
Search,
Send,
ShieldCheck,
Smartphone,
Sparkles,
Split,
Trash2,
Trophy,
UserPlus,
UserRound,
Users,
Volume2,
Warehouse,
X,
Zap
} from "lucide-react";
import React from "react";
import { createPortal } from "react-dom";

import { displayUserName, formatDate, fromDateTimeLocal, notificationEventLabel, titleCase, toDateTimeLocal } from "../../lib/format";
import { notificationChannelMeta } from "../../lib/notifications";
import { Badge, Toolbar } from "../../ui/primitives";
import type { NotificationTriggerOption, Person, Schedule, UnifiProtectCamera, UserAccount, Vehicle } from "../../api/types";
import type { BadgeTone } from "../../ui/primitives";

import {
  workflowApi,
  type AutomationAction,
  type AutomationCatalogGroup,
  type AutomationCatalogItem,
  type AutomationCatalogResponse,
  type AutomationNode,
  type AutomationRule,
  type AutomationVariable,
  type AutomationVariableGroup,
  type NotificationAction,
  type NotificationActionableOption,
  type NotificationCatalogResponse,
  type NotificationCondition,
  type NotificationConditionType,
  type NotificationEndpoint,
  type NotificationGateMalfunctionStage,
  type NotificationGateMalfunctionStageOption,
  type NotificationIntegration,
  type NotificationRule,
  type NotificationTargetMode,
  type NotificationTriggerGroup,
  type NotificationVariable,
  type NotificationActionType,
  type PresenceConditionMode
} from "../../api/workflows";



const VariableRichTextEditor = React.lazy(() => import("../../VariableRichTextEditor"));

type NotificationStatusFilter = "all" | "active" | "inactive";

type NotificationFilterCounts = Record<NotificationStatusFilter, number>;

type NotificationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: NotificationRule[];
};

type WorkflowRuleMenuState = {
  id: string;
  left: number;
  top: number;
};

type WorkflowRuleStatusFeedback = {
  nonce: number;
  ruleId: string;
  status: "paused" | "resumed" | "saved";
};

type WorkflowFeedback = { tone: "success" | "error" | "info"; text: string };

const workflowStatusFilterOptions: Array<{ key: NotificationStatusFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "inactive", label: "Inactive" },
];

function workflowRuleStatusFeedbackLabel(status: WorkflowRuleStatusFeedback["status"]) {
  if (status === "paused") return "Paused";
  if (status === "saved") return "Saved";
  return "Resumed";
}

function useRefreshableWorkflowLoad(load: () => Promise<void>, refreshToken: number) {
  const lastRefreshTokenRef = React.useRef(refreshToken);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
  }, [load, refreshToken]);
}

function useWorkflowRuleFilters<Rule extends { is_active: boolean }>(rules: Rule[]) {
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

function useTransientRuleStatusFeedback() {
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

function usePendingWorkflowIds() {
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

type AutomationRuleCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  rules: AutomationRule[];
};

type WorkflowTriggerCategory = {
  id: string;
  label: string;
  icon: React.ElementType;
  order: number;
};

type TwoPaneCategory = {
  id: string;
  label: string;
  count: number;
  icon?: React.ElementType;
  disabled?: boolean;
};

type NotificationActionMethod = {
  id: string;
  actionType: NotificationActionType;
  label: string;
  provider: string;
  detail: string;
  icon: React.ElementType;
  tone: BadgeTone;
  targets: NotificationEndpoint[];
  targetMode: NotificationTargetMode;
  requiresTarget: boolean;
  defaultTargetIds: string[];
  unavailableReason?: string;
};

const defaultWorkflowActionTemplates: Record<NotificationActionType, Pick<NotificationAction, "title_template" | "message_template">> = {
  mobile: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName. Gate status: @GateStatus."
  },
  in_app: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName."
  },
  voice: {
    title_template: "",
    message_template: "@FirstName has arrived at the gate."
  },
  discord: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName. Gate status: @GateStatus."
  },
  whatsapp: {
    title_template: "@FirstName arrived at the gate",
    message_template: "@FirstName arrived in the @VehicleName. Gate status: @GateStatus."
  }
};

const vehicleTtsPhonetics: Record<string, string> = {
  BMW: "bee em double you",
  BYD: "bee why dee",
  GMC: "gee em see",
  MG: "em gee",
  VW: "vee double you",
  DS: "dee ess"
};

const vehicleTtsPhoneticPattern = new RegExp(
  `\\b(${Object.keys(vehicleTtsPhonetics).sort((left, right) => right.length - left.length).join("|")})\\b`
);

export function AutomationsView({ people, refreshToken, vehicles }: { people: Person[]; refreshToken: number; vehicles: Vehicle[] }) {
  const [catalog, setCatalog] = React.useState<AutomationCatalogResponse | null>(null);
  const [rules, setRules] = React.useState<AutomationRule[]>([]);
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [draft, setDraft] = React.useState<AutomationRule | null>(null);
  const [modal, setModal] = React.useState<"trigger" | "condition" | "action" | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const { filterCounts, filteredRules, setStatusFilter, statusFilter } = useWorkflowRuleFilters(rules);
  const [togglingRuleIds, setRuleToggling] = usePendingWorkflowIds();
  const [ruleStatusFeedback, setRuleStatusFeedback] = useTransientRuleStatusFeedback();
  const [feedback, setFeedback] = React.useState<WorkflowFeedback | null>(null);
  const [dryRun, setDryRun] = React.useState<Record<string, unknown> | null>(null);
  const [error, setError] = React.useState("");

  const triggerByType = React.useMemo(() => new Map((catalog?.triggers ?? []).flatMap((group) => (group.triggers ?? []).map((item) => [item.type, item]))), [catalog]);
  const conditionByType = React.useMemo(() => new Map((catalog?.conditions ?? []).flatMap((group) => (group.conditions ?? []).map((item) => [item.type, item]))), [catalog]);
  const actionByType = React.useMemo(() => new Map((catalog?.actions ?? []).flatMap((group) => (group.actions ?? []).map((item) => [item.type, item]))), [catalog]);
  const activeTriggerType = draft?.triggers[0]?.type ?? "";
  const variables = React.useMemo(() => automationVariablesForTrigger(catalog?.variables ?? [], activeTriggerType), [catalog, activeTriggerType]);
  const previewContext = catalog?.mock_context ?? {};
  const renderedReasons = React.useMemo(() => (draft?.actions ?? []).map((action) => ({
    ...action,
    renderedReason: renderWorkflowTemplate(action.reason_template ?? "", previewContext)
  })), [draft, previewContext]);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await workflowApi.getAutomationData();
      setCatalog(data.catalog);
      setRules(data.rules);
      setUsers(data.users);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load automation rules.");
    } finally {
      setLoading(false);
    }
  }, []);

  useRefreshableWorkflowLoad(load, refreshToken);

  const updateDraft = (updater: (rule: AutomationRule) => AutomationRule) => {
    setDraft((current) => updater(current ?? createAutomationDraft()));
    setDryRun(null);
  };

  const addAutomation = () => {
    setDraft(createAutomationDraft());
    setModal(null);
    setFeedback(null);
    setDryRun(null);
  };

  const save = async () => {
    if (!draft) return;
    if (!draft.triggers.length) {
      setFeedback({ tone: "error", text: "Add at least one trigger before saving." });
      return;
    }
    if (!draft.actions.length) {
      setFeedback({ tone: "error", text: "Add at least one action before saving." });
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const payload = automationRulePayload(draft);
      const saved = await workflowApi.saveAutomationRule(draft, payload);
      await load();
      setRules((current) => current.map((item) => item.id === saved.id ? saved : item));
      setDraft(null);
      setModal(null);
      setDryRun(null);
      setFeedback({ tone: "success", text: "Automation saved. It will run when its trigger fires." });
    } catch (saveError) {
      setFeedback({ tone: "error", text: saveError instanceof Error ? saveError.message : "Unable to save automation." });
    } finally {
      setSaving(false);
    }
  };

  const deleteRule = async (rule: AutomationRule) => {
    if (rule.id.startsWith("draft-")) {
      setDraft(null);
      return;
    }
    if (!window.confirm(`Delete ${rule.name}?`)) return;
    try {
      await workflowApi.deleteAutomationRule(rule);
      setDraft(null);
      await load();
      setFeedback({ tone: "success", text: "Automation deleted." });
    } catch (deleteError) {
      setFeedback({ tone: "error", text: deleteError instanceof Error ? deleteError.message : "Unable to delete automation." });
    }
  };

  const toggleActive = async (rule: AutomationRule, isActive: boolean) => {
    if (rule.id.startsWith("draft-")) return;
    setFeedback(null);
    setRuleToggling(rule.id, true);
    try {
      const updated = await workflowApi.toggleAutomationRule(rule, isActive);
      setRules((current) => current.map((item) => item.id === updated.id ? updated : item));
      setDraft((current) => current?.id === updated.id ? updated : current);
      setRuleStatusFeedback({
        nonce: Date.now(),
        ruleId: updated.id,
        status: updated.is_active ? "resumed" : "paused",
      });
    } catch (toggleError) {
      setFeedback({ tone: "error", text: toggleError instanceof Error ? toggleError.message : "Unable to update automation." });
    } finally {
      setRuleToggling(rule.id, false);
    }
  };

  const runDryRun = async () => {
    if (!draft) return;
    setFeedback({ tone: "info", text: "Running automation dry-run." });
    try {
      const result = await workflowApi.runAutomationDryRun(automationRulePayload(draft));
      setDryRun(result);
      setFeedback({ tone: "success", text: "Dry-run complete. Actions were previewed only; no sync or device commands were executed." });
    } catch (dryRunError) {
      setFeedback({ tone: "error", text: dryRunError instanceof Error ? dryRunError.message : "Dry-run failed." });
    }
  };

  const parseAiSchedule = async (trigger: AutomationNode) => {
    const text = String(trigger.config.natural_text ?? "").trim();
    if (!text) {
      setFeedback({ tone: "error", text: "Enter a natural-language schedule first." });
      return;
    }
    setFeedback({ tone: "info", text: "Parsing schedule text." });
    try {
      const parsed = await workflowApi.parseAutomationSchedule(text);
      updateDraft((rule) => ({
        ...rule,
        triggers: rule.triggers.map((item) => item.id === trigger.id ? {
          ...item,
          config: {
            ...item.config,
            cron_expression: parsed.cron_expression ?? "",
            timezone: parsed.timezone ?? "Europe/London",
            end_at: parsed.end_at ?? "",
            summary: parsed.summary ?? text
          }
        } : item)
      }));
      setFeedback({ tone: parsed.requires_review ? "error" : "success", text: parsed.requires_review ? "Schedule parsed but needs review." : "Schedule parsed." });
    } catch (parseError) {
      setFeedback({ tone: "error", text: parseError instanceof Error ? parseError.message : "Schedule parsing failed." });
    }
  };

  if (loading) {
    return (
      <section className="view-stack notifications-page workflow-notifications-page">
        <Toolbar title="Automations" count={0} icon={GitBranch} />
        <div className="loading-panel">Loading automation rules</div>
      </section>
    );
  }

  return (
    <section className="view-stack notifications-page workflow-notifications-page">
      <Toolbar title="Automations" count={rules.length} icon={GitBranch}>
        <button className="secondary-button" onClick={addAutomation} type="button">
          <Plus size={15} /> Add Automation
        </button>
      </Toolbar>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {feedback && !draft ? <div className={`notification-feedback ${feedback.tone}`}>{feedback.text}</div> : null}

      <WorkflowStatusFilters
        activeFilter={statusFilter}
        ariaLabel="Automation status filter"
        counts={filterCounts}
        onFilterChange={setStatusFilter}
      />

      <AutomationWorkflowList
        activeId={draft?.id ?? ""}
        rules={filteredRules}
        ruleStatusFeedback={ruleStatusFeedback}
        statusFilter={statusFilter}
        totalRuleCount={rules.length}
        triggerGroups={catalog?.triggers ?? []}
        togglingRuleIds={togglingRuleIds}
        onDelete={deleteRule}
        onSelect={(rule) => {
          setDraft(cloneAutomationRule(rule));
          setDryRun(null);
          setFeedback(null);
        }}
        onToggleActive={toggleActive}
      />

      {draft ? (
        <div className="modal-backdrop workflow-editor-backdrop" role="presentation">
          <div className={modal ? "modal-card workflow-editor-modal selector-mode" : "modal-card workflow-editor-modal"} role="dialog" aria-modal="true">
            <>
              <div
                className={modal ? "workflow-modal-panel selector" : "workflow-modal-panel editor"}
                key={modal ?? "editor"}
              >
                {modal ? (
                  <AutomationSelectionModal
                    groups={catalog?.[`${modal}s` as "triggers" | "conditions" | "actions"] ?? []}
                    kind={modal}
                    onClose={() => setModal(null)}
                    onSelect={(node) => {
                      updateDraft((rule) => ({
                        ...rule,
                        [modal === "action" ? "actions" : `${modal}s`]: [
                          ...(modal === "action" ? rule.actions : modal === "condition" ? rule.conditions : rule.triggers),
                          node
                        ]
                      } as AutomationRule));
                      setModal(null);
                    }}
                  />
                ) : (
                  <>
                    <div className="modal-header">
                      <div>
                        <h2>{draft.id.startsWith("draft-") ? "Add Automation" : "Edit Automation"}</h2>
                        <p>Build the Trigger, If, and Then flow for autonomous system actions.</p>
                      </div>
                      <button className="icon-button" onClick={() => { setDraft(null); setModal(null); }} type="button" aria-label="Close automation editor"><X size={16} /></button>
                    </div>
                    <div className="workflow-editor-modal-grid">
                      <div className="workflow-editor-column">
                        <section className="notification-editor-panel workflow-builder-panel">
                          <div className="notification-editor-header workflow-editor-header">
                            <div>
                              <span className="eyebrow">Name</span>
                              <input aria-label="Automation name" value={draft.name} onChange={(event) => updateDraft((rule) => ({ ...rule, name: event.target.value }))} />
                            </div>
                            <div className="notification-editor-actions">
                              <label className={draft.is_active ? "notification-switch active" : "notification-switch"}>
                                <input checked={draft.is_active} onChange={(event) => updateDraft((rule) => ({ ...rule, is_active: event.target.checked }))} type="checkbox" />
                                <span>{draft.is_active ? "Active" : "Paused"}</span>
                              </label>
                              <button className="icon-button danger" onClick={() => deleteRule(draft)} type="button" aria-label="Delete automation"><Trash2 size={15} /></button>
                            </div>
                          </div>
                          <label className="field compact-field">
                            <span>Description</span>
                            <input value={draft.description} onChange={(event) => updateDraft((rule) => ({ ...rule, description: event.target.value }))} placeholder="Optional operator note" />
                          </label>
                          <div className="workflow-vertical">
                            <WorkflowBlock badge="When" tone="blue" title="Trigger" required>
                              <AutomationNodeStack
                                actionMeta={actionByType}
                                conditionMeta={conditionByType}
                                garageDoors={catalog?.garage_doors ?? []}
                                kind="trigger"
                                nodes={draft.triggers}
                                people={people}
                                triggerMeta={triggerByType}
                                users={users}
                                vehicles={vehicles}
                                onAdd={() => setModal("trigger")}
                                onChange={(node) => updateDraft((rule) => ({ ...rule, triggers: rule.triggers.map((item) => item.id === node.id ? node : item) }))}
                                onParseAiSchedule={parseAiSchedule}
                                onRemove={(node) => updateDraft((rule) => ({ ...rule, triggers: rule.triggers.filter((item) => item.id !== node.id) }))}
                              />
                            </WorkflowBlock>
                            <WorkflowBlock badge="If" tone="amber" title="Conditions" optional>
                              <AutomationNodeStack
                                actionMeta={actionByType}
                                conditionMeta={conditionByType}
                                garageDoors={catalog?.garage_doors ?? []}
                                kind="condition"
                                nodes={draft.conditions}
                                people={people}
                                triggerMeta={triggerByType}
                                users={users}
                                vehicles={vehicles}
                                onAdd={() => setModal("condition")}
                                onChange={(node) => updateDraft((rule) => ({ ...rule, conditions: rule.conditions.map((item) => item.id === node.id ? node : item) }))}
                                onRemove={(node) => updateDraft((rule) => ({ ...rule, conditions: rule.conditions.filter((item) => item.id !== node.id) }))}
                              />
                            </WorkflowBlock>
                            <WorkflowBlock badge="Then" tone="green" title="Actions" required>
                              <AutomationNodeStack
                                actionMeta={actionByType}
                                conditionMeta={conditionByType}
                                garageDoors={catalog?.garage_doors ?? []}
                                kind="action"
                                nodes={draft.actions}
                                notificationRules={catalog?.notification_rules ?? []}
                                people={people}
                                triggerMeta={triggerByType}
                                users={users}
                                variables={variables}
                                vehicles={vehicles}
                                onAdd={() => setModal("action")}
                                onChange={(node) => updateDraft((rule) => ({ ...rule, actions: rule.actions.map((item) => item.id === node.id ? node as AutomationAction : item) }))}
                                onRemove={(node) => updateDraft((rule) => ({ ...rule, actions: rule.actions.filter((item) => item.id !== node.id) }))}
                              />
                            </WorkflowBlock>
                          </div>
                          <div className="modal-actions workflow-editor-footer">
                            {feedback ? <div className={`notification-feedback workflow-editor-feedback ${feedback.tone}`} role="status">{feedback.text}</div> : null}
                            <button className="secondary-button" onClick={runDryRun} type="button"><Play size={15} /> Dry Run</button>
                            <button className="secondary-button" onClick={() => setDraft(null)} type="button">Cancel</button>
                            <button className="primary-button" onClick={save} disabled={saving} type="button"><Save size={15} /> {saving ? "Saving..." : "Save"}</button>
                          </div>
                        </section>
                      </div>
                      <AutomationPreviewPanel actions={renderedReasons} dryRun={dryRun} />
                    </div>
                  </>
                )}
              </div>
            </>
          </div>
        </div>
      ) : null}
    </section>
  );
}

type WorkflowRuleBase = { id: string; name: string; is_active: boolean; last_fired_at?: string | null };

type WorkflowListCategory<Rule extends WorkflowRuleBase> = { id: string; label: string; icon: React.ElementType; rules: Rule[] };

type WorkflowRuleListKind = "automation" | "notification";

function WorkflowRuleList<Rule extends WorkflowRuleBase>({
  activeId, ariaLabel, groupedRules, kind, renderConfigChips, ruleStatusFeedback, statusFilter, summaryAriaLabel, tableIdPrefix, totalRuleCount, togglingRuleIds, onDelete, onDuplicate, onSelect, onToggleActive
}: {
  activeId: string; ariaLabel: string; groupedRules: WorkflowListCategory<Rule>[]; kind: WorkflowRuleListKind; renderConfigChips: (rule: Rule) => React.ReactNode; ruleStatusFeedback: WorkflowRuleStatusFeedback | null; statusFilter: NotificationStatusFilter; summaryAriaLabel: string; tableIdPrefix: string; totalRuleCount: number; togglingRuleIds: Set<string>; onDelete: (rule: Rule) => void | Promise<void>; onDuplicate?: (rule: Rule) => void | Promise<void>; onSelect: (rule: Rule) => void; onToggleActive: (rule: Rule, isActive: boolean) => void | Promise<void>;
}) {
  const [openMenu, setOpenMenu] = React.useState<WorkflowRuleMenuState | null>(null);
  const [collapsedCategoryIds, setCollapsedCategoryIds] = React.useState<Set<string>>(() => new Set());

  React.useEffect(() => {
    if (!openMenu) return undefined;
    const closeOnPointerDown = (event: PointerEvent) => {
      if ((event.target as HTMLElement | null)?.closest("[data-workflow-rule-menu]")) return;
      setOpenMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenMenu(null);
    };
    const closeOnViewportChange = () => setOpenMenu(null);
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [openMenu]);

  React.useEffect(() => {
    setCollapsedCategoryIds(new Set());
    setOpenMenu(null);
  }, [statusFilter]);

  const toggleCategory = (categoryId: string) => {
    setCollapsedCategoryIds((current) => {
      const next = new Set(current);
      if (next.has(categoryId)) next.delete(categoryId);
      else next.add(categoryId);
      return next;
    });
  };
  const toggleRuleMenu = (ruleId: string, button: HTMLButtonElement) => {
    setOpenMenu((current) => {
      if (current?.id === ruleId) return null;
      const rect = button.getBoundingClientRect();
      const menuWidth = 178;
      const menuHeight = onDuplicate ? 136 : 94;
      const gap = 7;
      const left = Math.max(12, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 12));
      const below = rect.bottom + gap;
      const top = below + menuHeight > window.innerHeight - 12 ? Math.max(12, rect.top - menuHeight - gap) : below;
      return { id: ruleId, left, top };
    });
  };

  return (
    <aside className="workflow-rule-table notification-workflow-table card" aria-label={ariaLabel}>
      {groupedRules.length ? (
        <div className="notification-category-stack">
          {groupedRules.map((category) => {
            const Icon = category.icon;
            const collapsed = collapsedCategoryIds.has(category.id);
            const tableId = `${tableIdPrefix}-${category.id}`;
            return (
              <section className="notification-category-folder" key={category.id}>
                <button aria-controls={tableId} aria-expanded={!collapsed} className="notification-category-header" onClick={() => toggleCategory(category.id)} type="button">
                  {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                  <Icon size={16} />
                  <span><strong>{category.label}</strong></span>
                  <Badge tone="gray">{category.rules.length}</Badge>
                </button>
                {!collapsed ? (
                  <div className="notification-rule-table-wrap" id={tableId}>
                    <table className="notification-rule-data-table">
                      <thead><tr><th>Name</th><th>Configuration</th><th>Last Fired</th><th>Actions</th></tr></thead>
                      <tbody>
                        {category.rules.map((rule) => {
                          const menuOpen = openMenu?.id === rule.id;
                          const statusFeedback = ruleStatusFeedback?.ruleId === rule.id ? ruleStatusFeedback : null;
                          return (
                            <tr className={[activeId === rule.id ? "active" : "", rule.is_active ? "" : "paused"].filter(Boolean).join(" ")} key={rule.id}>
                              <td className="notification-rule-name-cell"><button className="notification-rule-name-button" onClick={() => onSelect(rule)} type="button"><strong>{rule.name}</strong></button></td>
                              <td><span className="notification-config-chips" aria-label={summaryAriaLabel}>{renderConfigChips(rule)}</span></td>
                              <td><span className="notification-last-fired">{formatCompactLastFired(rule.last_fired_at)}</span></td>
                              <td className="notification-rule-actions-cell">
                                <span className="notification-rule-actions-cluster">
                                  <span className="notification-rule-status-pill-slot">{statusFeedback ? <span className={`notification-rule-status-pill ${statusFeedback.status}`} key={statusFeedback.nonce} role="status">{workflowRuleStatusFeedbackLabel(statusFeedback.status)}</span> : null}</span>
                                  <label className={rule.is_active ? "workflow-rule-toggle active" : "workflow-rule-toggle"} aria-label={`${rule.is_active ? "Pause" : "Activate"} ${rule.name}`}>
                                    <input checked={rule.is_active} disabled={togglingRuleIds.has(rule.id)} onChange={(event) => onToggleActive(rule, event.target.checked)} type="checkbox" />
                                    <span className="workflow-rule-toggle-track" aria-hidden="true"><span /></span>
                                  </label>
                                  <span className="workflow-rule-menu" data-workflow-rule-menu>
                                    <button aria-expanded={menuOpen} aria-haspopup="menu" aria-label={`Options for ${rule.name}`} className="icon-button workflow-rule-menu-button" onClick={(event) => toggleRuleMenu(rule.id, event.currentTarget)} type="button"><MoreHorizontal size={16} /></button>
                                  </span>
                                </span>
                                {menuOpen ? <WorkflowRuleMenu left={openMenu.left} rule={rule} top={openMenu.top} onClose={() => setOpenMenu(null)} onDelete={onDelete} onDuplicate={onDuplicate} onSelect={onSelect} /> : null}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : <WorkflowEmptyState kind={kind} statusFilter={statusFilter} totalRuleCount={totalRuleCount} />}
    </aside>
  );
}

function WorkflowRuleMenu<Rule extends WorkflowRuleBase>({ left, rule, top, onClose, onDelete, onDuplicate, onSelect }: { left: number; rule: Rule; top: number; onClose: () => void; onDelete: (rule: Rule) => void | Promise<void>; onDuplicate?: (rule: Rule) => void | Promise<void>; onSelect: (rule: Rule) => void }) {
  return createPortal(
    <div className="workflow-rule-menu-popover" data-workflow-rule-menu role="menu" style={{ left, top }}>
      <button onClick={() => { onClose(); onSelect(rule); }} role="menuitem" type="button"><Pencil size={14} /> Edit</button>
      {onDuplicate ? <button onClick={() => { onClose(); onDuplicate(rule); }} role="menuitem" type="button"><Copy size={14} /> Duplicate</button> : null}
      <button className="danger" onClick={() => { onClose(); onDelete(rule); }} role="menuitem" type="button"><Trash2 size={14} /> Delete</button>
    </div>,
    document.body
  );
}

function WorkflowEmptyState({ kind, statusFilter, totalRuleCount }: { kind: WorkflowRuleListKind; statusFilter: NotificationStatusFilter; totalRuleCount: number }) {
  const automation = kind === "automation";
  const Icon = automation ? GitBranch : Bell;
  const emptyTitle = totalRuleCount === 0 ? (automation ? "No automation rules" : "No notification workflows") : statusFilter === "active" ? (automation ? "No active automation rules" : "No active notification workflows") : (automation ? "No paused automation rules" : "No inactive notification workflows");
  const emptyDetail = totalRuleCount === 0 ? (automation ? "Use Add Automation to create the first Trigger / If / Then rule." : "Use Add Notification to create the first automation.") : statusFilter === "active" ? (automation ? "Active automation rules will appear here as soon as they are switched on." : "Active workflows will appear here as soon as they are switched on.") : (automation ? "Paused automation rules will appear here as soon as they are switched off." : "Paused workflows will appear here as soon as they are switched off.");
  return <div className="notification-empty-list workflow-empty-list"><Icon size={20} /><strong>{emptyTitle}</strong><span>{emptyDetail}</span></div>;
}

function AutomationWorkflowList({
  activeId, rules, ruleStatusFeedback, statusFilter, totalRuleCount, triggerGroups, togglingRuleIds, onDelete, onSelect, onToggleActive
}: {
  activeId: string; rules: AutomationRule[]; ruleStatusFeedback: WorkflowRuleStatusFeedback | null; statusFilter: NotificationStatusFilter; totalRuleCount: number; triggerGroups: AutomationCatalogGroup[]; togglingRuleIds: Set<string>; onDelete: (rule: AutomationRule) => void | Promise<void>; onSelect: (rule: AutomationRule) => void; onToggleActive: (rule: AutomationRule, isActive: boolean) => void | Promise<void>;
}) {
  const groupedRules = React.useMemo(() => groupAutomationRulesByTriggerCategory(rules, triggerGroups), [rules, triggerGroups]);
  return (
    <WorkflowRuleList
      activeId={activeId}
      ariaLabel="Automation rules"
      groupedRules={groupedRules}
      kind="automation"
      renderConfigChips={(rule) => (<><NotificationConfigChip count={rule.triggers.length} icon={Zap} label="Triggers" /><NotificationConfigChip count={rule.conditions.length} icon={Split} label="Conditions" /><NotificationConfigChip count={rule.actions.length} icon={Play} label="Actions" /></>)}
      ruleStatusFeedback={ruleStatusFeedback}
      statusFilter={statusFilter}
      summaryAriaLabel="Automation summary"
      tableIdPrefix="automation-category"
      totalRuleCount={totalRuleCount}
      togglingRuleIds={togglingRuleIds}
      onDelete={onDelete}
      onSelect={onSelect}
      onToggleActive={onToggleActive}
    />
  );
}

function AutomationNodeStack({
  actionMeta,
  conditionMeta,
  garageDoors,
  kind,
  nodes,
  notificationRules = [],
  people,
  triggerMeta,
  users,
  variables = [],
  vehicles,
  onAdd,
  onChange,
  onParseAiSchedule,
  onRemove
}: {
  actionMeta: Map<string, AutomationCatalogItem>;
  conditionMeta: Map<string, AutomationCatalogItem>;
  garageDoors: Array<{ entity_id: string; name: string }>;
  kind: "trigger" | "condition" | "action";
  nodes: Array<AutomationNode | AutomationAction>;
  notificationRules?: Array<{ id: string; name: string }>;
  people: Person[];
  triggerMeta: Map<string, AutomationCatalogItem>;
  users: UserAccount[];
  variables?: Array<AutomationVariable & { group: string }>;
  vehicles: Vehicle[];
  onAdd: () => void;
  onChange: (node: AutomationNode | AutomationAction) => void;
  onParseAiSchedule?: (node: AutomationNode) => void;
  onRemove: (node: AutomationNode | AutomationAction) => void;
}) {
  const metaMap = kind === "trigger" ? triggerMeta : kind === "condition" ? conditionMeta : actionMeta;
  return (
    <div className="workflow-stack">
      {nodes.map((node) => (
        <AutomationNodeCard
          garageDoors={garageDoors}
          key={node.id}
          kind={kind}
          meta={metaMap.get(node.type)}
          node={node}
          notificationRules={notificationRules}
          people={people}
          variables={variables}
          users={users}
          vehicles={vehicles}
          onChange={onChange}
          onParseAiSchedule={onParseAiSchedule}
          onRemove={() => onRemove(node)}
        />
      ))}
      <button className="workflow-add-block" onClick={onAdd} type="button">
        <Plus size={15} /> Add {titleCase(kind)}
      </button>
    </div>
  );
}

function AutomationNodeCard({
  garageDoors,
  kind,
  meta,
  node,
  notificationRules,
  people,
  users,
  variables,
  vehicles,
  onChange,
  onParseAiSchedule,
  onRemove
}: {
  garageDoors: Array<{ entity_id: string; name: string }>;
  kind: "trigger" | "condition" | "action";
  meta?: AutomationCatalogItem;
  node: AutomationNode | AutomationAction;
  notificationRules: Array<{ id: string; name: string }>;
  people: Person[];
  users: UserAccount[];
  variables: Array<AutomationVariable & { group: string }>;
  vehicles: Vehicle[];
  onChange: (node: AutomationNode | AutomationAction) => void;
  onParseAiSchedule?: (node: AutomationNode) => void;
  onRemove: () => void;
}) {
  const Icon = automationNodeIcon(node.type);
  const updateConfig = (config: Record<string, unknown>) => onChange({ ...node, config: { ...node.config, ...config } });
  const activeWhatsappAdmins = users.filter((user) => user.is_active && user.role === "admin" && user.mobile_phone_number);
  const whatsappTargetMode = String(node.config.target_mode ?? "selected");
  const whatsappSelectedUserIds = Array.isArray(node.config.target_user_ids) ? node.config.target_user_ids.map(String) : [];
  return (
    <article className="workflow-action-card">
      <div className="workflow-card-title">
        <Icon size={16} />
        <span>
          <strong>{meta?.label ?? titleCase(node.type)}</strong>
          <small>{meta?.description ?? node.type}</small>
        </span>
        <button className="icon-button danger" onClick={onRemove} type="button" aria-label={`Remove ${kind}`}><Trash2 size={14} /></button>
      </div>

      {node.type.includes("person.") || node.type.includes("vehicle.") || node.type === "vehicle.known_plate" || node.type === "vehicle.outside_schedule" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field">
            <span>Person</span>
            <select value={String(node.config.person_id ?? "")} onChange={(event) => updateConfig({ person_id: event.target.value })}>
              <option value="">From trigger context</option>
              {people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}
            </select>
          </label>
          <label className="field compact-field">
            <span>Vehicle</span>
            <select value={String(node.config.vehicle_id ?? "")} onChange={(event) => updateConfig({ vehicle_id: event.target.value })}>
              <option value="">From trigger context</option>
              {vehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.registration_number}</option>)}
            </select>
          </label>
        </div>
      ) : null}

      {node.type === "vehicle.unknown_plate" || node.type === "vehicle.known_plate" ? (
        <label className="field compact-field">
          <span>Registration filter</span>
          <input value={String(node.config.registration_number ?? "")} onChange={(event) => updateConfig({ registration_number: event.target.value })} placeholder="Optional plate" />
        </label>
      ) : null}

      {node.type === "time.specific_datetime" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field"><span>Run at</span><input type="datetime-local" value={toDateTimeLocal(String(node.config.run_at ?? ""))} onChange={(event) => updateConfig({ run_at: fromDateTimeLocal(event.target.value) })} /></label>
          <label className="field compact-field"><span>Recurrence</span><select value={String(node.config.recurrence ?? "none")} onChange={(event) => updateConfig({ recurrence: event.target.value, single_use: event.target.value === "none" })}><option value="none">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
          <label className="field compact-field"><span>End date</span><input type="datetime-local" value={toDateTimeLocal(String(node.config.end_at ?? ""))} onChange={(event) => updateConfig({ end_at: fromDateTimeLocal(event.target.value) })} /></label>
        </div>
      ) : null}

      {node.type === "time.every_x" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field"><span>Every</span><input min={1} type="number" value={Number(node.config.interval ?? 5)} onChange={(event) => updateConfig({ interval: Number(event.target.value) })} /></label>
          <label className="field compact-field"><span>Unit</span><select value={String(node.config.unit ?? "minutes")} onChange={(event) => updateConfig({ unit: event.target.value })}><option value="minutes">Minutes</option><option value="hours">Hours</option><option value="days">Days</option></select></label>
        </div>
      ) : null}

      {node.type === "time.cron" || node.type === "time.ai_text" ? (
        <div className="field-grid compact-field-grid">
          {node.type === "time.ai_text" ? <label className="field compact-field wide-field"><span>AI schedule text</span><input value={String(node.config.natural_text ?? "")} onChange={(event) => updateConfig({ natural_text: event.target.value })} placeholder="Every Thursday at 9pm until 4th June" /></label> : null}
          <label className="field compact-field"><span>Cron</span><input value={String(node.config.cron_expression ?? "")} onChange={(event) => updateConfig({ cron_expression: event.target.value })} placeholder="0 21 * * 4" /></label>
          <label className="field compact-field"><span>End date</span><input type="datetime-local" value={toDateTimeLocal(String(node.config.end_at ?? ""))} onChange={(event) => updateConfig({ end_at: fromDateTimeLocal(event.target.value) })} /></label>
          {node.type === "time.ai_text" ? <button className="secondary-button compact" onClick={() => onParseAiSchedule?.(node)} type="button"><Sparkles size={14} /> Parse</button> : null}
        </div>
      ) : null}

      {node.type === "ai.phrase_received" ? (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field"><span>Phrase</span><input value={String(node.config.phrase ?? "")} onChange={(event) => updateConfig({ phrase: event.target.value })} /></label>
          <label className="field compact-field"><span>Match</span><select value={String(node.config.match_mode ?? "contains")} onChange={(event) => updateConfig({ match_mode: event.target.value })}><option value="contains">Contains</option><option value="exact">Exact</option></select></label>
        </div>
      ) : null}

      {node.type.startsWith("webhook.") ? (
        <label className="field compact-field">
          <span>Webhook key</span>
          <input value={String(node.config.webhook_key ?? "")} onChange={(event) => updateConfig({ webhook_key: event.target.value })} placeholder="Ungguessable endpoint key" />
        </label>
      ) : null}

      {node.type.startsWith("notification.") ? (
        <label className="field compact-field">
          <span>Notification rule</span>
          <select value={String(node.config.notification_rule_id ?? "")} onChange={(event) => updateConfig({ notification_rule_id: event.target.value })}>
            <option value="">Select notification</option>
            {notificationRules.map((rule) => <option key={rule.id} value={rule.id}>{rule.name}</option>)}
          </select>
        </label>
      ) : null}

      {node.type === "integration.whatsapp.send_message" ? (
        <div className="automation-integration-action-summary">
          <MessageCircle size={15} />
          <span>
            <strong>WhatsApp</strong>
            <small>Send text to Admin users or a dynamic phone number.</small>
          </span>
          <div className="field-grid compact-field-grid wide-field">
            <label className="field compact-field">
              <span>Target mode</span>
              <select
                value={whatsappTargetMode}
                onChange={(event) => updateConfig({
                  target_mode: event.target.value,
                  target_user_ids: event.target.value === "selected" ? whatsappSelectedUserIds : [],
                })}
              >
                <option value="selected">Selected Admins</option>
                <option value="all">All Admins</option>
                <option value="dynamic">Dynamic number</option>
              </select>
            </label>
          </div>
          {whatsappTargetMode === "selected" ? (
            <div className="workflow-target-chips">
              {activeWhatsappAdmins.length ? activeWhatsappAdmins.map((user) => {
                const selected = whatsappSelectedUserIds.includes(user.id);
                return (
                  <button
                    className={selected ? "workflow-target-chip selected" : "workflow-target-chip"}
                    key={user.id}
                    onClick={() => updateConfig({ target_user_ids: toggleStringList(node.config.target_user_ids, user.id) })}
                    type="button"
                  >
                    <strong>Admin</strong>{displayUserName(user) || user.username}
                  </button>
                );
              }) : <span className="workflow-target-chip unavailable"><strong>Admin</strong>No Admin mobile numbers</span>}
            </div>
          ) : null}
          {whatsappTargetMode === "dynamic" ? (
            <PlainTemplateEditor
              label="Phone number template"
              value={String(node.config.phone_number_template ?? "")}
              variables={variables}
              onChange={(phone_number_template) => updateConfig({ phone_number_template })}
            />
          ) : null}
          <PlainTemplateEditor
            label="Message template"
            multiline
            value={String(node.config.message_template ?? "@Subject")}
            variables={variables}
            onChange={(message_template) => updateConfig({ message_template })}
          />
        </div>
      ) : node.type.startsWith("integration.") ? (
        <div className="automation-integration-action-summary">
          <PlugZap size={15} />
          <span>
            <strong>{String(node.config.provider ?? "Integration").replace(/_/g, " ")}</strong>
            <small>{String(node.config.action ?? node.type).replace(/_/g, " ")}</small>
          </span>
        </div>
      ) : null}

      {node.type.startsWith("garage_door.") ? (
        <div className="workflow-target-chips">
          {garageDoors.map((door) => {
            const selected = Array.isArray(node.config.target_entity_ids) && (node.config.target_entity_ids as unknown[]).includes(door.entity_id);
            return (
              <button className={selected ? "workflow-target-chip selected" : "workflow-target-chip"} key={door.entity_id} onClick={() => updateConfig({ target_entity_ids: toggleStringList(node.config.target_entity_ids, door.entity_id) })} type="button">
                <strong>Garage</strong>{door.name}
              </button>
            );
          })}
        </div>
      ) : null}

      {kind === "action" ? (
        <PlainTemplateEditor
          label="Audit reason"
          multiline
          value={(node as AutomationAction).reason_template ?? ""}
          variables={variables}
          onChange={(reason_template) => onChange({ ...(node as AutomationAction), reason_template })}
        />
      ) : null}
    </article>
  );
}

function AutomationSelectionModal({
  groups,
  kind,
  onClose,
  onSelect
}: {
  groups: AutomationCatalogGroup[];
  kind: "trigger" | "condition" | "action";
  onClose: () => void;
  onSelect: (node: AutomationNode | AutomationAction) => void;
}) {
  const [activeCategoryId, setActiveCategoryId] = React.useState(groups[0]?.id ?? "");
  const [activeIntegrationId, setActiveIntegrationId] = React.useState("");
  const [searchQuery, setSearchQuery] = React.useState("");
  const query = searchQuery.trim().toLowerCase();
  const itemKey = `${kind}s` as "triggers" | "conditions" | "actions";
  const visibleGroups = groups
    .map((group) => {
      const categoryMatches = matchesSearchText(group.label, query);
      const items = (group[itemKey] ?? []).filter((item) => {
        if (!query || categoryMatches) return true;
        return matchesSearchText(`${item.label} ${item.description ?? ""} ${item.type} ${item.integration_provider_label ?? ""}`, query);
      });
      const integrations = (group.integrations ?? [])
        .map((integration) => {
          const integrationMatches = categoryMatches || matchesSearchText(`${integration.label} ${integration.description ?? ""}`, query);
          const actions = integration.actions.filter((item) => {
            if (!query || integrationMatches) return true;
            return matchesSearchText(`${item.label} ${item.description ?? ""} ${item.type}`, query);
          });
          return { ...integration, actions };
        })
        .filter((integration) => integration.actions.length || matchesSearchText(`${integration.label} ${integration.description ?? ""}`, query));
      return { ...group, [itemKey]: items, integrations };
    })
    .filter((group) => (group[itemKey] ?? []).length || (group.integrations ?? []).length || matchesSearchText(group.label, query));
  React.useEffect(() => {
    if (!visibleGroups.some((group) => group.id === activeCategoryId)) {
      setActiveCategoryId(visibleGroups[0]?.id ?? "");
      setActiveIntegrationId("");
    }
  }, [activeCategoryId, visibleGroups]);
  const activeGroup = visibleGroups.find((group) => group.id === activeCategoryId) ?? visibleGroups[0];
  const activeIntegrations = activeGroup?.integrations ?? [];
  const selectedIntegration = activeIntegrations.find((integration) => integration.id === activeIntegrationId);
  const showIntegrationDrilldown = kind === "action" && activeGroup?.id === "integrations" && activeIntegrations.length > 0;
  return (
    <TwoPaneSelectionModal
      activeCategoryId={activeGroup?.id ?? ""}
      categories={visibleGroups.map((group) => ({
        id: group.id,
        label: group.label,
        count: group.id === "integrations" && group.integrations?.length ? group.integrations.length : (group[itemKey] ?? []).length,
        icon: automationCategoryIcon(group.id)
      }))}
      embedded
      onBack={onClose}
      onCategoryChange={(categoryId) => {
        setActiveCategoryId(categoryId);
        setActiveIntegrationId("");
      }}
      onClose={onClose}
      onSearchChange={setSearchQuery}
      searchPlaceholder={`Search ${kind}s`}
      searchQuery={searchQuery}
      subtitle={`Choose a ${kind} for this automation.`}
      title={`Add ${titleCase(kind)}`}
      wide
    >
      {showIntegrationDrilldown && !selectedIntegration ? (
        <div className="two-pane-card-grid automation-selector-grid">
          {activeIntegrations.map((integration) => {
            const Icon = automationCategoryIcon(integration.id);
            return (
              <button className="two-pane-item-card automation-selector-card" key={integration.id} onClick={() => setActiveIntegrationId(integration.id)} type="button">
                <Icon size={18} />
                <span>
                  <strong>{integration.label}</strong>
                  <small>{integration.description ?? `${integration.actions.length} available ${pluralize("action", integration.actions.length)}`}</small>
                </span>
              </button>
            );
          })}
        </div>
      ) : showIntegrationDrilldown && selectedIntegration ? (
        <div className="automation-drilldown-stack">
          <div className="automation-drilldown-head">
            <button className="secondary-button compact" onClick={() => setActiveIntegrationId("")} type="button">
              <ArrowLeft size={14} /> Integrations
            </button>
            <div>
              <strong>{selectedIntegration.label}</strong>
              <span>{selectedIntegration.description ?? "Choose an integration action."}</span>
            </div>
          </div>
          <div className="two-pane-card-grid automation-selector-grid">
            {selectedIntegration.actions.map((item) => {
              const Icon = automationNodeIcon(item.type);
              const disabled = item.disabled || item.enabled === false;
              const disabledReason = item.disabled_reason || "Integration action is unavailable.";
              return (
                <button
                  className="two-pane-item-card automation-selector-card"
                  disabled={disabled}
                  key={item.type}
                  onClick={() => onSelect(createAutomationNode(kind, item.type, item))}
                  title={disabled ? disabledReason : undefined}
                  type="button"
                >
                  <Icon size={18} />
                  <span><strong>{item.label}</strong><small>{disabled ? disabledReason : item.description ?? item.type}</small></span>
                </button>
              );
            })}
          </div>
        </div>
      ) : activeGroup ? (
        <div className="two-pane-card-grid automation-selector-grid">
          {(activeGroup[itemKey] ?? []).map((item) => {
            const Icon = automationNodeIcon(item.type);
            return (
              <button className="two-pane-item-card automation-selector-card" key={item.type} onClick={() => onSelect(createAutomationNode(kind, item.type, item))} type="button">
                <Icon size={18} />
                <span><strong>{item.label}</strong><small>{item.description ?? item.type}</small></span>
              </button>
            );
          })}
        </div>
      ) : <div className="two-pane-empty">No {kind}s match this search.</div>}
    </TwoPaneSelectionModal>
  );
}

function AutomationPreviewPanel({
  actions,
  dryRun
}: {
  actions: Array<AutomationAction & { renderedReason: string }>;
  dryRun: Record<string, unknown> | null;
}) {
  const conditionResults = Array.isArray(dryRun?.condition_results) ? dryRun.condition_results as Array<Record<string, unknown>> : [];
  const actionPreviews = Array.isArray(dryRun?.action_previews) ? dryRun.action_previews as Array<Record<string, unknown>> : [];
  return (
    <aside className="notification-preview-panel" aria-label="Automation preview">
      <div className="notification-preview-rail-head">
        <div>
          <strong>Automation Preview</strong>
          <span>Dry runs validate context and conditions only; actions are not executed.</span>
        </div>
      </div>
      <div className="notification-preview-stack">
        {actions.length ? actions.map((action) => (
          <article className="notification-preview-card-inline" key={action.id}>
            <div><Play size={16} /><strong>{titleCase(action.type)}</strong><Badge tone="green">Then</Badge></div>
            <p>{action.renderedReason || action.reason_template || "Default audit reason will be used."}</p>
          </article>
        )) : <div className="notification-endpoint-empty">Add an action to preview automation output.</div>}
        {dryRun ? (
          <article className="notification-preview-card-inline">
            <div><CheckCircle2 size={16} /><strong>Dry Run</strong><Badge tone={dryRun.would_run ? "green" : "amber"}>{dryRun.would_run ? "Would Run" : "Skipped"}</Badge></div>
            <p>{stringifyTemplateValue(dryRun.message) || "Preview only. No automation actions were executed."}</p>
            <p>{conditionResults.length} condition result(s), {actionPreviews.length} action preview(s).</p>
          </article>
        ) : null}
        {actionPreviews.map((preview) => (
          <article className="notification-preview-card-inline" key={String(preview.id ?? preview.type)}>
            <div>
              <Play size={16} />
              <strong>{titleCase(String(preview.type ?? "Action"))}</strong>
              <Badge tone={preview.would_execute ? "blue" : "amber"}>{preview.would_execute ? "Preview Only" : "Skipped"}</Badge>
            </div>
            <p>
              {Array.isArray(preview.missing_variables) && preview.missing_variables.length
                ? `Missing ${preview.missing_variables.join(", ")}.`
                : stringifyTemplateValue(preview.rendered_reason) || "No action was executed during this dry-run."}
            </p>
          </article>
        ))}
      </div>
    </aside>
  );
}

function createAutomationDraft(): AutomationRule {
  return {
    id: draftId("automation"),
    name: "New Automation",
    description: "",
    is_active: true,
    triggers: [],
    trigger_keys: [],
    conditions: [],
    actions: [],
    run_count: 0,
    last_run_status: null,
    last_error: null,
  };
}

function createAutomationNode(kind: "trigger" | "condition" | "action", type: string, meta?: AutomationCatalogItem): AutomationNode | AutomationAction {
  const base = { id: draftId(kind), type, config: defaultAutomationConfig(type, meta) };
  if (kind === "action") return { ...base, reason_template: defaultAutomationReason(type) };
  return base;
}

function defaultAutomationConfig(type: string, meta?: AutomationCatalogItem): Record<string, unknown> {
  if (meta?.default_config) return { ...meta.default_config };
  if (type === "time.every_x") return { interval: 5, unit: "minutes" };
  if (type === "time.specific_datetime") return { run_at: "", recurrence: "none", single_use: true, end_at: "" };
  if (type === "time.cron") return { cron_expression: "0 9 * * *", timezone: "Europe/London", end_at: "" };
  if (type === "time.ai_text") return { natural_text: "", cron_expression: "", timezone: "Europe/London", end_at: "" };
  if (type === "ai.phrase_received") return { phrase: "", match_mode: "contains" };
  if (type.startsWith("webhook.")) return { webhook_key: `webhook-${Math.random().toString(16).slice(2)}${Date.now().toString(16)}` };
  if (type.startsWith("garage_door.")) return { target_entity_ids: [] };
  if (type.startsWith("notification.")) return { notification_rule_id: "" };
  if (type === "integration.icloud_calendar.sync") return { provider: "icloud_calendar", action: "sync_calendars" };
  if (type === "integration.whatsapp.send_message") {
    return {
      provider: "whatsapp",
      action: "send_message",
      target_mode: "selected",
      target_user_ids: [],
      phone_number_template: "",
      message_template: "@Subject",
    };
  }
  return {};
}

function defaultAutomationReason(type: string) {
  if (type === "gate.open") return "Automation opened the gate for @DisplayName.";
  if (type.startsWith("garage_door.")) return "Automation ran @EventType for @DisplayName.";
  if (type.startsWith("maintenance_mode.")) return "Automation changed Maintenance Mode: @Subject.";
  if (type.startsWith("integration.")) return "Automation ran integration action from @EventType.";
  return "Automation action from @EventType.";
}

function automationRulePayload(rule: AutomationRule) {
  return {
    name: rule.name.trim() || "Automation Rule",
    description: rule.description,
    is_active: rule.is_active,
    triggers: rule.triggers,
    conditions: rule.conditions,
    actions: rule.actions,
  };
}

function cloneAutomationRule(rule: AutomationRule): AutomationRule {
  return JSON.parse(JSON.stringify(rule)) as AutomationRule;
}

function groupRulesByTrigger<Rule>(
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

function groupAutomationRulesByTriggerCategory(
  rules: AutomationRule[],
  triggerGroups: AutomationCatalogGroup[]
): AutomationRuleCategory[] {
  const categoryByTrigger = new Map<string, WorkflowTriggerCategory>();
  triggerGroups.forEach((group, order) => {
    const category = {
      id: group.id,
      label: group.label,
      icon: automationCategoryIcon(group.id),
      order,
    };
    (group.triggers ?? []).forEach((trigger) => {
      categoryByTrigger.set(trigger.type, category);
    });
  });

  return groupRulesByTrigger(
    rules,
    categoryByTrigger,
    (rule) => rule.triggers[0]?.type ?? rule.trigger_keys[0] ?? "",
    GitBranch
  );
}

function automationVariablesForTrigger(groups: AutomationVariableGroup[], triggerType: string) {
  return groups.flatMap((group) => group.items
    .filter((item) => !triggerType || !item.trigger_types?.length || item.trigger_types.includes(triggerType))
    .map((item) => ({ ...item, group: group.group })));
}

function automationCategoryIcon(groupId: string) {
  if (groupId.includes("time")) return Clock3;
  if (groupId.includes("vehicle")) return Car;
  if (groupId.includes("maintenance")) return Construction;
  if (groupId.includes("visitor")) return UserPlus;
  if (groupId.includes("webhook")) return PlugZap;
  if (groupId.includes("whatsapp")) return MessageCircle;
  if (groupId.includes("icloud") || groupId.includes("calendar")) return CalendarDays;
  if (groupId.includes("integration")) return PlugZap;
  if (groupId.includes("notification")) return Bell;
  if (groupId.includes("garage")) return Warehouse;
  if (groupId.includes("gate")) return DoorOpen;
  if (groupId.includes("ai")) return Bot;
  return GitBranch;
}

function automationNodeIcon(type: string) {
  if (type.startsWith("time.")) return Clock3;
  if (type.startsWith("vehicle.")) return Car;
  if (type.startsWith("maintenance_mode.")) return Construction;
  if (type.startsWith("visitor_pass.")) return UserPlus;
  if (type.startsWith("webhook.")) return PlugZap;
  if (type.startsWith("integration.whatsapp")) return MessageCircle;
  if (type.startsWith("integration.icloud_calendar")) return CalendarDays;
  if (type.startsWith("integration.")) return PlugZap;
  if (type.startsWith("notification.")) return Bell;
  if (type.startsWith("garage_door.")) return Warehouse;
  if (type.startsWith("gate.")) return DoorOpen;
  if (type.startsWith("ai.")) return Bot;
  if (type.startsWith("person.")) return UserRound;
  return GitBranch;
}

function toggleStringList(value: unknown, item: string) {
  const current = Array.isArray(value) ? value.map(String) : [];
  return current.includes(item) ? current.filter((entry) => entry !== item) : [...current, item];
}

export function NotificationsView({ currentUser, people, refreshToken, schedules }: { currentUser: UserAccount; people: Person[]; refreshToken: number; schedules: Schedule[] }) {
  const [catalog, setCatalog] = React.useState<NotificationCatalogResponse | null>(null);
  const [rules, setRules] = React.useState<NotificationRule[]>([]);
  const [cameras, setCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [draft, setDraft] = React.useState<NotificationRule | null>(null);
  const [modal, setModal] = React.useState<"trigger" | "condition" | "action" | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const { filterCounts, filteredRules, setStatusFilter, statusFilter } = useWorkflowRuleFilters(rules);
  const [togglingRuleIds, setRuleToggling] = usePendingWorkflowIds();
  const [ruleStatusFeedback, setRuleStatusFeedback] = useTransientRuleStatusFeedback();
  const [feedback, setFeedback] = React.useState<WorkflowFeedback | null>(null);
  const [error, setError] = React.useState("");

  const triggerGroups = catalog?.triggers ?? [];
  const gateMalfunctionStageOptions = catalog?.gate_malfunction_stages ?? [];
  const variableGroups = catalog?.variables ?? [];
  const variables = React.useMemo(() => variableGroups.flatMap((group) => group.items.map((item) => ({ ...item, group: group.group }))), [variableGroups]);
  const triggerOptions = React.useMemo(() => triggerGroups.flatMap((group) => group.events), [triggerGroups]);
  const triggerByValue = React.useMemo(() => new Map(triggerOptions.map((trigger) => [trigger.value, trigger])), [triggerOptions]);
  const actionableOptionsByTrigger = React.useMemo(() => {
    return new Map((catalog?.actionable_notifications ?? []).map((group) => [group.trigger_event, group.actions]));
  }, [catalog?.actionable_notifications]);
  const activeDraft = draft;
  const workflowModalMode: "editor" | "trigger" | "action" = modal === "trigger" || modal === "action" ? modal : "editor";
  const previewContext = catalog?.mock_context ?? {};
  const previewActions = activeDraft
    ? renderWorkflowPreview(activeDraft.actions, previewContext, activeDraft.trigger_event, gateMalfunctionStageOptions)
    : [];

  const load = React.useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const data = await workflowApi.getNotificationData();
      setCatalog(data.catalog);
      setRules(data.rules);
      setCameras(data.cameras);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load notification workflows.");
    } finally {
      setLoading(false);
    }
  }, []);

  useRefreshableWorkflowLoad(load, refreshToken);

  const selectRule = (rule: NotificationRule) => {
    setDraft(cloneNotificationRule(rule));
    setModal(null);
    setFeedback(null);
  };

  const updateDraft = (updater: (rule: NotificationRule) => NotificationRule) => {
    setDraft((current) => updater(current ?? createWorkflowDraft()));
  };

  const addWorkflow = () => {
    const next = createWorkflowDraft();
    setDraft(next);
    setModal(null);
    setFeedback(null);
  };

  const deleteRule = async (rule: NotificationRule) => {
    if (rule.id.startsWith("draft-")) {
      setDraft(null);
      setModal(null);
      return;
    }
    if (!window.confirm(`Delete ${rule.name}?`)) return;
    setFeedback(null);
    try {
      await workflowApi.deleteNotificationRule(rule);
      await load();
      setDraft(null);
      setModal(null);
      setFeedback({ tone: "success", text: "Notification workflow deleted." });
    } catch (deleteError) {
      setFeedback({ tone: "error", text: deleteError instanceof Error ? deleteError.message : "Unable to delete notification workflow." });
    }
  };

  const toggleRuleActive = async (rule: NotificationRule, isActive: boolean) => {
    if (rule.id.startsWith("draft-")) return;
    setFeedback(null);
    setRuleToggling(rule.id, true);
    try {
      const updated = await workflowApi.toggleNotificationRule(rule, isActive);
      setRules((current) => current.map((item) => item.id === updated.id ? updated : item));
      setDraft((current) => current?.id === updated.id ? cloneNotificationRule(updated) : current);
      setRuleStatusFeedback({
        nonce: Date.now(),
        ruleId: updated.id,
        status: updated.is_active ? "resumed" : "paused",
      });
    } catch (toggleError) {
      setFeedback({ tone: "error", text: toggleError instanceof Error ? toggleError.message : "Unable to update notification workflow." });
    } finally {
      setRuleToggling(rule.id, false);
    }
  };

  const duplicateRule = async (rule: NotificationRule) => {
    if (rule.id.startsWith("draft-")) return;
    setFeedback(null);
    const payload = workflowRulePayload({
      ...cloneNotificationRule(rule),
      id: draftId("workflow"),
      name: `${rule.name} Copy`,
      is_active: false,
    });
    try {
      const created = await workflowApi.duplicateNotificationRule(payload);
      await load();
      setDraft(cloneNotificationRule(created));
      setModal(null);
      setFeedback({ tone: "success", text: "Notification workflow duplicated and paused for review." });
    } catch (duplicateError) {
      setFeedback({ tone: "error", text: duplicateError instanceof Error ? duplicateError.message : "Unable to duplicate notification workflow." });
    }
  };

  const save = async () => {
    if (!activeDraft) return;
    if (!activeDraft.trigger_event) {
      setFeedback({ tone: "error", text: "Add a trigger before saving this workflow." });
      return;
    }
    if (!activeDraft.actions.length) {
      setFeedback({ tone: "error", text: "Add at least one action before saving this workflow." });
      return;
    }
    setSaving(true);
    setFeedback(null);
    const payload = workflowRulePayload(activeDraft);
    try {
      const saved = await workflowApi.saveNotificationRule(activeDraft, payload);
      setDraft(null);
      setModal(null);
      await load();
      setRuleStatusFeedback({
        nonce: Date.now(),
        ruleId: saved.id,
        status: "saved",
      });
    } catch (saveError) {
      setFeedback({ tone: "error", text: saveError instanceof Error ? saveError.message : "Unable to save notification workflow." });
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async () => {
    if (!activeDraft) return;
    if (!activeDraft.trigger_event) {
      setFeedback({ tone: "error", text: "Add a trigger before sending a test." });
      return;
    }
    if (!activeDraft.actions.length) {
      setFeedback({ tone: "error", text: "Add at least one action before sending a test." });
      return;
    }
    setTesting(true);
    setFeedback({ tone: "info", text: "Sending workflow test through the configured providers." });
    try {
      await workflowApi.testNotificationRule(activeDraft, workflowRulePayload(activeDraft));
      setFeedback({ tone: "success", text: "Workflow test accepted by the configured providers." });
    } catch (testError) {
      setFeedback({ tone: "error", text: testError instanceof Error ? testError.message : "Notification workflow test failed." });
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <section className="view-stack notifications-page workflow-notifications-page">
        <Toolbar title="Notifications" count={0} icon={Bell} />
        <div className="loading-panel">Loading notification workflows</div>
      </section>
    );
  }

  return (
    <section className="view-stack notifications-page workflow-notifications-page">
      <Toolbar title="Notifications" count={rules.length} icon={Bell}>
        <button className="secondary-button" onClick={addWorkflow} type="button">
          <Plus size={15} /> Add Notification
        </button>
      </Toolbar>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {feedback && !activeDraft ? <div className={`notification-feedback ${feedback.tone}`}>{feedback.text}</div> : null}

      <WorkflowStatusFilters
        activeFilter={statusFilter}
        ariaLabel="Notification status filter"
        counts={filterCounts}
        onFilterChange={setStatusFilter}
      />

      <NotificationWorkflowList
        activeId={activeDraft?.id ?? ""}
        rules={filteredRules}
        statusFilter={statusFilter}
        totalRuleCount={rules.length}
        triggerGroups={triggerGroups}
        ruleStatusFeedback={ruleStatusFeedback}
        togglingRuleIds={togglingRuleIds}
        onDelete={deleteRule}
        onDuplicate={duplicateRule}
        onSelect={selectRule}
        onToggleActive={toggleRuleActive}
      />

      {activeDraft ? (
        <div className="modal-backdrop workflow-editor-backdrop" role="presentation">
          <div
            className={workflowModalMode === "editor" ? "modal-card workflow-editor-modal" : "modal-card workflow-editor-modal selector-mode"}
            role="dialog"
            aria-modal="true"
            aria-labelledby={workflowModalMode === "editor" ? "workflow-editor-title" : "two-pane-selection-title"}
          >
            <>
              <div
                className={workflowModalMode === "editor" ? "workflow-modal-panel editor" : "workflow-modal-panel selector"}
                key={workflowModalMode}
              >
                {workflowModalMode === "trigger" ? (
                  <NotificationTriggerModal
                    embedded
                    groups={triggerGroups}
                    selected={activeDraft.trigger_event}
                    onClose={() => setModal(null)}
                    onSelect={(triggerEvent) => {
                      const supportedActionable = actionableOptionsByTrigger.get(triggerEvent) ?? [];
                      updateDraft((rule) => ({
                        ...rule,
                        trigger_event: triggerEvent,
                        name: rule.name === "New Notification" ? notificationEventLabel(triggerEvent, triggerByValue) : rule.name,
                        actions: rule.actions.map((action) => notificationActionWithSupportedActionable(action, supportedActionable)),
                      }));
                      setModal(null);
                    }}
                  />
                ) : workflowModalMode === "action" ? (
                  <NotificationActionModal
                    embedded
                    currentUser={currentUser}
                    integrations={catalog?.integrations ?? []}
                    people={people}
                    onClose={() => setModal(null)}
                    onSelect={(action) => {
                      updateDraft((rule) => ({ ...rule, actions: [...rule.actions, action] }));
                      setModal(null);
                    }}
                  />
                ) : (
                  <>
                    <div className="modal-header">
                      <div>
                        <h2 id="workflow-editor-title">{activeDraft.id.startsWith("draft-") ? "Add Notification" : "Edit Notification"}</h2>
                        <p>Build the trigger, conditions, and delivery actions for this workflow.</p>
                      </div>
                      <button className="icon-button" onClick={() => { setDraft(null); setModal(null); }} type="button" aria-label="Close notification editor">
                        <X size={16} />
                      </button>
                    </div>
                    <NotificationWorkflowEditor
                      cameras={cameras}
                      feedback={feedback}
                      integrations={catalog?.integrations ?? []}
                      people={people}
                      previewActions={previewActions}
                      rule={activeDraft}
                      saving={saving}
                      schedules={schedules}
                      testing={testing}
                      actionableOptions={actionableOptionsByTrigger.get(activeDraft.trigger_event) ?? []}
                      gateMalfunctionStageOptions={gateMalfunctionStageOptions}
                      trigger={triggerByValue.get(activeDraft.trigger_event)}
                      variables={variables}
                      onAddAction={() => setModal("action")}
                      onAddCondition={() => setModal("condition")}
                      onCancel={() => { setDraft(null); setModal(null); }}
                      onDelete={() => deleteRule(activeDraft)}
                      onSave={save}
                      onSendTest={sendTest}
                      onShowTrigger={() => setModal("trigger")}
                      onUpdate={updateDraft}
                    />
                  </>
                )}
              </div>
            </>
          </div>
        </div>
      ) : null}

      {activeDraft && modal === "condition" ? (
        <NotificationConditionModal
          people={people}
          schedules={schedules}
          onClose={() => setModal(null)}
          onSelect={(condition) => {
            updateDraft((rule) => ({ ...rule, conditions: [...rule.conditions, condition] }));
            setModal(null);
          }}
        />
      ) : null}
    </section>
  );
}

function NotificationWorkflowList({
  activeId, rules, ruleStatusFeedback, statusFilter, totalRuleCount, triggerGroups, onDelete, onDuplicate, onSelect, onToggleActive, togglingRuleIds
}: {
  activeId: string; rules: NotificationRule[]; ruleStatusFeedback: WorkflowRuleStatusFeedback | null; statusFilter: NotificationStatusFilter; totalRuleCount: number; triggerGroups: NotificationTriggerGroup[]; onDelete: (rule: NotificationRule) => void | Promise<void>; onDuplicate: (rule: NotificationRule) => void | Promise<void>; onSelect: (rule: NotificationRule) => void; onToggleActive: (rule: NotificationRule, isActive: boolean) => void | Promise<void>; togglingRuleIds: Set<string>;
}) {
  const groupedRules = React.useMemo(() => groupNotificationRulesByTriggerCategory(rules, triggerGroups), [rules, triggerGroups]);
  return (
    <WorkflowRuleList
      activeId={activeId}
      ariaLabel="Notification workflows"
      groupedRules={groupedRules}
      kind="notification"
      renderConfigChips={(rule) => (<><NotificationConfigChip count={1} icon={Zap} label="Triggers" /><NotificationConfigChip count={rule.conditions.length} icon={Split} label="Conditions" /><NotificationConfigChip count={rule.actions.length} icon={Play} label="Actions" /></>)}
      ruleStatusFeedback={ruleStatusFeedback}
      statusFilter={statusFilter}
      summaryAriaLabel="Workflow summary"
      tableIdPrefix="notification-category"
      totalRuleCount={totalRuleCount}
      togglingRuleIds={togglingRuleIds}
      onDelete={onDelete}
      onDuplicate={onDuplicate}
      onSelect={onSelect}
      onToggleActive={onToggleActive}
    />
  );
}

function WorkflowStatusFilters({
  activeFilter,
  ariaLabel,
  counts,
  onFilterChange
}: {
  activeFilter: NotificationStatusFilter;
  ariaLabel: string;
  counts: NotificationFilterCounts;
  onFilterChange: (filter: NotificationStatusFilter) => void;
}) {
  return (
    <div className="notification-status-tabs" role="tablist" aria-label={ariaLabel}>
      {workflowStatusFilterOptions.map((option) => (
        <button
          aria-selected={activeFilter === option.key}
          className={activeFilter === option.key ? "active" : ""}
          key={option.key}
          onClick={() => onFilterChange(option.key)}
          role="tab"
          type="button"
        >
          <span>{option.label}</span>
          <Badge tone="gray">{counts[option.key]}</Badge>
        </button>
      ))}
    </div>
  );
}

function NotificationConfigChip({ count, icon: Icon, label }: { count: number; icon: React.ElementType; label: string }) {
  const itemName = label === "Triggers" ? "trigger" : label === "Conditions" ? "condition" : "action";
  const tooltip = `${count} ${pluralize(itemName, count)} configured`;
  return (
    <span
      className="notification-config-chip"
      aria-label={`${label}: ${count}`}
      title={tooltip}
    >
      <Icon size={13} />
      <span>{count}</span>
    </span>
  );
}

function groupNotificationRulesByTriggerCategory(
  rules: NotificationRule[],
  triggerGroups: NotificationTriggerGroup[]
): NotificationRuleCategory[] {
  const categoryByTrigger = new Map<string, WorkflowTriggerCategory>();
  triggerGroups.forEach((group, order) => {
    const category = {
      id: group.id,
      label: group.label,
      icon: notificationTriggerGroupIcon(group.id),
      order,
    };
    group.events.forEach((event) => {
      categoryByTrigger.set(event.value, category);
    });
  });

  return groupRulesByTrigger(rules, categoryByTrigger, (rule) => rule.trigger_event, Bell);
}

function NotificationWorkflowEditor({
  actionableOptions,
  cameras,
  feedback,
  gateMalfunctionStageOptions,
  integrations,
  people,
  previewActions,
  rule,
  saving,
  schedules,
  testing,
  trigger,
  variables,
  onAddAction,
  onAddCondition,
  onCancel,
  onDelete,
  onSave,
  onSendTest,
  onShowTrigger,
  onUpdate
}: {
  actionableOptions: NotificationActionableOption[];
  cameras: UnifiProtectCamera[];
  feedback: { tone: "success" | "error" | "info"; text: string } | null;
  gateMalfunctionStageOptions: NotificationGateMalfunctionStageOption[];
  integrations: NotificationIntegration[];
  people: Person[];
  previewActions: Array<NotificationAction & { title: string; message: string }>;
  rule: NotificationRule;
  saving: boolean;
  schedules: Schedule[];
  testing: boolean;
  trigger?: NotificationTriggerOption;
  variables: Array<NotificationVariable & { group: string }>;
  onAddAction: () => void;
  onAddCondition: () => void;
  onCancel: () => void;
  onDelete: () => void;
  onSave: () => void;
  onSendTest: () => void;
  onShowTrigger: () => void;
  onUpdate: (updater: (rule: NotificationRule) => NotificationRule) => void;
}) {
  const integrationById = React.useMemo(() => new Map(integrations.map((integration) => [integration.id, integration])), [integrations]);
  const isDraft = rule.id.startsWith("draft-");
  const isGateMalfunctionWorkflow = rule.trigger_event === "gate_malfunction";
  return (
    <div className="workflow-editor-modal-grid">
      <div className="workflow-editor-column">
        <section className="notification-editor-panel workflow-builder-panel">
          <div className="notification-editor-header workflow-editor-header">
            <div>
              <span className="eyebrow">Name</span>
              <input
                aria-label="Workflow name"
                value={rule.name}
                onChange={(event) => onUpdate((current) => ({ ...current, name: event.target.value }))}
              />
            </div>
            {!isDraft ? (
              <div className="notification-editor-actions">
                <label className={rule.is_active ? "notification-switch active" : "notification-switch"}>
                  <input checked={rule.is_active} onChange={(event) => onUpdate((current) => ({ ...current, is_active: event.target.checked }))} type="checkbox" />
                  <span>{rule.is_active ? "Active" : "Paused"}</span>
                </label>
                <button className="icon-button danger" onClick={onDelete} type="button" aria-label="Delete workflow">
                  <Trash2 size={15} />
                </button>
              </div>
            ) : null}
          </div>

          <div className="workflow-vertical">
            <WorkflowBlock badge="When" tone="blue" title="Trigger" required>
              {rule.trigger_event ? (
                <button className="workflow-selected-card" onClick={onShowTrigger} type="button">
                  <CircleDot size={18} />
                  <span>
                    <strong>{trigger?.label ?? notificationEventLabel(rule.trigger_event)}</strong>
                    <small>{trigger?.description ?? "Selected event trigger."}</small>
                  </span>
                  <Badge tone={notificationSeverityTone(trigger?.severity ?? "info")}>{titleCase(trigger?.severity ?? "info")}</Badge>
                </button>
              ) : (
                <button className="workflow-add-block" onClick={onShowTrigger} type="button">
                  <Plus size={15} /> Add Trigger
                </button>
              )}
            </WorkflowBlock>

            <WorkflowBlock badge="And If" tone="amber" title="Conditions" optional>
              <div className="workflow-stack">
                {rule.conditions.map((condition) => (
                  <NotificationConditionCard
                    condition={condition}
                    key={condition.id}
                    people={people}
                    schedules={schedules}
                    onChange={(nextCondition) => onUpdate((current) => ({ ...current, conditions: current.conditions.map((item) => item.id === condition.id ? nextCondition : item) }))}
                    onRemove={() => onUpdate((current) => ({ ...current, conditions: current.conditions.filter((item) => item.id !== condition.id) }))}
                  />
                ))}
                <button className="workflow-add-block" onClick={onAddCondition} type="button">
                  <Plus size={15} /> Add Condition
                </button>
              </div>
            </WorkflowBlock>

            <WorkflowBlock badge="Then" tone="green" title="Actions" required>
              <div className="workflow-stack">
                {rule.actions.map((action) => (
                  <NotificationActionCard
                    action={action}
                    actionableOptions={actionableOptions}
                    cameras={cameras}
                    integration={integrationById.get(action.type)}
                    isGateMalfunctionWorkflow={isGateMalfunctionWorkflow}
                    key={action.id}
                    stageOptions={gateMalfunctionStageOptions}
                    variables={variables}
                    onChange={(nextAction) => onUpdate((current) => ({ ...current, actions: current.actions.map((item) => item.id === action.id ? nextAction : item) }))}
                    onRemove={() => onUpdate((current) => ({ ...current, actions: current.actions.filter((item) => item.id !== action.id) }))}
                  />
                ))}
                <button className="workflow-add-block" onClick={onAddAction} type="button">
                  <Plus size={15} /> Add Action
                </button>
              </div>
            </WorkflowBlock>
          </div>

          <div className="modal-actions workflow-editor-footer">
            {feedback ? <div className={`notification-feedback workflow-editor-feedback ${feedback.tone}`} role="status">{feedback.text}</div> : null}
            <button className="secondary-button" onClick={onSendTest} disabled={testing} type="button">
              <Send size={15} /> {testing ? "Sending..." : "Send Test"}
            </button>
            <button className="secondary-button" onClick={onCancel} type="button">
              Cancel
            </button>
            <button className="primary-button" onClick={onSave} disabled={saving} type="button">
              <Save size={15} /> {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </section>
      </div>
      <NotificationLivePreviewPanel actions={previewActions} />
    </div>
  );
}

function WorkflowBlock({
  badge,
  children,
  optional,
  required,
  title,
  tone
}: {
  badge: string;
  children: React.ReactNode;
  optional?: boolean;
  required?: boolean;
  title: string;
  tone: BadgeTone;
}) {
  return (
    <section className="workflow-block">
      <div className="workflow-block-head">
        <Badge tone={tone}>{badge}</Badge>
        <strong>{title}</strong>
        <span>{required ? "Required" : optional ? "Optional" : ""}</span>
      </div>
      {children}
    </section>
  );
}

function NotificationConditionCard({
  condition,
  people,
  schedules,
  onChange,
  onRemove
}: {
  condition: NotificationCondition;
  people: Person[];
  schedules: Schedule[];
  onChange: (condition: NotificationCondition) => void;
  onRemove: () => void;
}) {
  return (
    <article className="workflow-condition-card">
      <div className="workflow-card-title">
        <Clock3 size={16} />
        <strong>{condition.type === "schedule" ? "Schedule" : "Presence"}</strong>
        <button className="icon-button danger" onClick={onRemove} type="button" aria-label="Remove condition">
          <Trash2 size={14} />
        </button>
      </div>
      {condition.type === "schedule" ? (
        <label className="field compact-field">
          <span>Schedule</span>
          <select value={condition.schedule_id ?? ""} onChange={(event) => onChange({ ...condition, schedule_id: event.target.value })}>
            <option value="">Select schedule</option>
            {schedules.map((schedule) => <option key={schedule.id} value={schedule.id}>{schedule.name}</option>)}
          </select>
        </label>
      ) : (
        <div className="field-grid compact-field-grid">
          <label className="field compact-field">
            <span>Presence</span>
            <select value={condition.mode ?? "someone_home"} onChange={(event) => onChange({ ...condition, mode: event.target.value as PresenceConditionMode })}>
              <option value="no_one_home">No one is home</option>
              <option value="someone_home">Someone is home</option>
              <option value="person_home">Specific person is home</option>
            </select>
          </label>
          {condition.mode === "person_home" ? (
            <label className="field compact-field">
              <span>Person</span>
              <select value={condition.person_id ?? ""} onChange={(event) => onChange({ ...condition, person_id: event.target.value })}>
                <option value="">Select person</option>
                {people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}
              </select>
            </label>
          ) : null}
        </div>
      )}
    </article>
  );
}

type TemplateEditorProps = {
  label: string;
  multiline?: boolean;
  value: string;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (value: string) => void;
};

class TemplateEditorBoundary extends React.Component<
  { children: React.ReactNode; fallback: React.ReactNode; resetKey: string },
  { hasError: boolean; retryCount: number }
> {
  state = { hasError: false, retryCount: 0 };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidUpdate(previousProps: { resetKey: string }) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, retryCount: 0 });
    }
  }

  componentDidCatch(error: unknown) {
    console.error("Notification template editor failed to render", error);
    if (this.state.retryCount > 0) return;
    window.requestAnimationFrame(() => {
      this.setState((current) => current.hasError
        ? { hasError: false, retryCount: current.retryCount + 1 }
        : null);
    });
  }

  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

function SafeVariableRichTextEditor(props: TemplateEditorProps) {
  const variableResetKey = React.useMemo(() => props.variables.map((variable) => variable.name).join("\u0000"), [props.variables]);
  const safeProps = {
    ...props,
    value: stringifyTemplateValue(props.value),
  };
  return (
    <TemplateEditorBoundary fallback={<PlainTemplateEditor {...safeProps} />} resetKey={variableResetKey}>
      <React.Suspense fallback={<div className="loading-panel compact">Loading template editor</div>}>
        <VariableRichTextEditor {...safeProps} />
      </React.Suspense>
    </TemplateEditorBoundary>
  );
}

function PlainTemplateEditor({ label, multiline = false, value, onChange }: TemplateEditorProps) {
  return (
    <label className="field variable-editor-field">
      <span>{label}</span>
      {multiline ? (
        <textarea
          className="template-editor-fallback"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={4}
        />
      ) : (
        <input
          className="template-editor-fallback"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}

function NotificationActionCard({
  action,
  actionableOptions,
  cameras,
  integration,
  isGateMalfunctionWorkflow,
  stageOptions,
  variables,
  onChange,
  onRemove
}: {
  action: NotificationAction;
  actionableOptions: NotificationActionableOption[];
  cameras: UnifiProtectCamera[];
  integration?: NotificationIntegration;
  isGateMalfunctionWorkflow: boolean;
  stageOptions: NotificationGateMalfunctionStageOption[];
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (action: NotificationAction) => void;
  onRemove: () => void;
}) {
  const meta = notificationChannelMeta[action.type];
  const Icon = meta.icon;
  const supportsTitle = action.type !== "voice" && !isGateMalfunctionWorkflow;
  const supportsMessageTemplate = !isGateMalfunctionWorkflow;
  const supportsMedia = action.type === "mobile" || action.type === "in_app" || action.type === "discord";
  const supportsActionable = action.type === "mobile" && actionableOptions.length > 0;
  const actionMedia = normalizeNotificationMedia(action.media);
  const actionActionable = normalizeNotificationActionable(action.actionable);
  const selectedActionable = actionableOptions.find((item) => item.value === actionActionable.action) ?? actionableOptions[0];
  const selectedCamera = cameras.find((camera) => camera.id === actionMedia.camera_id);
  const cameraSnapshotUrl = selectedCamera
    ? `/api/v1/integrations/unifi-protect/cameras/${selectedCamera.id}/snapshot?width=320&height=180`
    : "";
  const targetChips = notificationActionTargetChips(action, integration);
  const whatsappNumberTargets = action.target_ids
    .filter((target) => target.startsWith("whatsapp:number:"))
    .map((target) => target.replace(/^whatsapp:number:/, ""))
    .join("\n");
  const updateWhatsAppNumberTargets = (value: string) => {
    const manualTargets = value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => `whatsapp:number:${item}`);
    onChange({
      ...action,
      target_mode: "selected",
      target_ids: [
        ...action.target_ids.filter((target) => !target.startsWith("whatsapp:number:")),
        ...manualTargets,
      ],
    });
  };
  const selectedGateStages = normalizeGateMalfunctionStages(action.gate_malfunction_stages);
  const activeGateStages = selectedGateStages.length
    ? selectedGateStages
    : stageOptions.map((stage) => stage.value);
  const toggleGateMalfunctionStage = (stage: NotificationGateMalfunctionStage) => {
    const base = selectedGateStages.length
      ? selectedGateStages
      : stageOptions.map((item) => item.value);
    const next = base.includes(stage)
      ? base.filter((item) => item !== stage)
      : [...base, stage];
    const normalized = normalizeGateMalfunctionStages(next);
    onChange({
      ...action,
      gate_malfunction_stages: normalized.length === stageOptions.length || normalized.length === 0 ? [] : normalized,
    });
  };
  return (
    <article className="workflow-action-card">
      <div className="workflow-card-title">
        <Icon size={16} />
        <span>
          <strong>{meta.label}</strong>
          <small>{meta.description}</small>
        </span>
        <button className="icon-button danger" onClick={onRemove} type="button" aria-label="Remove action">
          <Trash2 size={14} />
        </button>
      </div>

      <div className="workflow-target-chips" aria-label={`${meta.label} selected endpoints`}>
        {targetChips.map((chip) => (
          <span className={chip.unavailable ? "workflow-target-chip unavailable" : "workflow-target-chip"} key={chip.id}>
            <strong>{chip.provider}</strong>
            {chip.label}
          </span>
        ))}
      </div>

      {isGateMalfunctionWorkflow ? (
        <section className="workflow-stage-row" aria-label={`${meta.label} gate malfunction stages`}>
          <div className="workflow-stage-row-head">
            <AlertTriangle size={14} />
            <span>Gate Malfunction Stages</span>
          </div>
          <div className="workflow-stage-toggles">
            {stageOptions.map((stage) => {
              const selected = activeGateStages.includes(stage.value);
              return (
                <button
                  className={selected ? "workflow-stage-toggle selected" : "workflow-stage-toggle"}
                  key={stage.value}
                  onClick={() => toggleGateMalfunctionStage(stage.value)}
                  type="button"
                >
                  {selected ? <Check size={13} /> : null}
                  {stage.label}
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      {action.type === "whatsapp" ? (
        <PlainTemplateEditor
          label="Phone numbers or @Variables"
          multiline
          value={whatsappNumberTargets}
          variables={variables}
          onChange={updateWhatsAppNumberTargets}
        />
      ) : null}

      {supportsTitle ? (
        <SafeVariableRichTextEditor
          label="Title"
          value={action.title_template}
          variables={variables}
          onChange={(title_template) => onChange({ ...action, title_template })}
        />
      ) : null}
      {supportsMessageTemplate ? (
        <SafeVariableRichTextEditor
          label={action.type === "voice" ? "Spoken message" : "Message"}
          multiline
          value={action.message_template}
          variables={variables}
          onChange={(message_template) => onChange({ ...action, message_template })}
        />
      ) : (
        <div className="workflow-generated-copy">
          <Sparkles size={14} />
          <span>LLM generated content</span>
        </div>
      )}

      {supportsMedia ? (
        <section className="workflow-media-row">
          <label className={actionMedia.attach_camera_snapshot ? "notification-switch active" : "notification-switch"}>
            <input
              checked={actionMedia.attach_camera_snapshot}
              onChange={(event) => onChange({ ...action, media: { ...actionMedia, attach_camera_snapshot: event.target.checked } })}
              type="checkbox"
            />
            <span>Camera Screenshot</span>
          </label>
          {actionMedia.attach_camera_snapshot ? (
            <select value={actionMedia.camera_id} onChange={(event) => onChange({ ...action, media: { ...actionMedia, camera_id: event.target.value } })}>
              <option value="">Select camera</option>
              {cameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.name}</option>)}
            </select>
          ) : null}
          {cameraSnapshotUrl ? (
            <div className="workflow-camera-preview">
              <img src={cameraSnapshotUrl} alt={`${selectedCamera?.name ?? "Camera"} snapshot preview`} />
              <span>{selectedCamera?.name ?? "Camera snapshot"}</span>
            </div>
          ) : null}
          {supportsActionable ? (
            <>
              <label className={actionActionable.enabled ? "notification-switch active" : "notification-switch"}>
                <input
                  checked={actionActionable.enabled}
                  onChange={(event) => onChange({
                    ...action,
                    actionable: {
                      enabled: event.target.checked,
                      action: event.target.checked ? selectedActionable.value : actionActionable.action,
                    },
                  })}
                  type="checkbox"
                />
                <span>Actionable Notification</span>
              </label>
              {actionActionable.enabled ? (
                <select
                  value={selectedActionable.value}
                  onChange={(event) => onChange({ ...action, actionable: { enabled: true, action: event.target.value } })}
                  aria-label="Actionable notification action"
                >
                  {actionableOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}
    </article>
  );
}

function NotificationLivePreviewPanel({
  actions
}: {
  actions: Array<NotificationAction & { title: string; message: string; phoneticsApplied?: boolean }>;
}) {
  return (
    <aside className="notification-preview-panel" aria-label="Live notification preview">
      <div className="notification-preview-rail-head">
        <div>
          <strong id="notification-preview-title">Live Preview</strong>
          <span>Mock context resolves @ variables as you type.</span>
        </div>
      </div>
      {actions.length ? (
        <div className="notification-preview-stack">
          {actions.map((action) => {
            const meta = notificationChannelMeta[action.type];
            const Icon = meta.icon;
            return (
              <article className="notification-preview-card-inline" key={action.id}>
                <div>
                  <Icon size={16} />
                  <strong>{meta.label}</strong>
                  <Badge tone={meta.tone}>{action.target_mode}</Badge>
                  {action.phoneticsApplied ? <span className="phonetic-preview-badge"><Volume2 size={12} /> Phonetics Applied</span> : null}
                </div>
                {action.title ? <h3>{action.title}</h3> : null}
                <p>{action.message}</p>
                {action.media.attach_camera_snapshot ? <span className="preview-media-chip"><Camera size={13} /> Camera Screenshot</span> : null}
                {action.actionable.enabled ? <span className="preview-media-chip"><DoorOpen size={13} /> {notificationActionableLabel(action.actionable.action)}</span> : null}
              </article>
            );
          })}
        </div>
      ) : (
        <div className="notification-endpoint-empty">Add an action to preview the outgoing notification.</div>
      )}
    </aside>
  );
}

function TwoPaneSelectionModal({
  activeCategoryId,
  backLabel = "Back to editor",
  categories,
  children,
  embedded = false,
  footer,
  onBack,
  onCategoryChange,
  onClose,
  onSearchChange,
  searchPlaceholder = "Search",
  searchQuery,
  subtitle,
  title,
  wide = false
}: {
  activeCategoryId: string;
  backLabel?: string;
  categories: TwoPaneCategory[];
  children: React.ReactNode;
  embedded?: boolean;
  footer?: React.ReactNode;
  onBack?: () => void;
  onCategoryChange: (categoryId: string) => void;
  onClose: () => void;
  onSearchChange: (query: string) => void;
  searchPlaceholder?: string;
  searchQuery: string;
  subtitle: string;
  title: string;
  wide?: boolean;
}) {
  const className = [
    "modal-card",
    "two-pane-selection-modal",
    embedded ? "embedded" : "",
    wide ? "wide" : "",
  ].filter(Boolean).join(" ");
  const content = (
    <div className={className} role={embedded ? undefined : "dialog"} aria-modal={embedded ? undefined : true} aria-labelledby="two-pane-selection-title">
      <div className="two-pane-selection-header">
        <div className="modal-header compact">
          <div>
            <h2 id="two-pane-selection-title">{title}</h2>
            <p>{subtitle}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label={`Close ${title}`}>
            <X size={16} />
          </button>
        </div>
        <label className="two-pane-search">
          <Search size={16} />
          <input
            autoFocus
            placeholder={searchPlaceholder}
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </label>
      </div>

      <div className="two-pane-selection-body">
        <nav className="two-pane-category-list" aria-label={`${title} categories`}>
          {categories.map((category) => {
            const Icon = category.icon;
            return (
              <button
                className={category.id === activeCategoryId ? "two-pane-category active" : "two-pane-category"}
                disabled={category.disabled}
                key={category.id}
                onClick={() => onCategoryChange(category.id)}
                type="button"
              >
                {Icon ? <Icon size={16} /> : null}
                <span>{category.label}</span>
                <Badge tone={category.count ? "blue" : "gray"}>{category.count}</Badge>
              </button>
            );
          })}
        </nav>
        <section className="two-pane-selection-content">{children}</section>
      </div>
      {footer || onBack ? (
        <div className="two-pane-selection-footer">
          {onBack ? (
            <button className="secondary-button two-pane-editor-back" onClick={onBack} type="button">
              <ArrowLeft size={15} /> {backLabel}
            </button>
          ) : null}
          {footer ? <div className="two-pane-selection-footer-actions">{footer}</div> : null}
        </div>
      ) : null}
    </div>
  );
  if (embedded) return content;
  return (
    <div className="modal-backdrop" role="presentation">
      {content}
    </div>
  );
}

function NotificationTriggerModal({
  embedded = false,
  groups,
  selected,
  onClose,
  onSelect
}: {
  embedded?: boolean;
  groups: NotificationTriggerGroup[];
  selected: string;
  onClose: () => void;
  onSelect: (triggerEvent: string) => void;
}) {
  const sortedGroups = React.useMemo(() => groups
    .map((group) => ({
      ...group,
      events: group.events.slice().sort((a, b) => a.label.localeCompare(b.label)),
    }))
    .filter((group) => group.events.length > 0)
    .sort((a, b) => a.label.localeCompare(b.label)), [groups]);
  const initialTriggerCategoryId = sortedGroups.find((group) => group.events.some((event) => event.value === selected))?.id ?? sortedGroups[0]?.id ?? "";
  const [activeCategoryId, setActiveCategoryId] = React.useState(initialTriggerCategoryId);
  const [searchQuery, setSearchQuery] = React.useState("");
  const query = searchQuery.trim().toLowerCase();
  const visibleGroups = React.useMemo(() => {
    return sortedGroups
      .map((group) => {
        const categoryMatches = matchesSearchText(group.label, query);
        const events = categoryMatches
          ? group.events
          : group.events.filter((event) => matchesSearchText(`${event.label} ${event.description} ${event.value}`, query));
        return { ...group, events };
      })
      .filter((group) => group.events.length > 0 || matchesSearchText(group.label, query));
  }, [query, sortedGroups]);
  React.useEffect(() => {
    if (!visibleGroups.length) return;
    if (!visibleGroups.some((group) => group.id === activeCategoryId)) {
      setActiveCategoryId(visibleGroups[0].id);
    }
  }, [activeCategoryId, visibleGroups]);
  const activeGroup = visibleGroups.find((group) => group.id === activeCategoryId) ?? visibleGroups[0];
  const categories = visibleGroups.map((group) => ({
    id: group.id,
    label: group.label,
    count: group.events.length,
    icon: notificationTriggerGroupIcon(group.id),
  }));
  return (
    <TwoPaneSelectionModal
      activeCategoryId={activeGroup?.id ?? ""}
      categories={categories}
      embedded={embedded}
      onBack={embedded ? onClose : undefined}
      onCategoryChange={setActiveCategoryId}
      onClose={onClose}
      onSearchChange={setSearchQuery}
      searchPlaceholder="Search triggers"
      searchQuery={searchQuery}
      subtitle="Choose the event that starts this workflow."
      title="Add Trigger"
    >
      {activeGroup ? (
        <div className="two-pane-card-grid">
          {activeGroup.events.map((event) => {
            const isSelected = selected === event.value;
            return (
              <button
                className={isSelected ? "two-pane-item-card selected" : "two-pane-item-card"}
                key={event.value}
                onClick={() => onSelect(event.value)}
                type="button"
              >
                <span>
                  <strong>{event.label}</strong>
                  <small>{event.description}</small>
                </span>
                <Badge tone={isSelected ? "green" : notificationSeverityTone(event.severity)}>{isSelected ? "Selected" : titleCase(event.severity)}</Badge>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="two-pane-empty">No triggers match this search.</div>
      )}
    </TwoPaneSelectionModal>
  );
}

function NotificationConditionModal({
  people,
  schedules,
  onClose,
  onSelect
}: {
  people: Person[];
  schedules: Schedule[];
  onClose: () => void;
  onSelect: (condition: NotificationCondition) => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card notification-add-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-condition-title">
        <div className="modal-header">
          <div>
            <h2 id="workflow-condition-title">Add Condition</h2>
            <p>Conditions are evaluated together before actions run.</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close condition selector"><X size={16} /></button>
        </div>
        <div className="two-pane-card-grid">
          <button className="two-pane-item-card" onClick={() => onSelect({ id: draftId("condition"), type: "schedule", schedule_id: schedules[0]?.id ?? "" })} type="button">
            <span><strong>Schedule</strong><small>Only continue when the event time falls inside a selected schedule.</small></span>
            <Clock3 size={18} />
          </button>
          <button className="two-pane-item-card" onClick={() => onSelect({ id: draftId("condition"), type: "presence", mode: "someone_home", person_id: people[0]?.id ?? "" })} type="button">
            <span><strong>Presence</strong><small>Check whether nobody, somebody, or a specific person is home.</small></span>
            <Users size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}

function NotificationActionModal({
  embedded = false,
  currentUser,
  integrations,
  people,
  onClose,
  onSelect
}: {
  embedded?: boolean;
  currentUser: UserAccount;
  integrations: NotificationIntegration[];
  people: Person[];
  onClose: () => void;
  onSelect: (action: NotificationAction) => void;
}) {
  const actionCategories = React.useMemo(() => notificationActionCategories(), []);
  const defaultCategory = actionCategories[0]?.id as NotificationActionType;
  const [activeCategory, setActiveCategory] = React.useState<NotificationActionType>(defaultCategory ?? "in_app");
  const [selectedMethodId, setSelectedMethodId] = React.useState<string | null>(null);
  const [selectedTargetIds, setSelectedTargetIds] = React.useState<Set<string>>(() => new Set());
  const [searchQuery, setSearchQuery] = React.useState("");
  const query = searchQuery.trim().toLowerCase();
  const currentUserPerson = React.useMemo(() => findCurrentUserPerson(people, currentUser), [currentUser, people]);
  const methodsByCategory = React.useMemo(
    () => buildNotificationActionMethods(integrations, currentUserPerson),
    [currentUserPerson, integrations]
  );
  const visibleCategoryRows = React.useMemo(() => {
    return actionCategories
      .map((category) => {
        const categoryMatches = matchesSearchText(category.label, query);
        const methods = (methodsByCategory[category.id as NotificationActionType] ?? []).filter((method) =>
          categoryMatches || matchesSearchText(`${method.label} ${method.provider} ${method.detail}`, query)
        );
        return { ...category, count: methods.length, disabled: false };
      })
      .filter((category) => category.count > 0 || matchesSearchText(category.label, query) || !query);
  }, [actionCategories, methodsByCategory, query]);
  React.useEffect(() => {
    if (!visibleCategoryRows.length) return;
    if (!visibleCategoryRows.some((category) => category.id === activeCategory)) {
      setActiveCategory(visibleCategoryRows[0].id as NotificationActionType);
      setSelectedMethodId(null);
      setSelectedTargetIds(new Set());
    }
  }, [activeCategory, visibleCategoryRows]);

  const activeCategoryMeta = actionCategories.find((category) => category.id === activeCategory) ?? actionCategories[0];
  const categoryMatches = matchesSearchText(activeCategoryMeta?.label ?? "", query);
  const activeMethods = (methodsByCategory[activeCategory] ?? []).filter((method) =>
    categoryMatches || matchesSearchText(`${method.label} ${method.provider} ${method.detail}`, query)
  );
  const selectedMethod = activeMethods.find((method) => method.id === selectedMethodId)
    ?? (selectedMethodId ? (methodsByCategory[activeCategory] ?? []).find((method) => method.id === selectedMethodId) : undefined);
  const targetQuery = query;
  const visibleTargets = selectedMethod
    ? selectedMethod.targets.filter((target) =>
      !targetQuery || matchesSearchText(`${target.label} ${target.detail} ${target.provider} ${target.id}`, targetQuery)
    )
    : [];
  const canConfirm = Boolean(selectedMethod && (!selectedMethod.requiresTarget || selectedTargetIds.size > 0));
  const suggestedTargetId = selectedMethod?.defaultTargetIds[0] ?? "";

  const chooseCategory = (categoryId: string) => {
    setActiveCategory(categoryId as NotificationActionType);
    setSelectedMethodId(null);
    setSelectedTargetIds(new Set());
  };

  const chooseMethod = (method: NotificationActionMethod) => {
    setSelectedMethodId(method.id);
    setSelectedTargetIds(new Set(method.defaultTargetIds));
  };

  const toggleTarget = (targetId: string) => {
    setSelectedTargetIds((current) => {
      const next = new Set(current);
      if (next.has(targetId)) next.delete(targetId);
      else next.add(targetId);
      return next;
    });
  };

  const confirm = () => {
    if (!selectedMethod || !canConfirm) return;
    onSelect(createWorkflowAction(selectedMethod.actionType, {
      target_mode: selectedMethod.targetMode,
      target_ids: selectedMethod.targetMode === "all" ? [] : Array.from(selectedTargetIds),
    }));
  };

  return (
    <TwoPaneSelectionModal
      activeCategoryId={activeCategory}
      categories={visibleCategoryRows}
      embedded={embedded}
      footer={selectedMethod ? (
        <>
          <button className="secondary-button" onClick={() => { setSelectedMethodId(null); setSelectedTargetIds(new Set()); }} type="button">
            <ArrowLeft size={15} /> Back to methods
          </button>
          <button className="primary-button" disabled={!canConfirm} onClick={confirm} type="button">
            <Check size={15} /> Confirm Selection
          </button>
        </>
      ) : null}
      onBack={embedded ? onClose : undefined}
      onCategoryChange={chooseCategory}
      onClose={onClose}
      onSearchChange={setSearchQuery}
      searchPlaceholder={selectedMethod ? "Search targets" : "Search actions"}
      searchQuery={searchQuery}
      subtitle={selectedMethod ? `Choose one or more targets for ${selectedMethod.label}.` : "Choose a delivery method, then select its targets."}
      title="Add Action"
      wide
    >
      <>
        <div
          className="two-pane-selection-panel"
          key={selectedMethod ? "targets" : "methods"}
        >
          {selectedMethod ? (
            <div className="action-target-step">
              <div className="action-target-step-head">
                <button className="secondary-button compact" onClick={() => { setSelectedMethodId(null); setSelectedTargetIds(new Set()); }} type="button">
                  <ArrowLeft size={14} /> Methods
                </button>
                <div>
                  <strong>{selectedMethod.label}</strong>
                  <span>{selectedMethod.provider}</span>
                </div>
              </div>
              {selectedMethod.unavailableReason ? (
                <div className="two-pane-empty warning">{selectedMethod.unavailableReason}</div>
              ) : null}
              {visibleTargets.length ? (
                <div className="two-pane-card-grid action-target-grid">
                  {visibleTargets.map((target) => {
                    const isSelected = selectedTargetIds.has(target.id) || selectedMethod.targetMode === "all";
                    const isSuggested = target.id === suggestedTargetId;
                    return (
                      <button
                        className={isSelected ? "two-pane-target-tile selected" : "two-pane-target-tile"}
                        key={target.id}
                        onClick={() => selectedMethod.targetMode === "all" ? undefined : toggleTarget(target.id)}
                        type="button"
                      >
                        <span className="target-select-mark">{isSelected ? <Check size={14} /> : null}</span>
                        <span className="target-tile-copy">
                          <span className="target-tile-title-line">
                            <strong>{target.label}</strong>
                            {isSuggested ? <Badge tone="green">Your device</Badge> : <Badge tone="gray">{target.provider}</Badge>}
                          </span>
                          <small>{target.detail || target.id}</small>
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="two-pane-empty">
                  {selectedMethod.targets.length ? "No targets match this search." : "No targets are available for this method."}
                </div>
              )}
            </div>
          ) : activeMethods.length ? (
            <div className="two-pane-card-grid action-method-grid">
              {activeMethods.map((method) => {
                const Icon = method.icon;
                return (
                  <button
                    className={method.unavailableReason ? "two-pane-item-card unavailable" : "two-pane-item-card"}
                    key={method.id}
                    onClick={() => chooseMethod(method)}
                    type="button"
                  >
                    <Icon size={18} />
                    <span>
                      <strong>{method.label}</strong>
                      <small>{method.detail}</small>
                    </span>
                    <Badge tone={method.unavailableReason ? "gray" : method.tone}>{method.provider}</Badge>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="two-pane-empty">
              {query ? "No action methods match this search." : "No methods are configured for this notification channel."}
            </div>
          )}
        </div>
      </>
    </TwoPaneSelectionModal>
  );
}

function notificationTriggerGroupIcon(groupId: string) {
  if (groupId === "ai_agents") return Bot;
  if (groupId === "compliance") return ShieldCheck;
  if (groupId === "gate_actions") return DoorOpen;
  if (groupId === "gate_malfunctions") return AlertTriangle;
  if (groupId === "integrations") return PlugZap;
  if (groupId === "leaderboard") return Trophy;
  if (groupId === "maintenance_mode") return Construction;
  if (groupId === "vehicle_detections") return Car;
  if (groupId === "visitor_pass") return UserPlus;
  return Bell;
}

function notificationActionCategories(): TwoPaneCategory[] {
  return (["mobile", "whatsapp", "discord", "voice", "in_app"] as NotificationActionType[])
    .map((id) => {
      const meta = notificationChannelMeta[id];
      return { id, label: meta.label, count: 0, icon: meta.icon };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

function buildNotificationActionMethods(
  integrations: NotificationIntegration[],
  currentUserPerson: Person | null
): Record<NotificationActionType, NotificationActionMethod[]> {
  const integrationById = new Map(integrations.map((integration) => [integration.id, integration]));
  const mobileIntegration = integrationById.get("mobile");
  const voiceIntegration = integrationById.get("voice");
  const inAppIntegration = integrationById.get("in_app");
  const discordIntegration = integrationById.get("discord");
  const whatsappIntegration = integrationById.get("whatsapp");
  const mobileEndpoints = concreteNotificationEndpoints(mobileIntegration?.endpoints ?? []);
  const homeAssistantMobileTargets = mobileEndpoints.filter((endpoint) => endpoint.id.startsWith("home_assistant_mobile:"));
  const appriseTargets = mobileEndpoints.filter((endpoint) => endpoint.id.startsWith("apprise:"));
  const currentUserTarget = currentUserPerson?.home_assistant_mobile_app_notify_service
    ? `home_assistant_mobile:${currentUserPerson.home_assistant_mobile_app_notify_service}`
    : "";
  const mobileMethods: NotificationActionMethod[] = [];

  if (homeAssistantMobileTargets.length) {
    mobileMethods.push({
      id: "home_assistant_mobile",
      actionType: "mobile",
      label: "Home Assistant",
      provider: "Home Assistant",
      detail: homeAssistantMobileTargets.length
        ? `${homeAssistantMobileTargets.length} mobile app target${homeAssistantMobileTargets.length === 1 ? "" : "s"}`
        : "No mobile app notify services discovered",
      icon: Home,
      tone: "blue",
      targets: homeAssistantMobileTargets,
      targetMode: "selected",
      requiresTarget: true,
      defaultTargetIds: homeAssistantMobileTargets.some((target) => target.id === currentUserTarget) ? [currentUserTarget] : [],
    });
  }

  for (const endpoint of appriseTargets) {
    mobileMethods.push({
      id: endpoint.id,
      actionType: "mobile",
      label: endpoint.label,
      provider: "Apprise",
      detail: endpoint.detail || "Configured Apprise destination",
      icon: Smartphone,
      tone: "blue",
      targets: [endpoint],
      targetMode: "selected",
      requiresTarget: true,
      defaultTargetIds: [endpoint.id],
    });
  }

  const voiceTargets = concreteNotificationEndpoints(voiceIntegration?.endpoints ?? []);
  const voiceMethods = providerNotificationMethods({
    actionType: "voice",
    detail: (count) => count ? `${count} media player target${count === 1 ? "" : "s"}` : "No media players discovered",
    id: "home_assistant_tts",
    icon: Volume2,
    integration: voiceIntegration,
    label: "Home Assistant",
    provider: "Home Assistant TTS",
    targets: voiceTargets,
    tone: "amber",
    unavailableReason: "Home Assistant TTS is configured, but no media_player targets are available.",
  });

  const dashboardEndpoint = inAppIntegration?.endpoints[0] ?? {
    id: "dashboard",
    provider: "Dashboard",
    label: "All signed-in dashboards",
    detail: "Realtime in-app notification stream",
  };
  const inAppMethods: NotificationActionMethod[] = [
    {
      id: "dashboard",
      actionType: "in_app",
      label: "Dashboard",
      provider: "Dashboard",
      detail: dashboardEndpoint.detail || "Realtime in-app notification stream",
      icon: Monitor,
      tone: "green",
      targets: [dashboardEndpoint],
      targetMode: "all",
      requiresTarget: false,
      defaultTargetIds: [dashboardEndpoint.id],
    },
  ];

  const discordTargets = concreteNotificationEndpoints(discordIntegration?.endpoints ?? []);
  const discordMethods = providerNotificationMethods({
    actionType: "discord",
    detail: (count) => count ? `${count} channel${count === 1 ? "" : "s"} available` : "No Discord channels discovered",
    id: "discord",
    icon: MessageCircle,
    integration: discordIntegration,
    label: "Discord",
    provider: "Discord",
    targets: discordTargets,
    tone: "purple",
    unavailableReason: "Discord is configured, but no channels are available yet.",
    wildcardId: "discord:*",
  });

  const whatsappTargets = concreteNotificationEndpoints(whatsappIntegration?.endpoints ?? []);
  const whatsappMethods = providerNotificationMethods({
    actionType: "whatsapp",
    detail: (count) => count ? `${count} Admin target${count === 1 ? "" : "s"} available` : "No Admin users with mobile numbers",
    id: "whatsapp",
    icon: MessageCircle,
    integration: whatsappIntegration,
    label: "WhatsApp",
    provider: "WhatsApp",
    targets: whatsappTargets,
    tone: "green",
    unavailableReason: "WhatsApp is configured, but no active Admin users have mobile phone numbers.",
    wildcardId: "whatsapp:*",
  });

  return {
    discord: discordMethods.sort(sortNotificationMethods),
    in_app: inAppMethods.sort(sortNotificationMethods),
    mobile: mobileMethods.sort(sortNotificationMethods),
    whatsapp: whatsappMethods.sort(sortNotificationMethods),
    voice: voiceMethods.sort(sortNotificationMethods),
  };
}

type ProviderNotificationMethodConfig = Pick<NotificationActionMethod, "actionType" | "id" | "icon" | "label" | "provider" | "targets" | "tone"> & { detail: (targetCount: number) => string; integration?: NotificationIntegration; unavailableReason: string; wildcardId?: string };

function providerNotificationMethods({ detail, integration, targets, unavailableReason, wildcardId, ...method }: ProviderNotificationMethodConfig): NotificationActionMethod[] {
  const wildcardConfigured = wildcardId ? integration?.endpoints.some((endpoint) => endpoint.id === wildcardId) : false;
  if (!wildcardConfigured && !targets.length && !integration?.configured) return [];
  return [{
    ...method,
    detail: detail(targets.length),
    targets,
    targetMode: "selected",
    requiresTarget: true,
    defaultTargetIds: targets[0]?.id ? [targets[0].id] : [],
    unavailableReason: targets.length ? undefined : unavailableReason,
  }];
}

function sortNotificationMethods(a: NotificationActionMethod, b: NotificationActionMethod) {
  return `${a.label} ${a.detail}`.localeCompare(`${b.label} ${b.detail}`);
}

function concreteNotificationEndpoints(endpoints: NotificationEndpoint[]) {
  return endpoints.filter((endpoint) => !endpoint.id.endsWith(":*"));
}

function findCurrentUserPerson(people: Person[], currentUser: UserAccount): Person | null {
  const eligible = people.filter((person) => person.is_active && person.home_assistant_mobile_app_notify_service);
  const userFirstLast = normalizeIdentityName(`${currentUser.first_name} ${currentUser.last_name}`);
  if (userFirstLast) {
    const primary = eligible.filter((person) => normalizeIdentityName(`${person.first_name} ${person.last_name}`) === userFirstLast);
    if (primary.length === 1) return primary[0];
    if (primary.length > 1) return null;
  }

  const userDisplay = normalizeIdentityName(currentUser.full_name || displayUserName(currentUser));
  if (!userDisplay) return null;
  const exactNameMatches = eligible.filter((person) => normalizeIdentityName(person.display_name) === userDisplay);
  return exactNameMatches.length === 1 ? exactNameMatches[0] : null;
}

function normalizeIdentityName(value: string) {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function matchesSearchText(value: string, query: string) {
  if (!query) return true;
  return value.toLowerCase().includes(query);
}

function notificationActionTargetChips(action: NotificationAction, integration?: NotificationIntegration) {
  if (action.target_mode === "all") {
    const aggregate = integration?.endpoints.find((endpoint) => endpoint.id.endsWith(":*")) ?? integration?.endpoints[0];
    return [
      {
        id: `${action.id}:all`,
        provider: aggregate?.provider ?? notificationChannelMeta[action.type].label,
        label: aggregate?.label ?? (action.type === "in_app" ? "All signed-in dashboards" : "All configured endpoints"),
        unavailable: !integration?.configured && action.type !== "in_app",
      },
    ];
  }

  if (!action.target_ids.length) {
    return [
      {
        id: `${action.id}:none`,
        provider: notificationChannelMeta[action.type].label,
        label: "No targets selected",
        unavailable: true,
      },
    ];
  }

  return action.target_ids.map((targetId) => {
    const endpoint = integration?.endpoints.find((item) => item.id === targetId);
    if (endpoint) {
      return { id: targetId, provider: endpoint.provider, label: endpoint.label, unavailable: false };
    }
    if (targetId.startsWith("whatsapp:number:")) {
      const value = targetId.replace(/^whatsapp:number:/, "");
      return { id: targetId, provider: "WhatsApp", label: value || "Dynamic phone number", unavailable: false };
    }
    return {
      id: targetId,
      provider: providerLabelForNotificationTarget(targetId, integration),
      label: unavailableNotificationTargetLabel(targetId),
      unavailable: true,
    };
  });
}

function providerLabelForNotificationTarget(targetId: string, integration?: NotificationIntegration) {
  if (targetId.startsWith("apprise:")) return "Apprise";
  if (targetId.startsWith("discord:")) return "Discord";
  if (targetId.startsWith("whatsapp:")) return "WhatsApp";
  if (targetId.startsWith("home_assistant_mobile:") || targetId.startsWith("home_assistant_tts:")) return "Home Assistant";
  if (targetId === "dashboard") return "Dashboard";
  return integration?.provider ?? "Target";
}

function unavailableNotificationTargetLabel(targetId: string) {
  const raw = targetId.includes(":") ? targetId.split(":").slice(1).join(":") : targetId;
  return `${raw || "Unknown target"} unavailable`;
}

function createWorkflowDraft(): NotificationRule {
  return {
    id: draftId("workflow"),
    name: "New Notification",
    trigger_event: "",
    conditions: [],
    actions: [],
    is_active: true
  };
}

function createWorkflowAction(
  type: NotificationActionType,
  overrides: Partial<Pick<NotificationAction, "target_mode" | "target_ids">> = {}
): NotificationAction {
  const templates = defaultWorkflowActionTemplates[type];
  return {
    id: draftId("action"),
    type,
    target_mode: overrides.target_mode ?? "all",
    target_ids: overrides.target_ids ?? [],
    title_template: templates.title_template,
    message_template: templates.message_template,
    gate_malfunction_stages: [],
    media: { attach_camera_snapshot: false, camera_id: "" },
    actionable: { enabled: false, action: "" }
  };
}

function cloneNotificationRule(rule: NotificationRule): NotificationRule {
  return normalizeNotificationRule(JSON.parse(JSON.stringify(rule)) as Partial<NotificationRule>);
}

function workflowRulePayload(rule: NotificationRule) {
  const normalized = normalizeNotificationRule(rule);
  return {
    name: normalized.name.trim() || "Notification Workflow",
    trigger_event: normalized.trigger_event,
    conditions: normalized.conditions,
    actions: normalized.actions,
    is_active: normalized.is_active
  };
}

function normalizeNotificationRule(rule: Partial<NotificationRule>): NotificationRule {
  const rawTrigger = stringifyTemplateValue(rule.trigger_event);
  const actions = Array.isArray(rule.actions) ? rule.actions.map(normalizeNotificationAction) : [];
  return {
    id: stringifyTemplateValue(rule.id) || draftId("workflow"),
    name: stringifyTemplateValue(rule.name) || "Notification Workflow",
    trigger_event: rawTrigger,
    conditions: Array.isArray(rule.conditions) ? rule.conditions.map(normalizeNotificationCondition) : [],
    actions,
    is_active: rule.is_active !== false,
    last_fired_at: rule.last_fired_at ?? null,
    created_at: rule.created_at,
    updated_at: rule.updated_at,
  };
}

function normalizeNotificationCondition(condition: Partial<NotificationCondition>): NotificationCondition {
  const rawType = stringifyTemplateValue(condition.type);
  const type: NotificationConditionType = rawType === "presence" ? "presence" : "schedule";
  return {
    id: stringifyTemplateValue(condition.id) || draftId("condition"),
    type,
    schedule_id: stringifyTemplateValue(condition.schedule_id),
    mode: normalizePresenceConditionMode(condition.mode),
    person_id: stringifyTemplateValue(condition.person_id),
  };
}

function normalizePresenceConditionMode(value: unknown): PresenceConditionMode {
  if (value === "no_one_home" || value === "person_home" || value === "someone_home") return value;
  return "someone_home";
}

function normalizeGateMalfunctionStages(value: unknown): NotificationGateMalfunctionStage[] {
  if (!Array.isArray(value)) return [];
  const stages: NotificationGateMalfunctionStage[] = [];
  value.forEach((item) => {
    const stage = stringifyTemplateValue(item);
    if (isGateMalfunctionStage(stage) && !stages.includes(stage)) stages.push(stage);
  });
  return stages;
}

function isGateMalfunctionStage(value: string): value is NotificationGateMalfunctionStage {
  return value === "initial" || value === "30m" || value === "60m" || value === "2hrs" || value === "fubar" || value === "resolved";
}

function normalizeNotificationAction(action: Partial<NotificationAction>): NotificationAction {
  const rawType = stringifyTemplateValue(action.type);
  const type = isNotificationActionType(rawType) ? rawType : "in_app";
  const templates = defaultWorkflowActionTemplates[type];
  return {
    id: stringifyTemplateValue(action.id) || draftId("action"),
    type,
    target_mode: normalizeNotificationTargetMode(action.target_mode),
    target_ids: Array.isArray(action.target_ids) ? action.target_ids.map(stringifyTemplateValue).filter(Boolean) : [],
    title_template: stringifyTemplateValue(action.title_template) || templates.title_template,
    message_template: stringifyTemplateValue(action.message_template) || templates.message_template,
    gate_malfunction_stages: normalizeGateMalfunctionStages(action.gate_malfunction_stages),
    media: normalizeNotificationMedia(action.media),
    actionable: normalizeNotificationActionable(action.actionable),
  };
}

function isNotificationActionType(value: string): value is NotificationActionType {
  return value === "mobile" || value === "in_app" || value === "voice" || value === "discord" || value === "whatsapp";
}

function normalizeNotificationTargetMode(value: unknown): NotificationTargetMode {
  if (value === "many" || value === "selected" || value === "all") return value;
  return "all";
}

function normalizeNotificationMedia(media: unknown): NotificationAction["media"] {
  const raw = media && typeof media === "object" ? media as Partial<NotificationAction["media"]> : {};
  return {
    attach_camera_snapshot: raw.attach_camera_snapshot === true,
    camera_id: stringifyTemplateValue(raw.camera_id),
  };
}

function normalizeNotificationActionable(actionable: unknown): NotificationAction["actionable"] {
  const raw = actionable && typeof actionable === "object" ? actionable as Partial<NotificationAction["actionable"]> : {};
  const action = stringifyTemplateValue(raw.action);
  return {
    enabled: raw.enabled === true && action === "gate.open",
    action: action === "gate.open" ? action : "",
  };
}

function notificationActionWithSupportedActionable(action: NotificationAction, options: NotificationActionableOption[]) {
  const normalized = normalizeNotificationAction(action);
  if (!normalized.actionable.enabled) return normalized;
  if (options.some((option) => option.value === normalized.actionable.action)) return normalized;
  return { ...normalized, actionable: { enabled: false, action: "" } };
}

function notificationActionableLabel(action: string) {
  if (action === "gate.open") return "Open Gate";
  return titleCase(action || "Action");
}

function stringifyTemplateValue(value: unknown) {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function pluralize(word: string, count: number) {
  return count === 1 ? word : `${word}s`;
}

function notificationSeverityTone(value: string): BadgeTone {
  if (value === "critical") return "red";
  if (value === "warning") return "amber";
  if (value === "info") return "blue";
  return "gray";
}

function renderWorkflowPreview(actions: NotificationAction[], context: Record<string, string>, triggerEvent = "", stageOptions: NotificationGateMalfunctionStageOption[] = []) {
  return actions.map(normalizeNotificationAction).map((action) => {
    const generated = triggerEvent === "gate_malfunction"
      ? gateMalfunctionPreviewContent(action.type, context, stageOptions)
      : null;
    const title = generated?.title ?? renderWorkflowTemplate(action.title_template, context);
    const message = generated?.message ?? renderWorkflowTemplate(action.message_template, context);
    return {
      ...action,
      title,
      message,
      phoneticsApplied: action.type === "voice" && hasVehicleTtsPhoneticMatch(message),
    };
  });
}

function gateMalfunctionPreviewContent(actionType: NotificationActionType, context: Record<string, string>, stageOptions: NotificationGateMalfunctionStageOption[] = []) {
  const stage = isGateMalfunctionStage(context.MalfunctionStage) ? context.MalfunctionStage : "initial";
  const stageLabel = stageOptions.find((item) => item.value === stage)?.label ?? titleCase(stage.replace("fubar", "FUBAR").replace("hrs", " hours").replace("m", " minutes"));
  const title = stage === "resolved" ? "Gate malfunction resolved" : `Gate Malfunction - ${stageLabel}`;
  const body = gateMalfunctionPlainPreviewBody(stage);
  return {
    title,
    message: actionType === "voice" ? `Attention. ${body}` : body,
  };
}

function gateMalfunctionPlainPreviewBody(stage: NotificationGateMalfunctionStage) {
  if (stage === "initial") return "The gate has malfunctioned and is stuck open. Alfred is trying to resolve it.";
  if (stage === "30m") return "The gate is still stuck open. Alfred is still working on it.";
  if (stage === "60m") return "The gate has been stuck open for about an hour. It is not looking good, but Alfred is still on the case.";
  if (stage === "2hrs") return "The gate has been stuck open for over two hours. Alfred has not been able to fix it yet.";
  if (stage === "fubar") return "The gate is still stuck open and Alfred has run out of automatic fixes. Please check the gate when you can.";
  return "The gate malfunction has been resolved and the gate is closed again.";
}

function renderWorkflowTemplate(template: string, context: Record<string, string>) {
  return template.replace(/@([A-Za-z][A-Za-z0-9_]*)/g, (_, token: string) => context[token] ?? "").trim();
}

function hasVehicleTtsPhoneticMatch(message: string) {
  return vehicleTtsPhoneticPattern.test(message);
}

function draftId(prefix: string) {
  return `draft-${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatCompactLastFired(value?: string | null) {
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
