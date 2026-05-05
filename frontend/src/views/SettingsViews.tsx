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
  api,
  Badge,
  CardHeader,
  coerceSettingsPayload,
  createActionConfirmation,
  displayUserName,
  fileToDataUrl,
  formatDate,
  Group,
  MaintenanceStatus,
  PanelHeader,
  Person,
  Schedule,
  SettingField,
  SettingFieldDefinition,
  stringifySetting,
  Toolbar,
  UnifiProtectCamera,
  UserAccount,
  UserAvatar,
  UserRole,
  useSettings,
  Vehicle
} from "../shared";



export function SettingsView({
  currentUser,
  groups,
  schedules,
  vehicles
}: {
  currentUser: UserAccount;
  groups: Group[];
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  return (
    <section className="dashboard-grid settings-grid">
      <div className="card span-2">
        <CardHeader icon={SlidersHorizontal} title="Appearance" />
        <div className="settings-list">
          <SettingRow label="Default mode" value="System" />
          <SettingRow label="Status palette" value="Blue, green, gray, amber, red" />
          <SettingRow label="Card radius" value="8px" />
        </div>
      </div>
      <div className="card">
        <CardHeader icon={Users} title="User Accounts" />
        <div className="compact-row">
          <UserAvatar user={currentUser} />
          <div>
            <strong>{displayUserName(currentUser)}</strong>
            <span>{currentUser.role === "admin" ? "Administrator" : "Standard access"}</span>
          </div>
          <Badge tone="green">protected</Badge>
        </div>
      </div>
      <div className="card span-3">
        <CardHeader icon={Database} title="Operational Data" action={<Badge tone="blue">current</Badge>} />
        <div className="settings-list">
          <SettingRow label="Access schedules" value={String(schedules.length)} />
          <SettingRow label="Access groups" value={String(groups.length)} />
          <SettingRow label="Active vehicles" value={`${activeVehicles}/${vehicles.length}`} />
        </div>
      </div>
    </section>
  );
}

export const GATE_LPR_CAMERA_NAME = "gate lpr";

export const GATE_LPR_CAMERA_DEVICE = "942A6FD09D64";

export type GateLprSmartZonesState = {
  loading: boolean;
  error: string;
  camera: UnifiProtectCamera | null;
  zones: UnifiProtectCamera["smart_detect_zones"];
};

export type AuthSecretStatus = {
  source: string;
  environment: string;
  file_path: string;
  env_configured: boolean;
  env_default_configured: boolean;
  rotation_required: boolean;
  ui_rotation_available: boolean;
  detail: string;
  rotated?: boolean;
  settings_reencrypted?: number;
  icloud_accounts_reencrypted?: number;
  action_contexts_invalidated?: number;
};

export function useGateLprSmartZones(enabled: boolean): GateLprSmartZonesState {
  const [state, setState] = React.useState<GateLprSmartZonesState>({
    loading: false,
    error: "",
    camera: null,
    zones: []
  });

  React.useEffect(() => {
    if (!enabled) {
      setState({ loading: false, error: "", camera: null, zones: [] });
      return;
    }
    let active = true;
    setState((current) => ({ ...current, loading: true, error: "" }));
    api.get<{ cameras: UnifiProtectCamera[] }>("/api/v1/integrations/unifi-protect/cameras")
      .then((payload) => {
        if (!active) return;
        const camera = findGateLprCamera(payload.cameras);
        setState({
          loading: false,
          error: "",
          camera,
          zones: camera?.smart_detect_zones ?? []
        });
      })
      .catch((loadError) => {
        if (!active) return;
        setState({
          loading: false,
          error: loadError instanceof Error ? loadError.message : "Unable to load UniFi Protect cameras.",
          camera: null,
          zones: []
        });
      });
    return () => {
      active = false;
    };
  }, [enabled]);

  return state;
}

export function findGateLprCamera(cameras: UnifiProtectCamera[]) {
  return cameras.find((camera) => normalizeCameraIdentifier(camera.name) === GATE_LPR_CAMERA_NAME)
    ?? cameras.find((camera) => normalizeCameraIdentifier(camera.mac) === normalizeCameraIdentifier(GATE_LPR_CAMERA_DEVICE))
    ?? cameras.find((camera) => {
      const label = normalizeCameraIdentifier(camera.name);
      return label.includes("gate") && label.includes("lpr");
    })
    ?? null;
}

export function normalizeCameraIdentifier(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

export function DynamicSettingsView({
  category,
  title,
  icon: Icon,
  currentUser,
  maintenanceStatus,
  onMaintenanceStatusChanged,
  refreshToken
}: {
  category: "general" | "auth" | "lpr";
  title: string;
  icon: React.ElementType;
  currentUser?: UserAccount;
  maintenanceStatus?: MaintenanceStatus | null;
  onMaintenanceStatusChanged?: (status: MaintenanceStatus) => void;
  refreshToken: number;
}) {
  const { values, loading, error, save, reload } = useSettings(category);
  const [form, setForm] = React.useState<Record<string, string>>({});
  const [saved, setSaved] = React.useState("");
  const fields = settingsFields(category);
  const gateLprSmartZones = useGateLprSmartZones(category === "lpr");
  const lastRefreshTokenRef = React.useRef(refreshToken);

  React.useEffect(() => {
    const next: Record<string, string> = {};
    for (const field of fields) {
      next[field.key] = stringifySetting(values[field.key]);
    }
    setForm(next);
  }, [values, category]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    reload().catch(() => undefined);
  }, [refreshToken, reload]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaved("");
    await save(coerceSettingsPayload(form));
    setSaved("Settings saved.");
  };

  return (
    <section className="view-stack settings-page">
      <Toolbar title={title} count={fields.length} icon={Icon} />
      <form className="dashboard-grid settings-grid" onSubmit={submit}>
        <div className="card span-2">
          <CardHeader icon={Icon} title={title} action={<Badge tone={loading ? "gray" : "green"}>{loading ? "loading" : "database"}</Badge>} />
          {category === "general" ? (
            <MaintenanceModeSettings
              currentUser={currentUser}
              status={maintenanceStatus ?? null}
              onStatusChanged={onMaintenanceStatusChanged}
            />
          ) : null}
          <div className="settings-form-grid">
            {fields.map((field) => {
              const onChange = (value: string) => setForm((current) => ({ ...current, [field.key]: value }));
              if (category === "lpr" && field.key === "lpr_allowed_smart_zones") {
                return (
                  <GateLprSmartZoneField
                    field={field}
                    key={field.key}
                    state={gateLprSmartZones}
                    value={form[field.key] ?? ""}
                    onChange={onChange}
                  />
                );
              }
              return (
                <SettingField
                  field={field}
                  key={field.key}
                  value={form[field.key] ?? ""}
                  onChange={onChange}
                />
              );
            })}
          </div>
          {error ? <div className="auth-error inline-error">{error}</div> : null}
          {saved ? <div className="success-note">{saved}</div> : null}
          <div className="modal-actions">
            <button className="primary-button" type="submit">Save Settings</button>
          </div>
        </div>
        {category === "auth" ? <AuthSecretSecurityPanel refreshToken={refreshToken} /> : null}
        <div className="card">
          <CardHeader icon={Database} title="Source" />
          <div className="settings-list">
            <SettingRow label="Storage" value="Database" />
            <SettingRow label="Secrets" value="Encrypted at rest" />
            <SettingRow label="Bootstrap" value="Secret file + env override" />
          </div>
        </div>
      </form>
    </section>
  );
}

export function AuthSecretSecurityPanel({ refreshToken }: { refreshToken: number }) {
  const [status, setStatus] = React.useState<AuthSecretStatus | null>(null);
  const [customSecret, setCustomSecret] = React.useState("");
  const [confirmed, setConfirmed] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await api.get<AuthSecretStatus>("/api/v1/settings/security/auth-secret"));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load auth secret status.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
  }, [load, refreshToken]);

  const rotate = async () => {
    if (!confirmed || saving) return;
    setSaving(true);
    setSaved("");
    setError("");
    try {
      const payload = {
        confirmed: true,
        new_secret: customSecret.trim() || undefined
      };
      const next = await api.post<AuthSecretStatus>("/api/v1/settings/security/auth-secret/rotate", payload);
      setStatus(next);
      setCustomSecret("");
      setConfirmed(false);
      setSaved("Auth secret rotated. Existing sessions and pending action links were invalidated.");
    } catch (rotateError) {
      setError(rotateError instanceof Error ? rotateError.message : "Unable to rotate auth secret.");
    } finally {
      setSaving(false);
    }
  };

  const sourceLabel = status?.source === "env"
    ? "Environment override"
    : status?.source === "generated"
      ? "Generated file"
      : "Secret file";

  return (
    <div className="card auth-secret-panel">
      <CardHeader icon={ShieldCheck} title="Auth Secret" action={<Badge tone={status?.rotation_required ? "amber" : "green"}>{status?.rotation_required ? "rotate" : "ready"}</Badge>} />
      {loading ? (
        <div className="compact-row"><Loader2 size={16} /> Loading security status...</div>
      ) : status ? (
        <div className="settings-list">
          <SettingRow label="Source" value={sourceLabel} />
          <SettingRow label="Environment" value={status.environment} />
          <SettingRow label="File" value={status.file_path} />
          <SettingRow label="UI rotation" value={status.ui_rotation_available ? "Available" : "Env managed"} />
        </div>
      ) : null}
      {status?.ui_rotation_available ? (
        <div className="auth-secret-rotate">
          <label className="field">
            <span>Custom secret</span>
            <div className="field-control">
              <Key size={17} />
              <input
                value={customSecret}
                onChange={(event) => setCustomSecret(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.preventDefault();
                }}
                placeholder="Leave blank to generate a secure secret"
                type="password"
              />
            </div>
            <small className="field-hint">Use at least 32 characters. Blank rotation generates a new random value.</small>
          </label>
          <label className="maintenance-switch auth-secret-confirm">
            <input checked={confirmed} disabled={saving} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
            <span>Confirm rotation</span>
          </label>
          <button className="primary-button" disabled={!confirmed || saving} onClick={rotate} type="button">
            {saving ? <Loader2 size={15} /> : <RefreshCw size={15} />}
            {saving ? "Rotating..." : "Rotate Secret"}
          </button>
        </div>
      ) : (
        <p className="dependency-storage-note">{status?.detail || "Auth secret rotation is managed outside the UI."}</p>
      )}
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {saved ? <div className="success-note">{saved}</div> : null}
    </div>
  );
}

