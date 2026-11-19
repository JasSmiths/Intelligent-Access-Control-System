from types import SimpleNamespace

import pytest
from uiprotect.data import Event
from uiprotect.data.websocket import WSJSONPacketFrame, WSPacket, WSPacketFrameHeader

from app.modules.unifi_protect.client import serialize_unifi_event
from app.modules.unifi_protect.event_evidence import (
    apply_event_thumbnails,
    capture_event_thumbnails,
)
from app.modules.unifi_protect.wire_client import IacsProtectBootstrap, event_from_wire
from app.services.lpr_timing import extract_unifi_protect_lpr_observations


def event_payload(event_id, rows):
    return {
        "id": event_id,
        "modelKey": "event",
        "type": "smartDetectZone",
        "start": 1_795_070_000_000,
        "smartDetectTypes": ["licensePlate"],
        "metadata": {"detectedThumbnails": rows},
    }


def plate_thumbnail(**changes):
    return {
        "type": "licensePlate",
        "clockBestWall": 1_795_070_001_000,
        "croppedId": "crop-1",
        "objectId": "object-1",
        "coord": [1, 2, 10, 12],
        "confidence": 99,
        "group": {"id": "group-1", "name": "AB12 CDE", "confidence": 0},
        **changes,
    }


def update(event, payload):
    evidence = capture_event_thumbnails(event.id, payload)
    delta = Event.unifi_dict_to_dict(payload)
    event.update_from_dict(delta)
    apply_event_thumbnails(event, evidence, changed_data=delta)
    return delta


def serialized_rows(event):
    return serialize_unifi_event(event)["metadata"]["detectedThumbnails"]


def event_packet(action, payload):
    frames = []
    for packet_type, fields in enumerate((
        {"action": action, "id": "event-1", "modelKey": "event", "newUpdateId": "stamp"},
        payload,
    ), start=1):
        frame = WSJSONPacketFrame()
        frame.header = WSPacketFrameHeader(packet_type, 1, 0, 0, 0)
        frame.data = fields
        frames.append(frame.packed)
    return WSPacket(b"".join(frames))


@pytest.mark.parametrize("confidence", [None, 0, 76])
def test_group_only_plate_keeps_its_own_name_and_score(confidence):
    group = {"id": "group-1", "name": "AB12 CDE", "unexpectedSecret": "omitted"}
    if confidence is not None:
        group["confidence"] = confidence
    wire_thumbnail = plate_thumbnail(group=group)
    raw = event_payload("event-1", [wire_thumbnail])
    event = event_from_wire(SimpleNamespace(), raw)

    row, = serialized_rows(event)
    assert row["group"]["name"] == "AB12 CDE"
    assert row["group"].get("confidence") == confidence
    assert "unexpectedSecret" not in row["group"]
    assert row["confidence"] == 99
    assert (row["croppedId"], row["objectId"], row["coord"]) == (
        "crop-1", "object-1", [1, 2, 10, 12]
    )
    observations = extract_unifi_protect_lpr_observations(SimpleNamespace(
        new_obj=event, changed_data={}, action="update"
    ))
    observed, = [
        item for item in observations if item.source_detail == "event_thumbnail.group.name"
    ]
    assert observed.event_id == "event-1"
    assert observed.registration_number == "AB12CDE"
    assert observed.confidence == confidence


def test_partial_updates_keep_evidence_and_replacements_clear_it():
    event = Event.from_unifi_dict(**event_payload("event-1", []))
    delta = update(event, {"metadata": {"detectedThumbnails": [plate_thumbnail()]}})
    assert delta["metadata"]["detected_thumbnails"][0]["group"] == {
        "id": "group-1", "name": "AB12 CDE", "confidence": 0,
    }
    update(event, {"score": 85})
    update(event, {"metadata": {"lightId": {"text": "light-1"}}})
    assert event.metadata.light_id == "light-1"
    assert serialized_rows(event)[0]["group"]["name"] == "AB12 CDE"
    update(event, {"metadata": {"detectedThumbnails": [
        {"type": "licensePlate", "group": {"matchedName": "ZX98 YTR"}}
    ]}})
    replacement, = serialized_rows(event)
    assert replacement["group"] == {"matchedName": "ZX98 YTR"}
    assert "croppedId" not in replacement
    update(event, {"metadata": {"detectedThumbnails": []}})
    assert serialized_rows(event) == []
    update(event, {"metadata": None})
    assert event.metadata is None


