"""General Alfred tool handlers.

These handlers live beside the General tool catalog so new entity/presence
tools can grow without making the legacy tools facade larger.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai import tools as tools_facade
from app.models import Group, Person, Presence, User, Vehicle


async def resolve_human_entity(arguments: dict[str, Any]) -> dict[str, Any]:
    query_text = str(arguments.get("query") or "").strip()
    if not query_text:
        return {"status": "not_found", "query": query_text, "matches": [], "error": "query is required."}

    requested_types = arguments.get("entity_types")
    if isinstance(requested_types, list) and requested_types:
        entity_types = {
            str(item).strip().lower()
            for item in requested_types
            if str(item).strip().lower() in {"person", "vehicle", "group", "device", "visitor_pass"}
        }
    else:
        entity_types = {"person", "vehicle", "group", "device", "visitor_pass"}
    include_inactive = bool(arguments.get("include_inactive"))
    query_key = tools_facade._entity_match_key(query_text)
    matches: list[dict[str, Any]] = []

    async with tools_facade.AsyncSessionLocal() as session:
        if "person" in entity_types:
            people = (
                await session.scalars(
                    select(Person)
                    .options(selectinload(Person.group), selectinload(Person.vehicles))
                    .order_by(Person.display_name)
                )
            ).all()
            for person in people:
                if not include_inactive and not person.is_active:
                    continue
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        person.display_name,
                        person.first_name,
                        person.last_name,
                        person.notes,
                        person.group.name if person.group else "",
                        " ".join(vehicle.registration_number for vehicle in person.vehicles),
                        " ".join(str(vehicle.make or "") for vehicle in person.vehicles),
                        " ".join(str(vehicle.model or "") for vehicle in person.vehicles),
                        " ".join(str(vehicle.color or "") for vehicle in person.vehicles),
                    ]
                )
                score = tools_facade._entity_match_score(query_key, haystack, exact_value=person.display_name)
                if score:
                    matches.append(
                        tools_facade._compact_observation(
                            {
                                "type": "person",
                                "score": score,
                                "id": str(person.id),
                                "display_name": person.display_name,
                                "group": person.group.name if person.group else None,
                                "is_active": person.is_active,
                                "vehicle_ids": [str(vehicle.id) for vehicle in person.vehicles],
                                "registration_numbers": [vehicle.registration_number for vehicle in person.vehicles],
                            }
                        )
                    )

        if "vehicle" in entity_types:
            vehicles = (
                await session.scalars(
                    select(Vehicle)
                    .options(selectinload(Vehicle.owner), selectinload(Vehicle.schedule))
                    .order_by(Vehicle.registration_number)
                )
            ).all()
            plate_query = tools_facade.normalize_registration_number(query_text)
            for vehicle in vehicles:
                if not include_inactive and not vehicle.is_active:
                    continue
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        vehicle.registration_number,
                        vehicle.make,
                        vehicle.model,
                        vehicle.color,
                        vehicle.description,
                        vehicle.owner.display_name if vehicle.owner else "",
                    ]
                )
                score = tools_facade._entity_match_score(query_key, haystack, exact_value=vehicle.registration_number)
                if plate_query and plate_query == vehicle.registration_number:
                    score = max(score, 100)
                elif plate_query and plate_query in vehicle.registration_number:
                    score = max(score, 90)
                if score:
                    matches.append(
                        tools_facade._compact_observation(
                            {
                                "type": "vehicle",
                                "score": score,
                                "id": str(vehicle.id),
                                "registration_number": vehicle.registration_number,
                                "make": vehicle.make,
                                "model": vehicle.model,
                                "color": vehicle.color,
                                "owner_id": str(vehicle.person_id) if vehicle.person_id else None,
                                "owner": vehicle.owner.display_name if vehicle.owner else None,
                                "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
                                "schedule_name": vehicle.schedule.name if vehicle.schedule else None,
                                "is_active": vehicle.is_active,
                            }
                        )
                    )

        if "group" in entity_types:
            groups = (await session.scalars(select(Group).order_by(Group.name))).all()
            for group in groups:
                haystack = " ".join(str(value or "") for value in [group.name, group.category.value, group.subtype, group.description])
                score = tools_facade._entity_match_score(query_key, haystack, exact_value=group.name)
                if score:
                    matches.append(
                        tools_facade._compact_observation(
                            {
                                "type": "group",
                                "score": score,
                                "id": str(group.id),
                                "name": group.name,
                                "category": group.category.value,
                                "subtype": group.subtype,
                            }
                        )
                    )

        if "visitor_pass" in entity_types:
            config = await tools_facade.get_runtime_config()
            service = tools_facade.get_visitor_pass_service()
            changed = await service.refresh_statuses(session=session, publish=False)
            if changed:
                await session.commit()
            visitor_passes = await service.list_passes(session, statuses=None, search=query_text, limit=10)
            for visitor_pass in visitor_passes:
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        visitor_pass.visitor_name,
                        visitor_pass.number_plate,
                        visitor_pass.vehicle_make,
                        visitor_pass.vehicle_colour,
                        visitor_pass.status.value,
                    ]
                )
                score = tools_facade._entity_match_score(query_key, haystack, exact_value=visitor_pass.visitor_name)
                if visitor_pass.status.value in {"cancelled", "expired"}:
                    score = max(0, score - 25)
                if score:
                    payload = tools_facade._visitor_pass_agent_payload(visitor_pass, config.site_timezone)
                    payload.update(
                        {
                            "type": "visitor_pass",
                            "score": score,
                            "display_name": visitor_pass.visitor_name,
                            "visitor_pass_id": str(visitor_pass.id),
                        }
                    )
                    matches.append(tools_facade._compact_observation(payload))

    if "device" in entity_types:
        config = await tools_facade.get_runtime_config()
        device_rows = [
            ("gate", entity)
            for entity in list(getattr(config, "home_assistant_gate_entities", None) or [])
            if isinstance(entity, dict)
        ] + [
            ("garage_door", entity)
            for entity in list(getattr(config, "home_assistant_garage_door_entities", None) or [])
            if isinstance(entity, dict)
        ]
        for kind, entity in device_rows:
            if not include_inactive and entity.get("enabled") is False:
                continue
            name = str(entity.get("name") or entity.get("entity_id") or "")
            haystack = f"{name} {entity.get('entity_id') or ''} {kind.replace('_', ' ')}"
            score = tools_facade._entity_match_score(query_key, haystack, exact_value=name)
            if score:
                matches.append(
                    tools_facade._compact_observation(
                        {
                            "type": "device",
                            "score": score,
                            "kind": kind,
                            "entity_id": str(entity.get("entity_id") or ""),
                            "name": name,
                            "enabled": bool(entity.get("enabled", True)),
                            "schedule_id": entity.get("schedule_id"),
                        }
                    )
                )

    matches = sorted(
        matches,
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("type") or ""),
            str(item.get("display_name") or item.get("name") or item.get("registration_number") or ""),
        ),
    )
    if not matches:
        return {"status": "not_found", "query": query_text, "entity_types": sorted(entity_types), "matches": []}

    top_score = int(matches[0].get("score") or 0)
    top_matches = [match for match in matches if int(match.get("score") or 0) >= top_score - 5]
    status = "unique" if len(top_matches) == 1 and top_score >= 70 else "ambiguous"
    return {
        "status": status,
        "query": query_text,
        "entity_types": sorted(entity_types),
        "match": matches[0] if status == "unique" else None,
        "matches": matches[:10],
    }


async def query_presence(arguments: dict[str, Any]) -> dict[str, Any]:
    person_filter = tools_facade._normalize(arguments.get("person"))
    config = await tools_facade.get_runtime_config()
    async with tools_facade.AsyncSessionLocal() as session:
        query = select(Presence).options(selectinload(Presence.person)).order_by(Presence.updated_at.desc())
        rows = (await session.scalars(query)).all()

    records = [
        {
            "person": row.person.display_name,
            "state": row.state.value,
            "last_changed_at": tools_facade._agent_datetime_iso(row.last_changed_at, config.site_timezone) if row.last_changed_at else None,
            "last_changed_at_display": tools_facade._agent_datetime_display(row.last_changed_at, config.site_timezone) if row.last_changed_at else None,
        }
        for row in rows
        if not person_filter or person_filter in row.person.display_name.lower()
    ]
    return {"presence": records, "timezone": config.site_timezone}


async def get_system_users(arguments: dict[str, Any]) -> dict[str, Any]:
    include_inactive = bool(arguments.get("include_inactive"))
    async with tools_facade.AsyncSessionLocal() as session:
        query = select(User).order_by(User.first_name, User.last_name)
        users = (await session.scalars(query)).all()

    records = [
        {
            "full_name": user.full_name,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "role": user.role.value,
            "is_active": user.is_active,
        }
        for user in users
        if include_inactive or user.is_active
    ]
    return {"users": records, "count": len(records)}
