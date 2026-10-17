import { Car, Plus, Trash2 } from "lucide-react";
import React from "react";
import { useGroups } from "../features/directory/catalogs";
import { useSchedules } from "../features/schedules/reads";
import { useDirectoryIdentities, useDirectoryPage } from "../features/directory/reads";
import { DirectoryPagination } from "../features/directory/DirectoryPagination";

import { api, createActionConfirmation } from "../api/client";
import { useScheduleDefaultPolicyOptionLabel } from "../lib/format";
import { Badge, EmptyState, ErrorState, LoadingState } from "../ui/primitives";
import { useConfirm } from "../ui/useConfirm";
import type { Vehicle } from "../api/types";

import { directoryGroupDefaultOpen, indexPeopleByVehicleId, ownerPeopleForVehicle, vehicleOwnerLabel, groupVehiclesByDirectoryGroup, vehicleTitle } from "../features/directory/model";
import { useDirectoryGroupOpenState } from "../features/directory/hooks";
import { DirectoryGroupAccordion, VehiclePhoto } from "../features/directory/components";
import { VehicleModal } from "../features/directory/VehicleEditor";

export function VehiclesView({ canRefreshInformation, query }: { canRefreshInformation: boolean; query: string }) {
  const directoryPage = useDirectoryPage("vehicles", { query });
  const ownerOptions = useDirectoryIdentities("people", directoryPage.items.flatMap((vehicle) => vehicle.person_ids ?? (vehicle.person_id ? [vehicle.person_id] : [])));
  const groupCatalog = useGroups(); const scheduleCatalog = useSchedules();
  const groups = groupCatalog.items; const schedules = scheduleCatalog.items;
  const readError = directoryPage.error || ownerOptions.error || groupCatalog.error || scheduleCatalog.error;
  const referencesReady = directoryPage.ready && groupCatalog.ready && scheduleCatalog.ready && ownerOptions.ready;
  const refresh = directoryPage.refresh;
  const retry = async () => {
    const results = await Promise.allSettled([directoryPage.refresh(), groupCatalog.refresh(), scheduleCatalog.refresh(), ownerOptions.refresh()]);
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  };
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedVehicle, setSelectedVehicle] = React.useState<Vehicle | null>(null);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  const [deletingId, setDeletingId] = React.useState("");
  const deletingRef = React.useRef("");
  const askConfirm = useConfirm();
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
    // One deletion at a time, from the confirmation prompt through the refresh.
    if (deletingRef.current) return;
    deletingRef.current = vehicle.id; setDeletingId(vehicle.id);
    try {
      if (!(await askConfirm({ title: `Delete ${vehicle.registration_number}?`, message: "This vehicle will be permanently removed from the directory.", confirmLabel: "Delete", tone: "danger" }))) return;
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
      } catch (deleteError) {
        setError(deleteError instanceof Error ? deleteError.message : "Unable to delete vehicle");
        return;
      }
      directoryPage.removeAccepted(vehicle.id);
      setSaved("Vehicle deleted.");
      try { await refresh(); }
      catch { setError("Vehicle deleted, but the list could not be refreshed. Try again to reload the list."); }
    } finally {
      deletingRef.current = ""; setDeletingId("");
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
        <button className="primary-button" onClick={openCreate} disabled={!referencesReady} type="button">
          <Plus size={17} /> Add Vehicle
        </button>
      </div>

      {readError ? <ErrorState title="Vehicles data unavailable" description={readError} onRetry={() => void retry().catch(() => undefined)} retrying={directoryPage.loading} /> : null}
      {error ? <div className="auth-error inline-error">{error}</div> : null}
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
                      <button className="vehicle-row-open" disabled={!referencesReady} onClick={() => openEdit(vehicle)} type="button" aria-label={`Edit vehicle ${vehicle.registration_number}`}>
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
                        disabled={deletingId === vehicle.id}
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
        ) : readError ? null : !directoryPage.ready ? <LoadingState label="Loading vehicles" /> : (
          <EmptyState icon={Car} label="No vehicles match this view" description={query ? "Try another registration, owner, or vehicle name in search." : "Keep registration details and owners together for clear access records."} action={!query ? <button className="secondary-button" onClick={openCreate} disabled={!referencesReady} type="button"><Plus size={15} /> Register vehicle</button> : undefined} />
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
              try { await refresh(); } catch { setError("Vehicle saved, but the list could not be refreshed. Refresh to see the latest data."); }
            }}
            refreshVehicles={refresh}
            schedules={schedules}
            setPageError={setError}
            vehicle={selectedVehicle}
          />
      ) : null}
    </section>
  );
}
