"""Current HA setting projection under the caller's already-acquired auth-root fence."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.home_assistant.input_boolean_contracts import FrozenHaConnection
from app.services.ha_presence_contracts import HA_SETTING_KEYS, HaConfigurationUnavailable
from app.services.ha_presence_database import lock_home_assistant_setting_rows
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, decrypted_value


async def lock_home_assistant_connection(session: AsyncSession) -> FrozenHaConnection:
    """NOWAIT before HA coordination; preserve existing coercion/secret semantics.

    Project only these settings. A corrupt unrelated setting cannot turn optional
    presence reservation into a failure of the core domain transaction.
    """
    rows = await lock_home_assistant_setting_rows(session)
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
