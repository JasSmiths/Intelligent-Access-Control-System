import { ArrowLeft, CheckCircle2, MessageCircle, Play, PlugZap, Plus, Sparkles, Trash2 } from "lucide-react";
import React from "react";
import type { Person, UserAccount, Vehicle } from "../../api/types";
import type { AutomationAction, AutomationCatalogGroup, AutomationCatalogItem, AutomationNode, AutomationVariable } from "../../api/workflows";
import { displayUserName, fromDateTimeLocal, titleCase, toDateTimeLocal } from "../../lib/format";
import { Badge } from "../../ui/primitives";
import { automationCategoryIcon, automationNodeIcon, createAutomationNode } from "./automationModel";
import { TwoPaneSelectionModal } from "./components";
import { matchesSearchText, pluralize, stringifyTemplateValue, toggleStringList } from "./model";
import { PlainTemplateEditor } from "./TemplateEditor";

export function AutomationNodeStack({
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

export function AutomationSelectionModal({
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

export function AutomationPreviewPanel({
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
