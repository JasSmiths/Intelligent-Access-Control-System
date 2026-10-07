import { useModalFocus } from "../../ui/useModalFocus";
import { useModalClose } from "../../ui/useModalClose";
import { useEditorDismiss } from "../../ui/useEditorDismiss";
import { Camera, Check, Home, Send, UserPlus, UserRound, X, Zap } from "lucide-react";
import React from "react";
import { useDirectoryOptions } from "./reads";
import { DirectoryPagination } from "./DirectoryPagination";

import { api, createActionConfirmation } from "../../api/client";
import { fileToDataUrl, mediaSource } from "../../lib/media";
import { Badge } from "../../ui/primitives";
import type { Group, HomeAssistantDiscovery, HomeAssistantManagedCover, Person, Schedule, Vehicle } from "../../api/types";

import type { PersonPronouns, PersonPronounFormValue, HomeAssistantInputBooleanAction, HomeAssistantPersonSuggestion } from "./types";
import { InputBooleanEntityPicker, InputBooleanActionToggle, MobileAppNotifySelectField } from "./PersonIntegrationFields";
import { suggestHomeAssistantPersonIntegrations } from "./model";
import { PersonAvatar } from "./components";

const SUGGESTED_PERSON_PRONOUNS_BY_FIRST_NAME: Record<string, PersonPronouns> = {
  jason: "he/him",
  john: "he/him",
  james: "he/him",
  david: "he/him",
  michael: "he/him",
  paul: "he/him",
  mark: "he/him",
  peter: "he/him",
  stephen: "he/him",
  steven: "he/him",
  sarah: "she/her",
  steph: "she/her",
  stephanie: "she/her",
  sylvia: "she/her",
  emma: "she/her",
  olivia: "she/her",
  amelia: "she/her",
  ava: "she/her",
  charlotte: "she/her",
  grace: "she/her"
};

function normalizePersonPronounFormValue(value: string): PersonPronounFormValue {
  return value === "he/him" || value === "she/her" ? value : "";
}

function suggestedPersonPronouns(firstName: string): PersonPronounFormValue {
  return SUGGESTED_PERSON_PRONOUNS_BY_FIRST_NAME[firstName.trim().toLowerCase()] ?? "";
}

