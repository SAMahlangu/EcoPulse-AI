"""Live password/JWT login verification using a disposable resident account.

Run from the repo root with the backend running on port 8000. Tokens/passwords
are never printed. The temporary account and AI analysis are deleted afterward.
"""
import os
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
load_dotenv(ROOT/'backend/.env')
from backend.app.services import network
from backend.app.security import postgres_connection

url=os.environ['SUPABASE_URL'].rstrip('/')
service_key=os.environ['SUPABASE_SERVICE_ROLE_KEY']
admin_headers={'apikey':service_key,'Authorization':f'Bearer {service_key}'}
email=f'login-verification-{uuid4().hex}@example.invalid'
password=secrets.token_urlsafe(32)
account_id=None
try:
    created=network.request('POST',url+'/auth/v1/admin/users',headers=admin_headers,
        json={'email':email,'password':password,'email_confirm':True,
              'user_metadata':{'full_name':'Temporary login verification'}},timeout=15)
    created.raise_for_status()
    account_id=created.json()['id']
    if '--browser' in sys.argv:
        with postgres_connection() as con:
            con.execute('''update public.profiles set onboarding_completed_at=now(),
              municipality_id=(select id from public.municipalities where municipality_type='local'
              and is_demonstration is false order by id limit 1) where id=%s''',(account_id,))
    with httpx.Client(base_url='http://127.0.0.1:8000/api/v1',timeout=70) as client:
        result=client.post('/auth/gateway/token?grant_type=password',json={'email':email,'password':password})
        assert result.status_code==200,f'Password sign-in returned HTTP {result.status_code}'
        token=result.json()['access_token']
        headers={'Authorization':f'Bearer {token}'}
        verified=client.get('/auth/me',headers=headers)
        assert verified.status_code==200,f'JWT/profile verification returned HTTP {verified.status_code}'
        assert verified.json()['id']==account_id and verified.json()['role']=='resident'
        print('PASS: real Supabase password login, signed JWT verification and resident profile loading')
        wrong=client.post('/auth/gateway/token?grant_type=password',json={'email':email,'password':'deliberately-incorrect-test-password'})
        assert wrong.status_code==400, 'Incorrect password was not rejected'
        print('PASS: incorrect password rejected by Supabase')
        provinces=client.get('/locations/provinces')
        assert provinces.status_code==200 and len(provinces.json()['items'])==9
        reports=client.get('/incidents',headers=headers)
        assert reports.status_code==200
        print('PASS: real database geography and authenticated resident reports endpoint')
        if '--check-ai' in sys.argv:
            analysis=client.post('/infrastructure/analyse',headers=headers,
                data={'description':'Several streetlights have not worked for three days. The road is dark at night.'})
            assert analysis.status_code==200,f'AI analysis returned HTTP {analysis.status_code}'
            assert analysis.json().get('available') and analysis.json()['category']=='electricity'
            print('PASS: authenticated infrastructure Gemini analysis and database provenance storage')
        refreshed=client.post('/auth/gateway/token?grant_type=refresh_token',json={'refresh_token':result.json()['refresh_token']})
        assert refreshed.status_code==200
        assert client.get('/auth/me',headers={'Authorization':f"Bearer {refreshed.json()['access_token']}"}).status_code==200
        signed_out=client.post('/auth/gateway/logout',headers={'Authorization':f"Bearer {refreshed.json()['access_token']}"},json={})
        assert signed_out.status_code in (200,204)
        print('PASS: session refresh and logout')
        if '--browser' in sys.argv:
            import subprocess
            import json
            subprocess.run(['node',str(ROOT/'frontend/scripts/verify-login-browser.cjs')],
                input=json.dumps({'email':email,'password':password}),text=True,
                check=True,cwd=ROOT,timeout=80)
finally:
    if account_id:
        with postgres_connection() as con:
            con.execute('delete from public.incident_ai_analyses where requester_id=%s',(account_id,))
        removed=network.request('DELETE',url+'/auth/v1/admin/users/'+account_id,headers=admin_headers,timeout=15)
        removed.raise_for_status()
        print('Temporary resident account and test analyses deleted.')
