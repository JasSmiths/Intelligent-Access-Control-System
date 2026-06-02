import { Bell, Bot, CalendarDays, Camera, CircleDot, Database, Home, MessageCircle, Search, Zap } from "lucide-react";
import React from "react";
import { isLlmProviderConfigured, llmProviderDefinitions, normalizeLlmProvider } from "../../lib/format";
import { discordListSettingKeys, secretSettingKeys, stringifySetting } from "../../lib/settings";
import type { IntegrationStatus, NotificationChannelId, SettingsMap } from "../../api/types";
import type { LlmProviderKey } from "../../lib/format";
import type { SettingFieldDefinition } from "../../lib/settings";
import type { BadgeTone } from "../../ui/primitives";
import {
  DependencyPackage,
  DiscordStatus,
  ICloudCalendarAccount,
  UnifiProtectStatus,
  UnifiProtectUpdateStatus,
  WhatsAppStatus
} from "../../api/integrations";
import { dependencyIsActionableUpdate } from "./dependencyUpdates";
export function LlmProviderSelector({
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
        <span>System LLM</span>
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
  updateAvailable?: boolean;
};
export type ProtectIntegrationTab = "general" | "exposes" | "updates";
export type IntegrationsPageTab = "integrations" | "updates";
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
    description: "LLM providers used by chat, summaries, and analysis."
  }
];
export function dependenciesForIntegration(definition: IntegrationDefinition, dependencies: DependencyPackage[]) {
  return dependenciesForIntegrationKey(definition.key, dependencies);
}
function dependenciesForIntegrationKey(key: string, dependencies: DependencyPackage[]) {
  const labels: Record<string, string[]> = {
    home_assistant: ["home assistant", "home-assistant"],
    icloud_calendar: ["icloud", "pyicloud"],
    apprise: ["apprise", "notifications"],
    discord: ["discord", "discord.py", "discord messaging"],
    whatsapp: ["whatsapp"],
    dvla: ["dvla"],
    unifi_protect: ["unifi", "uiprotect"],
    openai: ["openai"],
    gemini: ["gemini"],
    anthropic: ["anthropic", "claude"],
    ollama: ["ollama"]
  };
  const needles = labels[key] ?? [key.replace(/_/g, " ")];
  return dependencies.filter((dependency) => {
    const haystack = [
      dependency.package_name,
      dependency.normalized_name,
      dependency.dependant_area,
      dependency.manifest_path
    ].join(" ").toLowerCase();
    return needles.some((needle) => haystack.includes(needle.toLowerCase()));
  });
}
const integrationFieldSets: Record<string, SettingFieldDefinition[]> = {
  home_assistant: [
    { key: "home_assistant_url", label: "URL" },
    { key: "home_assistant_token", label: "Long-lived token", type: "password" },
    { key: "home_assistant_gate_open_service", label: "Cover open service" },
    { key: "home_assistant_tts_service", label: "TTS service" },
    { key: "home_assistant_default_media_player", label: "Default media player" }
  ],
  apprise: [{ key: "apprise_urls", label: "Apprise URLs", type: "textarea", href: "https://github.com/caronc/apprise/wiki", help: "For Pushover use pover://USER_KEY@APP_TOKEN. The app also accepts pushover://USER_KEY/APP_TOKEN and normalizes it." }],
  discord: [
    { key: "discord_bot_token", label: "Bot token", type: "password" },
    { key: "discord_guild_allowlist", label: "Guild allowlist", type: "textarea", help: "One Discord server ID per line." },
    { key: "discord_channel_allowlist", label: "Channel allowlist", type: "textarea", help: "One channel ID per line. Empty denies guild-channel messages." },
    { key: "discord_user_allowlist", label: "User allowlist", type: "textarea", help: "One Discord user ID per line." },
    { key: "discord_role_allowlist", label: "Role allowlist", type: "textarea", help: "One Discord role ID per line." },
    { key: "discord_admin_role_ids", label: "Admin role IDs", type: "textarea", help: "Members with these roles can resolve Alfred confirmations." },
    { key: "discord_default_notification_channel_id", label: "Default notification channel" },
    { key: "discord_allow_direct_messages", label: "Allow direct messages", type: "select", options: ["false", "true"] },
    { key: "discord_require_mention", label: "Require mention", type: "select", options: ["true", "false"] }
  ],
  whatsapp: [
    { key: "whatsapp_enabled", label: "Enabled", type: "select", options: ["false", "true"] },
    { key: "whatsapp_access_token", label: "Access token", type: "password" },
    { key: "whatsapp_phone_number_id", label: "Phone Number ID" },
    { key: "whatsapp_business_account_id", label: "WhatsApp Business Account ID" },
    { key: "whatsapp_webhook_verify_token", label: "Webhook verify token", type: "password" },
    { key: "whatsapp_app_secret", label: "App secret", type: "password", help: "Required for incoming POST webhooks. IACS rejects unsigned WhatsApp payloads when WhatsApp is enabled." },
    { key: "whatsapp_graph_api_version", label: "Graph API version" },
    { key: "whatsapp_visitor_pass_template_name", label: "Visitor Pass template name" },
    { key: "whatsapp_visitor_pass_template_language", label: "Visitor Pass template language" }
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
  protectUpdateStatus: UnifiProtectUpdateStatus | null,
  icloudAccounts: ICloudCalendarAccount[],
  icloudError: string,
  discordStatus: DiscordStatus | null,
  discordError: string,
  whatsappStatus: WhatsAppStatus | null,
  whatsappError: string,
  dependencies: DependencyPackage[] = []
): IntegrationDefinition[] {
  const activeProvider = normalizeLlmProvider(values.llm_provider);
  const providerStatus = (key: string, secretKey?: string): Pick<IntegrationDefinition, "statusLabel" | "statusTone"> => {
    if (activeProvider === key) return { statusLabel: "Active", statusTone: "green" };
    if (secretKey && values[secretKey]) return { statusLabel: "Configured", statusTone: "blue" };
    if (key === "ollama" && values.ollama_base_url) return { statusLabel: "Configured", statusTone: "blue" };
    return { statusLabel: "Not Configured", statusTone: "gray" };
  };
  const hasDependencyUpdate = (key: string) => dependenciesForIntegrationKey(key, dependencies).some(dependencyIsActionableUpdate);
  const activeIcloudAccounts = icloudAccounts.filter((account) => account.is_active);
  const icloudNeedsAttention = activeIcloudAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));
  const homeAssistantConfigured = Boolean(status?.configured || values.home_assistant_url || values.home_assistant_token);
  const homeAssistantDegraded = Boolean(homeAssistantConfigured && (status?.degraded || status?.connected === false || status?.last_error));
  const protectUpdateAvailable = Boolean(protectStatus?.connected && protectUpdateStatus?.update_available) || hasDependencyUpdate("unifi_protect");
  const base: IntegrationDefinition[] = [
    { key: "home_assistant", title: "Home Assistant", description: "Gate control, mobile app notifications, TTS announcements, and state sync.", category: "access", icon: Home, fields: integrationFieldSets.home_assistant, statusLabel: status?.connected ? "Connected" : homeAssistantDegraded ? "Degraded" : homeAssistantConfigured ? "Configured" : "Not Configured", statusTone: status?.connected ? "green" : homeAssistantDegraded ? "red" : homeAssistantConfigured ? "blue" : "gray", updateAvailable: hasDependencyUpdate("home_assistant"), notificationChannels: ["mobile", "voice"] },
    { key: "esphome", title: "ESPHome", description: "Direct native API access for gate and garage-door covers.", category: "access", icon: Zap, fields: [], statusLabel: values.esphome_devices ? "Configured" : "Not Configured", statusTone: values.esphome_devices ? "blue" : "gray", updateAvailable: hasDependencyUpdate("esphome") },
    { key: "icloud_calendar", title: "iCloud Calendar", description: "Create Visitor Passes from calendar events marked Open Gate.", category: "access", icon: CalendarDays, fields: [], statusLabel: icloudError ? "Error" : icloudNeedsAttention ? "Needs Attention" : activeIcloudAccounts.length ? `${activeIcloudAccounts.length} Connected` : "Not Configured", statusTone: icloudError ? "red" : icloudNeedsAttention ? "amber" : activeIcloudAccounts.length ? "green" : "gray", updateAvailable: hasDependencyUpdate("icloud_calendar") },
    { key: "apprise", title: "Apprise", description: "Mobile and push notification fan-out.", category: "notifications", icon: Bell, fields: integrationFieldSets.apprise, statusLabel: values.apprise_urls ? "Configured" : "Not Configured", statusTone: values.apprise_urls ? "green" : "gray", updateAvailable: hasDependencyUpdate("apprise"), notificationChannels: ["mobile"] },
    { key: "discord", title: "Discord", description: "Bidirectional Alfred chat and Discord notification channels.", category: "notifications", icon: MessageCircle, fields: integrationFieldSets.discord, statusLabel: discordError ? "Error" : discordStatus?.connected ? "Connected" : discordStatus?.configured || values.discord_bot_token ? "Configured" : "Not Configured", statusTone: discordError ? "red" : discordStatus?.connected ? "green" : discordStatus?.configured || values.discord_bot_token ? "blue" : "gray", updateAvailable: hasDependencyUpdate("discord"), notificationChannels: ["discord"] },
    { key: "whatsapp", title: "WhatsApp", description: "Bidirectional Alfred chat and WhatsApp notification messages.", category: "notifications", icon: MessageCircle, fields: integrationFieldSets.whatsapp, statusLabel: whatsappError ? "Error" : whatsappStatus?.enabled && whatsappStatus?.configured ? "Enabled" : whatsappStatus?.configured || values.whatsapp_access_token || values.whatsapp_phone_number_id ? "Configured" : "Not Configured", statusTone: whatsappError ? "red" : whatsappStatus?.enabled && whatsappStatus?.configured ? "green" : whatsappStatus?.configured || values.whatsapp_access_token || values.whatsapp_phone_number_id ? "blue" : "gray", updateAvailable: hasDependencyUpdate("whatsapp"), notificationChannels: ["whatsapp"] },
    { key: "dvla", title: "DVLA Lookup", description: "Vehicle Enquiry Service API plate lookups.", category: "data", icon: Search, fields: integrationFieldSets.dvla, statusLabel: values.dvla_api_key ? "Configured" : "Not Configured", statusTone: values.dvla_api_key ? "green" : "gray", updateAvailable: hasDependencyUpdate("dvla") },
    { key: "unifi_protect", title: "UniFi Protect", description: "Camera snapshots, detection events, and AI image analysis.", category: "data", icon: Camera, fields: integrationFieldSets.unifi_protect, statusLabel: protectUpdateAvailable ? `Update ${protectUpdateStatus?.latest_version}` : protectStatus?.connected ? "Connected" : protectStatus?.configured || values.unifi_protect_host ? "Configured" : "Not Configured", statusTone: protectUpdateAvailable ? "amber" : protectStatus?.connected ? "green" : protectStatus?.configured || values.unifi_protect_host ? "blue" : "gray", updateAvailable: protectUpdateAvailable }
  ];
  return base.concat([
    { key: "openai", title: "OpenAI", description: "Responses API provider for tool-capable chat.", icon: Bot, secret: "openai_api_key", oauth: true },
    { key: "gemini", title: "Gemini", description: "Google Gemini provider.", icon: CircleDot, secret: "gemini_api_key", oauth: true },
    { key: "anthropic", title: "Anthropic", description: "Claude provider.", icon: MessageCircle, secret: "anthropic_api_key" },
    { key: "ollama", title: "Ollama", description: "Local model endpoint.", icon: Database }
  ].map(({ key, title, description, icon, secret, oauth }) => ({
    key, title, description, icon, oauth, category: "ai", fields: integrationFieldSets[key], ...providerStatus(key, secret), updateAvailable: hasDependencyUpdate(key)
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
    unifi_protect_port: "443",
    unifi_protect_verify_ssl: "false",
    unifi_protect_snapshot_width: "1280",
    unifi_protect_snapshot_height: "720",
    home_assistant_gate_entities: "[]",
    home_assistant_garage_door_entities: "[]",
    discord_guild_allowlist: "",
    discord_channel_allowlist: "",
    discord_user_allowlist: "",
    discord_role_allowlist: "",
    discord_admin_role_ids: "",
    discord_allow_direct_messages: "false",
    discord_require_mention: "true",
    whatsapp_enabled: "false",
    whatsapp_graph_api_version: "v25.0",
    whatsapp_visitor_pass_template_name: "iacs_visitor_welcome",
    whatsapp_visitor_pass_template_language: "en"
  };
  return definition.fields.reduce<Record<string, string>>((acc, field) => {
    const current = values[field.key];
    const currentOrDefault = current !== undefined && current !== null ? current : defaults[field.key] || "";
    if (secretSettingKeys.has(field.key)) {
      acc[field.key] = "";
    } else if (discordListSettingKeys.has(field.key)) {
      acc[field.key] = Array.isArray(current) ? current.map(String).join("\n") : stringifySetting(currentOrDefault);
    } else if (["home_assistant_gate_entities", "home_assistant_garage_door_entities"].includes(field.key) && typeof current === "object") {
      acc[field.key] = JSON.stringify(current ?? {}, null, 2);
    } else {
      acc[field.key] = stringifySetting(currentOrDefault);
    }
    return acc;
  }, {});
}
