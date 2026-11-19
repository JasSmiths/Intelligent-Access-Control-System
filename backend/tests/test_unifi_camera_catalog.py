from app.modules.unifi_protect.camera_catalog import (
    CameraChannelStatus,
    CameraSmartZone,
    CameraWireCatalog,
    CameraWireMetadata,
)


def test_catalog_detaches_only_selected_fields_before_sdk_normalization() -> None:
    object_types = ["vehicle"]
    zone = {"id": 0, "name": "Gate", "objectTypes": object_types, "points": [[1, 2]]}
    channel = {
        "id": 0, "name": "High", "isRtspEnabled": True, "rtspAlias": "synthetic-stream-secret",
    }
    catalog = CameraWireCatalog.from_bootstrap([
        {
            "id": "gate", "smartDetectZones": [zone], "channels": [channel],
            "isMotionDetected": False, "featureFlags": {"hasPackageCamera": True},
        },
        {"id": "unadopted", "smartDetectZones": []},
    ])
    catalog.retain(["gate"])
    assert catalog.get("unadopted") is None

    object_types.clear()
    channel.clear()
    metadata = catalog.get("gate")
    assert metadata is not None
    assert metadata.zones == (CameraSmartZone(0, "Gate", ("vehicle",)),)
    assert metadata.channels == {"0": CameraChannelStatus("High", True, None)}
    assert metadata.is_motion_detected is False
    assert metadata.has_package_camera is True
    assert "synthetic-stream-secret" not in repr(metadata)


def test_catalog_merges_sparse_same_camera_updates_and_replaces_whole_lists() -> None:
    catalog = CameraWireCatalog.from_bootstrap([
        {
            "id": "gate",
            "smartDetectZones": [{"id": 2, "name": "Old"}, {"id": 3, "name": "Remove me"}],
            "channels": [{"id": 1, "name": "Medium"}],
            "featureFlags": {"hasPackageCamera": True},
        },
        {"id": "other", "smartDetectZones": [{"id": 2, "name": "Other"}]},
    ])
    other = catalog.get("other")
    captured = CameraWireMetadata.from_raw({
        "smartDetectZones": [{"id": 2, "name": "New"}], "isMotionDetected": True,
        "featureFlags": {"hasMic": True},
    })
    catalog.apply("update", "gate", captured)
    metadata = catalog.get("gate")
    assert metadata is not None
    assert metadata.zones == (CameraSmartZone(2, "New", ()),)
    assert metadata.channels == {"1": CameraChannelStatus("Medium", None, None)}
    assert metadata.is_motion_detected is True
    assert metadata.has_package_camera is True
    assert catalog.get("other") is other

    cleared = CameraWireMetadata.from_raw({
        "smartDetectZones": [], "channels": [], "featureFlags": {"hasPackageCamera": False},
    })
    catalog.apply("update", "gate", cleared)
    metadata = catalog.get("gate")
    assert metadata is not None
    assert metadata.zones == ()
    assert metadata.channels == {}
    assert metadata.has_package_camera is False
    assert metadata.is_motion_detected is True

    # A malformed replacement cannot silently retain last known zone assignments.
    catalog.apply("update", "gate", CameraWireMetadata.from_raw({"smartDetectZones": "invalid"}))
    metadata = catalog.get("gate")
    assert metadata is not None and metadata.zones is None


def test_removed_camera_cannot_be_recreated_by_a_late_update() -> None:
    catalog = CameraWireCatalog.from_bootstrap([])
    first = CameraWireMetadata.from_raw({"smartDetectZones": []})
    catalog.apply("add", "gate", first)
    assert catalog.get("gate") is first
    catalog.apply("remove", "gate", CameraWireMetadata.from_raw({}))
    catalog.apply("update", "gate", first)
    assert catalog.get("gate") is None
    replacement = CameraWireMetadata.from_raw({"featureFlags": {"hasPackageCamera": False}})
    catalog.apply("add", "gate", replacement)
    assert catalog.get("gate") is replacement
    assert replacement.zones is None