export function PersonModal({
  defaultPolicyOptionLabel,
  garageDoors,
  groups,
  mode,
  onClose: finishClose,
  onSaved: finishSaved,
  person,
  schedules,
  setPageError,
  vehicles
}: {
  defaultPolicyOptionLabel: string;
  garageDoors: HomeAssistantManagedCover[];
  groups: Group[];
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  person: Person | null;
  schedules: Schedule[];
  setPageError: (message: string) => void;
  vehicles: Vehicle[];
}) {
  const modalRef = React.useRef<HTMLFormElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const onSaved = useModalClose(modalRef, finishSaved);
  const [form, setForm] = React.useState({
    first_name: person?.first_name ?? "",
    last_name: person?.last_name ?? "",
    pronouns: (person?.pronouns ?? "") as PersonPronounFormValue,
    profile_photo_data_url: person?.profile_photo_data_url ?? "",
    group_id: person?.group_id ?? groups[0]?.id ?? "",
    schedule_id: person?.schedule_id ?? "",
    vehicle_ids: person?.vehicles.map((vehicle) => vehicle.id) ?? ([] as string[]),
    garage_door_entity_ids: person?.garage_door_entity_ids ?? ([] as string[]),
    home_assistant_mobile_app_notify_service: person?.home_assistant_mobile_app_notify_service ?? "",
    missed_exit_recovery_enabled: person?.missed_exit_recovery_enabled ?? false,
    missed_exit_recovery_tracker_entity_id: person?.missed_exit_recovery_tracker_entity_id ?? "",
    home_assistant_presence_input_boolean_entity_ids:
      person?.home_assistant_presence_input_boolean_entity_ids ?? ([] as string[]),
    home_assistant_presence_input_boolean_entry_action:
      (person?.home_assistant_presence_input_boolean_entry_action ?? "turn_off") as HomeAssistantInputBooleanAction,
    home_assistant_presence_input_boolean_exit_action:
      (person?.home_assistant_presence_input_boolean_exit_action ?? "turn_off") as HomeAssistantInputBooleanAction,
    notes: person?.notes ?? "",
    is_active: person?.is_active ?? true
  });
  const existingProfilePhotoSource = mediaSource(person?.profile_photo_url, person?.profile_photo_data_url);
  const [profilePhotoChanged, setProfilePhotoChanged] = React.useState(false);
  const profilePhotoPreview = form.profile_photo_data_url || (!profilePhotoChanged ? existingProfilePhotoSource : "");
  const [error, setError] = React.useState("");
  const [haDiscovery, setHaDiscovery] = React.useState<HomeAssistantDiscovery | null>(null);
  const [haDiscoveryError, setHaDiscoveryError] = React.useState("");
  const [haDiscoveryLoading, setHaDiscoveryLoading] = React.useState(false);
  const [haMobileSelectionTouched, setHaMobileSelectionTouched] = React.useState(Boolean(person?.home_assistant_mobile_app_notify_service));
  const [pronounSelectionTouched, setPronounSelectionTouched] = React.useState(Boolean(person?.pronouns));
  const [haSuggestion, setHaSuggestion] = React.useState<HomeAssistantPersonSuggestion>({});
  const [haTestFeedback, setHaTestFeedback] = React.useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [sendingHaTest, setSendingHaTest] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const submittingRef = React.useRef(false);
  const [dirty, setDirty] = React.useState(false);
  const vehicleOptions = useDirectoryOptions("vehicles", vehicles, form.vehicle_ids);
  const vehicleQuery = vehicleOptions.query;
  const setVehicleQuery = vehicleOptions.setQuery;
  const requestClose = useEditorDismiss(onClose, dirty, submitting, "person changes");
  useModalFocus(modalRef, true, requestClose);

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => { setDirty(true); setForm((current) => ({ ...current, [field]: value })); };

    const uploadPhoto = async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Please choose an image file.");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setError("Profile images must be 8 MB or smaller.");
      return;
    }
    setError("");
    setProfilePhotoChanged(true);
    update("profile_photo_data_url", await fileToDataUrl(file));
  };

  const toggleVehicle = (vehicleId: string) => {
    update(
      "vehicle_ids",
      form.vehicle_ids.includes(vehicleId)
        ? form.vehicle_ids.filter((id) => id !== vehicleId)
        : [...form.vehicle_ids, vehicleId]
    );
  };

  const toggleGarageDoor = (entityId: string) => {
    update(
      "garage_door_entity_ids",
      form.garage_door_entity_ids.includes(entityId)
        ? form.garage_door_entity_ids.filter((id) => id !== entityId)
        : [...form.garage_door_entity_ids, entityId]
    );
  };

  const updateMobileNotifyService = (serviceId: string) => {
    setHaMobileSelectionTouched(true);
    setHaTestFeedback(null);
    update("home_assistant_mobile_app_notify_service", serviceId);
  };

  const updatePronouns = (pronouns: string) => {
    setPronounSelectionTouched(true);
    update("pronouns", normalizePersonPronounFormValue(pronouns));
  };

  const updatePresenceInputBooleans = (entityIds: string[]) => {
    update("home_assistant_presence_input_boolean_entity_ids", entityIds);
  };

  const updatePresenceInputBooleanAction = (
    field: "home_assistant_presence_input_boolean_entry_action" | "home_assistant_presence_input_boolean_exit_action",
    action: HomeAssistantInputBooleanAction
  ) => {
    update(field, action);
  };

  const sendHomeAssistantMobileTest = async () => {
    if (!form.home_assistant_mobile_app_notify_service) {
      setHaTestFeedback({ tone: "error", text: "Select a mobile app notification service first." });
      return;
    }
    const personName = `${form.first_name} ${form.last_name}`.trim() || person?.display_name || "this person";
    setSendingHaTest(true);
    setHaTestFeedback({ tone: "info", text: "Sending Home Assistant test notification." });
    try {
      const payload = {
        service_name: form.home_assistant_mobile_app_notify_service,
        person_name: personName
      };
      const confirmation = await createActionConfirmation("notification.mobile_test", payload, {
        target_entity: "NotificationTarget",
        target_id: payload.service_name,
        target_label: personName,
        reason: "Send Home Assistant mobile test notification"
      });
      await api.post("/api/v1/integrations/home-assistant/mobile-notifications/test", {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      setHaTestFeedback({ tone: "success", text: "Home Assistant accepted the test notification." });
    } catch (testError) {
      setHaTestFeedback({
        tone: "error",
        text: testError instanceof Error ? testError.message : "Unable to send Home Assistant test notification."
      });
    } finally {
      setSendingHaTest(false);
    }
  };

  React.useEffect(() => {
    let active = true;
    setHaDiscoveryLoading(true);
    setHaDiscoveryError("");
    api.get<HomeAssistantDiscovery>("/api/v1/integrations/home-assistant/entities")
      .then((discovery) => {
        if (!active) return;
        setHaDiscovery(discovery);
      })
      .catch((loadError) => {
        if (!active) return;
        setHaDiscoveryError(loadError instanceof Error ? loadError.message : "Unable to load Home Assistant entities.");
      })
      .finally(() => {
        if (active) setHaDiscoveryLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  React.useEffect(() => {
    if (pronounSelectionTouched) return;
    const suggestedPronouns = suggestedPersonPronouns(form.first_name);
    setForm((current) => (
      current.pronouns === suggestedPronouns ? current : { ...current, pronouns: suggestedPronouns }
    ));
  }, [form.first_name, pronounSelectionTouched]);

  const suggestedPronounValue = suggestedPersonPronouns(form.first_name);
  const autoDetectedUnsavedPronouns = Boolean(
    suggestedPronounValue &&
    form.pronouns === suggestedPronounValue &&
    form.pronouns !== (person?.pronouns ?? "") &&
    !pronounSelectionTouched
  );

  React.useEffect(() => {
    if (!haDiscovery) return;
    const firstName = form.first_name.trim();
    const lastName = form.last_name.trim();
    if (!firstName || !lastName) {
      setHaSuggestion({});
      return;
    }

    const timeout = window.setTimeout(() => {
      const suggestion = suggestHomeAssistantPersonIntegrations(firstName, lastName, haDiscovery);
      setHaSuggestion(suggestion);
      setForm((current) => ({
        ...current,
        home_assistant_mobile_app_notify_service:
          !haMobileSelectionTouched && !current.home_assistant_mobile_app_notify_service && suggestion.mobile?.id
            ? suggestion.mobile.id
            : current.home_assistant_mobile_app_notify_service
      }));
    }, 700);

    return () => window.clearTimeout(timeout);
  }, [form.first_name, form.last_name, haDiscovery, haMobileSelectionTouched]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setError("");
    setPageError("");
    setSubmitting(true);
    const payload: Record<string, unknown> = {
      first_name: form.first_name,
      last_name: form.last_name,
      pronouns: form.pronouns || null,
      group_id: form.group_id || null,
      schedule_id: form.schedule_id || null,
      vehicle_ids: form.vehicle_ids,
      garage_door_entity_ids: form.garage_door_entity_ids,
      home_assistant_mobile_app_notify_service: form.home_assistant_mobile_app_notify_service || null,
      missed_exit_recovery_enabled: form.missed_exit_recovery_enabled,
      missed_exit_recovery_tracker_entity_id: form.missed_exit_recovery_tracker_entity_id.trim() || null,
      home_assistant_presence_input_boolean_entity_ids:
        form.home_assistant_presence_input_boolean_entity_ids,
      home_assistant_presence_input_boolean_entry_action:
        form.home_assistant_presence_input_boolean_entry_action,
      home_assistant_presence_input_boolean_exit_action:
        form.home_assistant_presence_input_boolean_exit_action,
      notes: form.notes || null,
      is_active: form.is_active
    };
    if (mode === "create" || profilePhotoChanged) {
      payload.profile_photo_data_url = form.profile_photo_data_url || null;
    }
    try {
      if (mode === "edit" && person) {
        const confirmation = await createActionConfirmation("person.update", { ...payload, person_id: person.id }, {
          target_entity: "Person",
          target_id: person.id,
          target_label: `${String(payload.first_name || "")} ${String(payload.last_name || "")}`.trim(),
          reason: "Update directory person"
        });
        await api.patch<Person>(`/api/v1/people/${person.id}`, {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      } else {
        const confirmation = await createActionConfirmation("person.create", payload, {
          target_entity: "Person",
          target_label: `${String(payload.first_name || "")} ${String(payload.last_name || "")}`.trim(),
          reason: "Create directory person"
        });
        await api.post<Person>("/api/v1/people", {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save person";
      setError(message);
      setPageError(message);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const previewPerson: Person = {
    id: "preview",
    first_name: form.first_name,
    last_name: form.last_name,
    display_name: `${form.first_name} ${form.last_name}`.trim() || "New person",
    pronouns: form.pronouns || null,
    profile_photo_data_url: profilePhotoPreview.startsWith("data:") ? profilePhotoPreview : null,
    profile_photo_url: profilePhotoPreview && !profilePhotoPreview.startsWith("data:") ? profilePhotoPreview : null,
    group_id: form.group_id || null,
    group: groups.find((group) => group.id === form.group_id)?.name ?? null,
    category: groups.find((group) => group.id === form.group_id)?.category ?? null,
    schedule_id: form.schedule_id || null,
    schedule: schedules.find((schedule) => schedule.id === form.schedule_id)?.name ?? null,
    is_active: form.is_active,
    notes: form.notes || null,
    garage_door_entity_ids: form.garage_door_entity_ids,
    home_assistant_mobile_app_notify_service: form.home_assistant_mobile_app_notify_service || null,
    missed_exit_recovery_enabled: form.missed_exit_recovery_enabled,
    missed_exit_recovery_tracker_entity_id: form.missed_exit_recovery_tracker_entity_id.trim() || null,
    home_assistant_presence_input_boolean_entity_ids:
      form.home_assistant_presence_input_boolean_entity_ids,
    home_assistant_presence_input_boolean_entry_action:
      form.home_assistant_presence_input_boolean_entry_action,
    home_assistant_presence_input_boolean_exit_action:
      form.home_assistant_presence_input_boolean_exit_action,
    vehicles: []
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <form ref={modalRef} role="dialog" aria-modal="true" aria-label="Person" className="modal-card person-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Person" : "Add Person"}</h2>
            <p>{mode === "edit" ? "Update the profile, group, and vehicle assignments." : "Create a directory profile and assign registered vehicles."}</p>
          </div>
          <button className="icon-button" onClick={requestClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="profile-upload-row">
          <PersonAvatar person={previewPerson} size="large" />
          <label className="upload-button">
            <Camera size={16} />
            <span>{profilePhotoPreview ? "Change photo" : "Upload profile picture"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {profilePhotoPreview ? (
            <button
              className="secondary-button"
              onClick={() => {
                setProfilePhotoChanged(true);
                update("profile_photo_data_url", "");
              }}
              type="button"
            >
              Remove
            </button>
          ) : null}
        </div>
        <div className="field-grid">
          <label className="field">
            <span>First name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} autoComplete="given-name" required />
            </div>
          </label>
          <label className="field">
            <span>Last name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} autoComplete="family-name" required />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Group</span>
            <select value={form.group_id} onChange={(event) => update("group_id", event.target.value)}>
              <option value="">No group</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>{group.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Access Schedule</span>
            <select value={form.schedule_id} onChange={(event) => update("schedule_id", event.target.value)}>
              <option value="">{defaultPolicyOptionLabel}</option>
              {schedules.map((schedule) => (
                <option key={schedule.id} value={schedule.id}>{schedule.name}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Status</span>
            <select value={form.is_active ? "active" : "inactive"} onChange={(event) => update("is_active", event.target.value === "active")}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label-row">
              <span>Pronouns</span>
              {autoDetectedUnsavedPronouns ? (
                <span className="pronoun-auto-pill">Auto Detected - Click Save to Apply</span>
              ) : null}
            </span>
            <select value={form.pronouns} onChange={(event) => updatePronouns(event.target.value)}>
              <option value="">Unspecified</option>
              <option value="he/him">He / him</option>
              <option value="she/her">She / her</option>
            </select>
          </label>
        </div>
        <section className="person-ha-section">
          <div className="person-ha-section-title">
            <span className="ha-device-icon"><Home size={17} /></span>
            <div>
              <strong>Home Assistant</strong>
              <span>{haDiscoveryLoading ? "Loading discovered entities" : haDiscovery ? "Mobile notification link" : "Save credentials in API & Integrations to enable discovery"}</span>
            </div>
          </div>
          {haDiscoveryError ? <div className="auth-error inline-error">{haDiscoveryError}</div> : null}
          <div className="field-grid">
            <MobileAppNotifySelectField
              label="Mobile app notification"
              value={form.home_assistant_mobile_app_notify_service}
              services={haDiscovery?.mobile_app_notification_services ?? []}
              onChange={updateMobileNotifyService}
            />
          </div>
          <div className="person-ha-actions">
            <button
              className="secondary-button"
              disabled={sendingHaTest || !form.home_assistant_mobile_app_notify_service}
              onClick={sendHomeAssistantMobileTest}
              type="button"
            >
              <Send size={15} /> {sendingHaTest ? "Sending..." : "Send Test"}
            </button>
            <span>{form.home_assistant_mobile_app_notify_service || "No mobile app service selected"}</span>
          </div>
          {haTestFeedback ? (
            <div className={`person-ha-test-feedback ${haTestFeedback.tone}`}>{haTestFeedback.text}</div>
          ) : null}
          {haSuggestion.mobile ? (
            <div className="person-ha-suggestions">
              <span>Mobile match {Math.round(haSuggestion.mobile.confidence * 100)}%</span>
            </div>
          ) : null}
        </section>
        <section className="person-ha-section">
          <strong>Missed Exit Recovery · Testing / debug</strong>
          <label className="field"><span>Owner recovery opt-in</span><select value={String(form.missed_exit_recovery_enabled)} onChange={(event) => update("missed_exit_recovery_enabled", event.target.value === "true")}><option value="false">Disabled</option><option value="true">Enabled</option></select></label>
          <label className="field"><span>Home Assistant phone tracker entity</span><input value={form.missed_exit_recovery_tracker_entity_id} onChange={(event) => update("missed_exit_recovery_tracker_entity_id", event.target.value)} placeholder="device_tracker.phone" pattern="device_tracker\.[a-z0-9_]+" /><small className="field-hint">Use an explicit device_tracker entity. Global recovery must also be enabled. Saving does not send a notification or command.</small></label>
        </section>
        <label className="field">
          <span>Operational notes</span>
          <textarea value={form.notes} onChange={(event) => update("notes", event.target.value)} rows={3} />
        </label>
        <div className="field">
          <span>Vehicles</span>
          <input aria-label="Search vehicle assignments" className="assignment-search" placeholder="Search plates or vehicles" value={vehicleQuery} onChange={(event) => setVehicleQuery(event.target.value)} />
          <DirectoryPagination page={vehicleOptions} />
          <div className="vehicle-picker">
            {vehicleOptions.items.length ? vehicleOptions.items.map((vehicle) => {
              const selected = form.vehicle_ids.includes(vehicle.id);
              const assigned = Boolean(vehicle.person_ids?.length || vehicle.person_id) && !selected;
              const owners = (vehicle.owners ?? []).filter((_, index) => vehicle.person_ids?.[index] !== person?.id);
              return (
                <label className={selected ? "vehicle-option selected" : "vehicle-option"} key={vehicle.id}>
                  <input checked={selected} onChange={() => toggleVehicle(vehicle.id)} type="checkbox" />
                  <span>
                    <strong>{vehicle.registration_number}</strong>
                    <small>{vehicle.description ?? ([vehicle.make, vehicle.model].filter(Boolean).join(" ") || "Registered vehicle")}</small>
                  </span>
                  {selected ? <Badge tone="blue">Selected</Badge> : assigned ? <Badge tone="amber">{owners.length ? `Assigned to ${owners.join(", ")}` : "Assigned"}</Badge> : <Badge tone="gray">Available</Badge>}
                </label>
              );
            }) : <div className="empty-state compact">No vehicles available</div>}
          </div>
        </div>
        <div className="field">
          <span>Garage Doors</span>
          <div className="vehicle-picker garage-door-picker">
            {garageDoors.length ? garageDoors.map((door) => {
              const selected = form.garage_door_entity_ids.includes(door.entity_id);
              return (
                <label className={selected ? "vehicle-option garage-door-option selected" : "vehicle-option garage-door-option"} key={door.entity_id}>
                  <input checked={selected} onChange={() => toggleGarageDoor(door.entity_id)} type="checkbox" />
                  <span>
                    <strong>{door.name || door.entity_id}</strong>
                    <small>{door.entity_id}</small>
                  </span>
                  {selected ? <Badge tone="blue">Selected</Badge> : <Badge tone="gray">Available</Badge>}
                </label>
              );
            }) : <div className="empty-state compact">No garage doors configured</div>}
          </div>
        </div>
        <section className="person-ha-section presence-input-boolean-section">
          <div className="person-ha-section-title">
            <span className="ha-device-icon"><Zap size={17} /></span>
            <div>
              <strong>Presence input_booleans</strong>
              <span>{haDiscoveryLoading ? "Loading discovered input_booleans" : "Run Home Assistant actions when this person arrives or leaves"}</span>
            </div>
          </div>
          <InputBooleanEntityPicker
            entities={haDiscovery?.input_boolean_entities ?? []}
            loading={haDiscoveryLoading}
            selectedEntityIds={form.home_assistant_presence_input_boolean_entity_ids}
            onChange={updatePresenceInputBooleans}
          />
          <div className="presence-input-boolean-actions">
            <InputBooleanActionToggle
              label="On arrival"
              value={form.home_assistant_presence_input_boolean_entry_action}
              onChange={(action) => updatePresenceInputBooleanAction("home_assistant_presence_input_boolean_entry_action", action)}
            />
            <InputBooleanActionToggle
              label="On leaving"
              value={form.home_assistant_presence_input_boolean_exit_action}
              onChange={(action) => updatePresenceInputBooleanAction("home_assistant_presence_input_boolean_exit_action", action)}
            />
          </div>
        </section>
        <div className="modal-actions">
          <button className="secondary-button" onClick={requestClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <UserPlus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Person"}
          </button>
        </div>
      </form>
    </div>
  );
}
