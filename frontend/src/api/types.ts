export type ScheduleTimeBlock = {
  start: string;
  end: string;
};
export type ScheduleTimeBlocks = Record<string, ScheduleTimeBlock[]>;
export type Schedule = {
  id: string;
  name: string;
  description: string | null;
  time_blocks: ScheduleTimeBlocks;
  created_at: string;
  updated_at: string;
};
export type Presence = {
  person_id: string;
  display_name: string;
  state: "present" | "exited" | "unknown";
  last_changed_at: string | null;
};
export type ExpectedPresencePerson = {
  person_id: string;
  display_name: string;
  confidence: number;
  evidence_days: number;
  observed_weekdays: number;
  typical_arrival: string | null;
  typical_departure: string | null;
};
export type ExpectedPresenceSummary = {
  date: string;
  timezone: string;
  generated_at: string;
  count: number;
  learning: boolean;
  coverage: {
    regular_candidates: number;
    learned_candidates: number;
    learning_population: number;
    ratio: number;
  };
  people: ExpectedPresencePerson[];
};
export type AccessEvent = {
  id: string;
  registration_number: string;
  direction: "entry" | "exit" | "denied";
  decision: "granted" | "denied";
  confidence: number;
  source: string;
  occurred_at: string;
  timing_classification: string;
  anomaly_count: number;
  visitor_pass_id: string | null;
  visitor_name: string | null;
  visitor_pass_mode: string | null;
  snapshot_url: string | null;
  snapshot_captured_at: string | null;
  snapshot_bytes: number | null;
  snapshot_width: number | null;
  snapshot_height: number | null;
  snapshot_camera: string | null;
  movement_saga: MovementSagaSummary | null;
};
export type MovementSagaSummary = {
  id?: string | null;
  state: string;
  reconciliation_required?: boolean;
  gate_command_required?: boolean;
  presence_committed?: boolean;
  failure_detail?: string | null;
  updated_at?: string | null;
  detail?: string | null;
  gate?: {
    command_id?: string | null;
    accepted?: boolean | null;
    state?: string | null;
    detail?: string | null;
    mechanically_confirmed?: boolean;
    requires_reconciliation?: boolean;
  } | null;
};
export type AlertSeverity = "info" | "warning" | "critical";
type AlertStatus = "open" | "resolved";
type AlertResolver = {
  id: string;
  username: string;
  display_name: string;
};
export type Anomaly = {
  id: string;
  alert_ids: string[];
  grouped: boolean;
  event_id?: string | null;
  type: string;
  severity: AlertSeverity;
  status: AlertStatus;
  message: string;
  registration_number: string;
  count: number;
  local_date: string | null;
  created_at: string;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolved_by_user_id: string | null;
  resolved_by: AlertResolver | null;
  resolution_note: string | null;
  snapshot_url?: string | null;
  snapshot_captured_at?: string | null;
  snapshot_bytes?: number | null;
};
export type Person = {
  id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  pronouns: "he/him" | "she/her" | null;
  profile_photo_data_url: string | null;
  profile_photo_url?: string | null;
  group_id: string | null;
  group: string | null;
  category: string | null;
  schedule_id: string | null;
  schedule: string | null;
  is_active: boolean;
  notes: string | null;
  garage_door_entity_ids: string[];
  home_assistant_mobile_app_notify_service: string | null;
  home_assistant_presence_input_boolean_entity_ids: string[];
  home_assistant_presence_input_boolean_entry_action: "turn_on" | "turn_off";
  home_assistant_presence_input_boolean_exit_action: "turn_on" | "turn_off";
  vehicles: Vehicle[];
};
export type Vehicle = {
  id: string;
  registration_number: string;
  vehicle_photo_data_url?: string | null;
  vehicle_photo_url?: string | null;
  description: string | null;
  make: string | null;
  model: string | null;
  color?: string | null;
  fuel_type?: string | null;
  mot_status?: string | null;
  tax_status?: string | null;
  mot_expiry?: string | null;
  tax_expiry?: string | null;
  last_dvla_lookup_date?: string | null;
  person_id?: string | null;
  owner?: string | null;
  person_ids?: string[];
  owners?: string[];
  schedule_id?: string | null;
  schedule?: string | null;
  is_active?: boolean;
};
export type Group = {
  id: string;
  name: string;
  category: string;
  subtype: string | null;
  description: string | null;
  people_count: number;
};
export type IntegrationStatus = {
  configured: boolean;
  connected?: boolean;
  degraded?: boolean;
  last_error?: string | null;
  last_connected_at?: string | null;
  last_failure_at?: string | null;
  state_refreshed_at?: string | null;
  listener_running?: boolean;
  provider_status?: Record<string, AccessDeviceProviderRuntimeStatus>;
  state_stream_status?: Record<string, AccessDeviceStreamStatus>;
  gate_entity_id: string | null;
  gate_entities?: HomeAssistantManagedCover[];
  garage_door_entities?: HomeAssistantManagedCover[];
  default_media_player: string | null;
  last_gate_state: string;
  current_gate_state?: string;
  front_door_state?: string;
  back_door_state?: string;
  main_garage_door_state?: string;
  mums_garage_door_state?: string;
};
type AccessDeviceProviderRuntimeStatus = {
  provider: string;
  configured?: boolean;
  connected?: boolean;
  degraded?: boolean;
  last_error?: string | null;
  metadata?: Record<string, unknown>;
};
type AccessDeviceStreamStatus = {
  provider: string;
  connected?: boolean;
  running?: boolean;
  last_error?: string | null;
  updated_at?: string | null;
  devices?: AccessDeviceStreamDeviceStatus[];
};
export type AccessDeviceStreamDeviceStatus = {
  device_id?: string | null;
  connected?: boolean;
  last_error?: string | null;
  cover_count?: number | null;
  updated_at?: string | null;
};
export type MaintenanceStatus = {
  is_active: boolean;
  enabled_by: string | null;
  enabled_at: string | null;
  source: string | null;
  reason: string | null;
  duration_seconds: number;
  duration_label: string | null;
  ha_entity_id?: string;
};
export type ActionConfirmation = {
  confirmation_id: string;
  confirmation_token: string;
  action: string;
  expires_at: string;
};
export type ActionConfirmationOptions = {
  target_entity?: string;
  target_id?: string;
  target_label?: string;
  reason?: string;
  metadata?: Record<string, unknown>;
};
export type RealtimeMessage = {
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};
export type AuditLog = {
  id: string;
  timestamp: string;
  category: string;
  action: string;
  actor: string;
  actor_user_id: string | null;
  target_entity: string | null;
  target_id: string | null;
  target_label: string | null;
  diff: Record<string, unknown>;
  metadata: Record<string, unknown>;
  outcome: string;
  level: string;
  trace_id: string | null;
  request_id: string | null;
};
export type UserRole = "admin" | "standard";
export type SystemSetting = {
  key: string;
  category: string;
  value: unknown;
  is_secret: boolean;
  description: string | null;
};
export type SettingsMap = Record<string, unknown>;
export type HomeAssistantEntity = {
  entity_id: string;
  name: string | null;
  state: string | null;
  device_class?: string | null;
};
export type HomeAssistantManagedCover = {
  entity_id: string;
  name: string;
  state?: string | null;
  enabled?: boolean;
  schedule_id?: string | null;
  open_service?: string;
  close_service?: string;
};
type AccessDeviceProviderBinding = {
  provider: "home_assistant" | "esphome" | string;
  external_id: string;
  enabled: boolean;
  config: Record<string, unknown>;
};
export type AccessDevice = {
  id: string;
  key: string;
  kind: "gate" | "garage_door";
  name: string;
  enabled: boolean;
  schedule_id: string | null;
  open_for_access: boolean;
  sort_order: number;
  bindings: AccessDeviceProviderBinding[];
};
export type HomeAssistantMobileAppService = {
  service_id: string;
  name: string | null;
  description: string | null;
};
type HomeAssistantMobileAppSuggestion = {
  person_id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  suggested_service_id: string | null;
  suggested_name: string | null;
  confidence: number;
};
export type HomeAssistantDiscovery = {
  cover_entities: HomeAssistantEntity[];
  input_boolean_entities: HomeAssistantEntity[];
  gate_suggestions?: HomeAssistantManagedCover[];
  garage_door_suggestions?: HomeAssistantManagedCover[];
  media_player_entities: HomeAssistantEntity[];
  mobile_app_notification_services: HomeAssistantMobileAppService[];
  mobile_app_notification_mappings: HomeAssistantMobileAppSuggestion[];
};
export type NotificationChannelId = "mobile" | "in_app" | "voice" | "discord" | "whatsapp";
export type NotificationTriggerOption = {
  value: string;
  label: string;
  severity: "info" | "warning" | "critical";
  description: string;
};
export type TooltipPositionState = {
  left: number;
  placement: "top" | "bottom";
  top: number;
};
export type UnifiProtectCamera = {
  id: string;
  mac?: string | null;
  name: string;
  model: string | null;
  state: string | null;
  is_recording: boolean;
  is_recording_enabled: boolean;
  is_video_ready: boolean;
  is_motion_detected: boolean;
  is_smart_detected: boolean;
  last_motion_at: string | null;
  last_smart_detect_at: string | null;
  snapshot_url: string;
  channels: Array<{
    id: string;
    name: string;
    width: number | null;
    height: number | null;
    fps: number | null;
    bitrate: number | null;
    is_rtsp_enabled: boolean;
    is_package: boolean;
  }>;
  feature_flags: {
    has_smart_detect: boolean;
    has_package_camera: boolean;
    has_mic: boolean;
    smart_detect_types: string[];
    smart_detect_audio_types: string[];
  };
  detections: {
    active: string[];
  };
  smart_detect_zones: Array<{
    id: number | string | null;
    name: string;
    object_types: string[];
  }>;
};
export type UserAccount = {
  id: string;
  username: string;
  first_name: string;
  last_name: string;
  full_name: string;
  profile_photo_data_url: string | null;
  profile_photo_url?: string | null;
  email: string | null;
  mobile_phone_number: string | null;
  role: UserRole;
  is_active: boolean;
  last_login_at: string | null;
  person_id: string | null;
  preferences: ProfilePreferences & Record<string, unknown>;
  created_at: string;
  updated_at: string;
};
export type ProfilePreferences = {
  sidebarCollapsed: boolean;
};
export type ViewKey =
  | "dashboard"
  | "people"
  | "groups"
  | "schedules"
  | "passes"
  | "vehicles"
  | "top_charts"
  | "events"
  | "movements"
  | "alerts"
  | "reports"
  | "integrations"
  | "logs"
  | "settings"
  | "settings_general"
  | "settings_gates"
  | "settings_garage_doors"
  | "settings_auth"
  | "alfred_training"
  | "settings_automations"
  | "settings_notifications"
  | "settings_lpr"
  | "settings_zones"
  | "users";
export type NavigateOptions = {
  replace?: boolean;
  search?: string;
  hash?: string;
};
export type NavigateToView = (nextView: ViewKey, options?: NavigateOptions) => void;
