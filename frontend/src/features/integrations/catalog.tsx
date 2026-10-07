import { Bell, Bot, CalendarDays, Camera, CircleDot, Database, Home, MessageCircle, Search, Zap } from "lucide-react";
import React from "react";
import { isLlmProviderConfigured, llmProviderDefinitions, normalizeLlmProvider } from "../../lib/format";
import { secretSettingKeys, stringifySetting } from "../../lib/settings";
import type { IntegrationStatus, NotificationChannelId, SettingsMap } from "../../api/types";
import type { LlmProviderKey } from "../../lib/format";
import type { SettingFieldDefinition } from "../../lib/settings";
import type { BadgeTone } from "../../ui/primitives";
import {
  ICloudCalendarAccount,
  UnifiProtectStatus,
} from "../../api/integrations";
export function CameraAiProviderSelector({
  saving,
  values,
  onChange
}: {
  saving: boolean;
  values: SettingsMap;
  onChange: (provider: LlmProviderKey) => Promise<void>;
}) {
  const activeProvider = normalizeLlmProvider(values.llm_provider);
  return (
    <div className="llm-provider-selector">
      <Bot size={15} />
      <label className="llm-provider-select">
        <span>Camera AI provider</span>
        <select
          disabled={saving}
          value={activeProvider}
          onChange={(event) => onChange(event.target.value as LlmProviderKey)}
        >
          {llmProviderDefinitions.map((provider) => {
            const configured = isLlmProviderConfigured(provider.key, values);
            return (
              <option disabled={!configured && provider.key !== activeProvider} key={provider.key} value={provider.key}>
                {provider.label}{configured ? "" : " (not configured)"}
              </option>
            );
          })}
        </select>
      </label>
    </div>
  );
}
export type IntegrationDefinition = {
  key: string;
  title: string;
  description: string;
  category: "access" | "notifications" | "data" | "ai";
  icon: React.ElementType;
  fields: SettingFieldDefinition[];
  statusLabel: string;
  statusTone: BadgeTone;
  notificationChannels?: NotificationChannelId[];
  oauth?: boolean;
};
export type ProtectIntegrationTab = "general" | "exposes";
export type IntegrationFeedback = {
  tone: "progress" | "success" | "error" | "info";
  title: string;
  detail: string;
  activeStep?: number;
};
export const integrationCategories: Array<{
  key: IntegrationDefinition["category"];
  label: string;
  description: string;
}> = [
  {
    key: "access",
    label: "Access Control",
    description: "Physical site controls and sensor integrations."
  },
  {
    key: "notifications",
    label: "Notification Providers",
    description: "Destinations made available to the notification rules engine."
  },
  {
    key: "data",
    label: "Data & Intelligence",
    description: "Vehicle data, cameras, and operational enrichment."
  },
  {
    key: "ai",
    label: "AI Providers",
    description: "Providers used to analyse camera images."
  }
];
const integrationFieldSets: Record<string, SettingFieldDefinition[]> = {
  home_assistant: [
    { key: "home_assistant_url", label: "URL" },
    { key: "home_assistant_token", label: "Long-lived token", type: "password" },
    { key: "home_assistant_gate_open_service", label: "Cover open service" },
    { key: "home_assistant_tts_service", label: "TTS service" },
    { key: "home_assistant_default_media_player", label: "Default media player" }
  ],
  apprise: [{ key: "apprise_urls", label: "Apprise URLs", type: "textarea", href: "https://github.com/caronc/apprise/wiki", help: "For Pushover use pover://USER_KEY@APP_TOKEN. The app also accepts pushover://USER_KEY/APP_TOKEN and normalizes it." }],
  dvsa: [
    { key: "dvsa_enabled", label: "Enable MOT History", type: "select", options: ["false", "true"] },
    { key: "dvsa_client_id", label: "Client ID" },
    { key: "dvsa_client_secret", label: "Client secret", type: "password" },
    { key: "dvsa_api_key", label: "API key", type: "password", href: "https://documentation.history.mot.api.gov.uk/" },
    { key: "dvsa_token_url", label: "Token URL", help: "Microsoft token URL supplied by DVSA." },
    { key: "dvsa_scope", label: "OAuth scope" },
    { key: "dvsa_timeout_seconds", label: "Timeout seconds", type: "number", min: 1, step: 1 },
    { key: "dvsa_test_registration_number", label: "Test registration" }
  ],
  dvla: [
    { key: "dvla_api_key", label: "DVLA API Key", type: "password", href: "https://developer-portal.driver-vehicle-licensing.api.gov.uk/apis/vehicle-enquiry-service/vehicle-enquiry-service-description.html" },
    { key: "dvla_vehicle_enquiry_url", label: "Vehicle enquiry URL", help: "Production endpoint for the DVLA Vehicle Enquiry Service API." },
    { key: "dvla_test_registration_number", label: "Test VRN", help: "Used only when this modal tests the DVLA connection." },
    { key: "dvla_timeout_seconds", label: "Timeout seconds", type: "number", min: 1, step: 1 }
  ],
  unifi_protect: [
    { key: "unifi_protect_host", label: "Console host" },
    { key: "unifi_protect_port", label: "HTTPS port", type: "number", min: 1, max: 65535, step: 1 },
    { key: "unifi_protect_username", label: "Local username", type: "password" },
    { key: "unifi_protect_password", label: "Local password", type: "password" },
    { key: "unifi_protect_api_key", label: "Integration API key", type: "password", href: "https://uiprotect.readthedocs.io" },
    { key: "unifi_protect_verify_ssl", label: "Verify TLS", type: "select", options: ["false", "true"] },
    { key: "unifi_protect_snapshot_width", label: "Snapshot width", type: "number", min: 160, max: 4096, step: 1 },
    { key: "unifi_protect_snapshot_height", label: "Snapshot height", type: "number", min: 90, max: 2160, step: 1 },
    { key: "lpr_webhook_token", label: "LPR webhook token", type: "password", help: "Configure UniFi Protect Alarm Manager to send X-IACS-LPR-Token with this same value." },
    { key: "lpr_webhook_allowed_source_ips", label: "LPR webhook source IPs", type: "textarea", help: "One static UNVR IP or CIDR range per line. IACS rejects LPR webhooks from every other source." }
  ],
  openai: [
    { key: "openai_api_key", label: "API key", type: "password", href: "https://platform.openai.com/api-keys" },
    { key: "openai_model", label: "Model" },
    { key: "openai_base_url", label: "Base URL" }
  ],
  gemini: [
    { key: "gemini_api_key", label: "API key", type: "password", href: "https://aistudio.google.com/app/apikey" },
    { key: "gemini_model", label: "Model" },
    { key: "gemini_base_url", label: "Base URL" }
  ],
  anthropic: [
    { key: "anthropic_api_key", label: "API key", type: "password", href: "https://console.anthropic.com/settings/keys" },
    { key: "anthropic_model", label: "Model" },
    { key: "anthropic_base_url", label: "Base URL" }
  ],
  ollama: [{ key: "ollama_model", label: "Model" }, { key: "ollama_base_url", label: "Base URL" }]
};
export function integrationDefinitions(
  status: IntegrationStatus | null,
  values: SettingsMap,
  protectStatus: UnifiProtectStatus | null,
  icloudAccounts: ICloudCalendarAccount[],
  icloudError: string,
): IntegrationDefinition[] {
  const activeProvider = normalizeLlmProvider(values.llm_provider);
  const providerStatus = (key: string, secretKey?: string): Pick<IntegrationDefinition, "statusLabel" | "statusTone"> => {
    if (activeProvider === key) return { statusLabel: "Selected", statusTone: "blue" };
    if (secretKey && values[secretKey]) return { statusLabel: "Configured", statusTone: "blue" };
    if (key === "ollama" && values.ollama_base_url) return { statusLabel: "Configured", statusTone: "blue" };
    return { statusLabel: "Not Configured", statusTone: "gray" };
  };
  const activeIcloudAccounts = icloudAccounts.filter((account) => account.is_active);
  const icloudNeedsAttention = activeIcloudAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));
  const homeAssistantConfigured = Boolean(status?.configured || values.home_assistant_url || values.home_assistant_token);
  const homeAssistantDegraded = Boolean(homeAssistantConfigured && (status?.degraded || status?.connected === false || status?.last_error));
  const protectRealtimeDegraded = Boolean(
    protectStatus?.connected && (protectStatus.realtime_connected === false || protectStatus.realtime_error)
  );
  const base: IntegrationDefinition[] = [
    { key: "home_assistant", title: "Home Assistant", description: "Gate control, mobile app notifications, TTS announcements, and state sync.", category: "access", icon: Home, fields: integrationFieldSets.home_assistant, statusLabel: status?.connected ? "Connected" : homeAssistantDegraded ? "Degraded" : homeAssistantConfigured ? "Configured" : "Not Configured", statusTone: status?.connected ? "green" : homeAssistantDegraded ? "red" : homeAssistantConfigured ? "blue" : "gray", notificationChannels: ["mobile", "voice"] },
    { key: "esphome", title: "ESPHome", description: "Direct native API access for gate and garage-door covers.", category: "access", icon: Zap, fields: [], statusLabel: values.esphome_devices ? "Configured" : "Not Configured", statusTone: values.esphome_devices ? "blue" : "gray" },
    { key: "icloud_calendar", title: "iCloud Calendar", description: "Create Visitor Passes from calendar events marked Open Gate.", category: "access", icon: CalendarDays, fields: [], statusLabel: icloudError ? "Error" : icloudNeedsAttention ? "Needs Attention" : activeIcloudAccounts.length ? `${activeIcloudAccounts.length} active accounts` : "Not Configured", statusTone: icloudError ? "red" : icloudNeedsAttention ? "amber" : activeIcloudAccounts.length ? "blue" : "gray" },
    { key: "apprise", title: "Apprise", description: "Mobile and push notification fan-out.", category: "notifications", icon: Bell, fields: integrationFieldSets.apprise, statusLabel: values.apprise_urls ? "Configured" : "Not Configured", statusTone: values.apprise_urls ? "blue" : "gray", notificationChannels: ["mobile"] },
    { key: "dvsa", title: "DVSA MOT History", description: "MOT tests, advisories and vehicle models.", category: "data", icon: Search, fields: integrationFieldSets.dvsa, statusLabel: (values.dvsa_enabled === true || values.dvsa_enabled === "true") ? "Enabled" : "Disabled", statusTone: (values.dvsa_enabled === true || values.dvsa_enabled === "true") ? "blue" : "gray" },
    { key: "dvla", title: "DVLA Lookup", description: "Vehicle Enquiry Service API plate lookups.", category: "data", icon: Search, fields: integrationFieldSets.dvla, statusLabel: values.dvla_api_key ? "Configured" : "Not Configured", statusTone: values.dvla_api_key ? "blue" : "gray" },
    { key: "unifi_protect", title: "UniFi Protect", description: "Camera snapshots, detection events, and AI image analysis.", category: "data", icon: Camera, fields: integrationFieldSets.unifi_protect, statusLabel: protectRealtimeDegraded ? "Realtime Degraded" : protectStatus?.connected ? "Connected" : protectStatus?.configured || values.unifi_protect_host ? "Configured" : "Not Configured", statusTone: protectRealtimeDegraded ? "red" : protectStatus?.connected ? "green" : protectStatus?.configured || values.unifi_protect_host ? "blue" : "gray" }
  ];
  return base.concat([
    { key: "openai", title: "OpenAI", description: "OpenAI image analysis provider.", icon: Bot, secret: "openai_api_key", oauth: true },
    { key: "gemini", title: "Gemini", description: "Google Gemini provider.", icon: CircleDot, secret: "gemini_api_key", oauth: true },
    { key: "anthropic", title: "Anthropic", description: "Claude provider.", icon: MessageCircle, secret: "anthropic_api_key" },
    { key: "ollama", title: "Ollama", description: "Local model endpoint.", icon: Database }
  ].map(({ key, title, description, icon, secret, oauth }) => ({
    key, title, description, icon, oauth, category: "ai", fields: integrationFieldSets[key], ...providerStatus(key, secret)
  } as IntegrationDefinition)));
}
export function integrationInitialValues(definition: IntegrationDefinition, values: SettingsMap) {
  const defaults: Record<string, string> = {
    openai_model: "gpt-4o",
    gemini_model: "gemini-1.5-pro",
    anthropic_model: "claude-3-5-sonnet-latest",
    ollama_model: "llama3",
    openai_base_url: "https://api.openai.com/v1",
    gemini_base_url: "https://generativelanguage.googleapis.com/v1beta",
    anthropic_base_url: "https://api.anthropic.com/v1",
    ollama_base_url: "http://host.docker.internal:11434",
    dvla_vehicle_enquiry_url: "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles",
    dvla_test_registration_number: "AA19AAA",
    dvla_timeout_seconds: "10",
    dvsa_enabled: "false",
    dvsa_timeout_seconds: "10",
    dvsa_scope: "https://tapi.dvsa.gov.uk/.default",
    dvsa_test_registration_number: "AA19AAA",
    unifi_protect_port: "443",
    unifi_protect_verify_ssl: "false",
    unifi_protect_snapshot_width: "1280",
    unifi_protect_snapshot_height: "720",
    home_assistant_gate_entities: "[]",
    home_assistant_garage_door_entities: "[]",
  };
  return definition.fields.reduce<Record<string, string>>((acc, field) => {
    const current = values[field.key];
    const currentOrDefault = current !== undefined && current !== null ? current : defaults[field.key] || "";
    if (secretSettingKeys.has(field.key)) {
      acc[field.key] = "";
    } else if (["home_assistant_gate_entities", "home_assistant_garage_door_entities"].includes(field.key) && typeof current === "object") {
      acc[field.key] = JSON.stringify(current ?? {}, null, 2);
    } else {
      acc[field.key] = stringifySetting(currentOrDefault);
    }
    return acc;
  }, {});
}
