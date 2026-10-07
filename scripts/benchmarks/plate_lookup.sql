-- Synthetic, transaction-local plate lookup evidence; never reads installation data.
BEGIN;
CREATE TEMP TABLE benchmark_vehicles (registration_number text, is_active boolean);
INSERT INTO benchmark_vehicles
SELECT CASE WHEN value = 1000 THEN 'ab-10 cde' ELSE 'PL' || lpad(value::text, 6, '0') END,
       value % 10 <> 0 OR value = 1000
FROM generate_series(1, 2000) value;
ANALYZE benchmark_vehicles;
-- Baseline: every active registration is materialized for every queued read.
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT registration_number FROM benchmark_vehicles WHERE is_active IS TRUE;
-- Equivalent normalization including historical punctuation, before the index.
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT registration_number FROM benchmark_vehicles WHERE is_active IS TRUE
AND upper(regexp_replace(registration_number, '[^A-Za-z0-9]', '', 'g')) IN ('AB10CDE');
CREATE INDEX benchmark_normalized_active_plate ON benchmark_vehicles
(upper(regexp_replace(registration_number, '[^A-Za-z0-9]', '', 'g')))
WHERE is_active IS TRUE;
ANALYZE benchmark_vehicles;
SET LOCAL plan_cache_mode = force_generic_plan;
PREPARE benchmark_plate_lookup (text) AS
SELECT registration_number FROM benchmark_vehicles WHERE is_active IS TRUE
AND upper(regexp_replace(registration_number, '[^A-Za-z0-9]', '', 'g')) IN ($1);
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
EXECUTE benchmark_plate_lookup('AB10CDE');
DEALLOCATE benchmark_plate_lookup;
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT registration_number FROM benchmark_vehicles WHERE is_active IS TRUE
AND upper(regexp_replace(registration_number, '[^A-Za-z0-9]', '', 'g')) IN ('AB10CDE');
ROLLBACK;
