# Database migrations

Apply reviewed SQL files to a development database in this order. The `010` and `011` prefixes are shared by separate migration streams, so use the filenames below rather than sorting by prefix alone.

1. `001_resident_portal.sql`
2. `002_climate_intelligence.sql`
3. `003_multi_role_platform.sql`
4. `004_incident_reporting_onboarding.sql`
5. `005_authenticated_profile_select.sql`
6. `006_south_african_geography.sql`
7. `007_treasury_municipality_review.sql`
8. `008_infrastructure_departments.sql`
9. `009_municipal_triage_access.sql`
10. `010_community_service_alerts.sql`
11. `010_ecomove.sql`
12. `011_default_municipal_departments.sql`
13. `011_ecomove_stop_validation.sql`
14. `012_municipality_wide_service_alerts.sql`
15. `013_municipality_reference_status_columns.sql`
16. `014_water_consumption_intelligence.sql`
17. `015_energy_consumption_intelligence.sql`
18. `016_notification_outbox.sql`
19. `017_incident_service_followup.sql`

Keep applied migrations immutable. Review each SQL file and the target schema before applying it. The notification worker and incident follow-up routes depend on migrations 016 and 017 respectively. A database connection or successful API health response does not prove that these migrations have been applied.

After applying, confirm that `notification_outbox` has RLS enabled and no `anon` or `authenticated` grants, check the incident service-time trigger, and verify that the backend service role can read/write both additions. Then exercise the affected flows in a development project before promoting the schema.
