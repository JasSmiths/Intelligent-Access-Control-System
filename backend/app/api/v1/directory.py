from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models import Group, Person, TimeSlot, Vehicle

router = APIRouter()


@router.get("/people")
async def list_people() -> list[dict]:
    async with AsyncSessionLocal() as session:
        people = (
            await session.scalars(
                select(Person)
                .options(selectinload(Person.group), selectinload(Person.vehicles))
                .order_by(Person.display_name)
            )
        ).all()

    return [
        {
            "id": str(person.id),
            "display_name": person.display_name,
            "group": person.group.name if person.group else None,
            "category": person.group.category.value if person.group else None,
            "is_active": person.is_active,
            "vehicles": [
                {
                    "id": str(vehicle.id),
                    "registration_number": vehicle.registration_number,
                    "description": vehicle.description,
                    "make": vehicle.make,
                    "model": vehicle.model,
                }
                for vehicle in person.vehicles
            ],
        }
        for person in people
    ]


@router.get("/vehicles")
async def list_vehicles() -> list[dict]:
    async with AsyncSessionLocal() as session:
        vehicles = (
            await session.scalars(
                select(Vehicle).options(selectinload(Vehicle.owner)).order_by(Vehicle.registration_number)
            )
        ).all()

    return [
        {
            "id": str(vehicle.id),
            "registration_number": vehicle.registration_number,
            "description": vehicle.description,
            "make": vehicle.make,
            "model": vehicle.model,
            "owner": vehicle.owner.display_name if vehicle.owner else None,
            "is_active": vehicle.is_active,
        }
        for vehicle in vehicles
    ]


@router.get("/groups")
async def list_groups() -> list[dict]:
    async with AsyncSessionLocal() as session:
        groups = (await session.scalars(select(Group).order_by(Group.name))).all()

    return [
        {
            "id": str(group.id),
            "name": group.name,
            "category": group.category.value,
            "subtype": group.subtype,
        }
        for group in groups
    ]


@router.get("/time-slots")
async def list_time_slots() -> list[dict]:
    async with AsyncSessionLocal() as session:
        slots = (await session.scalars(select(TimeSlot).order_by(TimeSlot.name))).all()

    return [
        {
            "id": str(slot.id),
            "name": slot.name,
            "kind": slot.kind.value,
            "days_of_week": slot.days_of_week,
            "start_time": slot.start_time.isoformat() if slot.start_time else None,
            "end_time": slot.end_time.isoformat() if slot.end_time else None,
            "is_active": slot.is_active,
        }
        for slot in slots
    ]
