import { AlertTriangle } from "lucide-react";
import React from "react";

import { EmptyState } from "./shared";

export class RouteErrorBoundary extends React.Component<
  { children: React.ReactNode; view: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(_: unknown) {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("route_render_failed", error);
  }

  componentDidUpdate(previousProps: { view: string }) {
    if (previousProps.view !== this.props.view && this.state.failed) {
      this.setState({ failed: false });
    }
  }

  render() {
    if (this.state.failed) {
      return <EmptyState icon={AlertTriangle} label="This view could not be loaded." />;
    }
    return this.props.children;
  }
}
