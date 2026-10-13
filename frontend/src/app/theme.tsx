import { Monitor, Moon, Sun } from "lucide-react";
import React from "react";
export type ThemeMode = "system" | "light" | "dark";
export function ThemeControl({ theme, setTheme }: { theme: ThemeMode; setTheme: (mode: ThemeMode) => void }) {
  const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <button className="icon-button theme-button" onClick={() => setTheme(next)} type="button" aria-label="Theme" title={`Theme: ${theme}. Switch to ${next}.`}>
      <Icon size={17} />
      <span>{theme}</span>
    </button>
  );
}
export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  // The document head selects the initial appearance before React loads.
  const [theme, setThemeState] = React.useState<ThemeMode>(() => {
    const initial = document.documentElement.dataset.theme;
    return initial === "light" || initial === "dark" ? initial : "system";
  });
  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("iacs-theme", theme);
    } catch {
      // Appearance controls still work when storage is unavailable.
    }
  }, [theme]);
  return [theme, setThemeState];
}
