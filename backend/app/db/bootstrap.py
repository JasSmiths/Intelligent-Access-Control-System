from datetime import time

from sqlalchemy import select, text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models import Group, Person, Presence, ScheduleAssignment, TimeSlot, Vehicle
from app.models.enums import GroupCategory, PresenceState, ScheduleKind
from app.services.settings import seed_dynamic_settings

logger = get_logger(__name__)


async def init_database() -> None:
    """Create schema and seed demo data for this early-phase deployment.

    Alembic migrations should take over once the schema stabilizes. Until then,
    startup creation keeps `docker compose up` useful on a clean machine.
    """

    if settings.auto_create_schema:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo_data_url TEXT"))
            await conn.execute(
                text(
                    """
                    UPDATE users
                    SET
                        first_name = CASE
                            WHEN first_name = '' THEN split_part(full_name, ' ', 1)
                            ELSE first_name
                        END,
                        last_name = CASE
                            WHEN last_name = '' AND position(' ' in full_name) > 0
                                THEN trim(substr(full_name, position(' ' in full_name) + 1))
                            ELSE last_name
                        END
                    """
                )
            )
        logger.info("database_schema_ready")

    await seed_dynamic_settings()

    if settings.seed_demo_data:
        await seed_demo_data()


async def seed_demo_data() -> None:
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(Person).where(Person.display_name == "Steph"))
        if existing:
            return

        family = Group(name="Family", category=GroupCategory.FAMILY)
        gardener_group = Group(
            name="Contractors - Gardener",
            category=GroupCategory.CONTRACTORS,
            subtype="Gardener",
        )

        all_day = TimeSlot(name="All Day Everyday", kind=ScheduleKind.ALWAYS)
        wed_morning = TimeSlot(
            name="Wed 08:00-12:00",
            kind=ScheduleKind.WEEKLY,
            days_of_week=[2],
            start_time=time(8, 0),
            end_time=time(12, 0),
        )

        steph = Person(display_name="Steph", group=family)
        bob = Person(display_name="Bob", group=gardener_group)

        session.add_all(
            [
                family,
                gardener_group,
                all_day,
                wed_morning,
                steph,
                bob,
                Vehicle(
                    owner=steph,
                    registration_number="STEPH26",
                    make="Tesla",
                    model="Model Y Dual Motor Long Range",
                    description="2026 Tesla Model Y Dual Motor Long Range",
                ),
                Vehicle(
                    owner=bob,
                    registration_number="BOB123",
                    make="Ford",
                    model="Transit",
                    description="Ford Transit",
                ),
                ScheduleAssignment(group=family, time_slot=all_day),
                ScheduleAssignment(person=bob, time_slot=wed_morning),
                Presence(person=steph, state=PresenceState.EXITED),
                Presence(person=bob, state=PresenceState.EXITED),
            ]
        )
        await session.commit()
        logger.info("demo_data_seeded")
