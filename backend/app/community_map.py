"""Resident community map API with account-scoped report locations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .security import postgres_connection, require_roles

router = APIRouter(prefix="/api/v1", tags=["resident community map"])


@router.get("/resident/community-map")
def resident_community_map(
    limit: int = Query(250, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_roles("resident")),
):
    """Return only the signed-in resident's own geocoded reports."""
    with postgres_connection() as connection:
        # The reporter predicate is the tenant boundary; never expose household pins by area.
        total = connection.execute(
            """select count(*) as total from public.incidents
               where reporter_id=%s and location is not null""",
            (user["id"],),
        ).fetchone()["total"]
        items = connection.execute(
            """select id::text,public_reference,title,category,status,location_text,created_at,
                      extensions.st_y(location::extensions.geometry) as latitude,
                      extensions.st_x(location::extensions.geometry) as longitude
               from public.incidents
               where reporter_id=%s and location is not null
               order by created_at desc,id desc limit %s offset %s""",
            (user["id"], limit, offset),
        ).fetchall()

    return {
        "items": [dict(row) for row in items],
        "total": total,
        "limit": limit,
        "offset": offset,
        "scope": "Your reports only; municipal notices are shown separately.",
    }
