"""Directory semantics and target-scale evidence on a disposable PostgreSQL database."""
import json
import os
from pathlib import Path
from time import perf_counter
import tracemalloc
import uuid

import pytest
from sqlalchemy import insert, select

# The harness owns this database and shares only its loopback network namespace.
assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
assert "@127.0.0.1:5432/iacs_validation_" in os.environ.get("IACS_DATABASE_URL", "")

from app.db.session import AsyncSessionLocal, engine
from app.models import Group, Person, Vehicle
from app.models.enums import GroupCategory
from app.schemas.directory import PersonResponse
from app.services.directory.errors import DirectoryOperationError
from app.services.directory.reads import PERSON_LOAD, list_groups, list_people, list_vehicles
from app.services.directory.representation import serialize_person


@pytest.mark.asyncio
async def test_target_scale_reads_and_keyset_semantics():
    prefix = uuid.uuid4().hex[:8]
    group_id = uuid.uuid4()
    people_ids = [uuid.UUID(int=index + 1) for index in range(1000)]
    vehicle_ids = [uuid.UUID(int=index + 1001) for index in range(2000)]
    try:
        async with AsyncSessionLocal() as session:
            session.add(Group(id=group_id, name=f"Synthetic {prefix}", category=GroupCategory.FAMILY))
            await session.flush()
            await session.execute(insert(Person), [dict(id=item_id, first_name="Synthetic", last_name=str(index), display_name=f"Synthetic {index // 2:04}", group_id=group_id, is_active=index % 2 == 0) for index, item_id in enumerate(people_ids)])
            await session.execute(insert(Vehicle), [dict(id=item_id, registration_number=f"{prefix.upper()}{index:04}", person_id=people_ids[index // 2]) for index, item_id in enumerate(vehicle_ids)])
            session.expunge_all()
            first = await list_people(session)
            second = await list_people(session, cursor=first.next_cursor)
            assert len(first.items) == len(second.items) == 50
            assert first.total == second.total == 1000
            assert not {item.id for item in first.items} & {item.id for item in second.items}
            assert [item.id for item in first.items[:2]] == [str(item_id) for item_id in people_ids[:2]]
            active = await list_people(session, active=True)
            assert active.total == 500 and all(item.is_active for item in active.items)
            selected = await list_people(session, ids=[people_ids[998], people_ids[998], uuid.uuid4()], limit=200)
            assert [item.id for item in selected.items] == [str(people_ids[998])]
            by_group = await list_people(session, group_id=group_id)
            assert by_group.total == 1000
            assert (await list_groups(session))[0].people_count == 1000
            by_plate = await list_people(session, q=f"{prefix.upper()}1998")
            assert [item.id for item in by_plate.items] == [str(people_ids[999])]
            registrations = await list_vehicles(session, registrations=[f" {prefix.upper()} 1999 ", "UNKNOWN"], limit=200)
            assert [item.id for item in registrations.items] == [str(vehicle_ids[-1])]
            assert (await list_vehicles(session, q="Synthetic 0499")).total == 4
            with pytest.raises(DirectoryOperationError, match="cursor"):
                await list_people(session, active=True, cursor=first.next_cursor)
            with pytest.raises(DirectoryOperationError, match="cursor"):
                await list_people(session, cursor="malformed")
            with pytest.raises(DirectoryOperationError, match="limited"):
                await list_people(session, limit=201)

            async def old_read():
                rows = (await session.scalars(select(Person).options(*PERSON_LOAD).order_by(Person.display_name))).all()
                return [PersonResponse(**serialize_person(person, include_media=False)) for person in rows]

            evidence = {}
            for label, operation in [("previous_unbounded_people", old_read), ("bounded_people_page", lambda: list_people(session)), ("bounded_vehicle_page", lambda: list_vehicles(session))]:
                samples = []
                peaks = []
                payload_bytes = 0
                for _ in range(3):
                    session.expunge_all()
                    tracemalloc.start()
                    started = perf_counter()
                    result = await operation()
                    samples.append(round((perf_counter() - started) * 1000, 2))
                    peaks.append(tracemalloc.get_traced_memory()[1])
                    tracemalloc.stop()
                    payload = [item.model_dump(mode="json") for item in result] if isinstance(result, list) else result.model_dump(mode="json")
                    payload_bytes = len(json.dumps(payload).encode())
                evidence[label] = {"timings_ms": samples, "peak_python_bytes": peaks, "payload_bytes": payload_bytes, "returned_items": len(result) if isinstance(result, list) else len(result.items)}
            print("DIRECTORY_TARGET_SCALE_EVIDENCE=" + json.dumps({"people": 1000, "vehicles": 2000, "measurements": evidence}, sort_keys=True))
            assert evidence["bounded_people_page"]["returned_items"] == 50
            assert evidence["bounded_people_page"]["payload_bytes"] < evidence["previous_unbounded_people"]["payload_bytes"] / 10
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_service_mutations_preserve_shared_assignments_and_durable_audit():
    from sqlalchemy import delete
    from app.models import AuditLog, User, VehiclePersonAssignment
    from app.models.enums import UserRole
    from app.schemas.directory import CreateGroupRequest, CreatePersonRequest, CreateVehicleRequest, UpdatePersonRequest, UpdateVehicleRequest
    from app.services.directory import groups, people, vehicles

    actor = User(username="synthetic-directory-" + uuid.uuid4().hex, full_name="Synthetic Directory Operator", password_hash="unused-synthetic", role=UserRole.ADMIN, is_active=True)
    group_id = None
    person_ids = []
    vehicle_id = None
    try:
        async with AsyncSessionLocal() as session:
            session.add(actor)
            await session.commit()
            group = await groups.add_group(CreateGroupRequest(name="Synthetic group " + uuid.uuid4().hex, category=GroupCategory.FAMILY), actor, session)
            group_id = uuid.UUID(group.id)
            first = await people.add_person(CreatePersonRequest(first_name=" Ash ", last_name=" Smith ", group_id=group_id), actor, session)
            person_ids.append(uuid.UUID(first.id))
            second = await people.add_person(CreatePersonRequest(first_name="Zoe", last_name="Smith"), actor, session)
            person_ids.append(uuid.UUID(second.id))
            shared = await vehicles.add_vehicle(CreateVehicleRequest(registration_number=" syn " + uuid.uuid4().hex[:10], person_ids=person_ids), actor, session)
            vehicle_id = uuid.UUID(shared.id)
            assert shared.person_id is None and set(shared.person_ids) == set(map(str, person_ids))
            single = await vehicles.update_vehicle(vehicle_id, UpdateVehicleRequest(person_ids=[person_ids[1]], is_active=False), actor, session)
            assert single.person_id == str(person_ids[1]) and single.person_ids == [str(person_ids[1])]
            restored = await people.update_person(person_ids[0], UpdatePersonRequest(vehicle_ids=[vehicle_id]), actor, session)
            assert restored.display_name == "Ash Smith" and [item.id for item in restored.vehicles] == [str(vehicle_id)]
            read_back = await list_vehicles(session, ids=[vehicle_id])
            assert read_back.items[0].person_id is None
            assert set(read_back.items[0].person_ids) == set(map(str, person_ids))
            assignments = (await session.scalars(select(VehiclePersonAssignment.person_id).where(VehiclePersonAssignment.vehicle_id == vehicle_id))).all()
            assert set(assignments) == set(person_ids)
            audits = (await session.scalars(select(AuditLog).where(AuditLog.actor_user_id == actor.id))).all()
            assert [row.action for row in audits].count("person.create") == 2
            assert {"group.create", "vehicle.create", "vehicle.update", "person.update"} <= {row.action for row in audits}
            assert all(row.actor_user_id == actor.id for row in audits)
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(AuditLog).where(AuditLog.actor_user_id == actor.id))
            if vehicle_id:
                await session.execute(delete(Vehicle).where(Vehicle.id == vehicle_id))
            if person_ids:
                await session.execute(delete(Person).where(Person.id.in_(person_ids)))
            if group_id:
                await session.execute(delete(Group).where(Group.id == group_id))
            await session.execute(delete(User).where(User.id == actor.id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_compact_directory_reads_defer_photo_bodies_without_lazy_queries():
    from sqlalchemy import event, inspect
    photo = "data:image/png;base64," + "X" * 100000
    try:
        async with AsyncSessionLocal() as session:
            person = Person(first_name="Synthetic", last_name="Photo", display_name="Synthetic Photo", profile_photo_data_url=photo)
            session.add(person)
            await session.flush()
            vehicle = Vehicle(registration_number="SYN" + uuid.uuid4().hex[:12].upper(), person_id=person.id, vehicle_photo_data_url=photo)
            session.add(vehicle)
            await session.flush()
            person_id, vehicle_id = person.id, vehicle.id
            session.expunge_all()
            loaded = []
            column_loads = []
            def record_loaded(_session, instance):
                loaded.append(instance)
            def record_execution(state):
                if state.is_column_load:
                    column_loads.append(str(state.statement))
            event.listen(session.sync_session, "loaded_as_persistent", record_loaded)
            event.listen(session.sync_session, "do_orm_execute", record_execution)
            try:
                people_page = await list_people(session, ids=[person_id])
                vehicles_page = await list_vehicles(session, ids=[vehicle_id])
                assert people_page.items[0].profile_photo_data_url is None
                assert people_page.items[0].profile_photo_url.startswith(f"/api/v1/people/{person_id}/photo")
                assert people_page.items[0].vehicles[0].vehicle_photo_data_url is None
                assert vehicles_page.items[0].vehicle_photo_data_url is None
                assert vehicles_page.items[0].vehicle_photo_url.startswith(f"/api/v1/vehicles/{vehicle_id}/photo")
                assert loaded
                for instance in loaded:
                    if isinstance(instance, Person):
                        assert "profile_photo_data_url" in inspect(instance).unloaded
                    elif isinstance(instance, Vehicle):
                        assert "vehicle_photo_data_url" in inspect(instance).unloaded
                assert column_loads == []
                full_people = await list_people(session, ids=[person_id], include_media=True)
                full_vehicles = await list_vehicles(session, ids=[vehicle_id], include_media=True)
                assert full_people.items[0].profile_photo_data_url == photo
                assert full_people.items[0].vehicles[0].vehicle_photo_data_url == photo
                assert full_vehicles.items[0].vehicle_photo_data_url == photo
                assert column_loads == []
            finally:
                event.remove(session.sync_session, "loaded_as_persistent", record_loaded)
                event.remove(session.sync_session, "do_orm_execute", record_execution)
            await session.rollback()
    finally:
        await engine.dispose()
