from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import engine
from app.services.settings import seed_dynamic_settings

logger = get_logger(__name__)


async def init_database() -> None:
    """Create schema and seed dynamic settings for this early-phase deployment.

    Alembic migrations should take over once the schema stabilizes. Until then,
    startup creation keeps `docker compose up` useful on a clean machine.
    """

    if settings.auto_create_schema:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS first_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS last_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS profile_photo_data_url TEXT"))
            await conn.execute(
                text(
                    """
                    UPDATE people
                    SET
                        first_name = CASE
                            WHEN first_name = '' THEN split_part(display_name, ' ', 1)
                            ELSE first_name
                        END,
                        last_name = CASE
                            WHEN last_name = '' AND position(' ' in display_name) > 0
                                THEN trim(substr(display_name, position(' ' in display_name) + 1))
                            ELSE last_name
                        END
                    """
                )
            )
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS vehicle_photo_data_url TEXT"))
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
        logger.warning("seed_demo_data_ignored")
