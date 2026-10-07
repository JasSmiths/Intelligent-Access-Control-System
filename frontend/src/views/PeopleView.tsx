import { Plus, UserPlus, Users } from "lucide-react";
import React from "react";
import { useDirectoryPage } from "../features/directory/reads";
import { DirectoryPagination } from "../features/directory/DirectoryPagination";

import { activeManagedCovers, titleCase, useScheduleDefaultPolicyOptionLabel } from "../lib/format";
import { Badge, EmptyState } from "../ui/primitives";
import type { Group, HomeAssistantManagedCover, Person, Schedule, Vehicle } from "../api/types";

import { directoryGroupDefaultOpen, groupPeopleByDirectoryGroup } from "../features/directory/model";
import { useDirectoryGroupOpenState } from "../features/directory/hooks";
import { DirectoryGroupAccordion, PersonAvatar } from "../features/directory/components";
import { PersonModal } from "../features/directory/PersonEditor";

export function PeopleView({
  garageDoors,
  groups,
  people,
  query,
  refresh,
  refreshToken = 0,
  schedules,
  vehicles
}: {
  garageDoors: HomeAssistantManagedCover[];
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
  refreshToken?: number;
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const directoryPage = useDirectoryPage("people", people, { query, refreshToken });
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedPerson, setSelectedPerson] = React.useState<Person | null>(null);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const availableGarageDoors = React.useMemo(() => activeManagedCovers(garageDoors), [garageDoors]);
  const garageDoorNameMap = React.useMemo(() => new Map(garageDoors.map((door) => [door.entity_id, door.name || door.entity_id])), [garageDoors]);
  const filtered = directoryPage.items;
  const groupedPeople = React.useMemo(() => groupPeopleByDirectoryGroup(filtered, groups), [filtered, groups]);
  const { openGroups: openPeopleGroups, toggleGroup: togglePeopleGroup } = useDirectoryGroupOpenState(groupedPeople);

  const openCreate = () => {
    setSelectedPerson(null);
    setModalOpen(true);
  };

  const openEdit = (person: Person) => {
    setSelectedPerson(person);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedPerson(null);
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Directory</span>
          <h1>People</h1>
          <p>Manage profiles, access groups, and vehicle assignments.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <UserPlus size={17} /> Add Person
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {saved ? <div className="success-note" role="status">{saved}</div> : null}

      <DirectoryPagination page={directoryPage} />
      <div className="card users-card people-card">
        {filtered.length ? (
          <div className="directory-group-list">
            {groupedPeople.map((section) => (
              <DirectoryGroupAccordion
                expanded={openPeopleGroups[section.id] ?? directoryGroupDefaultOpen(section)}
                key={section.id}
                onToggle={() => togglePeopleGroup(section.id)}
                pluralLabel="people"
                section={section}
                singularLabel="person"
              >
                <div className="users-table people-table">
                  {section.items.map((person) => (
                    <button
                      className="user-row person-row directory-row-button"
                      key={person.id}
                      onClick={() => openEdit(person)}
                      aria-label={`Edit person ${person.display_name}`}
                      type="button"
                    >
                      <PersonAvatar person={person} />
                      <span className="directory-row-copy">
                        <strong>{person.display_name}</strong>
                        <span>{person.category ? titleCase(person.category) : "No category"}{person.group ? ` • ${person.group}` : ""}</span>
                      </span>
                      <Badge tone={person.is_active ? "green" : "gray"}>{person.is_active ? "Active" : "Inactive"}</Badge>
                      <span className="vehicle-chip-list">
                        {person.schedule ? <span className="vehicle-chip schedule-chip">{person.schedule}</span> : null}
                        {person.vehicles.length ? person.vehicles.map((vehicle) => (
                          <span className="vehicle-chip" key={vehicle.id}>{vehicle.registration_number}</span>
                        )) : <span className="muted-value">No vehicles</span>}
                        {(person.garage_door_entity_ids ?? []).map((entityId) => (
                          <span className="vehicle-chip garage-chip" key={entityId}>{garageDoorNameMap.get(entityId) ?? entityId}</span>
                        ))}
                        {person.home_assistant_mobile_app_notify_service ? <span className="vehicle-chip ha-chip">HA mobile</span> : null}
                        {(person.home_assistant_presence_input_boolean_entity_ids ?? []).length ? (
                          <span className="vehicle-chip ha-chip">
                            {person.home_assistant_presence_input_boolean_entity_ids.length} input_boolean
                          </span>
                        ) : null}
                      </span>
                    </button>
                  ))}
                </div>
              </DirectoryGroupAccordion>
            ))}
          </div>
        ) : (
          <EmptyState icon={Users} label="No people match this view" description={query ? "Try another name, group, or registration in search." : "Add people to connect their vehicles, schedules, and access preferences."} action={!query ? <button className="secondary-button" onClick={openCreate} type="button"><Plus size={15} /> Create person</button> : undefined} />
        )}
      </div>

      {modalOpen ? (
        <PersonModal
          defaultPolicyOptionLabel={defaultPolicyOptionLabel}
          garageDoors={availableGarageDoors}
          groups={groups}
          mode={selectedPerson ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            closeModal();
            setSaved("Person saved.");
            try { await refresh(); directoryPage.refresh(); } catch { setError("Person saved, but the list could not be refreshed. Refresh to see the latest data."); }
          }}
          person={selectedPerson}
          schedules={schedules}
          setPageError={setError}
          vehicles={vehicles}
        />
      ) : null}
    </section>
  );
}
