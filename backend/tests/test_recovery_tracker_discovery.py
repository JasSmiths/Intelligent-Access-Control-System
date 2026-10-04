"""DB-free registry identity, privacy and read-only transport contracts."""
import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api.dependencies import admin_user
from app.api.v1 import integrations
from app.modules.home_assistant import client as vendor
from app.services import recovery_tracker_discovery as discovery

PERSON_ID = UUID("00000000-0000-0000-0000-000000000001")
NOTIFY = "notify.mobile_app_jane_s_iphone"
TRACKER = "device_tracker.renamed_without_notify_suffix"


def person(**kwargs):
    return SimpleNamespace(**({"id": PERSON_ID, "is_active": True,
        "home_assistant_mobile_app_notify_service": NOTIFY,
        "missed_exit_recovery_tracker_entity_id": None} | kwargs))


def state(entity_id=TRACKER, state_value="not_home", **attributes):
    return vendor.HomeAssistantState(entity_id, state_value, {
        "source_type": "gps", "friendly_name": "Unrelated renamed tracker",
        "latitude": 12.34567, "longitude": 45.6789, "gps_accuracy": 3,
    } | attributes)


def entity(**kwargs):
    return {"entity_id": TRACKER, "platform": "mobile_app", "device_id": "private-device-id",
            "config_entry_id": "private-entry-id", "disabled_by": None} | kwargs


def device(**kwargs):
    return {"id": "private-device-id", "name": "Jane's iPhone", "name_by_user": "NOT THE ORIGINAL",
            "manufacturer": "Apple", "model": "iPhone 16 Pro", "disabled_by": None,
            "identifiers": [["mobile_app", "private-app-id"]],
            "config_entries": ["private-entry-id"]} | kwargs


def result(*, people=None, states=None, entities=None, devices=None, services=None):
    device_rows = devices if devices is not None else [device()]
    canonical = {"Jane's iPhone": NOTIFY, "Jane s iPhone": NOTIFY,
                 "Jane’s iPhone": "notify.mobile_app_janes_iphone",
                 "Janes iPhone": "notify.mobile_app_janes_iphone",
                 "Ash’s MacBook Air": "notify.mobile_app_ashs_macbook_air",
                 "Jáné's iPhone": NOTIFY}
    normalized = {row["id"]: canonical[row["name"]] for row in device_rows}
    return discovery.build_discovery(
        people if people is not None else [person()], states if states is not None else [state()],
        services if services is not None else {NOTIFY},
        entities if entities is not None else [entity()], device_rows, device_services=normalized,
    )


def test_renamed_entity_and_device_override_do_not_affect_original_name_identity():
    found = result()
    assert found.mappings[0].status == "matched"
    assert found.mappings[0].suggested_tracker_entity_id == TRACKER
    assert found.trackers[0].name == "Unrelated renamed tracker"
    assert found.trackers[0].eligible is True


@pytest.mark.parametrize("changed_entity, changed_device, changed_state, reason", [
    ({"platform": "router"}, {}, {}, "not_mobile_app"),
    ({"config_entry_id": "another-private-entry"}, {}, {}, "registry_link_inconsistent"),
    ({"disabled_by": "user"}, {}, {}, "disabled"),
    ({}, {"disabled_by": "integration"}, {}, "disabled"),
    ({}, {"manufacturer": "Samsung", "model": "iPhone"}, {}, "not_iphone"),
    ({}, {"model": "iPad"}, {}, "not_iphone"),
    ({}, {}, {"source_type": "router"}, "not_gps"),
])
def test_registry_and_gps_eligibility(changed_entity, changed_device, changed_state, reason):
    found = result(entities=[entity(**changed_entity)], devices=[device(**changed_device)],
                   states=[state(**changed_state)])
    assert found.mappings[0].suggested_tracker_entity_id is None
    assert found.trackers[0].eligible is False
    assert found.trackers[0].reason == reason


@pytest.mark.parametrize("states", [[], [state(state_value="unavailable")], [state(state_value="unknown")]])
def test_missing_unavailable_unknown_tracker_never_suggested(states):
    found = result(states=states)
    assert found.mappings[0].status == "unavailable"
    assert found.mappings[0].suggested_tracker_entity_id is None


def test_device_name_collision_before_disabled_or_non_iphone_filter():
    found = result(devices=[device(), device(id="private-stale-id", name="Jane s iPhone",
                                            manufacturer="Samsung", disabled_by="user")])
    assert found.mappings[0].status == "ambiguous"
    assert found.mappings[0].reason == "device_name_collision"


def test_unicode_quote_original_name_collision_is_checked_before_eligibility():
    notify = "notify.mobile_app_janes_iphone"
    found = result(people=[person(home_assistant_mobile_app_notify_service=notify)], services={notify},
                   devices=[device(name="Jane’s iPhone"), device(id="stale-device", name="Janes iPhone", model="MacBook Air")])
    assert found.mappings[0].reason == "device_name_collision"


def test_unrelated_curly_quote_macbook_does_not_block_iphone_match():
    found = result(devices=[device(), device(id="macbook", name="Ash’s MacBook Air", model="MacBook Air")])
    assert found.mappings[0].status == "matched"


