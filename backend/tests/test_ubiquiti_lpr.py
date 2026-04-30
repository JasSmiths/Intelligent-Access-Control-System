from app.modules.lpr.ubiquiti import extract_smart_zone_names, smart_zone_allowed


def test_extracts_smart_zone_from_direct_lpr_payload() -> None:
    zones = extract_smart_zone_names(
        {
            "registrationNumber": "DP25MOU",
            "smartDetectZone": "Default",
            "confidence": 99,
        }
    )

    assert zones == ["Default"]
    assert smart_zone_allowed(zones, ["default"])


def test_extracts_smart_zone_from_alarm_manager_trigger() -> None:
    zones = extract_smart_zone_names(
        {
            "alarm": {
                "triggers": [
                    {"key": "License Plate", "value": "AN08OFB"},
                    {"key": "Smart Detection Zone", "value": "Outer Driveway"},
                ]
            }
        }
    )

    assert zones == ["Outer Driveway"]
    assert not smart_zone_allowed(zones, ["default"])


def test_missing_smart_zone_stays_backward_compatible() -> None:
    zones = extract_smart_zone_names({"registrationNumber": "YY66NLC"})

    assert zones == []
    assert smart_zone_allowed(zones, ["default"])


def test_empty_or_wildcard_allowed_smart_zones_accept_every_zone() -> None:
    zones = ["Outer Driveway"]

    assert smart_zone_allowed(zones, [])
    assert smart_zone_allowed(zones, ["*"])
