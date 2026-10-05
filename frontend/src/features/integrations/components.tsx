import { Activity, AlertTriangle, Check, CheckCircle2 } from "lucide-react";
import React from "react";
import { SettingField } from "../../lib/settings";
import type { SettingFieldDefinition } from "../../lib/settings";
import { IntegrationFeedback } from "./catalog";

export function ProviderSettingsGrid({
  fieldAction,
  fields,
  form,
  isConfiguredSecret,
  onChange,
  revealPasswordValue
}: {
  fieldAction?: (field: SettingFieldDefinition) => React.ReactNode;
  fields: SettingFieldDefinition[];
  form: Record<string, string>;
  isConfiguredSecret: (key: string) => boolean;
  onChange: (key: string, value: string) => void;
  revealPasswordValue?: (field: SettingFieldDefinition) => boolean;
}) {
  return (
    <div className="settings-form-grid">
      {fields.map((field) => (
        <SettingField
          action={fieldAction?.(field)}
          field={field}
          key={field.key}
          isConfiguredSecret={isConfiguredSecret(field.key)}
          revealPasswordValue={revealPasswordValue?.(field)}
          value={form[field.key] ?? ""}
          onChange={(value) => onChange(field.key, value)}
        />
      ))}
    </div>
  );
}

export function IntegrationFeedbackPanel({ feedback }: { feedback: IntegrationFeedback }) {
  const steps = ["Prepare", "Connect", "Validate"];
  const Icon = feedback.tone === "success" ? CheckCircle2 : feedback.tone === "error" ? AlertTriangle : Activity;
  return (
    <div className={`integration-feedback ${feedback.tone}`}>
      <div className="feedback-icon">
        <Icon size={18} />
      </div>
      <div className="feedback-copy">
        <strong>{feedback.title}</strong>
        <span>{feedback.detail}</span>
        {feedback.tone === "progress" ? (
          <div className="feedback-steps" aria-label="Connection test progress">
            {steps.map((step, index) => (
              <span
                className={index <= (feedback.activeStep ?? 0) ? "active" : ""}
                key={step}
              >
                {index < (feedback.activeStep ?? 0) ? <Check size={11} /> : null}
                {step}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
