"""Department administration and workflow extensions to the existing incident API."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .services import network
from .services.supabase_keys import supabase_secret_headers, supabase_secret_key
from .security import postgres_connection, require_roles

router = APIRouter(prefix='/api/v1/infrastructure')


class Department(BaseModel):
    municipality_id: UUID
    department_name: str = Field(min_length=3, max_length=100)
    department_code: str = Field(pattern=r'^[A-Z0-9_]{2,30}$')


class Membership(BaseModel):
    user_id: UUID
    role: Literal['employee', 'manager'] = 'employee'
    is_active: bool = True


class Routing(BaseModel):
    category: Literal['water', 'electricity', 'roads', 'waste', 'stormwater', 'environmental', 'other']
    department_id: UUID


class Assignment(BaseModel):
    assigned_to: UUID


class Reassignment(BaseModel):
    department_id: UUID
    notes: str = Field(min_length=10, max_length=2000)


def require_report(con, report_id, user, manager=False):
    row = con.execute('''select i.* from public.incidents i where id=%s
      and public.can_manage_incident(i.id,%s) for update''', (report_id, user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Report not found in your authorised department.')
    if manager:
        allowed = con.execute('''select exists(select 1 from public.municipal_memberships
          where user_id=%s and municipality_id=%s and status='active' and can_manage_all_departments)
          or exists(select 1 from public.department_memberships where user_id=%s and department_id=%s
          and is_active and role='manager') as allowed''', (user['id'], row['municipality_id'], user['id'], row['department_id'])).fetchone()
        if not allowed['allowed']:
            raise HTTPException(403, 'A department manager or municipal administrator is required.')
    return row


@router.get('/departments/my')
def my_departments(user=Depends(require_roles('municipal'))):
    with postgres_connection() as con:
        rows = con.execute('''select distinct d.id::text,d.department_name,d.department_code,
          d.municipality_id::text,d.is_triage,dm.role from public.municipal_departments d
          join public.municipal_memberships mm on mm.municipality_id=d.municipality_id
          left join public.department_memberships dm on dm.department_id=d.id and dm.user_id=mm.user_id and dm.is_active
          where mm.user_id=%s and mm.status='active' and d.is_active
          and (mm.can_manage_all_departments or dm.user_id is not null) order by d.department_name''', (user['id'],)).fetchall()
    return {'items': rows}


@router.get('/configuration')
def configuration(user=Depends(require_roles('admin'))):
    with postgres_connection() as con:
        return {'departments': con.execute('''select d.*,m.name as municipality from public.municipal_departments d
          join public.municipalities m on m.id=d.municipality_id order by m.name,d.department_name''').fetchall(),
                'memberships': con.execute('select * from public.department_memberships').fetchall(),
                'routing': con.execute('select * from public.department_routing').fetchall()}


@router.post('/departments', status_code=201)
def create_department(data: Department, user=Depends(require_roles('admin'))):
    with postgres_connection() as con:
        row=con.execute('''insert into public.municipal_departments(municipality_id,department_name,department_code)
          values(%s,%s,%s) returning id::text''', (data.municipality_id, data.department_name, data.department_code)).fetchone()
        con.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id) values(%s,'department_created','department',%s)", (user['id'], row['id']))
    return row


@router.put('/departments/{department_id}/memberships')
def set_membership(department_id: UUID, data: Membership, user=Depends(require_roles('admin'))):
    with postgres_connection() as con:
        allowed=con.execute('''select 1 from public.municipal_departments d join public.municipal_memberships mm
          on mm.municipality_id=d.municipality_id join public.profiles p on p.id=mm.user_id
          where d.id=%s and mm.user_id=%s and mm.status='active' and p.role='municipal' and p.account_status='active' ''', (department_id,data.user_id)).fetchone()
        if not allowed:
            raise HTTPException(422, 'The employee must have an active membership in this municipality.')
        con.execute('''insert into public.department_memberships(user_id,department_id,role,is_active) values(%s,%s,%s,%s)
          on conflict(user_id,department_id) do update set role=excluded.role,is_active=excluded.is_active''', (data.user_id,department_id,data.role,data.is_active))
        con.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id) values(%s,'department_membership_updated','profile',%s)", (user['id'],str(data.user_id)))
    return {'success': True}


@router.put('/routing/{municipality_id}')
def configure_routing(municipality_id: UUID, data: Routing, user=Depends(require_roles('admin'))):
    with postgres_connection() as con:
        if not con.execute('select 1 from public.municipal_departments where id=%s and municipality_id=%s and is_active', (data.department_id,municipality_id)).fetchone():
            raise HTTPException(422, 'Choose an active department in this municipality.')
        con.execute('''insert into public.department_routing(municipality_id,category,department_id) values(%s,%s,%s)
          on conflict(municipality_id,category) do update set department_id=excluded.department_id''', (municipality_id,data.category,data.department_id))
        con.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id) values(%s,'department_routing_updated','municipality',%s)", (user['id'],str(municipality_id)))
    return {'success': True}


@router.post('/reports/{report_id}/assign')
def assign_technician(report_id: UUID, data: Assignment, user=Depends(require_roles('municipal'))):
    with postgres_connection() as con:
        row=require_report(con, report_id, user, manager=True)
        if row['status'] in ('resolved','closed','rejected','duplicate','unable_to_verify'):
            raise HTTPException(409, 'A completed report cannot be assigned.')
        if not con.execute('select public.can_manage_incident(%s,%s) as allowed', (report_id,data.assigned_to)).fetchone()['allowed']:
            raise HTTPException(422, 'The technician must be authorised for this report department and municipality.')
        con.execute('''insert into public.incident_assignments(incident_id,assigned_to,assigned_by) values(%s,%s,%s)
          on conflict(incident_id,assigned_to) do nothing''', (report_id,data.assigned_to,user['id']))
        con.execute("update public.incidents set status='assigned',updated_at=now() where id=%s", (report_id,))
        con.execute("insert into public.incident_updates(incident_id,author_id,status,message,official) values(%s,%s,'assigned','A technician has been assigned.',true)", (report_id,user['id']))
    return {'success': True}


@router.get('/reports/{report_id}/workflow')
def workflow(report_id: UUID, user=Depends(require_roles('municipal'))):
    with postgres_connection() as con:
        row=require_report(con,report_id,user)
        departments=con.execute('select id::text,department_name from public.municipal_departments where municipality_id=%s and is_active order by department_name',(row['municipality_id'],)).fetchall()
        technicians=con.execute('''select p.id::text,p.full_name from public.profiles p
          where public.can_manage_incident(%s,p.id) order by p.full_name''',(report_id,)).fetchall()
        assignments=con.execute('''select p.full_name,a.assigned_to::text,a.created_at from public.incident_assignments a
          join public.profiles p on p.id=a.assigned_to where a.incident_id=%s''',(report_id,)).fetchall()
    return {'departments':departments,'technicians':technicians,'assignments':assignments}


@router.post('/reports/{report_id}/completion-image',status_code=201)
async def completion_image(report_id: UUID, request: Request, user=Depends(require_roles('municipal'))):
    import io
    import os
    import httpx
    from uuid import uuid4
    from starlette.concurrency import run_in_threadpool
    from .ai.incident_vision import prepare_image, InvalidIncidentImage
    length=request.headers.get('content-length','')
    if not length.isdigit() or int(length)>11*1024*1024:
        raise HTTPException(413,'Choose an image smaller than 10 MB.')
    with postgres_connection() as con:
        require_report(con,report_id,user)
    form=await request.form(max_files=1,max_fields=0)
    upload=form.get('image')
    if upload is None or not hasattr(upload,'read'):
        raise HTTPException(422,'Choose a repair completion photo.')
    try:
        image=await run_in_threadpool(prepare_image,await upload.read(10*1024*1024+1))
    except InvalidIncidentImage as exc:
        raise HTTPException(422,str(exc)) from exc
    buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=88)
    url=os.getenv('SUPABASE_URL','').rstrip('/');key=supabase_secret_key()
    if not url or not key: raise HTTPException(503,'Private storage is unavailable.')
    path=f"{user['id']}/{uuid4()}-repair.jpg"
    headers=supabase_secret_headers()
    uploaded=False
    try:
        response=await run_in_threadpool(lambda:network.request('POST', f'{url}/storage/v1/object/incident-media/{path}',headers={**headers,'Content-Type':'image/jpeg'},content=buffer.getvalue(),timeout=20))
        response.raise_for_status();uploaded=True
        with postgres_connection() as con:
            row=require_report(con,report_id,user)
            # The reporter can read repair evidence through the existing private report view.
            con.execute("insert into public.incident_media(incident_id,owner_id,storage_path,content_type,media_kind) values(%s,%s,%s,'image/jpeg','repair')",(report_id,row['reporter_id'],path))
            con.execute("insert into public.incident_updates(incident_id,author_id,message,official) values(%s,%s,'Repair completion photo uploaded for review.',true)",(report_id,user['id']))
    except Exception as exc:
        if uploaded:
            try: await run_in_threadpool(lambda:network.request('DELETE', f'{url}/storage/v1/object/incident-media/{path}',headers=headers,timeout=10))
            except httpx.HTTPError: pass
        if isinstance(exc,HTTPException): raise
        raise HTTPException(503,'Could not save the repair image. Please retry.') from exc
    return {'success':True}


@router.post('/reports/{report_id}/reassign')
def reassign(report_id: UUID, data: Reassignment, user=Depends(require_roles('municipal'))):
    with postgres_connection() as con:
        row=require_report(con, report_id,user,manager=True)
        if not con.execute('select 1 from public.municipal_departments where id=%s and municipality_id=%s and is_active',(data.department_id,row['municipality_id'])).fetchone():
            raise HTTPException(422, 'Reassignment must remain within this municipality.')
        con.execute('update public.incidents set department_id=%s,updated_at=now() where id=%s',(data.department_id,report_id))
        con.execute('delete from public.incident_assignments where incident_id=%s',(report_id,))
        con.execute('insert into public.incident_updates(incident_id,author_id,message,official) values(%s,%s,%s,true)',(report_id,user['id'],'Report reassigned: '+data.notes))
        con.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id) values(%s,'report_reassigned','incident',%s)",(user['id'],str(report_id)))
    return {'success': True}
