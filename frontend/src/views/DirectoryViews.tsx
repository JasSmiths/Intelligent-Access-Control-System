import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Camera,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Command,
  ClipboardPaste,
  Construction,
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  File as FileIcon,
  FileImage,
  FileText,
  Gauge,
  GitBranch,
  HardHat,
  Home,
  Key,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  Loader2,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  MoreHorizontal,
  Play,
  PlugZap,
  Plus,
  Paperclip,
  Pencil,
  RefreshCcw,
  RefreshCw,
  Search,
  Send,
  Smile,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Split,
  Sparkles,
  Sun,
  Terminal,
  Ticket,
  Trash2,
  Trophy,
  Type,
  Unlock,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X,
  Zap
} from "lucide-react";

import {
  activeManagedCovers,
  api,
  Badge,
  BadgeTone,
  EmptyState,
  fileToDataUrl,
  Group,
  HomeAssistantDiscovery,
  HomeAssistantManagedCover,
  HomeAssistantMobileAppService,
  initials,
  matches,
  PanelHeader,
  Person,
  Schedule,
  titleCase,
  titleFromEntityId,
  useScheduleDefaultPolicyOptionLabel,
  Vehicle
} from "../shared";

type PersonPronouns = NonNullable<Person["pronouns"]>;
type PersonPronounFormValue = PersonPronouns | "";

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

export type HomeAssistantPersonSuggestion = {
  mobile?: {
    id: string;
    label: string;
    confidence: number;
  };
};

export type DvlaLookupResponse = {
  registration_number: string;
  vehicle: {
    make?: string | null;
    model?: string | null;
    colour?: string | null;
    color?: string | null;
    fuelType?: string | null;
  } & Record<string, unknown>;
  display_vehicle?: {
    make?: string | null;
    model?: string | null;
    colour?: string | null;
    color?: string | null;
    fuelType?: string | null;
  } & Record<string, unknown>;
  normalized_vehicle?: {
    registration_number?: string | null;
    make?: string | null;
    colour?: string | null;
    color?: string | null;
    fuel_type?: string | null;
    mot_status?: string | null;
    mot_expiry?: string | null;
    tax_status?: string | null;
    tax_expiry?: string | null;
  };
};

export const groupCategoryOptions = [
  { value: "family", label: "Family" },
  { value: "friends", label: "Friends" },
  { value: "visitors", label: "Visitors" },
  { value: "contractors", label: "Contractors" }
] as const;

