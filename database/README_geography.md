# Canonical geography and municipal official accounts

Location dropdowns use public.provinces, public.municipalities and public.communities. Municipality labels are names and selected values are existing municipalities.id UUIDs. All non-demo canonical municipality rows are available, without verification, active-status or onboarding checks. Existing demo rows are preserved separately and excluded from production selection. No imported records are copied and no tables or views are created.

GET /api/v1/locations/provinces returns province IDs/names/codes. GET /api/v1/locations/municipalities supports province_id or province name, optional search/type filters and pagination. The shared searchable dropdown loads every page for the selected province. Changing province clears municipality and community selection. Communities are scoped to canonical municipality UUIDs. Inclusion in the reference list does not assert current municipal activity or a government partnership.

## Direct account creation

The Admin municipality page contains one Add municipal official form: full name, email, password, password confirmation, province and municipality. POST /api/v1/admin/municipal-officials is admin-only and accepts full_name, email, password, province_id and municipality_id. It validates the municipality/province pairing, rejects existing email accounts, creates a confirmed Supabase password account using the server-only admin API, and saves the municipal profile, active membership and audit entry. There is no invitation email or acceptance step for newly created officials. Passwords go only to Supabase Auth, not profile/membership/audit storage or API responses; password validation errors also omit the submitted input. Existing accounts and their roles are not overwritten.

Account provisioning uses a database transaction for profile, membership and audit changes. If that transaction fails, it attempts to delete only the just-created Auth account. If cleanup fails or Auth times out, the administrator receives an explicit instruction to check the user list before retrying. Authentication verification, municipal role definitions and dashboard membership scoping remain unchanged.

The original profiles schema stores province text rather than province_id. Official creation stores province there and links municipality_id; its province UUID is available through municipalities.province_id. Resident onboarding also derives province UUID through existing geography rather than requiring a new profile column.

## Existing province linkage repair

The 292 non-demo municipality rows had province names but NULL province_id. The idempotent operations/link_existing_municipality_provinces.sql operation linked those rows to existing provinces by case-insensitive name match. All municipality UUIDs, existing non-null province links, demo records, profile relationships, incident relationships and memberships were preserved. No unmatched non-demo rows remain.

## Validation (2026-10-22)

28 backend geography/account tests passed, along with TypeScript and frontend dropdown/form checks. Coverage includes administrator-only creation, duplicate email rejection, province/municipality validation, UUID relationships, password handling and cleanup on failed provisioning.

Live dropdown checks returned all 292 non-demo records: Gauteng 13, Western Cape 30 and KwaZulu-Natal 65. A temporary official account was created and verified by password sign-in (HTTP 200), authenticated profile resolution (HTTP 200), correct municipality membership scope, and Municipal dashboard access (HTTP 200). The temporary account was deleted afterward. No invitation emails were sent.

Supabase account creation reference: https://supabase.com/docs/reference/javascript/auth-admin-createuser
