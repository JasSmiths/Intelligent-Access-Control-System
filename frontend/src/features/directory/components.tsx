import { Car, ChevronDown, ChevronRight } from "lucide-react";
import React from "react";

import { titleCase } from "../../lib/format";
import { mediaSource } from "../../lib/media";
import type { Person, Vehicle } from "../../api/types";

import { personInitials } from "./model";
import type { DirectoryGroupSection } from "./model";

export function DirectoryGroupAccordion<T>({
  section,
  singularLabel,
  pluralLabel,
  expanded,
  onToggle,
  children
}: {
  section: DirectoryGroupSection<T>;
  singularLabel: string;
  pluralLabel: string;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const bodyId = React.useId();
  const count = section.items.length;
  return (
    <article className={expanded ? "directory-group expanded" : "directory-group"}>
      <button
        aria-controls={bodyId}
        aria-expanded={expanded}
        className="directory-group-header"
        onClick={onToggle}
        type="button"
      >
        <span className="directory-group-chevron" aria-hidden="true">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </span>
        <span className="directory-group-title">
          <strong>{section.name}</strong>
          {section.category ? <small>{titleCase(section.category)}</small> : null}
        </span>
        <span className="directory-group-count">{count} {count === 1 ? singularLabel : pluralLabel}</span>
      </button>
      {expanded ? (
        <div className="directory-group-body" id={bodyId}>
          {children}
        </div>
      ) : null}
    </article>
  );
}

export function PersonAvatar({ person, size = "normal" }: { person: Person; size?: "normal" | "large" }) {
  const imageSource = mediaSource(person.profile_photo_url, person.profile_photo_data_url, "thumb");
  return (
    <span className={size === "large" ? "profile-photo large" : "profile-photo"} aria-label={person.display_name}>
      {imageSource ? <img alt="" decoding="async" loading="lazy" src={imageSource} /> : personInitials(person)}
    </span>
  );
}

export function VehiclePhoto({ vehicle, size = "normal" }: { vehicle: Vehicle; size?: "normal" | "large" }) {
  const imageSource = mediaSource(vehicle.vehicle_photo_url, vehicle.vehicle_photo_data_url, "thumb");
  return (
    <span className={size === "large" ? "vehicle-photo large" : "vehicle-photo"} aria-label={vehicle.registration_number}>
      {imageSource ? <img alt="" decoding="async" loading="lazy" src={imageSource} /> : <Car size={size === "large" ? 24 : 18} />}
    </span>
  );
}
