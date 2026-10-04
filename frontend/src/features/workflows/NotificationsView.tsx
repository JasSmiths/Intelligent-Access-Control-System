import { useModalClose } from "../../ui/useModalClose";
import { useModalFocus } from "../../ui/useModalFocus";
import { useEditorDismiss } from "../../ui/useEditorDismiss";
import { Bell, Plus, Split, X } from "lucide-react";
import React from "react";
import type { Person, Schedule, UserAccount } from "../../api/types";
import type { NotificationRule, NotificationTriggerGroup } from "../../api/workflows";
import { workflowApi } from "../../api/workflows";
import { notificationEventLabel } from "../../lib/format";
import { notificationChannelMeta } from "../../lib/notifications";
import { ErrorState, LoadingState, Toolbar } from "../../ui/primitives";
import { NotificationConfigChip, WorkflowRuleList, WorkflowStatusFilters } from "./components";
import { useNotificationCameras, usePendingWorkflowIds, useTransientRuleStatusFeedback, useWorkflowData, useWorkflowRuleFilters } from "./hooks";
import type { NotificationStatusFilter, WorkflowFeedback, WorkflowRuleStatusFeedback } from "./model";
import { draftId } from "./model";
import { NotificationWorkflowEditor } from "./NotificationEditor";
import { cloneNotificationRule, createWorkflowDraft, groupNotificationRulesByTriggerCategory, notificationActionWithSupportedActionable, renderWorkflowPreview, workflowRulePayload } from "./notificationModel";
import { NotificationActionModal, NotificationConditionModal, NotificationTriggerModal } from "./NotificationSelection";

export function NotificationsView({ currentUser, people, refreshToken, schedules }: { currentUser: UserAccount; people: Person[]; refreshToken: number; schedules: Schedule[] }) {
  const { data, rules, setRules, loading, error, load } = useWorkflowData(workflowApi.getNotificationData, refreshToken);
  const catalog = data?.catalog ?? null;
  const [draft, setDraft] = React.useState<NotificationRule | null>(null);
  const [modal, setModal] = React.useState<"trigger" | "condition" | "action" | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [previewOpen, setPreviewOpen] = React.useState(false);
  const [selectorDirty, setSelectorDirty] = React.useState(false);
  const modalRef = React.useRef<HTMLDivElement>(null);
  const baseline = React.useRef("");
  const closeDraft = useModalClose(modalRef, () => { setDraft(null); setModal(null); });
  const dismissDraft = useEditorDismiss(closeDraft, Boolean(draft) && JSON.stringify(draft) !== baseline.current, saving, "notification changes");
  const requestClose = () => { if (saving) return; if (modal) { if (modal === "action" && selectorDirty && !window.confirm("Discard notification action selection?")) return; setModal(null); setSelectorDirty(false); } else dismissDraft(); };
  useModalFocus(modalRef, Boolean(draft), requestClose);
  const [testing, setTesting] = React.useState(false);
  const { filterCounts, filteredRules, setStatusFilter, statusFilter } = useWorkflowRuleFilters(rules);
  const [togglingRuleIds, setRuleToggling] = usePendingWorkflowIds();
  const [ruleStatusFeedback, setRuleStatusFeedback] = useTransientRuleStatusFeedback();
  const [feedback, setFeedback] = React.useState<WorkflowFeedback | null>(null);

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
  const cameras = useNotificationCameras(Boolean(draft?.actions.some((action) =>
    action.type === "mobile" || action.type === "in_app" || action.type === "discord"
  )), refreshToken);
  const workflowModalMode: "editor" | "trigger" | "action" = modal === "trigger" || modal === "action" ? modal : "editor";
  const previewContext = catalog?.mock_context ?? {};
  const previewActions = activeDraft
    ? renderWorkflowPreview(activeDraft.actions, previewContext, activeDraft.trigger_event, gateMalfunctionStageOptions)
    : [];



  const selectRule = (rule: NotificationRule) => {
    const next = cloneNotificationRule(rule);
    baseline.current = JSON.stringify(next);
    setDraft(next);
    setModal(null);
    setFeedback(null);
  };

  const updateDraft = (updater: (rule: NotificationRule) => NotificationRule) => {
    setDraft((current) => updater(current ?? createWorkflowDraft()));
  };

  const addWorkflow = () => {
    const next = createWorkflowDraft();
    baseline.current = JSON.stringify(next);
    setDraft(next);
    setModal(null);
    setFeedback(null);
  };

  const deleteRule = async (rule: NotificationRule) => {
    if (rule.id.startsWith("draft-")) {
      await closeDraft();
      return;
    }
    if (!window.confirm(`Delete ${rule.name}?`)) return;
    setFeedback(null);
    try {
      await workflowApi.deleteNotificationRule(rule);
      await load();
      await closeDraft();
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
      const next = cloneNotificationRule(created);
      baseline.current = JSON.stringify(next);
      setDraft(next);
      setModal(null);
      setFeedback({ tone: "success", text: "Notification workflow duplicated and paused for review." });
    } catch (duplicateError) {
      setFeedback({ tone: "error", text: duplicateError instanceof Error ? duplicateError.message : "Unable to duplicate notification workflow." });
    }
  };

  const save = async () => {
    if (!activeDraft || saving) return;
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
      setRules((current) => current.some((item) => item.id === saved.id) ? current.map((item) => item.id === saved.id ? saved : item) : [saved, ...current]);
      await closeDraft();
      void load();
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

  if (loading || (!data && !error)) {
    return (
      <section className="view-stack notifications-page workflow-notifications-page">
        <Toolbar title="Notifications" icon={Bell} />
        <LoadingState label="Loading notification workflows" />
      </section>
    );
  }

  if (!data) {
    return <section className="view-stack notifications-page workflow-notifications-page"><Toolbar title="Notifications" icon={Bell} /><ErrorState title="Notifications unavailable" description={error} onRetry={() => void load()} /></section>;
  }

  return (
    <section className="view-stack notifications-page workflow-notifications-page">
      <Toolbar title="Notifications" count={rules.length} icon={Bell}>
        <button className="secondary-button" onClick={addWorkflow} type="button">
          <Plus size={15} /> Add Notification
        </button>
      </Toolbar>

      {error ? <ErrorState title="Notifications unavailable" description={error} onRetry={() => void load()} /> : null}
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
        <div className="modal-backdrop workflow-editor-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
          <div
            className={`modal-card workflow-editor-modal${workflowModalMode === "editor" ? "" : " selector-mode"}${previewOpen ? " preview-open" : ""}`}
            ref={modalRef}
            aria-label="Notification editor"
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
                    onDirtyChange={setSelectorDirty}
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
                      <div className="workflow-header-actions"><button className="secondary-button workflow-preview-toggle" aria-expanded={previewOpen} aria-controls="workflow-preview" onClick={() => setPreviewOpen((open) => !open)} type="button">Preview</button><button className="icon-button" onClick={requestClose} type="button" aria-label="Close notification editor">
                        <X size={16} />
                      </button></div>
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
                      onCancel={requestClose}
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
      renderConfigChips={(rule) => (<><span className="workflow-flow-summary">When {notificationEventLabel(rule.trigger_event)} → {rule.actions.map((action) => notificationChannelMeta[action.type]?.label ?? action.type).join(", ") || "no action"}</span><NotificationConfigChip count={rule.conditions.length} icon={Split} label="Conditions" /></>)}
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