export function GroupsView({
  groups,
  people,
  query,
  refresh
}: {
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedGroup, setSelectedGroup] = React.useState<Group | null>(null);
  const [error, setError] = React.useState("");
  const peopleByGroup = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const person of people) {
      if (person.group_id) counts.set(person.group_id, (counts.get(person.group_id) ?? 0) + 1);
    }
    return counts;
  }, [people]);
  const filtered = groups.filter((group) =>
    matches(group.name, query) ||
    matches(titleCase(group.category), query) ||
    matches(group.subtype ?? "", query)
  );

  const openCreate = () => {
    setSelectedGroup(null);
    setModalOpen(true);
  };

  const openEdit = (group: Group) => {
    setSelectedGroup(group);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedGroup(null);
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Directory</span>
          <h1>Groups</h1>
          <p>Create access groups for family, friends, visitors, and contractors.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> Add Group
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      <div className="card users-card groups-card">
        <PanelHeader title="Group Directory" action={`${filtered.length} groups`} actionKind="select" />
        {filtered.length ? (
          <div className="users-table groups-table">
            {filtered.map((group) => {
              const peopleCount = group.people_count ?? peopleByGroup.get(group.id) ?? 0;
              return (
                <article
                  className="user-row group-row group-row-button"
                  key={group.id}
                  onClick={() => openEdit(group)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      openEdit(group);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <span className={`group-mark ${group.category}`}>
                    <Users size={17} />
                  </span>
                  <div>
                    <strong>{group.name}</strong>
                    <span>{group.subtype || group.description || "General access group"}</span>
                  </div>
                  <Badge tone={groupCategoryTone(group.category)}>{titleCase(group.category)}</Badge>
                  <span className="member-count">{peopleCount} {peopleCount === 1 ? "person" : "people"}</span>
                </article>
              );
            })}
          </div>
        ) : (
          <EmptyState icon={Users} label="No groups match this view" />
        )}
      </div>

      {modalOpen ? (
        <GroupModal
          group={selectedGroup}
          members={selectedGroup ? people.filter((person) => person.group_id === selectedGroup.id) : []}
          mode={selectedGroup ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          setPageError={setError}
        />
      ) : null}
    </section>
  );
}

export function GroupModal({
  group,
  members,
  mode,
  onClose,
  onSaved,
  setPageError
}: {
  group: Group | null;
  members: Person[];
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  setPageError: (message: string) => void;
}) {
  const [form, setForm] = React.useState({
    name: group?.name ?? "",
    category: group?.category ?? "family",
    subtype: group?.subtype ?? "",
    description: group?.description ?? ""
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const update = (field: keyof typeof form, value: string) => setForm((current) => ({ ...current, [field]: value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setPageError("");
    setSubmitting(true);
    try {
      const payload = {
        name: form.name,
        category: form.category,
        subtype: form.subtype || null,
        description: form.description || null
      };
      if (mode === "edit" && group) {
        await api.patch<Group>(`/api/v1/groups/${group.id}`, payload);
      } else {
        await api.post<Group>("/api/v1/groups", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save group";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card group-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Group" : "Add Group"}</h2>
            <p>{mode === "edit" ? "Update group details and review assigned members." : "Define a membership bucket for access schedules and directory profiles."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <label className="field">
          <span>Group name</span>
          <div className="field-control">
            <Users size={17} />
            <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
          </div>
        </label>
        <div className="field-grid">
          <label className="field">
            <span>Category</span>
            <select value={form.category} onChange={(event) => update("category", event.target.value)}>
              {groupCategoryOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Subtype</span>
            <div className="field-control">
              <CircleDot size={17} />
              <input value={form.subtype} onChange={(event) => update("subtype", event.target.value)} placeholder="Gardener, overnight guest..." />
            </div>
          </label>
        </div>
        <label className="field">
          <span>Description</span>
          <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
        </label>
        {mode === "edit" ? (
          <div className="group-members-panel">
            <div className="panel-header">
              <h2>Members</h2>
              <span className="member-count">{members.length} {members.length === 1 ? "person" : "people"}</span>
            </div>
            {members.length ? (
              <div className="group-member-list">
                {members.map((member) => (
                  <div className="group-member-row" key={member.id}>
                    <PersonAvatar person={member} />
                    <div>
                      <strong>{member.display_name}</strong>
                      <span>{member.vehicles.length ? member.vehicles.map((vehicle) => vehicle.registration_number).join(", ") : "No vehicles"}</span>
                    </div>
                    <Badge tone={member.is_active ? "green" : "gray"}>{member.is_active ? "Active" : "Inactive"}</Badge>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state compact">No members assigned</div>
            )}
          </div>
        ) : null}
        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <Plus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Group"}
          </button>
        </div>
      </form>
    </div>
  );
}

export type DirectoryGroupMeta = {
  id: string;
  name: string;
  category: string | null;
};

export type DirectoryGroupSection<T> = DirectoryGroupMeta & {
  items: T[];
};

export type DirectoryGroupBucket<T> = DirectoryGroupSection<T> & {
  order: number;
};

export const unassignedDirectoryGroup: DirectoryGroupMeta = {
  id: "__unassigned__",
  name: "Unassigned",
  category: null
};

export function isResidentsDirectoryGroup(section: DirectoryGroupMeta) {
  return section.category?.trim().toLowerCase() === "family" && section.name.trim().toLowerCase() === "residents";
}

export function directoryGroupDefaultOpen(section: DirectoryGroupMeta) {
  return isResidentsDirectoryGroup(section);
}

export function buildDirectoryGroupIndex(groups: Group[]) {
  const groupById = new Map<string, Group>();
  const groupOrder = new Map<string, number>();
  groups.forEach((group, index) => {
    groupById.set(group.id, group);
    groupOrder.set(group.id, index);
  });
  return { groupById, groupOrder };
}

export function directoryGroupMetaFromGroup(group: Group): DirectoryGroupMeta {
  return {
    id: group.id,
    name: group.name,
    category: group.category
  };
}

export function directoryGroupMetaForPerson(person: Person, groupById: Map<string, Group>): DirectoryGroupMeta {
  if (person.group_id) {
    const group = groupById.get(person.group_id);
    if (group) return directoryGroupMetaFromGroup(group);
    return {
      id: person.group_id,
      name: person.group ?? "Unknown Group",
      category: person.category
    };
  }

  if (person.group) {
    return {
      id: `group-name:${person.group.trim().toLowerCase()}`,
      name: person.group,
      category: person.category
    };
  }

  return unassignedDirectoryGroup;
}

export function directoryGroupOrder(meta: DirectoryGroupMeta, groupOrder: Map<string, number>, groupCount: number) {
  if (isResidentsDirectoryGroup(meta)) return -1;
  const knownOrder = groupOrder.get(meta.id);
  if (knownOrder !== undefined) return knownOrder;
  if (meta.id === unassignedDirectoryGroup.id) return groupCount + 1000;
  return groupCount + 100;
}

export function addToDirectoryBucket<T>(
  buckets: Map<string, DirectoryGroupBucket<T>>,
  meta: DirectoryGroupMeta,
  order: number,
  item: T
) {
  const existing = buckets.get(meta.id);
  if (existing) {
    existing.items.push(item);
    return;
  }

  buckets.set(meta.id, {
    ...meta,
    items: [item],
    order
  });
}

export function directoryBucketsToSections<T>(buckets: Map<string, DirectoryGroupBucket<T>>): DirectoryGroupSection<T>[] {
  return Array.from(buckets.values())
    .filter((bucket) => bucket.items.length)
    .sort((left, right) => left.order - right.order || left.name.localeCompare(right.name))
    .map((bucket) => ({
      id: bucket.id,
      name: bucket.name,
      category: bucket.category,
      items: bucket.items
    }));
}

export function groupPeopleByDirectoryGroup(people: Person[], groups: Group[]): DirectoryGroupSection<Person>[] {
  const { groupById, groupOrder } = buildDirectoryGroupIndex(groups);
  const buckets = new Map<string, DirectoryGroupBucket<Person>>();

  for (const person of people) {
    const meta = directoryGroupMetaForPerson(person, groupById);
    addToDirectoryBucket(buckets, meta, directoryGroupOrder(meta, groupOrder, groups.length), person);
  }

  return directoryBucketsToSections(buckets);
}

export function indexPeopleByVehicleId(people: Person[]) {
  const peopleByVehicleId = new Map<string, Person[]>();
  for (const person of people) {
    for (const vehicle of person.vehicles) {
      const owners = peopleByVehicleId.get(vehicle.id) ?? [];
      owners.push(person);
      peopleByVehicleId.set(vehicle.id, owners);
    }
  }
  return peopleByVehicleId;
}

export function ownerPeopleForVehicle(
  vehicle: Vehicle,
  peopleByVehicleId: Map<string, Person[]>,
  peopleById: Map<string, Person>
) {
  const ownersById = new Map<string, Person>();

  for (const personId of vehicle.person_ids ?? []) {
    const person = peopleById.get(personId);
    if (person) ownersById.set(person.id, person);
  }

  for (const person of peopleByVehicleId.get(vehicle.id) ?? []) {
    ownersById.set(person.id, person);
  }

  if (vehicle.person_id) {
    const person = peopleById.get(vehicle.person_id);
    if (person) ownersById.set(person.id, person);
  }

  return Array.from(ownersById.values()).sort((left, right) => left.display_name.localeCompare(right.display_name));
}

export function vehicleOwnerLabel(
  vehicle: Vehicle,
  peopleByVehicleId: Map<string, Person[]>,
  peopleById: Map<string, Person>
) {
  const owners = ownerPeopleForVehicle(vehicle, peopleByVehicleId, peopleById);
  if (owners.length) return owners.map((person) => person.display_name).join(", ");
  if (vehicle.owners?.length) return vehicle.owners.join(", ");
  return vehicle.owner ?? "Unassigned";
}

export function groupVehiclesByDirectoryGroup(
  vehicles: Vehicle[],
  peopleByVehicleId: Map<string, Person[]>,
  peopleById: Map<string, Person>,
  groups: Group[]
): DirectoryGroupSection<Vehicle>[] {
  const { groupById, groupOrder } = buildDirectoryGroupIndex(groups);
  const buckets = new Map<string, DirectoryGroupBucket<Vehicle>>();

  for (const vehicle of vehicles) {
    const owners = ownerPeopleForVehicle(vehicle, peopleByVehicleId, peopleById);
    const vehicleGroups = new Map<string, DirectoryGroupMeta>();

    for (const owner of owners) {
      const meta = directoryGroupMetaForPerson(owner, groupById);
      vehicleGroups.set(meta.id, meta);
    }

    if (!vehicleGroups.size) {
      vehicleGroups.set(unassignedDirectoryGroup.id, unassignedDirectoryGroup);
    }

    for (const meta of vehicleGroups.values()) {
      addToDirectoryBucket(buckets, meta, directoryGroupOrder(meta, groupOrder, groups.length), vehicle);
    }
  }

  return directoryBucketsToSections(buckets);
}

export function useDirectoryGroupOpenState<T>(sections: DirectoryGroupSection<T>[]) {
  const [openGroups, setOpenGroups] = React.useState<Record<string, boolean>>({});
  const defaultOpenById = React.useMemo(
    () => new Map(sections.map((section) => [section.id, directoryGroupDefaultOpen(section)])),
    [sections]
  );

  React.useEffect(() => {
    setOpenGroups((current) => {
      let changed = false;
      const next = { ...current };
      for (const section of sections) {
        if (next[section.id] === undefined) {
          next[section.id] = directoryGroupDefaultOpen(section);
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [sections]);

  const toggleGroup = React.useCallback((sectionId: string) => {
    setOpenGroups((current) => ({
      ...current,
      [sectionId]: !(current[sectionId] ?? defaultOpenById.get(sectionId) ?? false)
    }));
  }, [defaultOpenById]);

  return { openGroups, toggleGroup };
}

export function DirectoryGroupAccordion<T>({
  section,
  singularLabel,
  pluralLabel,
  expanded,
  onToggle,
  children
}: {
  section: DirectoryGroupSection<T>;
  singularLabel: string;
  pluralLabel: string;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const bodyId = React.useId();
  const count = section.items.length;
  return (
    <article className={expanded ? "directory-group expanded" : "directory-group"}>
      <button
        aria-controls={bodyId}
        aria-expanded={expanded}
        className="directory-group-header"
        onClick={onToggle}
        type="button"
      >
        <span className="directory-group-chevron" aria-hidden="true">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </span>
        <span className="directory-group-title">
          <strong>{section.name}</strong>
          {section.category ? <small>{titleCase(section.category)}</small> : null}
        </span>
        <span className="directory-group-count">{count} {count === 1 ? singularLabel : pluralLabel}</span>
      </button>
      {expanded ? (
        <div className="directory-group-body" id={bodyId}>
          {children}
        </div>
      ) : null}
    </article>
  );
}

export function PeopleView({
  garageDoors,
  groups,
  people,
  query,
  refresh,
  schedules,
  vehicles
}: {
  garageDoors: HomeAssistantManagedCover[];
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedPerson, setSelectedPerson] = React.useState<Person | null>(null);
  const [error, setError] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const availableGarageDoors = React.useMemo(() => activeManagedCovers(garageDoors), [garageDoors]);
  const garageDoorNameMap = React.useMemo(() => new Map(garageDoors.map((door) => [door.entity_id, door.name || door.entity_id])), [garageDoors]);
  const filtered = React.useMemo(() => people.filter((item) =>
    matches(item.display_name, query) ||
    matches(item.group ?? "", query) ||
    item.vehicles.some((vehicle) => matches(vehicle.registration_number, query)) ||
    (item.garage_door_entity_ids ?? []).some((entityId) => matches(garageDoorNameMap.get(entityId) ?? entityId, query)) ||
    matches(item.home_assistant_mobile_app_notify_service ?? "", query)
  ), [garageDoorNameMap, people, query]);
  const groupedPeople = React.useMemo(() => groupPeopleByDirectoryGroup(filtered, groups), [filtered, groups]);
  const { openGroups: openPeopleGroups, toggleGroup: togglePeopleGroup } = useDirectoryGroupOpenState(groupedPeople);
  const assignedVehicleIds = React.useMemo(() => new Set(people.flatMap((person) => person.vehicles.map((vehicle) => vehicle.id))), [people]);

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
                    <article
                      className="user-row person-row person-row-button"
                      key={person.id}
                      onClick={() => openEdit(person)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          openEdit(person);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <PersonAvatar person={person} />
                      <div>
                        <strong>{person.display_name}</strong>
                        <span>{person.category ? titleCase(person.category) : "No category"}{person.group ? ` • ${person.group}` : ""}</span>
                      </div>
                      <Badge tone={person.is_active ? "green" : "gray"}>{person.is_active ? "Active" : "Inactive"}</Badge>
                      <div className="vehicle-chip-list">
                        {person.schedule ? <span className="vehicle-chip schedule-chip">{person.schedule}</span> : null}
                        {person.vehicles.length ? person.vehicles.map((vehicle) => (
                          <span className="vehicle-chip" key={vehicle.id}>{vehicle.registration_number}</span>
                        )) : <span className="muted-value">No vehicles</span>}
                        {(person.garage_door_entity_ids ?? []).map((entityId) => (
                          <span className="vehicle-chip garage-chip" key={entityId}>{garageDoorNameMap.get(entityId) ?? entityId}</span>
                        ))}
                        {person.home_assistant_mobile_app_notify_service ? <span className="vehicle-chip ha-chip">HA mobile</span> : null}
                      </div>
                    </article>
                  ))}
                </div>
              </DirectoryGroupAccordion>
            ))}
          </div>
        ) : (
          <EmptyState icon={Users} label="No people match this view" />
        )}
      </div>

      {modalOpen ? (
        <PersonModal
          assignedVehicleIds={assignedVehicleIds}
          defaultPolicyOptionLabel={defaultPolicyOptionLabel}
          garageDoors={availableGarageDoors}
          groups={groups}
          mode={selectedPerson ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
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

export function PersonModal({
  assignedVehicleIds,
  defaultPolicyOptionLabel,
  garageDoors,
  groups,
  mode,
  onClose,
  onSaved,
  person,
  schedules,
  setPageError,
  vehicles
}: {
  assignedVehicleIds: Set<string>;
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
    notes: person?.notes ?? "",
    is_active: person?.is_active ?? true
  });
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

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => setForm((current) => ({ ...current, [field]: value }));

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

  const sendHomeAssistantMobileTest = async () => {
    if (!form.home_assistant_mobile_app_notify_service) {
      setHaTestFeedback({ tone: "error", text: "Select a mobile app notification service first." });
      return;
    }
    const personName = `${form.first_name} ${form.last_name}`.trim() || person?.display_name || "this person";
    setSendingHaTest(true);
    setHaTestFeedback({ tone: "info", text: "Sending Home Assistant test notification." });
    try {
      await api.post("/api/v1/integrations/home-assistant/mobile-notifications/test", {
        service_name: form.home_assistant_mobile_app_notify_service,
        person_name: personName
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
    setError("");
    setPageError("");
    setSubmitting(true);
    const payload = {
      first_name: form.first_name,
      last_name: form.last_name,
      pronouns: form.pronouns || null,
      profile_photo_data_url: form.profile_photo_data_url || null,
      group_id: form.group_id || null,
      schedule_id: form.schedule_id || null,
      vehicle_ids: form.vehicle_ids,
      garage_door_entity_ids: form.garage_door_entity_ids,
      home_assistant_mobile_app_notify_service: form.home_assistant_mobile_app_notify_service || null,
      notes: form.notes || null,
      is_active: form.is_active
    };
    try {
      if (mode === "edit" && person) {
        await api.patch<Person>(`/api/v1/people/${person.id}`, payload);
      } else {
        await api.post<Person>("/api/v1/people", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save person";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const previewPerson: Person = {
    id: "preview",
    first_name: form.first_name,
    last_name: form.last_name,
    display_name: `${form.first_name} ${form.last_name}`.trim() || "New person",
    pronouns: form.pronouns || null,
    profile_photo_data_url: form.profile_photo_data_url || null,
    group_id: form.group_id || null,
    group: groups.find((group) => group.id === form.group_id)?.name ?? null,
    category: groups.find((group) => group.id === form.group_id)?.category ?? null,
    schedule_id: form.schedule_id || null,
    schedule: schedules.find((schedule) => schedule.id === form.schedule_id)?.name ?? null,
    is_active: form.is_active,
    notes: form.notes || null,
    garage_door_entity_ids: form.garage_door_entity_ids,
    home_assistant_mobile_app_notify_service: form.home_assistant_mobile_app_notify_service || null,
    vehicles: []
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card person-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Person" : "Add Person"}</h2>
            <p>{mode === "edit" ? "Update the profile, group, and vehicle assignments." : "Create a directory profile and assign registered vehicles."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="profile-upload-row">
          <PersonAvatar person={previewPerson} size="large" />
          <label className="upload-button">
            <Camera size={16} />
            <span>{form.profile_photo_data_url ? "Change photo" : "Upload profile picture"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {form.profile_photo_data_url ? (
            <button className="secondary-button" onClick={() => update("profile_photo_data_url", "")} type="button">
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
            <span>Pronouns</span>
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
        <label className="field">
          <span>Operational notes</span>
          <textarea value={form.notes} onChange={(event) => update("notes", event.target.value)} rows={3} />
        </label>
        <div className="field">
          <span>Vehicles</span>
          <div className="vehicle-picker">
            {vehicles.length ? vehicles.map((vehicle) => {
              const selected = form.vehicle_ids.includes(vehicle.id);
              const assigned = assignedVehicleIds.has(vehicle.id) && !selected;
              return (
                <label className={selected ? "vehicle-option selected" : "vehicle-option"} key={vehicle.id}>
                  <input checked={selected} onChange={() => toggleVehicle(vehicle.id)} type="checkbox" />
                  <span>
                    <strong>{vehicle.registration_number}</strong>
                    <small>{vehicle.description ?? ([vehicle.make, vehicle.model].filter(Boolean).join(" ") || "Registered vehicle")}</small>
                  </span>
                  {selected ? <Badge tone="blue">Selected</Badge> : assigned ? <Badge tone="amber">Assigned</Badge> : <Badge tone="gray">Available</Badge>}
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
        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <UserPlus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Person"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function VehiclesView({
  groups,
  people,
  query,
  refresh,
  schedules,
  vehicles
}: {
  groups: Group[];
  people: Person[];
  query: string;
  refresh: () => Promise<void>;
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedVehicle, setSelectedVehicle] = React.useState<Vehicle | null>(null);
  const [error, setError] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const peopleById = React.useMemo(() => new Map(people.map((person) => [person.id, person])), [people]);
  const peopleByVehicleId = React.useMemo(() => indexPeopleByVehicleId(people), [people]);
  const filtered = React.useMemo(() => vehicles.filter((item) => {
    const owners = ownerPeopleForVehicle(item, peopleByVehicleId, peopleById);
    return (
      matches(item.registration_number, query) ||
      matches(item.owner ?? "", query) ||
      owners.some((person) =>
        matches(person.display_name, query) ||
        matches(person.group ?? "", query) ||
        matches(person.category ?? "", query)
      ) ||
      matches(item.make ?? "", query) ||
      matches(item.model ?? "", query) ||
      matches(item.color ?? "", query)
    );
  }), [peopleById, peopleByVehicleId, query, vehicles]);
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
      await api.delete(`/api/v1/vehicles/${vehicle.id}`);
      await refresh();
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

      {error ? <div className="auth-error inline-error">{error}</div> : null}

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
                    <article
                      className="user-row vehicle-row vehicle-row-button"
                      key={vehicle.id}
                      onClick={() => openEdit(vehicle)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          openEdit(vehicle);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <VehiclePhoto vehicle={vehicle} />
                      <div className="vehicle-row-main">
                        <strong>{vehicle.registration_number}</strong>
                        <span>{vehicleTitle(vehicle)}</span>
                      </div>
                      <span className="vehicle-owner">{vehicleOwnerLabel(vehicle, peopleByVehicleId, peopleById)}</span>
                      <span className={vehicle.schedule ? "vehicle-chip schedule-chip" : "vehicle-chip inherit-chip"}>
                        {vehicle.schedule ?? "Inherit"}
                      </span>
                      <Badge tone={vehicle.is_active !== false ? "green" : "gray"}>{vehicle.is_active !== false ? "Active" : "Inactive"}</Badge>
                      <button
                        className="icon-button danger"
                        onClick={(event) => {
                          event.stopPropagation();
                          deleteVehicle(vehicle).catch(() => undefined);
                        }}
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
          <EmptyState icon={Car} label="No vehicles match this view" />
        )}
      </div>

      {modalOpen ? (
        <VehicleModal
          defaultPolicyOptionLabel={defaultPolicyOptionLabel}
          groups={groups}
          mode={selectedVehicle ? "edit" : "create"}
            onClose={closeModal}
            onSaved={async () => {
              await refresh();
              closeModal();
            }}
            people={people}
            refreshVehicles={refresh}
            schedules={schedules}
            setPageError={setError}
            vehicle={selectedVehicle}
          />
      ) : null}
    </section>
  );
}

export function VehiclePeoplePicker({
  groups,
  onToggle,
  people,
  selectedPersonIds
}: {
  groups: Group[];
  onToggle: (personId: string) => void;
  people: Person[];
  selectedPersonIds: string[];
}) {
  const selectedPersonIdSet = React.useMemo(() => new Set(selectedPersonIds), [selectedPersonIds]);
  const selectedPeople = React.useMemo(
    () => people
      .filter((person) => selectedPersonIdSet.has(person.id))
      .sort((left, right) => left.display_name.localeCompare(right.display_name)),
    [people, selectedPersonIdSet]
  );
  const groupSections = React.useMemo(() => {
    const allPeople = [...people].sort((left, right) => left.display_name.localeCompare(right.display_name));
    return [
      {
        id: "all",
        title: "All People",
        description: "Every directory person",
        items: allPeople
      },
      ...groupPeopleByDirectoryGroup(people, groups).map((section) => ({
        id: section.id,
        title: section.name,
        description: section.category ? titleCase(section.category) : "No group",
        items: section.items
      }))
    ];
  }, [groups, people]);
  const [activeGroupId, setActiveGroupId] = React.useState("all");
  const activeGroup = groupSections.find((section) => section.id === activeGroupId) ?? groupSections[0];

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
            {activeGroup.items.length ? activeGroup.items.map((person) => {
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
            }) : <div className="empty-state compact">No people in this group</div>}
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
  onClose,
  onSaved,
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
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [complianceRefreshing, setComplianceRefreshing] = React.useState(false);
  const [dvlaLookup, setDvlaLookup] = React.useState<{ status: "idle" | "loading" | "found" | "error"; message: string }>({
    status: "idle",
    message: ""
  });
  const lookupRequestRef = React.useRef(0);
  const lastLookupRegistrationRef = React.useRef("");
  const initialRegistrationRef = React.useRef(vehicle?.registration_number ?? "");

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => setForm((current) => ({ ...current, [field]: value }));

  const toggleAssignedPerson = (personId: string) => {
    update(
      "person_ids",
      form.person_ids.includes(personId)
        ? form.person_ids.filter((id) => id !== personId)
        : [...form.person_ids, personId]
    );
  };

  React.useEffect(() => {
    const registrationNumber = normalizePlateInput(form.registration_number);
    const initialRegistration = normalizePlateInput(initialRegistrationRef.current);
    if (registrationNumber.length < 2 || (mode === "edit" && registrationNumber === initialRegistration)) {
      setDvlaLookup({ status: "idle", message: "" });
      return;
    }
    if (registrationNumber === lastLookupRegistrationRef.current) return;

    const requestId = lookupRequestRef.current + 1;
    lookupRequestRef.current = requestId;
    setDvlaLookup({ status: "loading", message: "Looking up DVLA vehicle details" });

    const timer = window.setTimeout(async () => {
      try {
          const result = await api.post<DvlaLookupResponse>("/api/v1/integrations/dvla/lookup", {
            registration_number: registrationNumber
          });
          if (lookupRequestRef.current !== requestId) return;
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
        if (lookupRequestRef.current !== requestId) return;
        const message = lookupError instanceof Error ? lookupError.message : "DVLA lookup failed";
        if (message.toLowerCase().includes("api key is not configured")) {
          lastLookupRegistrationRef.current = registrationNumber;
          setDvlaLookup({ status: "idle", message: "" });
          return;
        }
        setDvlaLookup({ status: "error", message });
      }
    }, 850);

    return () => window.clearTimeout(timer);
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
      update("vehicle_photo_data_url", await fileToDataUrl(file));
    };

    const refreshCompliance = async () => {
      if (mode !== "edit" || !vehicle) return;
      setError("");
      setPageError("");
      setComplianceRefreshing(true);
      try {
        const refreshed = await api.post<Vehicle>(`/api/v1/vehicles/${vehicle.id}/dvla-refresh`);
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
    setError("");
    setPageError("");
    setSubmitting(true);
      const payload = {
        registration_number: form.registration_number,
        vehicle_photo_data_url: form.vehicle_photo_data_url || null,
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
    try {
      if (mode === "edit" && vehicle) {
        await api.patch<Vehicle>(`/api/v1/vehicles/${vehicle.id}`, payload);
      } else {
        await api.post<Vehicle>("/api/v1/vehicles", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save vehicle";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const previewVehicle: Vehicle = {
    id: vehicle?.id ?? "preview",
    registration_number: form.registration_number || "NEW",
    vehicle_photo_data_url: form.vehicle_photo_data_url || null,
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
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card vehicle-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Vehicle" : "Add Vehicle"}</h2>
            <p>{mode === "edit" ? "Update vehicle details and assignments." : "Register a vehicle and assign it to people."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="vehicle-upload-row">
          <VehiclePhoto vehicle={previewVehicle} size="large" />
          <label className="upload-button">
            <Camera size={16} />
            <span>{form.vehicle_photo_data_url ? "Change photo" : "Upload vehicle photo"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {form.vehicle_photo_data_url ? (
            <button className="secondary-button" onClick={() => update("vehicle_photo_data_url", "")} type="button">
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
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <Plus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Vehicle"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function motComplianceTone(status: string | null | undefined): BadgeTone {
  const normalized = String(status || "").trim().toLowerCase().replace(/_/g, " ");
  if (!normalized) return "gray";
  return normalized === "valid" || normalized === "not required" ? "green" : "red";
}

export function taxComplianceTone(status: string | null | undefined): BadgeTone {
  const normalized = String(status || "").trim().toLowerCase();
  if (!normalized) return "gray";
  if (normalized === "taxed") return "green";
  if (normalized === "sorn") return "gray";
  return "red";
}

export function vehicleComplianceExpiryLabel(value: string | null | undefined) {
  return value ? `Expires ${formatDateOnly(value)}` : "Expiry unavailable";
}

export function vehicleLastDvlaCheckLabel(value: string | null | undefined) {
  if (!value) return "Not checked yet";
  return dateOnlyKey(value) === localDateKey() ? "Last checked with DVLA: Today" : `Last checked with DVLA: ${formatDateOnly(value)}`;
}

export function MobileAppNotifySelectField({
  label,
  value,
  services,
  onChange
}: {
  label: string;
  value: string;
  services: HomeAssistantMobileAppService[];
  onChange: (value: string) => void;
}) {
  const hasCurrentValue = value && !services.some((service) => service.service_id === value);
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select mobile app service</option>
        {hasCurrentValue ? <option value={value}>{value}</option> : null}
        {services.map((service) => (
          <option key={service.service_id} value={service.service_id}>
            {service.name ? `${service.name} - ${service.service_id}` : service.service_id}
          </option>
        ))}
      </select>
    </label>
  );
}

export function suggestHomeAssistantPersonIntegrations(
  firstName: string,
  lastName: string,
  discovery: HomeAssistantDiscovery
): HomeAssistantPersonSuggestion {
  const displayName = `${firstName} ${lastName}`.trim();
  const mobile = bestHomeAssistantMatch(
    displayName,
    discovery.mobile_app_notification_services.map((service) => ({
      id: service.service_id,
      label: service.name ? `${service.name} ${service.service_id}` : service.service_id
    })),
    0.45
  );
  return {
    mobile: mobile ? { id: mobile.id, label: titleFromEntityId(mobile.id), confidence: mobile.confidence } : undefined
  };
}

export function bestHomeAssistantMatch(
  personName: string,
  candidates: Array<{ id: string; label: string }>,
  threshold: number
): { id: string; confidence: number } | null {
  const personTokens = homeAssistantNameTokens(personName);
  if (!personTokens.size) return null;
  let best: { id: string; confidence: number } | null = null;
  for (const candidate of candidates) {
    const candidateTokens = homeAssistantNameTokens(`${candidate.id} ${candidate.label}`);
    if (!candidateTokens.size) continue;
    const overlap = [...personTokens].filter((token) => candidateTokens.has(token)).length / personTokens.size;
    const personCompact = [...personTokens].sort().join("");
    const candidateCompact = [...candidateTokens].sort().join("");
    const substringScore = candidateCompact.includes(personCompact) || [...personTokens].some((token) => candidateCompact.includes(token))
      ? 0.7
      : 0;
    const confidence = Math.max(overlap, substringScore);
    if (!best || confidence > best.confidence) {
      best = { id: candidate.id, confidence };
    }
  }
  return best && best.confidence >= threshold ? best : null;
}

export function homeAssistantNameTokens(value: string) {
  return new Set(
    value
      .toLowerCase()
      .replace(/notify\.mobile_app_/g, " ")
      .split(/[^a-z0-9]+/)
      .filter(Boolean)
  );
}

export function groupCategoryTone(category: string): BadgeTone {
  if (category === "family") return "green";
  if (category === "friends") return "blue";
  if (category === "visitors") return "amber";
  if (category === "contractors") return "gray";
  return "gray";
}

export function vehicleTitle(vehicle: Vehicle) {
  return vehicle.description || [vehicle.color, vehicle.make, vehicle.model].filter(Boolean).join(" ") || "Vehicle details pending";
}

export function normalizePlateInput(value: string) {
  return value.replace(/[^a-z0-9]/gi, "").toUpperCase();
}

export function personInitials(person: Pick<Person, "first_name" | "last_name" | "display_name">) {
  const first = person.first_name?.trim()[0] ?? "";
  const last = person.last_name?.trim()[0] ?? "";
  return (first + last || initials(person.display_name)).toUpperCase();
}

export function PersonAvatar({ person, size = "normal" }: { person: Person; size?: "normal" | "large" }) {
  return (
    <span className={size === "large" ? "profile-photo large" : "profile-photo"} aria-label={person.display_name}>
      {person.profile_photo_data_url ? <img alt="" src={person.profile_photo_data_url} /> : personInitials(person)}
    </span>
  );
}

export function VehiclePhoto({ vehicle, size = "normal" }: { vehicle: Vehicle; size?: "normal" | "large" }) {
  return (
    <span className={size === "large" ? "vehicle-photo large" : "vehicle-photo"} aria-label={vehicle.registration_number}>
      {vehicle.vehicle_photo_data_url ? <img alt="" src={vehicle.vehicle_photo_data_url} /> : <Car size={size === "large" ? 24 : 18} />}
    </span>
  );
}

export function formatDateOnly(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(dateOnlyToDate(value));
}

export function dateOnlyKey(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;
  return localDateKey(new Date(value));
}

export function localDateKey(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function dateOnlyToDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return new Date(value);
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}
