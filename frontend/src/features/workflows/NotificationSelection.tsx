import { ArrowLeft, Check, Clock3, Users, X } from "lucide-react";
import React from "react";
import type { Person, Schedule, UserAccount } from "../../api/types";
import type { NotificationAction, NotificationActionType, NotificationCondition, NotificationIntegration, NotificationTriggerGroup } from "../../api/workflows";
import { titleCase } from "../../lib/format";
import { Badge } from "../../ui/primitives";
import { TwoPaneSelectionModal } from "./components";
import { draftId, matchesSearchText } from "./model";
import type { NotificationActionMethod } from "./notificationModel";
import { buildNotificationActionMethods, createWorkflowAction, findCurrentUserPerson, notificationActionCategories, notificationSeverityTone, notificationTriggerGroupIcon } from "./notificationModel";

export function NotificationTriggerModal({
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

export function NotificationConditionModal({
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

export function NotificationActionModal({
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