def test_multiple_eligible_trackers_ambiguous():
    found = result(states=[state(), state("device_tracker.second")],
                   entities=[entity(), entity(entity_id="device_tracker.second")])
    assert found.mappings[0].reason == "multiple_eligible_trackers"
    assert found.mappings[0].suggested_tracker_entity_id is None


def test_disabled_extra_tracker_does_not_compete_with_one_eligible_tracker():
    found = result(states=[state(), state("device_tracker.second")],
                   entities=[entity(), entity(entity_id="device_tracker.second", disabled_by="user")])
    assert found.mappings[0].status == "matched"


def test_multiple_mobile_registrations_on_device_are_ambiguous_before_eligibility():
    found = result(entities=[entity(), entity(entity_id="device_tracker.disabled", config_entry_id="second-entry", disabled_by="user")],
                   devices=[device(config_entries=["private-entry-id", "second-entry"])])
    assert found.mappings[0].reason == "multiple_mobile_app_registrations"


@pytest.mark.parametrize("entities, devices", [
    ([entity(), entity()], [device()]), ([entity()], [device(), device()]),
    ([entity()], [device(config_entries="private-entry-id")]),
    ([entity()], [device(config_entries=[None])]),
])
def test_malformed_duplicate_registry_identity_fails_closed(entities, devices):
    with pytest.raises(ValueError):
        result(entities=entities, devices=devices)


@pytest.mark.parametrize("active", [True, False])
def test_shared_notify_destination_including_inactive_owner_is_ambiguous(active):
    found = result(people=[person(), person(id=UUID(int=2), is_active=active)])
    assert found.mappings[0].reason == "notify_service_shared"
    assert len(found.mappings) == (2 if active else 1)


def test_existing_tracker_owned_by_another_person_is_blocked():
    found = result(people=[person(), person(id=UUID(int=2), is_active=False,
        home_assistant_mobile_app_notify_service=None, missed_exit_recovery_tracker_entity_id=TRACKER)])
    assert found.mappings[0].reason == "tracker_assigned_elsewhere"
    assert result(people=[person(missed_exit_recovery_tracker_entity_id=TRACKER)]).mappings[0].status == "matched"


def test_missing_saved_notify_service_and_nonadvertised_target():
    assert result(people=[person(home_assistant_mobile_app_notify_service=None)]).mappings[0].reason == "saved_notify_service_missing"
    assert result(services=set()).mappings[0].reason == "saved_notify_service_unavailable"
    assert result(devices=[]).mappings[0].reason == "device_not_found"


def test_unicode_name_normalized_by_home_assistant_participates_in_collision_check():
    found = result(devices=[device(), device(id="another-id", name="Jáné's iPhone")])
    assert found.mappings[0].reason == "device_name_collision"


def test_response_excludes_location_registry_identifiers_and_provider_diagnostics():
    serialized = result().model_dump_json()
    for private_value in ("latitude", "longitude", "12.34567", "45.6789", "gps_accuracy",
                          "private-device-id", "private-entry-id", "private-app-id", "name_by_user"):
        assert private_value not in serialized


class FakeSession:
    async def scalars(self, statement):
        self.statement = str(statement)
        return SimpleNamespace(all=lambda: [person()])


class FakeClient:
    async def config(self):
        return SimpleNamespace(home_assistant_url="https://synthetic.invalid", home_assistant_token="synthetic-token")

    async def list_states(self, **kwargs):
        return [state()]

    async def list_services(self, **kwargs):
        return [SimpleNamespace(service_id=NOTIFY)]

    async def list_recovery_registries(self, **kwargs):
        return [entity()], [device()]

    async def normalize_mobile_app_service_names(self, names, **kwargs):
        assert names == ["Jane's iPhone"]
        return [NOTIFY]


async def test_discovery_reads_people_without_mutations_and_protects_admin_route():
    session = FakeSession()  # Has no execute/add/commit/flush/write methods.
    found = await discovery.discover_recovery_trackers(session, FakeClient())
    assert found.mappings[0].status == "matched"
    assert "ORDER BY" in session.statement
    route = next(route for route in integrations.router.routes if route.path.endswith("/home-assistant/recovery-trackers"))
    assert admin_user in [dependency.call for dependency in route.dependant.dependencies]
    assert route.methods == {"GET"}


@pytest.mark.parametrize("error", [vendor.HomeAssistantError("PRIVATE-TOKEN PROVIDER-DIAGNOSTICS"), TimeoutError()])
async def test_registry_failure_retains_manual_list_without_suggestions(error):
    class Failed(FakeClient):
        async def list_recovery_registries(self, **kwargs):
            raise error
    found = await discovery.discover_recovery_trackers(FakeSession(), Failed())
    assert found.status == "unavailable"
    assert found.trackers[0].entity_id == TRACKER
    assert found.mappings[0].suggested_tracker_entity_id is None
    assert "PRIVATE" not in found.model_dump_json()


