from app.modules.lpr.ubiquiti import extract_plate_smart_zone_evidence, extract_smart_zone_names


def test_extracts_smart_zone_from_direct_lpr_payload() -> None:
    zones = extract_smart_zone_names(
        {
            "registrationNumber": "DP25MOU",
            "smartDetectZone": "Default",
            "confidence": 99,
        }
    )

    assert zones == ["Default"]
    evidence = extract_plate_smart_zone_evidence(
        {
            "registrationNumber": "DP25MOU",
            "smartDetectZone": "Default",
            "confidence": 99,
        },
        "DP25MOU",
    )
    assert evidence.smart_zones == ["Default"]
    assert evidence.present
    assert not evidence.explicit_empty


def test_extracts_plate_scoped_zone_id_from_alarm_manager_trigger() -> None:
    evidence = extract_plate_smart_zone_evidence(
        {
            "alarm": {
                "triggers": [
                    {
                        "key": "license_plate_unknown",
                        "value": "AN08OFB",
                        "device": "942A6FD09D64",
                        "zones": {"line": [], "zone": [2], "loiter": []},
                    },
                ]
            }
        },
        "AN08OFB",
    )

    assert evidence.smart_zones == ["2"]
    assert evidence.camera_identifier == "942A6FD09D64"
    assert evidence.present
    assert not evidence.explicit_empty


def test_empty_plate_zone_rejects_even_when_sibling_detection_has_zone() -> None:
    payload = {
        "alarm": {
            "triggers": [
                {
                    "key": "vehicle",
                    "value": "vehicle",
                    "device": "942A6FD09D64",
                    "zones": {"line": [], "zone": [2], "loiter": []},
                },
                {
                    "key": "license_plate_unknown",
                    "value": "TESTPL8",
                    "group": {"name": "TESTPL8"},
                    "device": "942A6FD09D64",
                    "zones": {"line": [], "zone": [], "loiter": []},
                },
            ]
        }
    }

    assert extract_smart_zone_names(payload) == ["2"]
    evidence = extract_plate_smart_zone_evidence(payload, "TESTPL8")

    assert evidence.smart_zones == []
    assert evidence.present
    assert evidence.explicit_empty


def test_missing_smart_zone_has_empty_diagnostic_evidence() -> None:
    zones = extract_smart_zone_names({"registrationNumber": "YY66NLC"})
    evidence = extract_plate_smart_zone_evidence({"registrationNumber": "YY66NLC"}, "YY66NLC")

    assert zones == []
    assert evidence.smart_zones == []
    assert not evidence.present
