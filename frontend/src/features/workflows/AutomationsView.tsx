import { GitBranch, Play, Plus, Save, Split, Trash2, X, Zap } from "lucide-react";
import React from "react";
import type { Person, UserAccount, Vehicle } from "../../api/types";
import type { AutomationAction, AutomationCatalogGroup, AutomationNode, AutomationRule } from "../../api/workflows";
import { workflowApi } from "../../api/workflows";
import { Toolbar } from "../../ui/primitives";
import { AutomationNodeStack, AutomationPreviewPanel, AutomationSelectionModal } from "./AutomationEditor";
import { automationRulePayload, automationVariablesForTrigger, cloneAutomationRule, createAutomationDraft, groupAutomationRulesByTriggerCategory } from "./automationModel";
import { NotificationConfigChip, WorkflowBlock, WorkflowRuleList, WorkflowStatusFilters } from "./components";
import { usePendingWorkflowIds, useTransientRuleStatusFeedback, useWorkflowData, useWorkflowRuleFilters } from "./hooks";
import type { NotificationStatusFilter, WorkflowFeedback, WorkflowRuleStatusFeedback } from "./model";
import { renderWorkflowTemplate } from "./model";
import { AutomationRunHistory } from "./AutomationRunHistory";

export function AutomationsView({ currentUser, people, refreshToken, vehicles }: { currentUser: UserAccount; people: Person[]; refreshToken: number; vehicles: Vehicle[] }) {
  const { data, rules, setRules, loading, error, load } = useWorkflowData(workflowApi.getAutomationData, refreshToken);
  const catalog = data?.catalog ?? null;
  const users = data?.users ?? [];
  const [draft, setDraft] = React.useState<AutomationRule | null>(null);
  const [modal, setModal] = React.useState<"trigger" | "condition" | "action" | null>(null);
  const [saving, setSaving] = React.useState(false);
  const { filterCounts, filteredRules, setStatusFilter, statusFilter } = useWorkflowRuleFilters(rules);
  const [togglingRuleIds, setRuleToggling] = usePendingWorkflowIds();
  const [ruleStatusFeedback, setRuleStatusFeedback] = useTransientRuleStatusFeedback();
  const [feedback, setFeedback] = React.useState<WorkflowFeedback | null>(null);
  const [dryRun, setDryRun] = React.useState<Record<string, unknown> | null>(null);
  const [showRuns, setShowRuns] = React.useState(false);

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
        {currentUser.role === "admin" ? <button className="secondary-button" type="button" aria-expanded={showRuns} onClick={() => setShowRuns((value) => !value)}>{showRuns ? "Hide run history" : "Run history"}</button> : null}
        <button className="secondary-button" onClick={addAutomation} type="button">
          <Plus size={15} /> Add Automation
        </button>
      </Toolbar>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {feedback && !draft ? <div className={`notification-feedback ${feedback.tone}`}>{feedback.text}</div> : null}
      {showRuns ? <AutomationRunHistory currentUser={currentUser} refreshToken={refreshToken} rules={rules} /> : null}

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
