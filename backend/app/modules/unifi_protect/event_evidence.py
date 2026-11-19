"""Read-only Protect thumbnail fields used by IACS's plate evidence and event views."""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from uiprotect.data import Event
from uiprotect.data.nvr import EventDetectedThumbnail, EventThumbnailGroup


class _PlateGroup(EventThumbnailGroup):
    id: str | None = None
    name: str | None = None
    confidence: int | None = None

    def unifi_dict(
        self, data: dict[str, Any] | None = None, exclude: set[str] | None = None
    ) -> dict[str, Any]:
        fields = super().unifi_dict(data=data, exclude=exclude)
        return {key: value for key, value in fields.items() if value is not None}


class _PlateThumbnail(EventDetectedThumbnail):
    cropped_id: str | None = None
    object_id: str | None = None
    coord: list[int] | None = None
    group: _PlateGroup | None = None

    def unifi_dict(
        self, data: dict[str, Any] | None = None, exclude: set[str] | None = None
    ) -> dict[str, Any]:
        fields = super().unifi_dict(data=data, exclude=exclude)
        for key in ("croppedId", "objectId", "coord"):
            if fields.get(key) is None:
                fields.pop(key, None)
        return fields

    def extend_delta(self, delta: dict[str, Any]) -> None:
        # The private SDK delta has already been converted to snake_case.
        for field in ("cropped_id", "object_id", "coord"):
            if (value := getattr(self, field)) is not None:
                delta[field] = value
        group = delta.get("group")
        if isinstance(group, dict) and self.group is not None:
            for field in ("id", "name", "confidence"):
                if (value := getattr(self.group, field)) is not None:
                    group[field] = value


@dataclass(frozen=True, slots=True)
class EventThumbnailEvidence:
    event_id: str
    row_count: int
    thumbnails: tuple[tuple[int, _PlateThumbnail], ...]


def capture_event_thumbnails(event_id: str, raw: object) -> EventThumbnailEvidence | None:
    """Capture the supplied thumbnail list before the SDK normalizes the wire object in place."""
    if not event_id or not isinstance(raw, dict) or raw.get("id", event_id) != event_id:
        return None
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        return None
    rows = metadata.get("detectedThumbnails")
    if not isinstance(rows, list):
        return None
    captured: list[tuple[int, _PlateThumbnail]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            # This SDK can discard non-object list entries; ordinals could drift.
            return None
        group = row.get("group")
        if group is not None and not isinstance(group, dict):
            continue
        if not (
            row.keys() & {"croppedId", "objectId", "coord"}
            or group and group.keys() & {"id", "name", "confidence"}
        ):
            continue
        coord = row.get("coord")
        # Only the four scalar bounds of a Protect bounding box belong here.
        if (
            not isinstance(coord, list) or len(coord) != 4
            or not all(type(v) is int for v in coord)
        ):
            coord = None
        try:
            match = (
                _PlateGroup(
                    id=group.get("id"),
                    name=group.get("name"),
                    matched_name=group.get("matchedName"),
                    confidence=group.get("confidence"),
                )
                if group is not None else None
            )
            thumbnail = _PlateThumbnail(
                type=row.get("type"),
                name=row.get("name"),
                confidence=row.get("confidence"),
                cropped_id=row.get("croppedId"),
                object_id=row.get("objectId"),
                coord=coord,
                group=match,
            )
            captured.append((index, thumbnail))
        except ValidationError:
            # Leave this accepted SDK row alone; independent plate groups stay usable.
            continue
    return EventThumbnailEvidence(event_id, len(rows), tuple(captured)) if captured else None


def apply_event_thumbnails(
    event: Event,
    evidence: EventThumbnailEvidence | None,
    *,
    changed_data: dict[str, Any] | None = None,
) -> None:
    """Enrich only the SDK-accepted list belonging to this event and this payload."""
    if evidence is None or event.id != evidence.event_id or event.metadata is None:
        return
    rows = event.metadata.detected_thumbnails
    if rows is None or len(rows) != evidence.row_count:
        return
    for index, captured in evidence.thumbnails:
        item = rows[index]
        if (
            item.type != captured.type
            or item.name != captured.name
            or item.confidence != captured.confidence
            or (item.group is None) != (captured.group is None)
            or (
                item.group is not None and captured.group is not None
                and item.group.matched_name != captured.group.matched_name
            )
        ):
            return
    delta_metadata = changed_data.get("metadata") if changed_data else None
    delta_rows = (
        delta_metadata.get("detected_thumbnails") if isinstance(delta_metadata, dict) else None
    )
    for index, captured in evidence.thumbnails:
        item = rows[index]
        rows[index] = captured.model_copy(update={
            "clock_best_wall": item.clock_best_wall,
            "attributes": item.attributes,
        })
        if isinstance(delta_rows, list) and len(delta_rows) == evidence.row_count:
            delta = delta_rows[index]
            if isinstance(delta, dict) and captured.type == delta.get("type"):
                captured.extend_delta(delta)
