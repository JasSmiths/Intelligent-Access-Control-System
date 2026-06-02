import { X } from "lucide-react";
import React from "react";
import { notificationEventLabel } from "../lib/format";
import { Badge } from "../ui/primitives";
import type { NotificationToast, NotificationToastAction } from "./realtimeEvents";
export function NotificationToastStack({
  notifications,
  onAction,
  onDismiss
}: {
  notifications: NotificationToast[];
  onAction: (notificationId: string, action: NotificationToastAction) => Promise<void>;
  onDismiss: (id: string) => void;
}) {
  const [busyAction, setBusyAction] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<Record<string, string>>({});
  React.useEffect(() => {
    if (!notifications.length) return undefined;
    const timers = notifications.filter((notification) => !notification.actions?.length).map((notification) =>
      window.setTimeout(() => onDismiss(notification.id), 9000)
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [notifications, onDismiss]);
  if (!notifications.length) return null;
  return (
    <div className="notification-toast-stack" aria-live="polite">
      {notifications.map((notification) => (
        <article className={`notification-toast ${notification.severity}`} key={notification.id}>
          {notification.snapshot_url ? <img alt="" src={notification.snapshot_url} /> : null}
          <div>
            <div className="notification-toast-head">
              <Badge tone={notification.severity === "critical" ? "red" : notification.severity === "warning" ? "amber" : "blue"}>
                {notificationEventLabel(notification.event_type)}
              </Badge>
              <button className="icon-button" onClick={() => onDismiss(notification.id)} type="button" aria-label="Dismiss notification">
                <X size={14} />
              </button>
            </div>
            <strong>{notification.title}</strong>
            <p>{notification.body}</p>
            {notification.actions?.length ? (
              <div className="notification-toast-actions">
                {notification.actions.map((action) => {
                  const actionKey = `${notification.id}:${action.id}`;
                  return (
                    <button
                      className={action.id === "deny" ? "secondary-button danger" : "secondary-button"}
                      disabled={busyAction !== null}
                      key={action.id}
                      onClick={() => {
                        setBusyAction(actionKey);
                        setActionError((current) => ({ ...current, [notification.id]: "" }));
                        onAction(notification.id, action)
                          .catch((error) => setActionError((current) => ({
                            ...current,
                            [notification.id]: error instanceof Error ? error.message : "Unable to complete action"
                          })))
                          .finally(() => setBusyAction(null));
                      }}
                      type="button"
                    >
                      {busyAction === actionKey ? "Working..." : action.label}
                    </button>
                  );
                })}
              </div>
            ) : null}
            {actionError[notification.id] ? <small className="notification-toast-error">{actionError[notification.id]}</small> : null}
          </div>
        </article>
      ))}
    </div>
  );
}