export function MaintenanceModeSettings({
  currentUser,
  status,
  onStatusChanged
}: {
  currentUser?: UserAccount;
  status: MaintenanceStatus | null;
  onStatusChanged?: (status: MaintenanceStatus) => void;
}) {
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const active = status?.is_active === true;
  const isAdmin = currentUser?.role === "admin";
  const toggle = async () => {
    if (saving) return;
    if (!isAdmin) {
      setError("Admin access is required to update Maintenance Mode.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const path = active ? "/api/v1/maintenance/disable" : "/api/v1/maintenance/enable";
      const action = active ? "maintenance_mode.disable" : "maintenance_mode.enable";
      const payload = {
        reason: active ? "Disabled from Settings General" : "Enabled from Settings General"
      };
      const confirmation = await createActionConfirmation(action, payload, {
        target_entity: "MaintenanceMode",
        target_label: "Maintenance Mode",
        reason: payload.reason
      });
      const next = await api.post<MaintenanceStatus>(path, {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      onStatusChanged?.(next);
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : "Unable to update Maintenance Mode.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className={active ? "maintenance-settings active" : "maintenance-settings"}>
      <div className="maintenance-settings-copy">
        <span className="maintenance-settings-icon">
          <Construction size={20} strokeWidth={1} />
        </span>
        <div>
          <strong>Maintenance Mode</strong>
          <span>{active ? "All automated actions are disabled" : "Automated actions are available"}</span>
          {active && status?.enabled_by ? <small>Enabled by {status.enabled_by}{status.duration_label ? ` for ${status.duration_label}` : ""}</small> : null}
        </div>
      </div>
      <label className={active ? "maintenance-switch active" : "maintenance-switch"}>
        <input checked={active} disabled={saving || !status || !isAdmin} onChange={toggle} type="checkbox" />
        <span>{saving ? "Updating" : active ? "Enabled" : "Disabled"}</span>
      </label>
      {error ? <div className="auth-error inline-error maintenance-settings-error">{error}</div> : null}
    </div>
  );
}

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
        api.get<Person[]>("/api/v1/people")
      ]);
      setUsers(nextUsers);
      setPeople(nextPeople);
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

  const closeModal = () => {
    setModal(null);
    setSelectedUser(null);
  };

  const deleteUser = async (user: UserAccount) => {
    if (!window.confirm(`Delete ${displayUserName(user)}?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/users/${user.id}`);
      await loadUsers();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete user");
    }
  };

  const toggleActive = async (user: UserAccount) => {
    setError("");
    try {
      const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, { is_active: !user.is_active });
      if (savedUser.id === currentUser.id) {
        onCurrentUserUpdated(savedUser);
      }
      await loadUsers();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Unable to update user");
    }
  };

  const resetPassword = async (user: UserAccount) => {
    setError("");
    try {
      const result = await api.post<{ temporary_password: string }>(`/api/v1/users/${user.id}/reset-password`, {
        generate_password: true
      });
      setSelectedUser(user);
      setTemporaryPassword(result.temporary_password);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Unable to reset password");
    }
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
          <button className="secondary-button" onClick={() => navigator.clipboard?.writeText(temporaryPassword)} type="button">
            Copy
          </button>
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
                    <button className="secondary-button" onClick={() => openEdit(user)} type="button">Edit</button>
                    <button className="secondary-button" onClick={() => resetPassword(user)} type="button">Reset</button>
                    <button className="secondary-button" onClick={() => toggleActive(user)} type="button">{user.is_active ? "Deactivate" : "Activate"}</button>
                    <button className="icon-button danger" onClick={() => deleteUser(user)} type="button" aria-label={`Delete ${displayUserName(user)}`}>
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
            if (savedUser?.id === currentUser.id) {
              onCurrentUserUpdated(savedUser);
            }
            await loadUsers();
            closeModal();
            setSelectedUser(savedUser ?? null);
          }}
        />
      ) : null}
    </section>
  );
}