async def test_discovery_cancellation_propagates():
    class Cancelled(FakeClient):
        async def list_recovery_registries(self, **kwargs):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await discovery.discover_recovery_trackers(FakeSession(), Cancelled())


class Socket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def recv(self):
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return json.dumps(response)

    async def send(self, message):
        self.sent.append(json.loads(message))


def ws_responses():
    return [{"type": "auth_required"}, {"type": "auth_ok"},
            {"type": "result", "id": 1, "success": True, "result": [entity()]},
            {"type": "result", "id": 2, "success": True, "result": [device()]}]


async def test_vendor_one_shot_socket_only_reads_authenticated_registries(monkeypatch):
    socket = Socket(ws_responses())
    options = {}
    def connect(url, **kwargs):
        options.update(kwargs)
        assert url == "wss://synthetic.invalid/api/websocket"
        return socket
    monkeypatch.setattr(vendor.websockets, "connect", connect)
    found = await vendor.HomeAssistantClient().list_recovery_registries(runtime_config=await FakeClient().config())
    assert found == ([entity()], [device()])
    assert socket.closed is True
    assert [message["type"] for message in socket.sent] == ["auth", "config/entity_registry/list", "config/device_registry/list"]
    assert socket.sent[0]["access_token"] == "synthetic-token"
    assert options["proxy"] is None and options["max_size"] == 4 * 1024 * 1024


@pytest.mark.parametrize("index, response", [
    (0, {"type": "wrong"}), (1, {"type": "auth_invalid", "message": "PRIVATE TOKEN"}),
    (2, {"type": "result", "id": 1, "success": False, "error": "PRIVATE TOKEN"}),
    (2, {"type": "result", "id": 3, "success": True, "result": []}),
    (2, {"type": "result", "id": 1, "success": True, "result": {}}),
    (2, {"type": "result", "id": 1, "success": True, "result": ["bad row"]}),
    (3, TimeoutError("PRIVATE TOKEN")),
])
async def test_vendor_rejects_malformed_auth_registry_or_timeout_and_closes(monkeypatch, index, response):
    responses = ws_responses()
    responses[index] = response
    socket = Socket(responses)
    monkeypatch.setattr(vendor.websockets, "connect", lambda *args, **kwargs: socket)
    with pytest.raises(vendor.HomeAssistantError) as error:
        await vendor.HomeAssistantClient().list_recovery_registries(runtime_config=await FakeClient().config())
    assert str(error.value) == "Home Assistant registry discovery is unavailable."
    assert socket.closed is True
    assert "PRIVATE" not in str(error.value)


async def test_vendor_cancellation_closes_and_propagates(monkeypatch):
    socket = Socket([{"type": "auth_required"}, asyncio.CancelledError()])
    monkeypatch.setattr(vendor.websockets, "connect", lambda *args, **kwargs: socket)
    with pytest.raises(asyncio.CancelledError):
        await vendor.HomeAssistantClient().list_recovery_registries(runtime_config=await FakeClient().config())
    assert socket.closed is True


async def test_template_normalization_uses_constant_template_and_untrusted_names_only_as_variables(monkeypatch):
    client = vendor.HomeAssistantClient()
    calls = []
    config = await FakeClient().config()
    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return ["mobile_app_janes_iphone", "mobile_app_untrusted"]
    monkeypatch.setattr(client, "_request", request)
    names = ["Jane’s iPhone", "{{ states('device_tracker.private') }}"]
    found = await client.normalize_mobile_app_service_names(names, runtime_config=config)
    assert found == ["notify.mobile_app_janes_iphone", "notify.mobile_app_untrusted"]
    assert calls == [("POST", "/api/template", {
        "runtime_config": config,
        "json": {"template": "{{ names | map('slugify') | list | to_json }}",
                 "variables": {"names": [f"mobile_app_{name}" for name in names]}},
    })]


@pytest.mark.parametrize("values", [None, {}, [], ["bad-domain"], ["mobile_app_upperCase"], ["mobile_app_safe", "extra"], [123]])
async def test_template_normalization_rejects_malformed_server_result(monkeypatch, values):
    client = vendor.HomeAssistantClient()
    async def request(*args, **kwargs):
        return values
    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(vendor.HomeAssistantError):
        await client.normalize_mobile_app_service_names(["synthetic"])


@pytest.mark.parametrize("names", [[""], [None], ["x" * 256], ["synthetic"] * 1001])
async def test_template_normalization_bounded_inputs_never_make_request(names):
    with pytest.raises(vendor.HomeAssistantError):
        await vendor.HomeAssistantClient().normalize_mobile_app_service_names(names)


async def test_template_failure_does_not_produce_mapping_and_keeps_manual_list():
    class Failed(FakeClient):
        async def normalize_mobile_app_service_names(self, names, **kwargs):
            raise vendor.HomeAssistantError("PRIVATE PROVIDER ERROR")
    found = await discovery.discover_recovery_trackers(FakeSession(), Failed())
    assert found.status == "unavailable"
    assert found.trackers[0].entity_id == TRACKER
    assert found.mappings[0].suggested_tracker_entity_id is None
    assert "PRIVATE" not in found.model_dump_json()
