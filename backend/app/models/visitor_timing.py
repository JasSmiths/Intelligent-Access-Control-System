"""Immutable UTC SQL expressions shared by visitor scheduling and schema indexes."""

# Match pg_get_indexdef's expression spelling, including CASE newlines retained
# by Alembic's PostgreSQL expression comparator, for schema and indexed reads.
MINUTES_SQL = "GREATEST(1, LEAST(COALESCE(NULLIF(window_minutes, 0), 30), 1440))::double precision"
START_SQL = (
    "\nCASE\n"
    "    WHEN valid_from IS NOT NULL AND valid_until IS NOT NULL "
    "THEN (valid_from AT TIME ZONE 'UTC'::text)\n"
    "    ELSE (expected_time AT TIME ZONE 'UTC'::text) - "
    + MINUTES_SQL + " * '00:01:00'::interval\nEND"
)
EXPIRY_SQL = (
    "\nCASE\n"
    "    WHEN valid_from IS NOT NULL AND valid_until IS NOT NULL "
    "THEN (valid_until AT TIME ZONE 'UTC'::text)\n"
    "    ELSE (expected_time AT TIME ZONE 'UTC'::text) + "
    + MINUTES_SQL + " * '00:01:00'::interval + '00:00:00.000001'::interval\nEND"
)

INVALID_WINDOW_SQL = "status IN ('SCHEDULED','ACTIVE') AND valid_from IS NOT NULL AND valid_until IS NOT NULL AND valid_until <= valid_from"
