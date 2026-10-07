import { Car, Plus, Trash2 } from "lucide-react";
import React from "react";
import { useDirectoryIdentities, useDirectoryPage } from "../features/directory/reads";
import { DirectoryPagination } from "../features/directory/DirectoryPagination";

import { api, createActionConfirmation } from "../api/client";
import { useScheduleDefaultPolicyOptionLabel } from "../lib/format";
import { Badge, EmptyState } from "../ui/primitives";
import type { Group, Person, Schedule, Vehicle } from "../api/types";

import { directoryGroupDefaultOpen, indexPeopleByVehicleId, ownerPeopleForVehicle, vehicleOwnerLabel, groupVehiclesByDirectoryGroup, vehicleTitle } from "../features/directory/model";
import { useDirectoryGroupOpenState } from "../features/directory/hooks";
import { DirectoryGroupAccordion, VehiclePhoto } from "../features/directory/components";
import { VehicleModal } from "../features/directory/VehicleEditor";

export function VehiclesView({
  canRefreshInformation,
  groups,
  people,
  query,
  refresh,
  refreshToken = 0,
  schedules,
  vehicles
}: {
  canRefreshInformation: boolean;
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
  refreshToken?: number;
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const directoryPage = useDirectoryPage("vehicles", vehicles, { query, refreshToken });
  const ownerOptions = useDirectoryIdentities("people", people, directoryPage.items.flatMap((vehicle) => vehicle.person_ids ?? (vehicle.person_id ? [vehicle.person_id] : [])), refreshToken);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedVehicle, setSelectedVehicle] = React.useState<Vehicle | null>(null);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const peopleById = React.useMemo(() => new Map(ownerOptions.items.map((person) => [person.id, person])), [ownerOptions.items]);
  const peopleByVehicleId = React.useMemo(() => indexPeopleByVehicleId(ownerOptions.items), [ownerOptions.items]);
  const filtered = directoryPage.items;
  const groupedVehicles = React.useMemo(
    () => groupVehiclesByDirectoryGroup(filtered, peopleByVehicleId, peopleById, groups),
    [filtered, groups, peopleById, peopleByVehicleId]
  );
  const { openGroups: openVehicleGroups, toggleGroup: toggleVehicleGroup } = useDirectoryGroupOpenState(groupedVehicles);

  const openCreate = () => {
    setSelectedVehicle(null);
    setModalOpen(true);
  };

  const openEdit = (vehicle: Vehicle) => {
    setSelectedVehicle(vehicle);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedVehicle(null);
  };

  const deleteVehicle = async (vehicle: Vehicle) => {
    if (!window.confirm(`Delete ${vehicle.registration_number}?`)) return;
    setError("");
    try {
      const confirmationPayload = { vehicle_id: vehicle.id };
      const confirmation = await createActionConfirmation("vehicle.delete", confirmationPayload, {
        target_entity: "Vehicle",
        target_id: vehicle.id,
        target_label: vehicle.registration_number,
        reason: "Delete directory vehicle"
      });
      await api.delete(`/api/v1/vehicles/${vehicle.id}`, {
        confirmation_token: confirmation.confirmation_token
      });
      await refresh(); directoryPage.refresh();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete vehicle");
    }
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Directory</span>
          <h1>Vehicles</h1>
          <p>Manage registered vehicles, photos, plates, and assigned drivers.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> Add Vehicle
        </button>
      </div>

      {error || ownerOptions.error ? <div className="auth-error inline-error">{error || ownerOptions.error}</div> : null}
      {saved ? <div className="success-note" role="status">{saved}</div> : null}

      <DirectoryPagination page={directoryPage} />
      <div className="card users-card vehicles-card">
        {filtered.length ? (
          <div className="directory-group-list">
            {groupedVehicles.map((section) => (
              <DirectoryGroupAccordion
                expanded={openVehicleGroups[section.id] ?? directoryGroupDefaultOpen(section)}
                key={section.id}
                onToggle={() => toggleVehicleGroup(section.id)}
                pluralLabel="vehicles"
                section={section}
                singularLabel="vehicle"
              >
                <div className="users-table vehicles-table">
                  {section.items.map((vehicle) => (
                    <article className="user-row vehicle-row" key={vehicle.id}>
                      <button className="vehicle-row-open" onClick={() => openEdit(vehicle)} type="button" aria-label={`Edit vehicle ${vehicle.registration_number}`}>
                      <VehiclePhoto vehicle={vehicle} />
                      <span className="vehicle-row-main directory-row-copy">
                        <strong>{vehicle.registration_number}</strong>
                        <span>{vehicleTitle(vehicle)}</span>
                      </span>
                      <span className="vehicle-owner">{vehicleOwnerLabel(vehicle, peopleByVehicleId, peopleById)}</span>
                      <span className="vehicle-row-schedule">
                        <span className="directory-field-label">Schedule</span>
                        <span className={vehicle.schedule ? "vehicle-chip schedule-chip" : "vehicle-chip inherit-chip"}>
                          {vehicle.schedule ?? "Inherit"}
                        </span>
                      </span>
                      <Badge tone={vehicle.is_active !== false ? "green" : "gray"}>{vehicle.is_active !== false ? "Active" : "Inactive"}</Badge>
                      </button>
                      <button
                        className="icon-button danger vehicle-delete"
                        onClick={() => { deleteVehicle(vehicle).catch(() => undefined); }}
                        type="button"
                        aria-label={`Delete ${vehicle.registration_number}`}
                      >
                        <Trash2 size={16} />
                      </button>
                    </article>
                  ))}
                </div>
              </DirectoryGroupAccordion>
            ))}
          </div>
        ) : (
          <EmptyState icon={Car} label="No vehicles match this view" description={query ? "Try another registration, owner, or vehicle name in search." : "Keep registration details and owners together for clear access records."} action={!query ? <button className="secondary-button" onClick={openCreate} type="button"><Plus size={15} /> Register vehicle</button> : undefined} />
        )}
      </div>

      {modalOpen ? (
        <VehicleModal
            canRefreshInformation={canRefreshInformation}
          defaultPolicyOptionLabel={defaultPolicyOptionLabel}
          groups={groups}
          mode={selectedVehicle ? "edit" : "create"}
            onClose={closeModal}
            onSaved={async () => {
              closeModal();
              setSaved("Vehicle saved.");
              try { await refresh(); directoryPage.refresh(); } catch { setError("Vehicle saved, but the list could not be refreshed. Refresh to see the latest data."); }
            }}
            people={ownerOptions.items}
            refreshVehicles={refresh}
            schedules={schedules}
            setPageError={setError}
            vehicle={selectedVehicle}
          />
      ) : null}
    </section>
  );
}
