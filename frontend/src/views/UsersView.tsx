import { useModalFocus } from "../ui/useModalFocus";
import { useModalClose } from "../ui/useModalClose";
import { useEditorDismiss } from "../ui/useEditorDismiss";
import { Camera, Key, MessageCircle, Smartphone, Trash2, UserPlus, UserRound, X } from "lucide-react";
import React from "react";
import { listPeople, lookupPeople } from "../api/directory";
import { useDirectoryOptions } from "../features/directory/reads";
import { DirectoryPagination } from "../features/directory/DirectoryPagination";

import { api, createActionConfirmation } from "../api/client";
import { displayUserName, formatDate } from "../lib/format";
import { fileToDataUrl, mediaSource, UserAvatar } from "../lib/media";
import { Badge, PanelHeader } from "../ui/primitives";
import type { Person, UserAccount, UserRole } from "../api/types";

export function UsersView({
  currentUser,
  onCurrentUserUpdated,
  refreshToken
}: {
  currentUser: UserAccount;
  onCurrentUserUpdated: (user: UserAccount) => void;
  refreshToken: number;
}) {
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [people, setPeople] = React.useState<Person[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [modal, setModal] = React.useState<"create" | "edit" | null>(null);
  const [selectedUser, setSelectedUser] = React.useState<UserAccount | null>(null);
  const [temporaryPassword, setTemporaryPassword] = React.useState<string | null>(null);
  const isAdmin = currentUser.role === "admin";
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const loadUsers = React.useCallback(async () => {
    setError("");
    try {
      const [nextUsers, nextPeople] = await Promise.all([
        api.get<UserAccount[]>("/api/v1/users"),
        listPeople()
      ]);
      setUsers(nextUsers);
      const linked = await lookupPeople(nextUsers.flatMap((item) => item.person_id ? [item.person_id] : []));
      setPeople([...new Map([...nextPeople.items, ...linked].map((item) => [item.id, item])).values()]);
    } catch (userError) {
      setError(userError instanceof Error ? userError.message : "Unable to load users");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    loadUsers().catch(() => undefined);
  }, [loadUsers]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadUsers().catch(() => undefined);
  }, [loadUsers, refreshToken]);

  const openCreate = () => {
    setTemporaryPassword(null);
    setSelectedUser(null);
    setModal("create");
  };

  const openEdit = (user: UserAccount) => {
    setTemporaryPassword(null);
    setSelectedUser(user);
    setModal("edit");
  };

  const closeModal = (savedUser?: UserAccount) => {
    setModal(null);
    setSelectedUser(savedUser ?? null);
  };
  const [pendingUserIds, setPendingUserIds] = React.useState<Set<string>>(() => new Set());
  const pendingUserIdsRef = React.useRef(new Set<string>());
  const [copyFeedback, setCopyFeedback] = React.useState("");
  const beginUserAction = (id: string) => {
    if (pendingUserIdsRef.current.has(id)) return false;
    pendingUserIdsRef.current.add(id);
    setPendingUserIds(new Set(pendingUserIdsRef.current));
    return true;
  };
  const endUserAction = (id: string) => {
    pendingUserIdsRef.current.delete(id);
    setPendingUserIds(new Set(pendingUserIdsRef.current));
  };

  const deleteUser = async (user: UserAccount) => {
    if (!beginUserAction(user.id)) return;
    try {
    if (!window.confirm(`Delete ${displayUserName(user)}?`)) return;
    setError("");
    try {
      const confirmation = await createActionConfirmation("user.delete", { user_id: user.id }, {
        target_entity: "User",
        target_id: user.id,
        target_label: displayUserName(user),
        reason: "Delete user"
      });
      await api.delete(`/api/v1/users/${user.id}`, { confirmation_token: confirmation.confirmation_token });
      setUsers((current) => current.filter((item) => item.id !== user.id));
      await loadUsers();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete user");
    }
    } finally { endUserAction(user.id); }
  };

  const toggleActive = async (user: UserAccount) => {
    if (!beginUserAction(user.id)) return;
    setError("");
    try {
      const payload = { is_active: !user.is_active };
      const confirmation = await createActionConfirmation("user.update", { user_id: user.id, ...payload }, {
        target_entity: "User",
        target_id: user.id,
        target_label: displayUserName(user),
        reason: payload.is_active ? "Activate user" : "Deactivate user"
      });
      const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, { ...payload, confirmation_token: confirmation.confirmation_token });
      if (savedUser.id === currentUser.id) {
        onCurrentUserUpdated(savedUser);
      }
      setUsers((current) => current.map((item) => item.id === savedUser.id ? savedUser : item));
      await loadUsers();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Unable to update user");
    } finally { endUserAction(user.id); }
  };

  const resetPassword = async (user: UserAccount) => {
    if (!beginUserAction(user.id)) return;
    setError("");
    try {
      const confirmationPayload = { user_id: user.id, generate_password: true };
      const confirmation = await createActionConfirmation("user.reset_password", confirmationPayload, {
        target_entity: "User",
        target_id: user.id,
        target_label: displayUserName(user),
        reason: "Reset user password"
      });
      const result = await api.post<{ temporary_password: string }>(`/api/v1/users/${user.id}/reset-password`, {
        generate_password: true,
        confirmation_token: confirmation.confirmation_token
      });
      setSelectedUser(user);
      setTemporaryPassword(result.temporary_password);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Unable to reset password");
    } finally { endUserAction(user.id); }
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Settings</span>
          <h1>Users</h1>
          <p>Manage dashboard access for family members.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <UserPlus size={17} /> Add User
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {temporaryPassword ? (
        <div className="temporary-password-card card">
          <div>
            <strong>Temporary password for {selectedUser ? displayUserName(selectedUser) : "user"}</strong>
            <span>{temporaryPassword}</span>
          </div>
          <button className="secondary-button" onClick={async () => { try { if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable"); await navigator.clipboard.writeText(temporaryPassword); setCopyFeedback("Copied temporary password."); } catch { setCopyFeedback("Could not copy. Select the password above to copy it manually."); } }} type="button">
            Copy
          </button>
          {copyFeedback ? <span role="status">{copyFeedback}</span> : null}
        </div>
      ) : null}

      <div className="card users-card">
        <PanelHeader title="User Roster" action={`${users.length} accounts`} actionKind="select" />
        {loading ? (
          <div className="empty-state">Loading users</div>
        ) : (
          <div className="users-table">
            {users.map((user) => (
              <article className="user-row" key={user.id}>
                <UserAvatar user={user} />
                <div>
                  <strong>{displayUserName(user)}</strong>
                  <span>
                    @{user.username}{user.email ? ` • ${user.email}` : ""}
                    {user.mobile_phone_number ? ` • ${user.mobile_phone_number}` : ""}
                    {user.person_id ? ` • linked to ${people.find((person) => person.id === user.person_id)?.display_name ?? "directory person"}` : ""}
                  </span>
                </div>
                <Badge tone={user.role === "admin" ? "blue" : "gray"}>{user.role === "admin" ? "Admin" : "Standard"}</Badge>
                <Badge tone={user.is_active ? "green" : "amber"}>{user.is_active ? "Active" : "Inactive"}</Badge>
                <time>{user.last_login_at ? formatDate(user.last_login_at) : "Never signed in"}</time>
                {isAdmin ? (
                  <div className="user-actions">
                    <button className="secondary-button" disabled={pendingUserIds.has(user.id)} onClick={() => openEdit(user)} type="button">Edit</button>
                    <button className="secondary-button" disabled={pendingUserIds.has(user.id)} onClick={() => resetPassword(user)} type="button">{pendingUserIds.has(user.id) ? "Working…" : "Reset"}</button>
                    <button className="secondary-button" disabled={pendingUserIds.has(user.id)} onClick={() => toggleActive(user)} type="button">{user.is_active ? "Deactivate" : "Activate"}</button>
                    <button className="icon-button danger" disabled={pendingUserIds.has(user.id)} onClick={() => deleteUser(user)} type="button" aria-label={`Delete ${displayUserName(user)}`}>
                      <Trash2 size={16} />
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>

      {modal ? (
        <UserModal
          mode={modal}
          people={people}
          user={selectedUser}
          onClose={closeModal}
          onSaved={async (password, savedUser) => {
            setTemporaryPassword(password);
            setSelectedUser(savedUser ?? null);
            if (savedUser?.id === currentUser.id) {
              onCurrentUserUpdated(savedUser);
            }
            try { await loadUsers(); } catch { setError("User saved, but the list could not be refreshed. Refresh to see the latest data."); }
          }}
        />
      ) : null}
    </section>
  );
}

function UserModal({
  mode,
  people,
  user,
  onClose: finishClose,
  onSaved
}: {
  mode: "create" | "edit";
  people: Person[];
  user: UserAccount | null;
  onClose: (savedUser?: UserAccount) => void;
  onSaved: (temporaryPassword: string | null, savedUser?: UserAccount) => Promise<void>;
}) {
  const modalRef = React.useRef<HTMLFormElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const [form, setForm] = React.useState({
    username: user?.username ?? "",
    first_name: user?.first_name ?? "",
    last_name: user?.last_name ?? "",
    email: user?.email ?? "",
    mobile_phone_number: user?.mobile_phone_number ?? "",
    profile_photo_data_url: user?.profile_photo_data_url ?? "",
    person_id: user?.person_id ?? "",
    role: user?.role ?? "standard",
    is_active: user?.is_active ?? true,
    temporary_password: "",
    generate_password: mode === "create"
  });
  const existingProfilePhotoSource = mediaSource(user?.profile_photo_url, user?.profile_photo_data_url);
  const personOptions = useDirectoryOptions("people", people, form.person_id ? [form.person_id] : []);
  const [profilePhotoChanged, setProfilePhotoChanged] = React.useState(false);
  const profilePhotoPreview = form.profile_photo_data_url || (!profilePhotoChanged ? existingProfilePhotoSource : "");
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const initialForm = React.useRef(JSON.stringify(form));
  const requestClose = useEditorDismiss(onClose, JSON.stringify(form) !== initialForm.current, submitting, "user changes");
  useModalFocus(modalRef, true, requestClose);

  const update = (field: keyof typeof form, value: string | boolean) => setForm((current) => ({ ...current, [field]: value }));

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

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "create") {
        const payload: Record<string, unknown> = {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          mobile_phone_number: form.mobile_phone_number || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active,
          temporary_password: form.generate_password ? null : form.temporary_password,
          generate_password: form.generate_password
        };
        payload.profile_photo_data_url = form.profile_photo_data_url || null;
        const confirmationPayload = { ...payload };
        delete confirmationPayload.temporary_password;
        delete confirmationPayload.profile_photo_data_url;
        const confirmation = await createActionConfirmation("user.create", confirmationPayload, {
          target_entity: "User",
          target_label: form.username,
          reason: "Create user"
        });
        const result = await api.post<{ user: UserAccount; temporary_password: string | null }>("/api/v1/users", { ...payload, confirmation_token: confirmation.confirmation_token });
        await onSaved(result.temporary_password, result.user);
        await onClose(result.user);
      } else if (user) {
        const payload: Record<string, unknown> = {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          mobile_phone_number: form.mobile_phone_number || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active
        };
        if (profilePhotoChanged) {
          payload.profile_photo_data_url = form.profile_photo_data_url || null;
        }
        const confirmationPayload = { ...payload };
        delete confirmationPayload.profile_photo_data_url;
        const confirmation = await createActionConfirmation("user.update", { user_id: user.id, ...confirmationPayload }, {
          target_entity: "User",
          target_id: user.id,
          target_label: displayUserName(user),
          reason: "Update user"
        });
        const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, { ...payload, confirmation_token: confirmation.confirmation_token });
        await onSaved(null, savedUser);
        await onClose(savedUser);
      }
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save user");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <form ref={modalRef} role="dialog" aria-modal="true" aria-label={mode === "create" ? "Add User" : "Edit User"} className="modal-card" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "create" ? "Add User" : "Edit User"}</h2>
            <p>{mode === "create" ? "Create a dashboard login." : "Update account access."}</p>
          </div>
          <button className="icon-button" onClick={requestClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="profile-upload-row">
          <UserAvatar
            user={{
              id: user?.id ?? "preview",
              username: form.username,
              first_name: String(form.first_name),
              last_name: String(form.last_name),
              full_name: `${form.first_name} ${form.last_name}`.trim(),
              profile_photo_data_url: profilePhotoPreview.startsWith("data:") ? profilePhotoPreview : null,
              profile_photo_url: profilePhotoPreview && !profilePhotoPreview.startsWith("data:") ? profilePhotoPreview : null,
              email: form.email || null,
              mobile_phone_number: String(form.mobile_phone_number || "") || null,
              role: form.role as UserRole,
              is_active: Boolean(form.is_active),
              last_login_at: user?.last_login_at ?? null,
              person_id: String(form.person_id || "") || null,
              preferences: user?.preferences ?? { sidebarCollapsed: false },
              created_at: user?.created_at ?? new Date().toISOString(),
              updated_at: user?.updated_at ?? new Date().toISOString()
            }}
            size="large"
          />
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
              <input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} required />
            </div>
          </label>
          <label className="field">
            <span>Last name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} required />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Username</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.username} onChange={(event) => update("username", event.target.value)} required />
            </div>
          </label>
        </div>
        <label className="field">
          <span>Email</span>
          <div className="field-control">
            <MessageCircle size={17} />
            <input value={form.email} onChange={(event) => update("email", event.target.value)} type="email" />
          </div>
        </label>
        <label className="field">
          <span>Mobile phone</span>
          <div className="field-control">
            <Smartphone size={17} />
            <input value={form.mobile_phone_number} onChange={(event) => update("mobile_phone_number", event.target.value)} type="tel" />
          </div>
        </label>
        <label className="field">
          <span>Directory person</span>
          <input aria-label="Search linked person" placeholder="Search directory people" value={personOptions.query} onChange={(event) => personOptions.setQuery(event.target.value)} />
          <DirectoryPagination page={personOptions} />
          <select aria-label="Linked person" value={form.person_id} onChange={(event) => update("person_id", event.target.value)}>
            <option value="">No linked person</option>
            {personOptions.items.map((person) => (
              <option key={person.id} value={person.id}>{person.display_name}</option>
            ))}
          </select>
        </label>
        <div className="field-grid">
          <label className="field">
            <span>Role</span>
            <select value={form.role} onChange={(event) => update("role", event.target.value)}>
              <option value="standard">Standard User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <label className="field">
            <span>Status</span>
            <select value={form.is_active ? "active" : "inactive"} onChange={(event) => update("is_active", event.target.value === "active")}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        {mode === "create" ? (
          <>
            <label className="check-row">
              <input checked={form.generate_password} onChange={(event) => update("generate_password", event.target.checked)} type="checkbox" />
              <span>Generate a temporary password</span>
            </label>
            {!form.generate_password ? (
              <label className="field">
                <span>Temporary password</span>
                <div className="field-control">
                  <Key size={17} />
                  <input value={form.temporary_password} onChange={(event) => update("temporary_password", event.target.value)} type="password" minLength={10} required />
                </div>
              </label>
            ) : null}
          </>
        ) : null}
        <div className="modal-actions">
          <button className="secondary-button" onClick={requestClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "Saving..." : "Save User"}
          </button>
        </div>
      </form>
    </div>
  );
}
