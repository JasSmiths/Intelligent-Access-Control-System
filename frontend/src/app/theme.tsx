import { Monitor, Moon, Sun } from "lucide-react";
import React from "react";
export type ThemeMode = "system" | "light" | "dark";
export function ThemeControl({ theme, setTheme }: { theme: ThemeMode; setTheme: (mode: ThemeMode) => void }) {
  const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <button className="icon-button theme-button" onClick={() => setTheme(next)} type="button" aria-label="Theme">
      <Icon size={17} />
      <span>{theme}</span>
    </button>
  );
}
export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  const [theme, setThemeState] = React.useState<ThemeMode>(() => (localStorage.getItem("iacs-theme") as ThemeMode | null) ?? "system");
  React.useEffect(() => {
    localStorage.setItem("iacs-theme", theme);
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  return [theme, setThemeState];
}