export function UserModal({
  mode,
  people,
  user,
  onClose,
  onSaved
}: {
  mode: "create" | "edit";
  people: Person[];
  user: UserAccount | null;
  onClose: () => void;
  onSaved: (temporaryPassword: string | null, savedUser?: UserAccount) => Promise<void>;
}) {
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
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

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
    update("profile_photo_data_url", await fileToDataUrl(file));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "create") {
        const result = await api.post<{ user: UserAccount; temporary_password: string | null }>("/api/v1/users", {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          mobile_phone_number: form.mobile_phone_number || null,
          profile_photo_data_url: form.profile_photo_data_url || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active,
          temporary_password: form.generate_password ? null : form.temporary_password,
          generate_password: form.generate_password
        });
        await onSaved(result.temporary_password, result.user);
      } else if (user) {
        const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          mobile_phone_number: form.mobile_phone_number || null,
          profile_photo_data_url: form.profile_photo_data_url || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active
        });
        await onSaved(null, savedUser);
      }
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save user");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "create" ? "Add User" : "Edit User"}</h2>
            <p>{mode === "create" ? "Create a dashboard login." : "Update account access."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
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
              profile_photo_data_url: String(form.profile_photo_data_url || "") || null,
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
          <select value={form.person_id} onChange={(event) => update("person_id", event.target.value)}>
            <option value="">No linked person</option>
            {people.map((person) => (
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
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "Saving..." : "Save User"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="setting-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function GateLprSmartZoneField({
  field,
  state,
  value,
  onChange
}: {
  field: SettingFieldDefinition;
  state: GateLprSmartZonesState;
  value: string;
  onChange: (value: string) => void;
}) {
  const selected = firstSettingListValue(value);
  const zones = uniqueGateLprSmartZones(state.zones);
  const selectedZone = zones.find((zone) => normalizeSmartZoneName(zone.name) === normalizeSmartZoneName(selected));
  const selectValue = selectedZone?.name ?? selected;
  const disabled = state.loading || Boolean(state.error) || !state.camera || zones.length === 0;
  const status = gateLprSmartZoneStatus(state);
  return (
    <label className="field">
      <span>{field.label}</span>
      <select value={selectValue} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        <option value="">{state.loading ? "Loading zones..." : "Select smart zone"}</option>
        {selected && !selectedZone ? <option value={selected}>{selected}</option> : null}
        {zones.map((zone) => (
          <option key={`${zone.id ?? zone.name}:${zone.name}`} value={zone.name}>
            {zone.name}
          </option>
        ))}
      </select>
      <small className="field-hint">{status}</small>
    </label>
  );
}

export function firstSettingListValue(value: string) {
  return value.replace(/,/g, "\n").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)[0] ?? "";
}

export function uniqueGateLprSmartZones(zones: UnifiProtectCamera["smart_detect_zones"]) {
  const seen = new Set<string>();
  return zones.filter((zone) => {
    const name = String(zone.name ?? "").trim();
    const normalized = normalizeSmartZoneName(name);
    if (!normalized || seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
}

export function normalizeSmartZoneName(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

export function gateLprSmartZoneStatus(state: GateLprSmartZonesState) {
  if (state.loading) return "Loading Gate LPR smart zones from UniFi Protect.";
  if (state.error) return `UniFi Protect zones unavailable: ${state.error}`;
  if (!state.camera) return "Gate LPR camera was not found.";
  if (state.zones.length === 0) return "Gate LPR camera has no smart detect zones.";
  return `Gate LPR camera: ${state.camera.name}.`;
}

export function settingsFields(category: "general" | "auth" | "lpr"): SettingFieldDefinition[] {
  if (category === "general") {
    return [
      { key: "app_name", label: "App name" },
      { key: "site_timezone", label: "Timezone" },
      { key: "log_level", label: "Log level", type: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"] }
    ];
  }
  if (category === "auth") {
    return [
      { key: "auth_cookie_name", label: "Cookie name" },
      { key: "auth_access_token_minutes", label: "Access token minutes", type: "number", min: 5, step: 5 },
      { key: "auth_remember_days", label: "Remember-me days", type: "number", min: 1, step: 1 },
      { key: "auth_cookie_secure", label: "Secure cookie", type: "select", options: ["true", "false"] }
    ];
  }
  return [
    { key: "lpr_debounce_quiet_seconds", label: "Debounce quiet seconds", type: "number", min: 0.5, step: 0.1 },
    { key: "lpr_debounce_max_seconds", label: "Debounce max seconds", type: "number", min: 1, step: 0.1 },
    { key: "lpr_vehicle_session_idle_seconds", label: "Vehicle session idle seconds", type: "number", min: 10, step: 5 },
    { key: "lpr_similarity_threshold", label: "Similarity threshold", type: "number", min: 0, max: 1, step: 0.01 },
  ];
}
