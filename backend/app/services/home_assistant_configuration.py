"""Current HA setting projection under the caller's already-acquired auth-root fence."""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting
from app.modules.home_assistant.input_boolean_contracts import FrozenHaConnection
from app.services.ha_presence_contracts import HaConfigurationUnavailable, HaCoordinationBusy
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, decrypted_value

HA_SETTING_KEYS = ("home_assistant_token", "home_assistant_url")


async def lock_home_assistant_connection(session: AsyncSession) -> FrozenHaConnection:
    """NOWAIT before HA coordination; preserve existing coercion/secret semantics.

    Project only these settings. A corrupt unrelated setting cannot turn optional
    presence reservation into a failure of the core domain transaction.
    """
    for key in HA_SETTING_KEYS:
        acquired = await session.scalar(text(
            "SELECT pg_try_advisory_xact_lock(hashtext(:setting))"
        ), {"setting": f"iacs:setting:{key}"})
        if acquired is not True:
            raise HaCoordinationBusy("ha_setting_busy")
    rows = (await session.scalars(select(SystemSetting).where(
        SystemSetting.key.in_(HA_SETTING_KEYS),
    ).order_by(SystemSetting.key).with_for_update(nowait=True)
        .execution_options(populate_existing=True))).all()
    values = {key: DEFAULT_DYNAMIC_SETTINGS[key][1] for key in HA_SETTING_KEYS}
    try:
        for row in rows:
            values[row.key] = decrypted_value(row)
        return FrozenHaConnection(
            home_assistant_url=str(values["home_assistant_url"] or ""),
            home_assistant_token=str(values["home_assistant_token"] or ""),
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise HaConfigurationUnavailable("ha_configuration_unavailable") from exc
