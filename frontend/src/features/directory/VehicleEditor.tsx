import { useModalFocus } from "../../ui/useModalFocus";
import { useModalClose } from "../../ui/useModalClose";
import { useEditorDismiss } from "../../ui/useEditorDismiss";
import { Camera, Car, Check, CircleDot, Plus, RefreshCw, ShieldCheck, Type, X } from "lucide-react";
import React from "react";
import { useDirectoryOptions } from "./reads";
import { DirectoryPagination } from "./DirectoryPagination";

import { api, createActionConfirmation } from "../../api/client";
import { titleCase } from "../../lib/format";
import { fileToDataUrl, mediaSource } from "../../lib/media";
import { Badge } from "../../ui/primitives";
import type { Group, Person, Schedule, Vehicle } from "../../api/types";

import type { DvlaLookupResponse } from "./types";
import { groupPeopleByDirectoryGroup, motComplianceTone, taxComplianceTone, vehicleComplianceExpiryLabel, vehicleLastDvlaCheckLabel, normalizePlateInput, localDateKey } from "./model";
import { PersonAvatar, VehiclePhoto } from "./components";

export function VehiclePeoplePicker({
  groups,
  onToggle,
  people: initialPeople,
  selectedPersonIds
}: {
  groups: Group[];
  onToggle: (personId: string) => void;
  people: Person[];
  selectedPersonIds: string[];
}) {
  const personOptions = useDirectoryOptions("people", initialPeople, selectedPersonIds);
  const people = personOptions.items;
  const selectedPersonIdSet = React.useMemo(() => new Set(selectedPersonIds), [selectedPersonIds]);
  const selectedPeople = React.useMemo(
    () => people
      .filter((person) => selectedPersonIdSet.has(person.id))
      .sort((left, right) => left.display_name.localeCompare(right.display_name)),
    [people, selectedPersonIdSet]
  );
  const groupSections = React.useMemo(() => {
    const allPeople = [...personOptions.pageItems].sort((left, right) => left.display_name.localeCompare(right.display_name));
    return [
      {
        id: "all",
        title: "All People",
        description: "Every directory person",
        items: allPeople
      },
      ...groupPeopleByDirectoryGroup(personOptions.pageItems, groups).map((section) => ({
        id: section.id,
        title: section.name,
        description: section.category ? titleCase(section.category) : "No group",
        items: section.items
      }))
    ];
  }, [groups, personOptions.pageItems]);
  const [activeGroupId, setActiveGroupId] = React.useState("all");
  const personQuery = personOptions.query;
  const setPersonQuery = personOptions.setQuery;
  const activeGroup = groupSections.find((section) => section.id === activeGroupId) ?? groupSections[0];
  const visiblePeople = activeGroup.items;

  React.useEffect(() => {
    if (!groupSections.some((section) => section.id === activeGroupId)) {
      setActiveGroupId("all");
    }
  }, [activeGroupId, groupSections]);

  return (
    <div className="field vehicle-person-field">
      <span>Assigned people</span>
      <div className="vehicle-person-picker">
        <div className="vehicle-person-selected">
          {selectedPeople.length ? selectedPeople.map((person) => (
            <button className="vehicle-person-chip" key={person.id} onClick={() => onToggle(person.id)} type="button">
              <PersonAvatar person={person} />
              <span>{person.display_name}</span>
              <X size={13} />
            </button>
          )) : <span className="vehicle-person-empty">No people assigned</span>}
        </div>
        <div className="vehicle-person-browser">
          <input aria-label="Search people to assign" className="assignment-search" placeholder="Search people" value={personQuery} onChange={(event) => setPersonQuery(event.target.value)} />
          <DirectoryPagination page={personOptions} />
          <div className="vehicle-person-groups" role="tablist" aria-label="Person groups">
            {groupSections.map((section) => (
              <button
                aria-selected={activeGroup.id === section.id}
                className={activeGroup.id === section.id ? "vehicle-person-group active" : "vehicle-person-group"}
                key={section.id}
                onClick={() => setActiveGroupId(section.id)}
                type="button"
              >
                <span>
                  <strong>{section.title}</strong>
                  <small>{section.description}</small>
                </span>
                <Badge tone="gray">{section.items.length}</Badge>
              </button>
            ))}
          </div>
          <div className="vehicle-person-list">
            {visiblePeople.length ? visiblePeople.map((person) => {
              const selected = selectedPersonIdSet.has(person.id);
              return (
                <label className={selected ? "vehicle-person-row selected" : "vehicle-person-row"} key={person.id}>
                  <input checked={selected} onChange={() => onToggle(person.id)} type="checkbox" />
                  <PersonAvatar person={person} />
                  <span>
                    <strong>{person.display_name}</strong>
                    <small>{person.group ? `${person.group} - ${titleCase(person.category ?? "")}` : titleCase(person.category ?? "No group")}</small>
                  </span>
                </label>
              );
            }) : <div className="empty-state compact">{personQuery ? "No people match this search" : "No people in this group"}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

export function VehicleModal({
  defaultPolicyOptionLabel,
  groups,
  mode,
  onClose: finishClose,
  onSaved: finishSaved,
  people,
  refreshVehicles,
  schedules,
  setPageError,
  vehicle
}: {
  defaultPolicyOptionLabel: string;
  groups: Group[];
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  people: Person[];
  refreshVehicles: () => Promise<void>;
  schedules: Schedule[];
  setPageError: (message: string) => void;
  vehicle: Vehicle | null;
}) {
  const modalRef = React.useRef<HTMLFormElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const onSaved = useModalClose(modalRef, finishSaved);
  const [form, setForm] = React.useState({
    registration_number: vehicle?.registration_number ?? "",
    vehicle_photo_data_url: vehicle?.vehicle_photo_data_url ?? "",
    make: vehicle?.make ?? "",
    model: vehicle?.model ?? "",
    color: vehicle?.color ?? "",
    fuel_type: vehicle?.fuel_type ?? "",
    mot_status: vehicle?.mot_status ?? "",
    tax_status: vehicle?.tax_status ?? "",
    mot_expiry: vehicle?.mot_expiry ?? "",
    tax_expiry: vehicle?.tax_expiry ?? "",
    last_dvla_lookup_date: vehicle?.last_dvla_lookup_date ?? "",
    description: vehicle?.description ?? "",
    person_ids: vehicle?.person_ids ?? (vehicle?.person_id ? [vehicle.person_id] : []),
    schedule_id: vehicle?.schedule_id ?? "",
    is_active: vehicle?.is_active ?? true
  });
  const existingVehiclePhotoSource = mediaSource(vehicle?.vehicle_photo_url, vehicle?.vehicle_photo_data_url);
  const [vehiclePhotoChanged, setVehiclePhotoChanged] = React.useState(false);
  const vehiclePhotoPreview = form.vehicle_photo_data_url || (!vehiclePhotoChanged ? existingVehiclePhotoSource : "");
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const submittingRef = React.useRef(false);
  const [dirty, setDirty] = React.useState(false);
  const requestClose = useEditorDismiss(onClose, dirty, submitting, "vehicle changes");
  useModalFocus(modalRef, true, requestClose);
  const [complianceRefreshing, setComplianceRefreshing] = React.useState(false);
  const [dvlaLookup, setDvlaLookup] = React.useState<{ status: "idle" | "loading" | "found" | "error"; message: string }>({
    status: "idle",
    message: ""
  });
  const lookupRequestRef = React.useRef(0);
  const lastLookupRegistrationRef = React.useRef("");
  const initialRegistrationRef = React.useRef(vehicle?.registration_number ?? "");

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => { setDirty(true); setForm((current) => ({ ...current, [field]: value })); };

  const toggleAssignedPerson = (personId: string) => {
    update(
      "person_ids",
      form.person_ids.includes(personId)
        ? form.person_ids.filter((id) => id !== personId)
        : [...form.person_ids, personId]
    );
  };

  React.useEffect(() => {
    const requestId = ++lookupRequestRef.current;
    const registrationNumber = normalizePlateInput(form.registration_number);
    const initialRegistration = normalizePlateInput(initialRegistrationRef.current);
    if (registrationNumber.length < 2 || (mode === "edit" && registrationNumber === initialRegistration)) {
      setDvlaLookup({ status: "idle", message: "" });
      return;
    }
    if (registrationNumber === lastLookupRegistrationRef.current) return;

    const controller = new AbortController();
    setDvlaLookup({ status: "loading", message: "Looking up DVLA vehicle details" });

    const timer = window.setTimeout(async () => {
      try {
          const result = await api.post<DvlaLookupResponse>("/api/v1/integrations/dvla/lookup", {
            registration_number: registrationNumber
          }, { signal: controller.signal });
          if (controller.signal.aborted || lookupRequestRef.current !== requestId) return;
          lastLookupRegistrationRef.current = registrationNumber;
          const displayVehicle = result.display_vehicle ?? result.vehicle;
          const normalizedVehicle = result.normalized_vehicle;
          const make = normalizedVehicle?.make || (typeof displayVehicle.make === "string" ? displayVehicle.make : "");
          const model = typeof displayVehicle.model === "string" ? displayVehicle.model : "";
          const normalizedColor = normalizedVehicle?.colour ?? normalizedVehicle?.color;
          const color = normalizedColor || (typeof (displayVehicle.colour ?? displayVehicle.color) === "string" ? String(displayVehicle.colour ?? displayVehicle.color) : "");
          const fuelType = normalizedVehicle?.fuel_type || (typeof displayVehicle.fuelType === "string" ? displayVehicle.fuelType : "");
          setForm((current) => ({
            ...current,
            registration_number: result.registration_number || current.registration_number,
            make: make || current.make,
            model: model || current.model,
            color: color || current.color,
            fuel_type: fuelType || current.fuel_type,
            mot_status: normalizedVehicle?.mot_status ?? current.mot_status,
            tax_status: normalizedVehicle?.tax_status ?? current.tax_status,
            mot_expiry: normalizedVehicle?.mot_expiry ?? current.mot_expiry,
            tax_expiry: normalizedVehicle?.tax_expiry ?? current.tax_expiry,
            last_dvla_lookup_date: normalizedVehicle ? localDateKey() : current.last_dvla_lookup_date
          }));
        setDvlaLookup({ status: "found", message: "DVLA details applied" });
      } catch (lookupError) {
        if (controller.signal.aborted || lookupRequestRef.current !== requestId) return;
        const message = lookupError instanceof Error ? lookupError.message : "DVLA lookup failed";
        if (message.toLowerCase().includes("api key is not configured")) {
          lastLookupRegistrationRef.current = registrationNumber;
          setDvlaLookup({ status: "idle", message: "" });
          return;
        }
        setDvlaLookup({ status: "error", message });
      }
    }, 850);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
      ++lookupRequestRef.current;
    };
  }, [form.registration_number, mode]);

  const uploadPhoto = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Please choose an image file.");
      return;
    }
      if (file.size > 8 * 1024 * 1024) {
        setError("Vehicle images must be 8 MB or smaller.");
        return;
      }
      setError("");
      setVehiclePhotoChanged(true);
      update("vehicle_photo_data_url", await fileToDataUrl(file));
    };

    const refreshCompliance = async () => {
      if (mode !== "edit" || !vehicle) return;
      setError("");
      setPageError("");
      setComplianceRefreshing(true);
      try {
        const confirmationPayload = { vehicle_id: vehicle.id };
        const confirmation = await createActionConfirmation("vehicle.dvla_refresh", confirmationPayload, {
          target_entity: "Vehicle",
          target_id: vehicle.id,
          target_label: vehicle.registration_number,
          reason: "Refresh DVLA compliance data"
        });
        const refreshed = await api.post<Vehicle>(`/api/v1/vehicles/${vehicle.id}/dvla-refresh`, {
          confirmation_token: confirmation.confirmation_token
        });
        setForm((current) => ({
          ...current,
          make: refreshed.make ?? current.make,
          color: refreshed.color ?? current.color,
          fuel_type: refreshed.fuel_type ?? current.fuel_type,
          mot_status: refreshed.mot_status ?? "",
          tax_status: refreshed.tax_status ?? "",
          mot_expiry: refreshed.mot_expiry ?? "",
          tax_expiry: refreshed.tax_expiry ?? "",
          last_dvla_lookup_date: refreshed.last_dvla_lookup_date ?? ""
        }));
        await refreshVehicles();
      } catch (lookupError) {
        const message = lookupError instanceof Error ? lookupError.message : "Unable to refresh DVLA compliance";
        setError(message);
        setPageError(message);
      } finally {
        setComplianceRefreshing(false);
      }
    };

    const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setError("");
    setPageError("");
    setSubmitting(true);
      const payload: Record<string, unknown> = {
        registration_number: form.registration_number,
        make: form.make || null,
        model: form.model || null,
        color: form.color || null,
        fuel_type: form.fuel_type || null,
        mot_status: form.mot_status || null,
        tax_status: form.tax_status || null,
        mot_expiry: form.mot_expiry || null,
        tax_expiry: form.tax_expiry || null,
        last_dvla_lookup_date: form.last_dvla_lookup_date || null,
        description: form.description || null,
        person_ids: form.person_ids,
        schedule_id: form.schedule_id || null,
      is_active: form.is_active
    };
    if (mode === "create" || vehiclePhotoChanged) {
      payload.vehicle_photo_data_url = form.vehicle_photo_data_url || null;
    }
    try {
      if (mode === "edit" && vehicle) {
        const confirmation = await createActionConfirmation("vehicle.update", { ...payload, vehicle_id: vehicle.id }, {
          target_entity: "Vehicle",
          target_id: vehicle.id,
          target_label: String(payload.registration_number || vehicle.registration_number),
          reason: "Update directory vehicle"
        });
        await api.patch<Vehicle>(`/api/v1/vehicles/${vehicle.id}`, {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      } else {
        const confirmation = await createActionConfirmation("vehicle.create", payload, {
          target_entity: "Vehicle",
          target_label: String(payload.registration_number || ""),
          reason: "Create directory vehicle"
        });
        await api.post<Vehicle>("/api/v1/vehicles", {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save vehicle";
      setError(message);
      setPageError(message);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const previewVehicle: Vehicle = {
    id: vehicle?.id ?? "preview",
    registration_number: form.registration_number || "NEW",
    vehicle_photo_data_url: vehiclePhotoPreview.startsWith("data:") ? vehiclePhotoPreview : null,
    vehicle_photo_url: vehiclePhotoPreview && !vehiclePhotoPreview.startsWith("data:") ? vehiclePhotoPreview : null,
    description: form.description || null,
    make: form.make || null,
    model: form.model || null,
    color: form.color || null,
      mot_status: form.mot_status || null,
      tax_status: form.tax_status || null,
      mot_expiry: form.mot_expiry || null,
      tax_expiry: form.tax_expiry || null,
      last_dvla_lookup_date: form.last_dvla_lookup_date || null,
    person_id: form.person_ids.length === 1 ? form.person_ids[0] : null,
    owner: form.person_ids.length === 1
      ? people.find((person) => person.id === form.person_ids[0])?.display_name ?? null
      : null,
    person_ids: form.person_ids,
    owners: people.filter((person) => form.person_ids.includes(person.id)).map((person) => person.display_name),
    schedule_id: form.schedule_id || null,
    schedule: schedules.find((schedule) => schedule.id === form.schedule_id)?.name ?? null,
    is_active: form.is_active
  };
    const motStatus = form.mot_status || null;
    const taxStatus = form.tax_status || null;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <form ref={modalRef} role="dialog" aria-modal="true" aria-label="Vehicle" className="modal-card vehicle-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Vehicle" : "Add Vehicle"}</h2>
            <p>{mode === "edit" ? "Update vehicle details and assignments." : "Register a vehicle and assign it to people."}</p>
          </div>
          <button className="icon-button" onClick={requestClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="vehicle-upload-row">
          <VehiclePhoto vehicle={previewVehicle} size="large" />
          <label className="upload-button">
            <Camera size={16} />
            <span>{vehiclePhotoPreview ? "Change photo" : "Upload vehicle photo"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {vehiclePhotoPreview ? (
            <button
              className="secondary-button"
              onClick={() => {
                setVehiclePhotoChanged(true);
                update("vehicle_photo_data_url", "");
              }}
              type="button"
            >
              Remove
            </button>
          ) : null}
        </div>
        <label className="field">
          <span>Vehicle Registration</span>
          <div className="field-control">
            <Car size={17} />
            <input value={form.registration_number} onChange={(event) => update("registration_number", event.target.value.toUpperCase())} required />
          </div>
          {dvlaLookup.status !== "idle" ? (
            <small className={`field-hint dvla-lookup-hint ${dvlaLookup.status}`}>
              {dvlaLookup.status === "loading" ? <span className="inline-spinner" aria-hidden="true" /> : null}
              {dvlaLookup.message}
            </small>
          ) : null}
        </label>
        <div className="field-grid">
          <label className="field">
            <span>Vehicle Make</span>
            <div className="field-control">
              <Car size={17} />
              <input value={form.make} onChange={(event) => update("make", event.target.value)} />
            </div>
          </label>
          <label className="field">
            <span>Vehicle Model</span>
            <div className="field-control">
              <Car size={17} />
              <input value={form.model} onChange={(event) => update("model", event.target.value)} />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Colour</span>
            <div className="field-control">
              <CircleDot size={17} />
              <input value={form.color} onChange={(event) => update("color", event.target.value)} />
            </div>
          </label>
          <label className="field">
            <span>Status</span>
            <select value={form.is_active ? "active" : "inactive"} onChange={(event) => update("is_active", event.target.value === "active")}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        <label className="field">
          <span>Friendly description</span>
          <div className="field-control">
            <Type size={17} />
            <input value={form.description} onChange={(event) => update("description", event.target.value)} />
          </div>
        </label>
        <VehiclePeoplePicker
          groups={groups}
          onToggle={toggleAssignedPerson}
          people={people}
          selectedPersonIds={form.person_ids}
        />
        <label className="field">
          <span>Access Schedule</span>
          <select value={form.schedule_id} onChange={(event) => update("schedule_id", event.target.value)}>
            <option value="">{defaultPolicyOptionLabel}</option>
            {schedules.map((schedule) => (
              <option key={schedule.id} value={schedule.id}>{schedule.name}</option>
            ))}
          </select>
        </label>
          <div className="vehicle-compliance-card">
            <div className="vehicle-compliance-title">
              <ShieldCheck size={17} />
              <div>
                <strong>Compliance</strong>
                <span>{vehicleLastDvlaCheckLabel(form.last_dvla_lookup_date || null)}</span>
              </div>
              {mode === "edit" ? (
                <button
                  aria-label="Refresh DVLA compliance"
                  className="icon-button vehicle-compliance-refresh"
                  disabled={complianceRefreshing}
                  onClick={refreshCompliance}
                  title="Refresh DVLA compliance"
                  type="button"
                >
                  <RefreshCw className={complianceRefreshing ? "spin" : undefined} size={15} />
                </button>
              ) : null}
            </div>
            <div className="vehicle-compliance-grid">
              <div className="vehicle-compliance-row">
                <span className="vehicle-compliance-label">MOT</span>
                <Badge tone={motComplianceTone(motStatus)}>{motStatus || "Unknown"}</Badge>
                <span className="vehicle-compliance-expiry">{vehicleComplianceExpiryLabel(form.mot_expiry || null)}</span>
              </div>
              <div className="vehicle-compliance-row">
                <span className="vehicle-compliance-label">Tax</span>
                <Badge tone={taxComplianceTone(taxStatus)}>{taxStatus || "Unknown"}</Badge>
                <span className="vehicle-compliance-expiry">{vehicleComplianceExpiryLabel(form.tax_expiry || null)}</span>
              </div>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={requestClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <Plus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Vehicle"}
          </button>
        </div>
      </form>
    </div>
  );
}
