"""Live Supabase verification. Every fixture and schema dry run is rolled back.

Run from the repo root: backend/venv/Scripts/python.exe backend/tests/verify_infrastructure_database.py
Pass --apply to commit migration 008 only after the isolation checks pass.
"""
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
load_dotenv(ROOT/'backend/.env')


def verify(con):
    ids=[str(uuid4()) for _ in range(5)]
    resident,water,electric,outsider,other_resident=ids
    municipalities=con.execute("select id from public.municipalities where municipality_type='local' and is_demonstration is false order by id limit 2").fetchall()
    a,b=[row['id'] for row in municipalities]
    for index,actor in enumerate(ids):
        con.execute('insert into auth.users(id,email) values(%s,%s)',(actor,f'infra-verification-{actor}@example.invalid'))
        con.execute("update public.profiles set role=%s,account_status='active' where id=%s",('resident' if index in (0,4) else 'municipal',actor))
    for actor,municipality in [(water,a),(electric,a),(outsider,b)]:
        con.execute("insert into public.municipal_memberships(user_id,municipality_id,status) values(%s,%s,'active')",(actor,municipality))
    departments=[]
    for municipality,code in [(a,'TEST_WATER'),(a,'TEST_POWER'),(b,'TEST_WATER')]:
        departments.append(con.execute('insert into public.municipal_departments(municipality_id,department_name,department_code) values(%s,%s,%s) returning id',(municipality,code,code)).fetchone()['id'])
    for actor,department in zip((water,electric,outsider),departments):
        con.execute('insert into public.department_memberships(user_id,department_id) values(%s,%s)',(actor,department))
    con.execute("insert into public.department_routing(municipality_id,category,department_id) values(%s,'water',%s)",(a,departments[0]))
    report=con.execute("""insert into public.incidents(reporter_id,municipality_id,category,title,description,location_text,public_reference)
      values(%s,%s,'water','Burst pipe verification','A resident reports a burst pipe.','Verification location',%s) returning id,department_id""",(resident,a,'TEST-'+uuid4().hex)).fetchone()
    assert report['department_id']==departments[0], 'Incorrect automatic department routing'
    for actor,expected in [(water,True),(electric,False),(outsider,False)]:
        result=con.execute('select public.can_manage_incident(%s,%s) as allowed',(report['id'],actor)).fetchone()['allowed']
        assert result is expected, 'API department authorization failed'
    for actor,expected in [(resident,1),(water,1),(electric,0),(outsider,0),(other_resident,0)]:
        con.execute('set local role authenticated')
        con.execute("select set_config('request.jwt.claims',%s,true)",(__import__('json').dumps({'sub':actor,'role':'authenticated'}),))
        count=con.execute('select count(*) as n from public.incidents where id=%s',(report['id'],)).fetchone()['n']
        assert count==expected, 'PostgreSQL RLS department isolation failed'
        con.execute('reset role')
    history=con.execute('select count(*) n from public.department_assignment_history where incident_id=%s',(report['id'],)).fetchone()['n']
    assert history==1
    print('PASS: burst pipe routing; authorised water access; electricity/other municipality/resident isolation; PostgreSQL RLS; assignment audit')
    # Exercise the real HTTP handlers against the same rollback-only connection.
    from contextlib import contextmanager, ExitStack
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.security import current_user, optional_current_user
    from backend.app import main, workspace, infrastructure
    @contextmanager
    def shared_connection():
        yield con
    client=TestClient(app)
    def acting(actor,role,municipality=None):
        user={'id':actor,'role':role,'municipality_ids':[str(municipality)] if municipality else []}
        app.dependency_overrides[current_user]=lambda:user
        app.dependency_overrides[optional_current_user]=lambda:user
    try:
        with ExitStack() as stack:
            for module in (main,workspace,infrastructure):
                stack.enter_context(patch.object(module,'postgres_connection',shared_connection))
            acting(resident,'resident')
            response=client.post('/api/v1/incidents',json={'category':'water','title':'Burst pipe HTTP test',
                'description':'Water is visibly flowing from a damaged pipe.','location':'Test street',
                'municipality_id':str(a),'latitude':-26.1,'longitude':28.1})
            assert response.status_code==200,response.text
            report_id=response.json()['id']
            for actor,municipality,expected in [(water,a,200),(electric,a,404),(outsider,b,404)]:
                acting(actor,'municipal',municipality)
                assert client.get(f'/api/v1/municipal/incidents/{report_id}').status_code==expected
                visible=client.get('/api/v1/municipal/map').json()['items']
                assert (report_id in {r['id'] for r in visible}) is (expected==200)
            acting(water,'municipal',a)
            for status in ('under_review','in_progress','resolved'):
                response=client.patch(f'/api/v1/municipal/incidents/{report_id}/status',json={'status':status,'message':'Verification workflow update.'})
                assert response.status_code==200,response.text
            acting(resident,'resident')
            tracked=client.get(f'/api/v1/incidents/{report_id}')
            assert tracked.status_code==200,tracked.text
            assert tracked.json()['incident']['status']=='resolved'
            assert len(tracked.json()['updates'])==4
            # A real private Storage upload, followed by explicit object cleanup.
            from io import BytesIO
            from PIL import Image
            import httpx
            image=BytesIO();Image.new('RGB',(40,40),'white').save(image,format='JPEG')
            photo_report=None
            try:
                photo=client.post('/api/v1/incidents/submit',data={'report':__import__('json').dumps({
                    'category':'water','title':'Storage verification report','description':'Synthetic storage verification image.',
                    'location':'Verification location','municipality_id':str(a)})},files={'image':('verification.jpg',image.getvalue(),'image/jpeg')})
                assert photo.status_code==200,photo.text
                photo_report=photo.json()['id']
                detail=client.get(f'/api/v1/incidents/{photo_report}')
                assert detail.status_code==200 and detail.json()['media'][0]['url'],detail.text
                print('PASS: validated multipart photo upload, private Supabase Storage and authorised signed URL')
            finally:
                if photo_report:
                    paths=con.execute('select storage_path from public.incident_media where incident_id=%s',(photo_report,)).fetchall()
                    for item in paths:
                        key=os.environ['SUPABASE_SERVICE_ROLE_KEY'];url=os.environ['SUPABASE_URL'].rstrip('/')
                        removed=httpx.delete(f"{url}/storage/v1/object/incident-media/{item['storage_path']}",headers={'apikey':key,'Authorization':f'Bearer {key}'},timeout=15)
                        removed.raise_for_status()
            acting(other_resident,'resident')
            assert client.get(f'/api/v1/incidents/{report_id}').status_code==404
            print('PASS: HTTP submission, municipal detail/map isolation, staff status transitions, resident tracking and private report access')
    finally:
        app.dependency_overrides.clear()
        client.close()


with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=10,row_factory=dict_row) as con:
    exists=con.execute("select to_regclass('public.municipal_departments') as name").fetchone()['name']
    migration=(ROOT/'database/migrations/008_infrastructure_departments.sql').read_text()
    if not exists:
        # Keep the migration's entire transaction under this script's control.
        con.execute(migration.replace('begin;','',1).rsplit('commit;',1)[0])
    verify(con)
    con.rollback()
    print('Rolled back all verification fixtures and schema dry-run changes.')
    if '--apply' in sys.argv and not exists:
        con.execute(migration)
        print('Migration 008 applied. No test accounts or reports retained.')
