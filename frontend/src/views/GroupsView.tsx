import { useModalFocus } from "../ui/useModalFocus";
import { useModalClose } from "../ui/useModalClose";
import { useEditorDismiss } from "../ui/useEditorDismiss";
import { Check, CircleDot, Plus, Users, X } from "lucide-react";
import React from "react";

import { api, createActionConfirmation } from "../api/client";
import { matches, titleCase } from "../lib/format";
import { Badge, EmptyState, PanelHeader } from "../ui/primitives";
import type { Group, Person } from "../api/types";

import { groupCategoryTone } from "../features/directory/model";
import { PersonAvatar } from "../features/directory/components";

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
  const [saved, setSaved] = React.useState("");
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
      {saved ? <div className="success-note" role="status">{saved}</div> : null}

      <div className="card users-card groups-card">
        <PanelHeader title="Group Directory" action={`${filtered.length} groups`} actionKind="select" />
        {filtered.length ? (
          <div className="users-table groups-table">
            {filtered.map((group) => {
              const peopleCount = group.people_count ?? peopleByGroup.get(group.id) ?? 0;
              return (
                <button
                  className="user-row group-row directory-row-button"
                  key={group.id}
                  onClick={() => openEdit(group)}
                  aria-label={`Edit group ${group.name}`}
                  type="button"
                >
                  <span className={`group-mark ${group.category}`}>
                    <Users size={17} />
                  </span>
                  <span className="directory-row-copy">
                    <strong>{group.name}</strong>
                    <span>{group.subtype || group.description || "General access group"}</span>
                  </span>
                  <Badge tone={groupCategoryTone(group.category)}>{titleCase(group.category)}</Badge>
                  <span className="member-count">{peopleCount} {peopleCount === 1 ? "person" : "people"}</span>
                </button>
              );
            })}
          </div>
        ) : (
          <EmptyState icon={Users} label="No groups match this view" description={query ? "Try another group name or category in search." : "Organise people into groups to keep access policies easier to manage."} action={!query ? <button className="secondary-button" onClick={openCreate} type="button"><Plus size={15} /> Create group</button> : undefined} />
        )}
      </div>

      {modalOpen ? (
        <GroupModal
          group={selectedGroup}
          members={selectedGroup ? people.filter((person) => person.group_id === selectedGroup.id) : []}
          mode={selectedGroup ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            closeModal();
            setSaved("Group saved.");
            try { await refresh(); } catch { setError("Group saved, but the list could not be refreshed. Refresh to see the latest data."); }
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
  onClose: finishClose,
  onSaved: finishSaved,
  setPageError
}: {
  group: Group | null;
  members: Person[];
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  setPageError: (message: string) => void;
}) {
  const modalRef = React.useRef<HTMLFormElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const onSaved = useModalClose(modalRef, finishSaved);
  const [form, setForm] = React.useState({
    name: group?.name ?? "",
    category: group?.category ?? "family",
    subtype: group?.subtype ?? "",
    description: group?.description ?? ""
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const submittingRef = React.useRef(false);
  const originalForm = React.useRef(JSON.stringify(form));
  const requestClose = useEditorDismiss(onClose, JSON.stringify(form) !== originalForm.current, submitting, "group changes");
  useModalFocus(modalRef, true, requestClose);

  const update = (field: keyof typeof form, value: string) => setForm((current) => ({ ...current, [field]: value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
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
        const confirmation = await createActionConfirmation("group.update", { ...payload, group_id: group.id }, {
          target_entity: "Group",
          target_id: group.id,
          target_label: payload.name,
          reason: "Update directory group"
        });
        await api.patch<Group>(`/api/v1/groups/${group.id}`, {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      } else {
        const confirmation = await createActionConfirmation("group.create", payload, {
          target_entity: "Group",
          target_label: payload.name,
          reason: "Create directory group"
        });
        await api.post<Group>("/api/v1/groups", {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save group";
      setError(message);
      setPageError(message);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <form ref={modalRef} role="dialog" aria-modal="true" aria-label="Group" className="modal-card group-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Group" : "Add Group"}</h2>
            <p>{mode === "edit" ? "Update group details and review assigned members." : "Define a membership bucket for access schedules and directory profiles."}</p>
          </div>
          <button className="icon-button" onClick={requestClose} type="button" aria-label="Close">
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
          <button className="secondary-button" onClick={requestClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {mode === "edit" ? <Check size={16} /> : <Plus size={16} />}
            {submitting ? "Saving..." : mode === "edit" ? "Save Changes" : "Save Group"}
          </button>
        </div>
      </form>
    </div>
  );
}
