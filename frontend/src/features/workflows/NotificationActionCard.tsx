import { AlertTriangle, Check, Plus, Search, Smartphone, Sparkles, Trash2, Users, X } from "lucide-react";
import React from "react";
import { createPortal } from "react-dom";
import type { UnifiProtectCamera } from "../../api/types";
import type { NotificationAction, NotificationActionableOption, NotificationEndpoint, NotificationGateMalfunctionStage, NotificationGateMalfunctionStageOption, NotificationIntegration, NotificationVariable } from "../../api/workflows";
import { notificationChannelMeta } from "../../lib/notifications";
import { matchesSearchText } from "./model";
import { concreteNotificationEndpoints, normalizeGateMalfunctionStages, normalizeNotificationActionable, normalizeNotificationMedia, notificationActionTargetChips } from "./notificationModel";
import { PlainTemplateEditor, SafeVariableRichTextEditor } from "./TemplateEditor";

export function NotificationActionCard({
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
  const [addingRecipients, setAddingRecipients] = React.useState(false);
  const mobileEndpoints = concreteNotificationEndpoints(integration?.endpoints ?? []);
  const editableTargetIds = action.target_mode === "all"
    ? mobileEndpoints.map((endpoint) => endpoint.id)
    : Array.from(new Set(action.target_ids.flatMap((id) => {
      if (!id.endsWith(":*")) return [id];
      const matches = mobileEndpoints.filter((endpoint) => endpoint.id.startsWith(id.slice(0, -1)));
      return matches.length ? matches.map((endpoint) => endpoint.id) : [id];
    })));
  const targetChips = notificationActionTargetChips(
    action.type === "mobile" && editableTargetIds.length
      ? { ...action, target_mode: "selected", target_ids: editableTargetIds }
      : action,
    integration,
  );
  const availableRecipients = mobileEndpoints.filter((endpoint) => !editableTargetIds.includes(endpoint.id));
  const removeRecipient = (id: string) => {
    const target_ids = editableTargetIds.filter((target) => target !== id);
    if (target_ids.length) onChange({ ...action, target_mode: "selected", target_ids });
  };
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
          <span className={`workflow-target-chip${chip.unavailable ? " unavailable" : ""}${action.type === "mobile" ? " workflow-recipient-pill" : ""}`} key={chip.id}>
            <strong>{chip.provider}</strong>
            <span>{chip.label}</span>
            {action.type === "mobile" && editableTargetIds.includes(chip.id) ? (
              <button
                className="workflow-recipient-remove"
                type="button"
                aria-label={`Remove ${chip.label}`}
                title={editableTargetIds.length === 1 ? "Keep at least one recipient" : `Remove ${chip.label}`}
                disabled={editableTargetIds.length === 1}
                onClick={() => removeRecipient(chip.id)}
              >
                <X size={12} />
              </button>
            ) : null}
          </span>
        ))}
      </div>

      {action.type === "mobile" ? (
        <div className="workflow-recipient-add-row">
          <button className="workflow-recipient-add" type="button" onClick={() => setAddingRecipients(true)}>
            <Plus size={14} /> Add Recipient
          </button>
          {addingRecipients ? (
            <NotificationRecipientModal
              endpoints={availableRecipients}
              onClose={() => setAddingRecipients(false)}
              onAdd={(ids) => {
                onChange({ ...action, target_mode: "selected", target_ids: Array.from(new Set([...editableTargetIds, ...ids])) });
                setAddingRecipients(false);
              }}
            />
          ) : null}
        </div>
      ) : null}

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
          <div className="workflow-camera-settings">
            <div className="workflow-camera-controls">
              <label className={actionMedia.attach_camera_snapshot ? "notification-switch active" : "notification-switch"}>
                <input
                  checked={actionMedia.attach_camera_snapshot}
                  onChange={(event) => onChange({ ...action, media: { ...actionMedia, attach_camera_snapshot: event.target.checked } })}
                  type="checkbox"
                />
                <span>Camera Screenshot</span>
              </label>
              {actionMedia.attach_camera_snapshot ? (
                <label className="field compact-field">
                  <span>Camera</span>
                  <select value={actionMedia.camera_id} onChange={(event) => onChange({ ...action, media: { ...actionMedia, camera_id: event.target.value } })}>
                    <option value="">Select camera</option>
                    {cameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.name}</option>)}
                  </select>
                </label>
              ) : null}
            </div>
            {actionMedia.attach_camera_snapshot && cameraSnapshotUrl ? (
              <div className="workflow-camera-preview">
                <img src={cameraSnapshotUrl} alt={`${selectedCamera?.name ?? "Camera"} snapshot preview`} />
                <span>{selectedCamera?.name ?? "Camera snapshot"}</span>
              </div>
            ) : null}
          </div>
          {supportsActionable ? (
            <div className="workflow-actionable-settings">
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
            </div>
          ) : null}
        </section>
      ) : null}
    </article>
  );
}

