import React from "react";
import type { NotificationVariable } from "../../api/workflows";
import { stringifyTemplateValue } from "./model";

const VariableRichTextEditor = React.lazy(() => import("../../VariableRichTextEditor"));

type TemplateEditorProps = {
  label: string;
  multiline?: boolean;
  value: string;
  variables: Array<NotificationVariable & { group: string }>;
  onChange: (value: string) => void;
};

class TemplateEditorBoundary extends React.Component<
  { children: React.ReactNode; fallback: React.ReactNode; resetKey: string },
  { hasError: boolean; retryCount: number }
> {
  state = { hasError: false, retryCount: 0 };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidUpdate(previousProps: { resetKey: string }) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, retryCount: 0 });
    }
  }

  componentDidCatch(error: unknown) {
    console.error("Notification template editor failed to render", error);
    if (this.state.retryCount > 0) return;
    window.requestAnimationFrame(() => {
      this.setState((current) => current.hasError
        ? { hasError: false, retryCount: current.retryCount + 1 }
        : null);
    });
  }

  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

export function SafeVariableRichTextEditor(props: TemplateEditorProps) {
  const variableResetKey = React.useMemo(() => props.variables.map((variable) => variable.name).join("\u0000"), [props.variables]);
  const safeProps = {
    ...props,
    value: stringifyTemplateValue(props.value),
  };
  return (
    <TemplateEditorBoundary fallback={<PlainTemplateEditor {...safeProps} />} resetKey={variableResetKey}>
      <React.Suspense fallback={<div className="loading-panel compact">Loading template editor</div>}>
        <VariableRichTextEditor {...safeProps} />
      </React.Suspense>
    </TemplateEditorBoundary>
  );
}

export function PlainTemplateEditor({ label, multiline = false, value, onChange }: TemplateEditorProps) {
  return (
    <label className="field variable-editor-field">
      <span>{label}</span>
      {multiline ? (
        <textarea
          className="template-editor-fallback"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={4}
        />
      ) : (
        <input
          className="template-editor-fallback"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}
