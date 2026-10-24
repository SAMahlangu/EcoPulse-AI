"""Ephemeral real-auth browser fixture. JSON stdout is consumed in-memory by Node.

Never run --create with stdout displayed; use frontend/scripts/test-ecomove-browser.cjs.
The companion script deletes the temporary Auth account in its finally block.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / 'backend/.env')

# Load environment before importing application modules that initialize database access.
from backend.app.services import network
from backend.app.security import postgres_connection
from backend.app.services.supabase_keys import (
    supabase_publishable_key,
    supabase_secret_headers,
    supabase_secret_key,
)


def main():
    url = os.environ['SUPABASE_URL'].rstrip('/')
    key = supabase_secret_key()
    headers = supabase_secret_headers()
    if not key:
        raise RuntimeError('Configure the backend Supabase secret key before running this test.')
    if sys.argv[1] == '--delete':
        response = network.request(
            'DELETE',
            f'{url}/auth/v1/admin/users/{sys.argv[2]}',
            headers=headers,
        )
        response.raise_for_status()
        print('Temporary browser account deleted.')
        return
    if sys.argv[1] != '--create':
        raise ValueError('Unsupported operation')
    actor = None
    try:
        email = f'ecomove-browser-{uuid4()}@example.invalid'
        password = secrets.token_urlsafe(32) + 'aA1!'
        response = network.request(
            'POST',
            f'{url}/auth/v1/admin/users',
            headers=headers,
            json={
                'email': email,
                'password': password,
                'email_confirm': True,
                'user_metadata': {'full_name': 'EcoMove browser verification'},
            },
        )
        response.raise_for_status()
        actor = response.json()['id']
        with postgres_connection() as con:
            con.execute(
                """update public.profiles
                   set municipality_id=(
                         select id from public.municipalities
                         where code='TSH' and not is_demonstration
                       ),
                       province='Gauteng',
                       suburb='Pretoria',
                       onboarding_completed_at=now()
                   where id=%s""",
                (actor,),
            )
        response = network.request(
            'POST',
            f'{url}/auth/v1/token?grant_type=password',
            headers={'apikey': supabase_publishable_key()},
            json={'email': email, 'password': password},
        )
        response.raise_for_status()
        token = response.json()
        print(json.dumps({
            'id': actor,
            'session': {
                'access_token': token['access_token'],
                'refresh_token': token['refresh_token'],
                'expires_at': int(time.time()) + token['expires_in'],
                'user': {'id': actor, 'email': email},
            },
        }))
    except Exception:
        if actor:
            network.request('DELETE', f'{url}/auth/v1/admin/users/{actor}', headers=headers)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print upstream auth payloads, keys, credentials or tokens.
        print(f'Browser account operation failed ({type(exc).__name__}).', file=sys.stderr)
        raise SystemExit(1)
