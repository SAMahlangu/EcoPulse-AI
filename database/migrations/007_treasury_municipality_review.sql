-- ============================================================
-- ECOPULSE AI
-- South African Province and Municipality Dropdown Integration
-- ============================================================
--
-- Existing tables:
-- public.provinces
-- public.municipalities_import
--
-- No new tables are created.
-- No municipality records are copied or deleted.
-- ============================================================


-- 1. Verify that the existing tables are available.

DO $$
BEGIN

    IF to_regclass('public.provinces') IS NULL THEN
        RAISE EXCEPTION
        'public.provinces does not exist';
    END IF;

    IF to_regclass('public.municipalities_import') IS NULL THEN
        RAISE EXCEPTION
        'public.municipalities_import does not exist';
    END IF;

END $$;


-- 2. Create indexes to improve municipality lookup speed.

CREATE INDEX IF NOT EXISTS
idx_municipalities_import_province
ON public.municipalities_import
("municipality.province_name");


CREATE INDEX IF NOT EXISTS
idx_municipalities_import_code
ON public.municipalities_import
("municipality.demarcation_code");


CREATE INDEX IF NOT EXISTS
idx_municipalities_import_name
ON public.municipalities_import
("municipality.name");


-- 3. Update PostgreSQL statistics.

ANALYZE public.municipalities_import;


-- 4. Verify province records.

SELECT
    id,
    name,
    code
FROM public.provinces
ORDER BY name;


-- 5. Verify municipality records.

SELECT
    "municipality.demarcation_code" AS code,
    "municipality.name" AS name,
    "municipality.province_name" AS province,
    "municipality.category" AS category
FROM public.municipalities_import
ORDER BY
    "municipality.province_name",
    "municipality.name";