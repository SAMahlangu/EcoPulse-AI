from backend.app.services.supabase_keys import (
    supabase_publishable_key,
    supabase_secret_headers,
    supabase_secret_key,
)


def test_current_key_names_are_preferred_and_secret_is_never_sent_as_bearer(monkeypatch):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test_only")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "legacy_test_only")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test_only")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "legacy_public_test_only")

    assert supabase_secret_key() == "sb_secret_test_only"
    assert supabase_secret_headers() == {"apikey": "sb_secret_test_only"}
    assert supabase_publishable_key() == "sb_publishable_test_only"


def test_legacy_key_fallback_preserves_existing_bearer_header(monkeypatch):
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "legacy_secret_test_only")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "legacy_public_test_only")

    assert supabase_secret_headers() == {
        "apikey": "legacy_secret_test_only",
        "Authorization": "Bearer legacy_secret_test_only",
    }
    assert supabase_publishable_key() == "legacy_public_test_only"
