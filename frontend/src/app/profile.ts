import React from "react";
import { api, isAbortError } from "../api/client";
import type { ProfilePreferences, UserAccount } from "../api/types";
export function useProfilePreferences(user: UserAccount | null): [ProfilePreferences, (next: Partial<ProfilePreferences>) => void] {
  const [preferences, setPreferences] = React.useState<ProfilePreferences>(() => {
    try {
      const stored = localStorage.getItem("iacs-profile-preferences");
      return { sidebarCollapsed: stored ? Boolean(JSON.parse(stored).sidebarCollapsed) : false };
    } catch {
      return { sidebarCollapsed: false };
    }
  });
  const saveTimerRef = React.useRef<number | null>(null);
  const saveAbortRef = React.useRef<AbortController | null>(null);
  const saveSequenceRef = React.useRef(0);
  React.useEffect(() => {
    if (!user?.preferences) return;
    const profilePreferences = {
      sidebarCollapsed: Boolean(user.preferences.sidebarCollapsed)
    };
    setPreferences(profilePreferences);
    localStorage.setItem("iacs-profile-preferences", JSON.stringify(profilePreferences));
  }, [user?.id, user?.preferences]);
  React.useEffect(() => () => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveAbortRef.current?.abort();
  }, []);
  const updatePreferences = React.useCallback((next: Partial<ProfilePreferences>) => {
    setPreferences((current) => {
      const merged = { ...current, ...next };
      localStorage.setItem("iacs-profile-preferences", JSON.stringify(merged));
      if (user) {
        if (saveTimerRef.current !== null) {
          window.clearTimeout(saveTimerRef.current);
        }
        saveTimerRef.current = window.setTimeout(() => {
          saveTimerRef.current = null;
          saveAbortRef.current?.abort();
          const controller = new AbortController();
          const sequence = saveSequenceRef.current + 1;
          saveSequenceRef.current = sequence;
          saveAbortRef.current = controller;
          api.patch<UserAccount>("/api/v1/auth/me/preferences", merged, { signal: controller.signal })
            .catch((error: unknown) => {
              if (!isAbortError(error)) {
                console.warn("Failed to save profile preferences", error);
              }
            })
            .finally(() => {
              if (saveSequenceRef.current === sequence && saveAbortRef.current === controller) {
                saveAbortRef.current = null;
              }
            });
        }, 350);
      }
      return merged;
    });
  }, [user]);
  return [preferences, updatePreferences];
}