def test_enrichment_retains_sdk_attributes_and_skips_unusable_optional_data():
    attrs = {"zone": [1, 2], "vehicleType": {"val": "sedan", "confidence": 92}}
    direct = {"type": "vehicle", "confidence": 83, "attributes": attrs}
    minimal = event_payload("event-1", [direct])
    assert capture_event_thumbnails("event-1", minimal) is None
    raw = event_payload("event-1", [
        direct,
        plate_thumbnail(attributes=attrs, coord=[1] * 4096),
        {"type": "vehicle", "croppedId": {"unexpected": "invalid identifier"}},
    ])
    evidence = capture_event_thumbnails("event-1", raw)
    event = Event.from_unifi_dict(**raw)
    original = list(event.metadata.detected_thumbnails)
    apply_event_thumbnails(event, evidence)
    native, enriched, malformed = event.metadata.detected_thumbnails
    assert native is original[0] and malformed is original[2]
    assert enriched.attributes is original[1].attributes
    row = serialized_rows(event)[1]
    assert row["clockBestWall"] == 1_795_070_001_000
    assert row["attributes"]["zone"] == [1, 2]
    assert row["attributes"]["vehicleType"] == {"val": "sedan", "confidence": 92}
    assert row["group"]["name"] == "AB12 CDE"
    assert "coord" not in row


def test_private_sdk_packets_admit_group_evidence_only_for_the_accepted_event():
    bootstrap = IacsProtectBootstrap.model_construct(cameras={}, last_update_id="initial")
    added = bootstrap.process_ws_packet(
        event_packet("add", event_payload("event-1", [plate_thumbnail()]))
    )
    assert added.new_obj is bootstrap.events["event-1"]
    assert serialized_rows(added.new_obj)[0]["group"]["confidence"] == 0
    assert added.changed_data["metadata"]["detected_thumbnails"][0]["group"]["name"] == "AB12 CDE"
    replacement = plate_thumbnail(group={"matchedName": "ZX98 YTR", "confidence": 62})
    updated = bootstrap.process_ws_packet(
        event_packet("update", {"metadata": {"detectedThumbnails": [replacement]}})
    )
    assert serialized_rows(updated.new_obj)[0]["group"] == {
        "matchedName": "ZX98 YTR", "confidence": 62
    }
    assert updated.changed_data["metadata"]["detected_thumbnails"][0]["group"]["confidence"] == 62
    observations = extract_unifi_protect_lpr_observations(updated)
    assert any(
        item.registration_number == "ZX98YTR" and item.confidence == 62 for item in observations
    )
    assert all(item.registration_number != "AB12CDE" for item in observations)


def test_evidence_must_match_event_and_all_thumbnail_rows():
    raw = event_payload("event-1", [plate_thumbnail(), plate_thumbnail(name="second")])
    assert capture_event_thumbnails("other", raw) is None
    evidence = capture_event_thumbnails("event-1", raw)
    other = Event.from_unifi_dict(**event_payload(
        "other", [plate_thumbnail(), plate_thumbnail(name="second")]
    ))
    apply_event_thumbnails(other, evidence)
    assert "croppedId" not in serialized_rows(other)[0]
    different = Event.from_unifi_dict(**event_payload(
        "event-1", [plate_thumbnail(), plate_thumbnail(name="changed")]
    ))
    apply_event_thumbnails(different, evidence)
    assert "croppedId" not in serialized_rows(different)[0]