function NotificationRecipientModal({ endpoints, onClose, onAdd }: {
  endpoints: NotificationEndpoint[];
  onClose: () => void;
  onAdd: (ids: string[]) => void;
}) {
  const dialogRef = React.useRef<HTMLDialogElement>(null);
  const titleId = React.useId();
  const [query, setQuery] = React.useState("");
  const [selected, setSelected] = React.useState<string[]>([]);
  React.useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    dialog?.showModal();
    dialog?.querySelector<HTMLInputElement>("input")?.focus();
    return () => {
      dialog?.close();
      previousFocus?.focus();
    };
  }, []);
  const visible = endpoints.filter((endpoint) => matchesSearchText(`${endpoint.label} ${endpoint.provider}`, query.trim().toLowerCase()));
  const providers = Array.from(new Set(visible.map((endpoint) => endpoint.provider)));
  return createPortal(
    <dialog className="workflow-recipient-modal" ref={dialogRef} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <div className="workflow-recipient-modal-header">
        <span className="workflow-recipient-modal-icon"><Users size={21} /></span>
        <div><h2 id={titleId}>Add recipients</h2><p>Choose who receives this mobile notification.</p></div>
        <button className="icon-button" type="button" aria-label="Close recipient selector" onClick={onClose}><X size={17} /></button>
      </div>
      <div className="workflow-recipient-search">
        <Search size={17} />
        <input aria-label="Search recipients" placeholder="Search by name or provider…" value={query} onChange={(event) => setQuery(event.target.value)} autoFocus />
      </div>
      <div className="workflow-recipient-results">
        {providers.map((provider) => (
          <section className="workflow-recipient-provider" key={provider} aria-label={provider}>
            <h3>{provider}</h3>
            {visible.filter((endpoint) => endpoint.provider === provider).map((endpoint) => {
              const checked = selected.includes(endpoint.id);
              return (
                <button
                  className={`workflow-recipient-choice${checked ? " selected" : ""}`}
                  key={endpoint.id}
                  type="button"
                  aria-pressed={checked}
                  onClick={() => setSelected((current) => current.includes(endpoint.id) ? current.filter((id) => id !== endpoint.id) : [...current, endpoint.id])}
                >
                  <span className="workflow-recipient-avatar"><Smartphone size={18} /></span>
                  <span className="workflow-recipient-choice-name">{endpoint.label}</span>
                  <span className="workflow-recipient-check">{checked ? <Check size={14} /> : <Plus size={14} />}</span>
                </button>
              );
            })}
          </section>
        ))}
        {!visible.length ? <div className="workflow-recipient-empty"><Users size={26} /><strong>{endpoints.length ? "No matching recipients" : "No more recipients available"}</strong><p>{endpoints.length ? "Try another name or provider." : "Configured recipients are already added, or no mobile recipients are configured."}</p></div> : null}
      </div>
      <div className="workflow-recipient-modal-footer">
        <span>{selected.length ? `${selected.length} selected` : "Select recipients to add"}</span>
        <button className="secondary-button" type="button" onClick={onClose}>Cancel</button>
        <button className="primary-button" type="button" disabled={!selected.length} onClick={() => onAdd(selected)}><Plus size={14} />{selected.length > 1 ? `Add ${selected.length} recipients` : "Add recipient"}</button>
      </div>
    </dialog>,
    document.body,
  );
}
