"""Read Supabase API keys without treating new secret keys as JWTs."""

from __future__ import annotations

import os


def supabase_publishable_key() -> str:
    """Prefer the current public key name while allowing a gradual legacy migration."""
    return os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()


def supabase_secret_key() -> str:
    """Prefer the current server-only key name while supporting the old name temporarily."""
    return os.getenv("SUPABASE_SECRET_KEY", "").strip() or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()


def supabase_secret_headers() -> dict[str, str]:
    """Return headers for Storage and Auth admin calls using either key generation."""
    current_key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if current_key:
        # Current secret keys are opaque API keys, never JWT bearer tokens.
        return {"apikey": current_key}

    legacy_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if legacy_key:
        return {"apikey": legacy_key, "Authorization": f"Bearer {legacy_key}"}
    return {}
