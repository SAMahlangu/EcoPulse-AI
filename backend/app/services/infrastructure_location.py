from fastapi import HTTPException


def resolve_municipality(connection, latitude, longitude, selected):
    matches = []
    if latitude is not None and longitude is not None:
        matches = connection.execute('''select id::text,name,municipality_type from public.municipalities
          where is_demonstration is false and boundary is not null
          and extensions.st_covers(boundary::extensions.geometry,
              extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326))
          and municipality_type in ('local','metropolitan')''', (longitude, latitude)).fetchall()
    if len(matches) == 1:
        if selected and str(selected) != matches[0]['id']:
            raise HTTPException(422, 'The selected municipality does not match the mapped municipal boundary.')
        return matches[0]['id']
    if not selected:
        raise HTTPException(422, 'Confirm the province and municipality responsible for this location.')
    row = connection.execute('''select id::text from public.municipalities where id=%s
      and is_demonstration is false and municipality_type in ('local','metropolitan')''', (selected,)).fetchone()
    if not row:
        raise HTTPException(422, 'Choose a valid local or metropolitan municipality.')
    if matches and str(selected) not in {item['id'] for item in matches}:
        raise HTTPException(422, 'Choose one of the municipalities covering this point.')
    return row['id']
