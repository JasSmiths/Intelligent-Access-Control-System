import React from "react";


import { directoryGroupDefaultOpen } from "./model";
import type { DirectoryGroupSection } from "./model";

export function useDirectoryGroupOpenState<T>(sections: DirectoryGroupSection<T>[]) {
  const [openGroups, setOpenGroups] = React.useState<Record<string, boolean>>({});
  const defaultOpenById = React.useMemo(
    () => new Map(sections.map((section) => [section.id, directoryGroupDefaultOpen(section)])),
    [sections]
  );

  React.useEffect(() => {
    setOpenGroups((current) => {
      let changed = false;
      const next = { ...current };
      for (const section of sections) {
        if (next[section.id] === undefined) {
          next[section.id] = directoryGroupDefaultOpen(section);
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [sections]);

  const toggleGroup = React.useCallback((sectionId: string) => {
    setOpenGroups((current) => ({
      ...current,
      [sectionId]: !(current[sectionId] ?? defaultOpenById.get(sectionId) ?? false)
    }));
  }, [defaultOpenById]);

  return { openGroups, toggleGroup };
}
