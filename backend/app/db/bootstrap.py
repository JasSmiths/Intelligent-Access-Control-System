import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import settings
from app.core.logging import get_logger
from app.services.alfred.feedback import alfred_feedback_service
from app.services.auth_secret_management import migrate_encrypted_payloads_for_active_auth_secret
from app.services.access_devices import seed_access_devices_from_settings
from app.services.settings import seed_dynamic_settings

logger = get_logger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def init_database() -> None:
    """Prepare the database schema and seed dynamic settings."""

    if settings.auto_create_schema:
        await _run_alembic_upgrade_head()
        logger.info("database_migrations_ready")

    migration = await migrate_encrypted_payloads_for_active_auth_secret()
    if migration["settings"] or migration["icloud_accounts"]:
        logger.info("auth_secret_encrypted_payloads_migrated", extra=migration)

    await seed_dynamic_settings()
    await seed_access_devices_from_settings()
    await alfred_feedback_service.seed_default_lessons()
    await alfred_feedback_service.seed_default_eval_examples()

    if settings.seed_demo_data:
        logger.warning("seed_demo_data_ignored")


async def _run_alembic_upgrade_head() -> None:
    def upgrade() -> None:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        command.upgrade(config, "head")

    await asyncio.to_thread(upgrade)
