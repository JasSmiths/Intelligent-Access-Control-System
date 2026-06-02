import { Loader2, MessageCircle, X } from "lucide-react";
import React from "react";
import type { MaintenanceStatus, UserAccount } from "../api/types";
import { displayUserName } from "../lib/format";
const loadChatWidgetModule = () => import("../views/ChatWidgetView").then((module) => ({ default: module.ChatWidget }));
const ChatWidget = React.lazy(loadChatWidgetModule);
export function DeferredChatWidget({
  currentUser,
  maintenanceStatus
}: {
  currentUser: UserAccount;
  maintenanceStatus: MaintenanceStatus | null;
}) {
  const [loaded, setLoaded] = React.useState(false);
  const teaserStorageKey = `iacs-chat-teaser-dismissed:${currentUser.id}`;
  const [showTeaser, setShowTeaser] = React.useState(() => sessionStorage.getItem(teaserStorageKey) !== "true");
  const firstName = currentUser.first_name || displayUserName(currentUser).split(" ")[0] || "there";
  React.useEffect(() => {
    setShowTeaser(sessionStorage.getItem(teaserStorageKey) !== "true");
  }, [teaserStorageKey]);
  const preloadChat = React.useCallback(() => {
    void loadChatWidgetModule();
  }, []);
  const openChat = React.useCallback(() => {
    setLoaded(true);
  }, []);
  const dismissTeaser = React.useCallback(() => {
    sessionStorage.setItem(teaserStorageKey, "true");
    setShowTeaser(false);
  }, [teaserStorageKey]);
  if (loaded) {
    return (
      <React.Suspense
        fallback={(
          <div className="chat-widget">
            <button className="chat-pill" disabled type="button" aria-label="Opening Alfred">
              <Loader2 className="spin" size={18} />
              <span>Alfred</span>
            </button>
          </div>
        )}
      >
        <ChatWidget currentUser={currentUser} initialOpen maintenanceStatus={maintenanceStatus} />
      </React.Suspense>
    );
  }
  return (
    <div className="chat-widget">
      {showTeaser ? (
        <div className="chat-teaser">
          <button className="teaser-close" onClick={dismissTeaser} type="button" aria-label="Dismiss chat prompt">
            <X size={16} />
          </button>
          <strong>Alfred is ready</strong>
          <p>Hi {firstName}, how can I help?</p>
        </div>
      ) : null}
      <button
        className="chat-pill"
        onClick={openChat}
        onFocus={preloadChat}
        onPointerEnter={preloadChat}
        type="button"
        aria-label="Open Alfred"
      >
        <MessageCircle size={18} />
        <span>Alfred</span>
      </button>
    </div>
  );
}
