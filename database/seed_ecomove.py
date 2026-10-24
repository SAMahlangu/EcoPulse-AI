"""Intentional, additive EcoMove setup; never overwrites existing transport records.

Run from the repository root with backend/venv/Scripts/python.exe.
--download-geometry fetches road geometry only; --verify rolls all DB work back.
--apply-schema and --seed-demo explicitly commit their named operations.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / 'database/seeds/ecomove_pretoria_demo.json'
NAMESPACE = UUID('d7863478-1d29-4c68-9a01-6e8f415c1e3f')


def identifier(key: str) -> str:
    return str(uuid5(NAMESPACE, key))


def download_geometry():
    import httpx
    stops = {
        'soshanguve': {'name': 'Soshanguve · demo pickup', 'latitude': -25.521, 'longitude': 28.101, 'stop_type': 'pickup'},
        'mabopane': {'name': 'Mabopane · demo pickup', 'latitude': -25.498, 'longitude': 28.043, 'stop_type': 'pickup'},
        'pretoria-north': {'name': 'Pretoria North · demo pickup', 'latitude': -25.676, 'longitude': 28.173, 'stop_type': 'pickup'},
        'cbd': {'name': 'Pretoria CBD · demo interchange', 'latitude': -25.746, 'longitude': 28.188, 'stop_type': 'interchange'},
        'mamelodi': {'name': 'Mamelodi · demo pickup', 'latitude': -25.711, 'longitude': 28.373, 'stop_type': 'pickup'},
        'centurion': {'name': 'Centurion · demo drop-off', 'latitude': -25.861, 'longitude': 28.184, 'stop_type': 'dropoff'},
    }
    corridors = [
        ('soshanguve-cbd', 'Soshanguve → Pretoria CBD', ['soshanguve', 'pretoria-north', 'cbd'], '#087f6e'),
        ('mabopane-cbd', 'Mabopane → Pretoria CBD', ['mabopane', 'pretoria-north', 'cbd'], '#a65dce'),
        ('mamelodi-cbd', 'Mamelodi → Pretoria CBD', ['mamelodi', 'cbd'], '#df8a25'),
        ('cbd-centurion', 'Pretoria CBD → Centurion', ['cbd', 'centurion'], '#337cbd'),
    ]
    routes = []
    snapped = set()
    # Fixed public demo coordinates only. No resident location is sent here.
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for key, name, keys, color in corridors:
            coordinates = ';'.join(f"{stops[k]['longitude']},{stops[k]['latitude']}" for k in keys)
            response = client.get(f'https://router.project-osrm.org/route/v1/driving/{coordinates}',
                                  params={'overview': 'full', 'geometries': 'geojson', 'steps': 'false'})
            response.raise_for_status()
            payload = response.json()
            if payload.get('code') != 'Ok' or not payload.get('routes'):
                raise ValueError('OSRM did not return road geometry; no synthetic fallback will be generated.')
            road = payload['routes'][0]
            geometry = road['geometry']
            if geometry.get('type') != 'LineString' or len(geometry.get('coordinates', [])) < 10:
                raise ValueError('Road geometry is missing or too sparse for this demo corridor.')
            for stop_key, waypoint in zip(keys, payload['waypoints']):
                if stop_key not in snapped:
                    stops[stop_key]['longitude'], stops[stop_key]['latitude'] = waypoint['location']
                    snapped.add(stop_key)
            offsets = [0.0]
            for leg in road['legs']:
                offsets.append(round(offsets[-1] + leg['duration'] / 60, 2))
            routes.append({'key': key, 'name': name, 'origin': stops[keys[0]]['name'].split(' · ')[0],
                           'destination': stops[keys[-1]]['name'].split(' · ')[0], 'stop_keys': keys,
                           'color': color, 'route_geometry': geometry, 'stop_offsets': offsets,
                           'road_distance_meters': road['distance'],
                           'estimated_duration_minutes': round(road['duration'] / 60, 1)})
            print(f"Retrieved road geometry: {key} ({len(geometry['coordinates'])} vertices)")
    fixture = {'is_demo': True, 'data_source': 'demo_osrm_openstreetmap',
               'retrieved_at': datetime.now(timezone.utc).isoformat(),
               'routing_source': 'https://router.project-osrm.org',
               'documentation': 'https://project-osrm.org/docs/v5.22.0/api/',
               'attribution': 'Road geometry © OpenStreetMap contributors, routed with OSRM. ODbL.',
                'notice': (
                    'Demonstration corridors and boarding points only. Not verified taxi routes, '
                    'official ranks, operator participation, schedules or fares. '
                    'Car-routing times are illustrative estimates.'
                ),
               'stops': stops, 'routes': routes}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print('Saved database/seeds/ecomove_pretoria_demo.json; no database changes.')


def seed_demo(con):
    from psycopg.types.json import Jsonb
    if not FIXTURE.exists():
        raise ValueError('Road fixture missing. Run --download-geometry first.')
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    municipality = con.execute("select id from public.municipalities where code='TSH' and not is_demonstration").fetchone()
    if not municipality:
        raise ValueError('Canonical City of Tshwane municipality is missing; no municipality will be created.')
    muni = municipality['id']
    for key, stop in fixture['stops'].items():
        con.execute('''insert into public.taxi_stops
            (id,name,municipality_id,latitude,longitude,stop_type,is_demo,is_interchange)
            values(%s,%s,%s,%s,%s,%s,true,%s) on conflict do nothing''',
            (identifier('stop:' + key), stop['name'], muni, stop['latitude'], stop['longitude'],
             stop['stop_type'], stop['stop_type'] == 'interchange'))
    for route in fixture['routes']:
        route_id = identifier('route:' + route['key'])
        inserted = con.execute('''insert into public.taxi_routes
            (id,name,origin,destination,municipality_id,route_geometry,direction,description,color,
             estimated_duration_minutes,estimated_fare,data_source,is_demo,simulation_mode)
            values(%s,%s,%s,%s,%s,extensions.ST_GeomFromGeoJSON(%s)::extensions.geography,
                   'forward',%s,%s,%s,null,%s,true,'once') on conflict do nothing returning id''',
            (route_id, route['name'], route['origin'], route['destination'], muni,
             Jsonb(route['route_geometry']), fixture['notice'], route['color'],
             route['estimated_duration_minutes'], fixture['data_source'])).fetchone()
        if not inserted:
            print(f"Preserved existing route: {route['key']}")
            continue
        for sequence, (key, offset) in enumerate(zip(route['stop_keys'], route['stop_offsets'])):
            con.execute('''insert into public.taxi_route_stops
                (id,route_id,stop_id,stop_sequence,estimated_arrival_offset_minutes)
                values(%s,%s,%s,%s,%s) on conflict do nothing''',
                (identifier(f"route-stop:{route['key']}:{key}"), route_id, identifier('stop:' + key), sequence, offset))
        route_index = fixture['routes'].index(route)
        for index, progress in enumerate((.1, .39, .68)):
            code = f'SIM-{route_index * 3 + index + 1:03d}'
            con.execute('''insert into public.taxi_simulation_vehicles
                (id,vehicle_code,route_id,simulation_speed,initial_progress,is_active)
                values(%s,%s,%s,%s,%s,true) on conflict do nothing''',
                (identifier('vehicle:' + code), code, route_id, 36 + index * 5, progress))
    con.execute('set constraints all immediate')
    print('Demo seed checked: 4 one-way corridors, 6 illustrative stops, 12 simulation assignments; existing rows preserved.')


def verify_database(con):
    """Exercise actual PostGIS SQL, HTTP serialization and database permissions."""
    from contextlib import contextmanager
    from unittest.mock import patch
    from uuid import uuid4
    import logging
    import psycopg
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import ecomove
    from backend.app.security import current_user
    logging.getLogger('httpx').setLevel(logging.WARNING)
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    origin = fixture['stops']['soshanguve']
    cbd = fixture['stops']['cbd']
    centurion = fixture['stops']['centurion']
    point = lambda stop: {k: stop[k] for k in ('latitude', 'longitude')}
    actor = str(uuid4())
    con.execute('insert into auth.users(id,email) values(%s,%s)', (actor, f'ecomove-check-{actor}@example.invalid'))
    con.execute("update public.profiles set account_status='active' where id=%s", (actor,))
    muni = con.execute(
        'select municipality_id from public.taxi_routes where id=%s',
        (identifier('route:soshanguve-cbd'),),
    ).fetchone()['municipality_id']
    incident = con.execute('''insert into public.incidents
        (reporter_id,municipality_id,category,title,description,location_text,location,status,public_reference)
        values(%s,%s,'roads','Demo verification road hazard','PRIVATE_DESCRIPTION_MUST_NOT_LEAK',
        'Synthetic test location',extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography,
        'submitted',%s) returning id::text''',
        (actor, muni, origin['longitude'], origin['latitude'], 'TEST-' + uuid4().hex)).fetchone()['id']

    @contextmanager
    def shared_connection():
        yield con

    previous = dict(app.dependency_overrides)
    try:
        with patch.object(ecomove, 'postgres_connection', shared_connection), TestClient(app) as client:
            for path in ('routes', 'stops', 'simulation/vehicles', 'disruptions'):
                assert client.get('/api/v1/ecomove/' + path).status_code == 401
            app.dependency_overrides[current_user] = lambda: {'id': actor, 'role': 'resident', 'municipality_id': str(muni)}
            routes = client.get('/api/v1/ecomove/routes', params={'municipality_id': str(muni)})
            assert routes.status_code == 200, routes.text
            assert len(routes.json()['items']) >= 4
            assert all(x['route_geometry']['type'] == 'LineString' for x in routes.json()['items'])
            paged = client.get('/api/v1/ecomove/routes?limit=1&offset=1').json()
            assert len(paged['items']) == 1 and paged['total'] >= 4
            assert client.get('/api/v1/ecomove/routes/' + identifier('route:soshanguve-cbd')).status_code == 200
            assert client.get('/api/v1/ecomove/stops').status_code == 200
            nearby = client.get('/api/v1/ecomove/routes/nearby', params={**point(origin), 'radius_meters': 100})
            assert nearby.status_code == 200, nearby.text
            assert identifier('route:soshanguve-cbd') in {x['id'] for x in nearby.json()['items']}
            assert client.get('/api/v1/ecomove/stops/nearby', params=point(origin)).json()['total'] >= 1
            vehicles = client.get('/api/v1/ecomove/simulation/vehicles').json()['items']
            assert len(vehicles) >= 12 and all(x['position_source'] == 'simulation' for x in vehicles)
            def search(start, end):
                return client.post(
                    '/api/v1/ecomove/journey/search',
                    json={
                        'pickup': point(start),
                        'destination': point(end),
                        'radius_meters': 100,
                        'municipality_id': str(muni),
                    },
                )

            direct = search(origin, cbd)
            assert direct.status_code == 200, direct.text
            assert direct.json()['routes'] and direct.json()['routes'][0]['transfers'] == 0
            transfer = search(origin, centurion)
            assert transfer.status_code == 200, transfer.text
            assert transfer.json()['routes'] and transfer.json()['routes'][0]['transfers'] == 1
            assert search(centurion, origin).json()['routes'] == []
            assert search({'latitude': 0, 'longitude': 0}, cbd).json()['routes'] == []
            assert client.get('/api/v1/ecomove/routes/nearby?latitude=91&longitude=28').status_code == 422
            assert client.get('/api/v1/ecomove/routes?bbox=28,-26,27,-25').status_code == 422
            hazard = client.get('/api/v1/ecomove/disruptions', params={'route_ids': identifier('route:soshanguve-cbd')})
            assert hazard.status_code == 200, hazard.text
            assert incident in {x['id'] for x in hazard.json()['items']}
            assert all(
                'reporter_id' not in item and 'description' not in item and 'media' not in item
                for item in hazard.json()['items']
            )
            assert 'PRIVATE_DESCRIPTION_MUST_NOT_LEAK' not in hazard.text
            print(
                'PASS: all 8 HTTP endpoints, PostGIS proximity, pagination, direct/transfer/reverse/'
                'no-route matching, hazard privacy and validation.'
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    con.execute('set local role authenticated')
    con.execute("select set_config('request.jwt.claims',%s,true)", (json.dumps({'sub': actor, 'role': 'authenticated'}),))
    assert con.execute('select count(*) n from public.taxi_routes').fetchone()['n'] >= 4
    try:
        with con.transaction():
            con.execute("update public.taxi_routes set name='unauthorized change'")
        raise AssertionError('Resident could modify transport configuration')
    except psycopg.errors.InsufficientPrivilege:
        pass
    con.execute('reset role')
    con.execute("update public.profiles set account_status='suspended' where id=%s", (actor,))
    con.execute('set local role authenticated')
    assert con.execute('select count(*) n from public.taxi_routes').fetchone()['n'] == 0
    con.execute('reset role')
    con.execute('set local role anon')
    try:
        with con.transaction():
            con.execute('select * from public.taxi_routes')
        raise AssertionError('Anonymous role could access transport configuration')
    except psycopg.errors.InsufficientPrivilege:
        pass
    con.execute('reset role')
    print(
        'PASS: PostgreSQL RLS active/suspended profiles, resident writes denied, anonymous reads '
        'denied. Test accounts/reports will be rolled back.'
    )
    try:
        with con.transaction():
            con.execute('''update public.taxi_route_stops set estimated_arrival_offset_minutes=
                case stop_sequence when 0 then 5 when 1 then null else 3 end where route_id=%s''',
                (identifier('route:soshanguve-cbd'),))
            con.execute('set constraints all immediate')
        raise AssertionError('Decreasing arrival offsets across an unknown stop were accepted')
    except psycopg.errors.RaiseException:
        pass
    with con.transaction(force_rollback=True):
        con.execute('''update public.taxi_route_stops set estimated_arrival_offset_minutes=
            case stop_sequence when 0 then 5 when 1 then null else 8 end where route_id=%s''',
            (identifier('route:soshanguve-cbd'),))
        con.execute('set constraints all immediate')
    print('PASS: nullable stop timing constraint rejects [5,null,3] and permits [5,null,8].')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-geometry', action='store_true')
    parser.add_argument('--verify', action='store_true', help='Validate migration/seed in a rolled-back transaction')
    parser.add_argument('--apply-schema', action='store_true')
    parser.add_argument('--seed-demo', action='store_true')
    args = parser.parse_args()
    if args.download_geometry:
        download_geometry()
        return
    if not any((args.verify, args.apply_schema, args.seed_demo)):
        parser.print_help()
        return
    if args.verify and (args.apply_schema or args.seed_demo):
        parser.error('--verify cannot be combined with committed operations.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / 'backend/.env')
    from backend.app.security import postgres_connection
    try:
        with postgres_connection() as con:
            con.execute("set local statement_timeout='30s'")
            if args.verify or args.apply_schema:
                tables = con.execute("""select table_name from information_schema.tables
                    where table_schema='public' and table_name in
                    ('taxi_routes','taxi_stops','taxi_route_stops','taxi_simulation_vehicles')""").fetchall()
                if tables and len(tables) != 4:
                    raise ValueError('Partial transport schema exists; inspect it before applying migration 010.')
                if not tables:
                    migration = (ROOT / 'database/migrations/010_ecomove.sql').read_text(encoding='utf-8')
                    # The script owns the transaction, including rollback-only verification.
                    migration = '\n'.join(line for line in migration.splitlines()
                                          if line.strip().lower() not in {'begin;', 'commit;'})
                    con.execute(migration)
                else:
                    print('Existing EcoMove schema preserved; migration not replayed.')
                correction = (ROOT / 'database/migrations/011_ecomove_stop_validation.sql').read_text(encoding='utf-8')
                correction = '\n'.join(line for line in correction.splitlines()
                                       if line.strip().lower() not in {'begin;', 'commit;'})
                con.execute(correction)
            if args.verify or args.seed_demo:
                seed_demo(con)
            if args.verify:
                verify_database(con)
                con.rollback()
                print('PASS: migration + road demo + HTTP/RLS checks; all verification changes rolled back.')
            else:
                print('EcoMove requested operations committed on successful transaction exit.')
    except Exception as exc:
        # Never echo database DSNs, keys, or full driver diagnostics.
        print(f'EcoMove setup failed ({type(exc).__name__}). No partial transaction was committed.')
        if getattr(getattr(exc, 'diag', None), 'message_primary', None):
            print(exc.diag.message_primary)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
