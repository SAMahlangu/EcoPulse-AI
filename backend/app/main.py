from __future__ import annotations

import logging
import os
import uuid
import sqlite3
import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
load_dotenv(Path(__file__).resolve().parents[1]/".env")
from .ai.climate_risk import ENGINE_VERSION, assess_dry_conditions, evaluate_forecast
from .services import weather_service, network
from .services.supabase_keys import supabase_secret_headers, supabase_secret_key
from .workspace import router as workspace_router
from .services.infrastructure_location import resolve_municipality
from .infrastructure import router as infrastructure_router
from .auth_gateway import router as auth_gateway_router
from .ecomove import router as ecomove_router
from .service_alerts import router as service_alerts_router
from .email_notifications import router as email_notifications_router
from .assistant import router as assistant_router
from .water import router as water_router, validation_error_response as water_validation_error_response
from .energy import router as energy_router, validation_error_response as energy_validation_error_response
from .community_map import router as community_map_router
from .community_resources import router as community_resources_router
from .security import database_url, local_demo_enabled, optional_current_user, postgres_connection
from .security_middleware import RequestBodyLimitMiddleware, SecurityHeadersMiddleware

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ecopulse")
DB_PATH = Path(os.getenv("ECOPULSE_DB_PATH", "./ecopulse.sqlite3"))
_vision_attempts: dict[str, deque[datetime]] = defaultdict(deque)

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def require_local_demo_mode() -> None:
    if not local_demo_enabled():
        raise HTTPException(status_code=503, detail="Shared database is not configured. Local SQLite mode must be explicitly enabled for development.")

@asynccontextmanager
async def lifespan(_: FastAPI):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, location TEXT NOT NULL, latitude REAL, longitude REAL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'submitted', public_reference TEXT, submission_key TEXT UNIQUE, community_map_consent INTEGER NOT NULL DEFAULT 0)""")
        columns={row[1] for row in con.execute("PRAGMA table_info(incidents)")}
        if "public_reference" not in columns: con.execute("ALTER TABLE incidents ADD COLUMN public_reference TEXT")
        if "submission_key" not in columns: con.execute("ALTER TABLE incidents ADD COLUMN submission_key TEXT")
        if "community_map_consent" not in columns: con.execute("ALTER TABLE incidents ADD COLUMN community_map_consent INTEGER NOT NULL DEFAULT 0")
        con.execute("""CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, previous REAL NOT NULL, current REAL NOT NULL, previous_date TEXT NOT NULL, current_date TEXT NOT NULL, household_size INTEGER, created_at TEXT NOT NULL)""")
    yield

app = FastAPI(title="EcoPulse AI Platform API", version="0.2.0", description="Shared resident, municipal, and platform services. Weather data is sourced from Open-Meteo.", lifespan=lifespan)
app.include_router(workspace_router)
app.include_router(infrastructure_router)
app.include_router(auth_gateway_router)
app.include_router(ecomove_router)
app.include_router(service_alerts_router)
app.include_router(email_notifications_router)
app.include_router(assistant_router)
app.include_router(water_router)
app.include_router(energy_router)
app.include_router(community_map_router)
app.include_router(community_resources_router)
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if x.strip()]
# Development URLs may use either loopback hostname, including Vite's fallback
# and preview ports. Production origins remain explicitly configured.
local_origin_regex = r"http://(?:localhost|127\.0\.0\.1|\[::1\]):(?:517[3-9]|4173)" if any(
    origin in {"http://localhost:5173", "http://127.0.0.1:5173"} for origin in origins
) else None


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith('/api/v1/water/'):
        return water_validation_error_response()
    if request.url.path.startswith('/api/v1/energy/'):
        return energy_validation_error_response()
    if request.url.path.startswith('/api/v1/auth/gateway/'):
        return JSONResponse({'detail':'Check the authentication details and try again.'},status_code=422,headers={'Cache-Control':'no-store'})
    if request.url.path in {"/api/v1/admin/municipal-officials", "/api/v1/auth/register-local"}:
        # Do not echo password inputs in FastAPI's default validation error response.
        return JSONResponse({"detail": "Check the full name, email, province, municipality and password (8-128 characters)."}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def limit_incident_upload_body(request: Request, call_next):
    if request.method == "POST" and request.url.path in {"/api/v1/incidents/analyze-image", "/api/v1/incidents/submit", "/api/v1/infrastructure/analyse", "/api/v1/infrastructure/detect"}:
        length=request.headers.get("content-length")
        if not length or not length.isdigit() or int(length)>11*1024*1024:
            return JSONResponse({"detail":"Incident image requests must include a Content-Length and stay below 11 MB."},status_code=413)
    return await call_next(request)


# Add CORS after the upload guard so it wraps that middleware and adds CORS
# headers to early responses (for example, a 413 caused by a missing or large
# Content-Length). Otherwise browsers mask the useful response as a CORS error.
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_origin_regex=local_origin_regex, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])

app.add_middleware(SecurityHeadersMiddleware)

class Reading(BaseModel):
    previous_reading: float = Field(ge=0)
    current_reading: float = Field(ge=0)
    previous_date: date
    current_date: date
    household_size: int | None = Field(default=None, ge=1, le=30)
    @model_validator(mode="after")
    def validate_reading(self):
        if self.current_reading < self.previous_reading: raise ValueError("Current reading must be greater than or equal to previous reading")
        if self.current_date <= self.previous_date: raise ValueError("Current date must be after previous date")
        return self

class Incident(BaseModel):
    category: str = Field(min_length=2, max_length=40)
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)
    location: str = Field(min_length=2, max_length=250)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    analysis_id: str | None = None
    training_consent: bool = False
    community_map_consent: bool = False
    submission_key: uuid.UUID | None = None
    municipality_id: uuid.UUID | None = None

    @model_validator(mode='after')
    def validate_infrastructure(self):
        from .ai.incident_vision import ALLOWED_CATEGORIES
        if self.category not in ALLOWED_CATEGORIES:
            raise ValueError('Choose a supported municipal service category')
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('Latitude and longitude must be provided together')
        return self


def _incident_form(form: Any) -> Incident:
    try:
        return Incident.model_validate_json(form.get("report", "{}"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Incident details are invalid.") from exc


async def _submitted_image(request: Request) -> tuple[Incident, bytes | None]:
    try:
        form = await request.form(max_files=1, max_fields=2, max_part_size=64 * 1024)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Image upload dependencies are missing. Install backend requirements.") from exc
    report = _incident_form(form)
    upload = form.get("image")
    if upload is None or not hasattr(upload, "read"):
        return report, None
    data = await upload.read(10 * 1024 * 1024 + 1)
    return report, data


@app.post("/api/v1/incidents/analyze-image")
@app.post("/api/v1/infrastructure/analyse")
@app.post("/api/v1/infrastructure/detect")
async def analyze_incident_image(request: Request, user: dict[str, Any] | None = Depends(optional_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Sign in as a resident before analysing an incident image.")
    if user["role"] != "resident":
        raise HTTPException(status_code=403, detail="Only residents can analyse incident images.")
    now=datetime.now(timezone.utc); attempts=_vision_attempts[user["id"]]
    while attempts and (now-attempts[0]).total_seconds()>60: attempts.popleft()
    if len(attempts)>=10: raise HTTPException(status_code=429,detail="Please wait before requesting more image analyses.")
    attempts.append(now)
    try:
        form = await request.form(max_files=1, max_fields=3, max_part_size=64 * 1024)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Image upload dependencies are missing. Install backend requirements.") from exc
    upload = form.get("image")
    description = str(form.get('description', '')).strip()[:2000]
    image = None
    image_digest = None
    from .ai import incident_vision
    if upload is not None and hasattr(upload, 'read'):
        data = await upload.read(10 * 1024 * 1024 + 1)
        image_digest = __import__('hashlib').sha256(data).hexdigest()
        try:
            image = await run_in_threadpool(incident_vision.prepare_image, data)
        except incident_vision.InvalidIncidentImage as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if image is None and (not description or request.url.path.endswith('/detect')):
        raise HTTPException(422, 'Choose a photo or describe the infrastructure problem.')
    from .services.infrastructure_analysis import analyze
    try:
        result = await asyncio.wait_for(run_in_threadpool(analyze, image, description, str(form.get('rejected', '')).lower() == 'true', request.url.path.endswith('/detect')), timeout=60)
    except asyncio.TimeoutError:
        result = {"available": False, "reason": "Image analysis timed out. You can still submit this report manually.", "model_name": "configured", "model_version": "unknown"}
    analysis_id = str(uuid.uuid4())
    result['image_digest'] = image_digest
    observations = result.get("observations", [])
    with postgres_connection() as con:
        con.execute("""insert into public.incident_ai_analyses(id,requester_id,model_name,model_version,available,suggested_category,confidence,observations,result_metadata)
          values(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)""", (analysis_id,user["id"],result.get("model_name","unavailable"),result.get("model_version","none"),result.get("available",False),result.get("category"),result.get("confidence"),__import__("json").dumps(observations),__import__("json").dumps({k:v for k,v in result.items() if k not in {"observations","explanation","annotated_image"}})))
    return {"analysis_id":analysis_id, **result}


@app.post("/api/v1/incidents/submit")
async def submit_incident_with_media(request: Request, user: dict[str, Any] | None = Depends(optional_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Sign in as a resident before submitting a report.")
    if user["role"] != "resident":
        raise HTTPException(status_code=403, detail="Only residents can submit reports.")
    incident, data = await _submitted_image(request)
    if not data:
        return create_incident(incident, user)
    from .ai import incident_vision
    try:
        image = await run_in_threadpool(incident_vision.prepare_image, data)
    except incident_vision.InvalidIncidentImage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    import io
    clean = io.BytesIO()
    image.save(clean, format="JPEG", quality=88, optimize=True)
    image_bytes = clean.getvalue()
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = supabase_secret_key()
    service_headers = supabase_secret_headers()
    if not database_url() or not supabase_url or not service_key:
        raise HTTPException(status_code=503, detail="Private incident image storage is not configured. Remove the photo to submit this report manually.")
    if incident.submission_key:
        with postgres_connection() as con:
            existing=con.execute("select id::text,status,created_at,public_reference from public.incidents where reporter_id=%s and submission_key=%s",(user["id"],str(incident.submission_key))).fetchone()
            if existing:return {"id":existing["id"],"reference":existing["public_reference"],"category":incident.category,"status":existing["status"],"created_at":existing["created_at"],"classification":"unverified resident report"}
    object_path = f"{user['id']}/{uuid.uuid4()}.jpg"
    uploaded_paths = []
    try:
        uploaded_paths.append(object_path)
        response = await run_in_threadpool(lambda: network.request('POST', f"{supabase_url}/storage/v1/object/incident-media/{object_path}", headers={**service_headers,"Content-Type":"image/jpeg","x-upsert":"false"}, content=image_bytes, timeout=20))
        response.raise_for_status()
        reference = "ECO-" + str(datetime.now(timezone.utc).year) + "-" + uuid.uuid4().hex[:16].upper()
        with postgres_connection() as con:
            analysis = None
            if incident.analysis_id:
                try:
                    analysis_uuid = str(uuid.UUID(incident.analysis_id))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="The selected AI analysis reference is invalid.") from exc
                analysis = con.execute("select id,model_name,model_version,suggested_category,result_metadata from public.incident_ai_analyses where id=%s and requester_id=%s", (analysis_uuid,user["id"])).fetchone()
                if not analysis:
                    raise HTTPException(status_code=403, detail="That AI analysis does not belong to your account.")
                if analysis['result_metadata'].get('image_digest') != __import__('hashlib').sha256(data).hexdigest():
                    raise HTTPException(422, 'The photo changed after analysis. Analyse it again or continue manually.')
            municipality = resolve_municipality(con, incident.latitude, incident.longitude, incident.municipality_id)
            row=con.execute("""insert into public.incidents(reporter_id,category,title,description,location,location_text,municipality_id,public_reference,submission_key,ai_suggested_category,resident_confirmed_category,ai_model_name,ai_model_version,training_consent,community_map_consent)
              values(%s,%s,%s,%s,case when %s::double precision is null or %s::double precision is null then null else extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography end,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              returning id::text,status,created_at,public_reference""",(user["id"],incident.category,incident.title,incident.description,incident.longitude,incident.latitude,incident.longitude,incident.latitude,incident.location,municipality,reference,incident.submission_key,analysis["suggested_category"] if analysis else None,incident.category,analysis["model_name"] if analysis else None,analysis["model_version"] if analysis else None,incident.training_consent,incident.community_map_consent)).fetchone()
            con.execute("insert into public.incident_media(incident_id,owner_id,storage_path,content_type) values(%s,%s,%s,'image/jpeg')",(row["id"],user["id"],object_path))
            if analysis and analysis['result_metadata'].get('detections'):
                from PIL import ImageDraw
                annotated = image.copy()
                draw = ImageDraw.Draw(annotated)
                for detection in analysis['result_metadata']['detections']:
                    draw.rectangle(detection['bounding_box'], outline='#f59e0b', width=4)
                    draw.text(tuple(detection['bounding_box'][:2]), detection['label'], fill='#f59e0b')
                annotated_bytes = io.BytesIO()
                annotated.save(annotated_bytes, format='JPEG', quality=85)
                annotated_path = f"{user['id']}/{uuid.uuid4()}-annotated.jpg"
                uploaded_paths.append(annotated_path)
                annotated_response = await run_in_threadpool(lambda: network.request('POST', f"{supabase_url}/storage/v1/object/incident-media/{annotated_path}",headers={**service_headers,"Content-Type":"image/jpeg","x-upsert":"false"},content=annotated_bytes.getvalue(),timeout=20))
                annotated_response.raise_for_status()
                con.execute("insert into public.incident_media(incident_id,owner_id,storage_path,content_type,media_kind) values(%s,%s,%s,'image/jpeg','annotated')", (row['id'],user['id'],annotated_path))
            con.execute("insert into public.incident_updates(incident_id,message,official,internal) values(%s,'Report submitted.',false,false)",(row["id"],))
            if analysis:
                con.execute("update public.incident_ai_analyses set incident_id=%s where id=%s",(row["id"],analysis["id"]))
            if municipality:
                con.execute("""insert into public.notifications(resident_id,title,body,category,official)
                  select mm.user_id,%s,%s,'incident_report',false from public.municipal_memberships mm where mm.municipality_id=%s and mm.status='active' and public.can_manage_incident(%s,mm.user_id)""",(f"New community report: {incident.title}","A resident submitted a report in your assigned department.",municipality,row["id"]))
        return {"id":row["id"],"reference":row["public_reference"],"category":incident.category,"status":row["status"],"created_at":row["created_at"],"classification":"unverified resident report"}
    except HTTPException:
        for uploaded_path in uploaded_paths:
            try:
                await run_in_threadpool(lambda: network.request('DELETE', f"{supabase_url}/storage/v1/object/incident-media/{uploaded_path}",headers=service_headers,timeout=10))
            except httpx.HTTPError:
                log.error('Incident storage cleanup needs retry for an uncommitted upload.')
        raise
    except Exception as exc:
        for uploaded_path in uploaded_paths:
            try:
                await run_in_threadpool(lambda: network.request('DELETE', f"{supabase_url}/storage/v1/object/incident-media/{uploaded_path}",headers=service_headers,timeout=10))
            except httpx.HTTPError:
                log.error('Incident storage cleanup needs retry for an uncommitted upload.')
        log.error("Incident with media submission failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not save this report and its image. Please retry or submit manually without the photo.") from exc

@app.get("/api/v1/health")
def health(): return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

async def fetch_weather(latitude: float, longitude: float) -> dict[str, Any]:
    try:
        return await weather_service.forecast(latitude, longitude)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("Open-Meteo forecast request failed: %s", exc)
        raise HTTPException(status_code=503, detail="Weather service is currently unavailable") from exc

@app.get("/api/v1/climate/forecast")
async def climate_forecast(latitude: float = Query(-25.7479, ge=-90, le=90), longitude: float = Query(28.2293, ge=-180, le=180)):
    return await fetch_weather(latitude, longitude)


@app.get("/api/v1/climate/current")
async def climate_current(latitude: float = Query(-25.7479, ge=-90, le=90), longitude: float = Query(28.2293, ge=-180, le=180)):
    try:
        return await weather_service.forecast(latitude,longitude,current_request=True)
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        log.warning("Open-Meteo current conditions request failed: %s",exc)
        raise HTTPException(status_code=503,detail="Weather service is currently unavailable") from exc

@app.get("/api/v1/climate/risks")
async def risks(latitude: float = Query(-25.7479, ge=-90, le=90), longitude: float = Query(28.2293, ge=-180, le=180)):
    weather=await fetch_weather(latitude,longitude)
    result=evaluate_forecast(weather)
    try:
        result["risks"][2]=await dry_condition_risk(latitude,longitude)
    except (httpx.HTTPError, ValueError, KeyError, HTTPException) as exc:
        log.info("Dry-condition comparison unavailable: %s",exc)
    active=[r for r in result["risks"] if r["level"] not in ("low","unavailable")]
    result["category"]=max(active,key=lambda r:r["score"] or 0)["level"] if active else ("low" if result["category"]=="low" else result["category"])
    return result


async def dry_condition_risk(latitude: float, longitude: float) -> dict[str, Any]:
    """Compare the latest complete 30-day rainfall with matching 1991–2020 windows."""
    end=date.today()-timedelta(days=5)  # archive completeness lag buffer
    start=end-timedelta(days=29)
    recent=await weather_service.history(latitude,longitude,start,end)
    baseline=await weather_service.history(latitude,longitude,date(1991,1,1),date(2020,12,31))
    recent_values=[r["precipitation_sum"] for r in recent["daily"] if r["precipitation_sum"] is not None]
    if len(recent_values)<25:
        return {"risk":"dry_conditions","level":"unavailable","score":None,"indicator_name":"30-day rainfall departure","indicator_value":None,"unit":"%","explanation":"Recent archive data is incomplete; no dry-condition assessment is available.","precautions":["Follow official water-restriction notices."]}
    annual_totals=[]
    by_year: dict[int,float]={}
    for row in baseline["daily"]:
        try: day=date.fromisoformat(row["date"])
        except ValueError: continue
        if row["precipitation_sum"] is None: continue
        try: window_end=date(day.year,end.month,end.day)
        except ValueError: continue
        window_start=window_end-timedelta(days=29)
        if window_start <= day <= window_end:
            by_year[day.year]=by_year.get(day.year,0.0)+float(row["precipitation_sum"])
    annual_totals=list(by_year.values())
    if len(annual_totals)<10:
        return {"risk":"dry_conditions","level":"unavailable","score":None,"indicator_name":"30-day rainfall departure","indicator_value":None,"unit":"%","explanation":"Fewer than ten matched reference years were returned; no dry-condition assessment is available.","precautions":["Follow official water-restriction notices."]}
    return assess_dry_conditions(sum(float(v) for v in recent_values),sum(annual_totals)/len(annual_totals),len(annual_totals))


def _aggregate_history(rows: list[dict[str, Any]], aggregation: str) -> list[dict[str, Any]]:
    if aggregation=="daily": return rows
    groups: dict[str,list[dict[str,Any]]]={}
    for row in rows:
        key=row["date"][:7] if aggregation=="monthly" else row["date"][:4]
        groups.setdefault(key,[]).append(row)
    result=[]
    for key,items in sorted(groups.items()):
        means=[float(x["temperature_mean"]) for x in items if x["temperature_mean"] is not None]
        highs=[float(x["temperature_max"]) for x in items if x["temperature_max"] is not None]
        lows=[float(x["temperature_min"]) for x in items if x["temperature_min"] is not None]
        rain=[float(x["precipitation_sum"]) for x in items if x["precipitation_sum"] is not None]
        result.append({"date":key,"temperature_mean":sum(means)/len(means) if means else None,"temperature_max":max(highs) if highs else None,"temperature_min":min(lows) if lows else None,"precipitation_sum":sum(rain) if rain else None,"days_with_data":len(means)})
    return result


def _reference_groups(rows: list[dict[str, Any]], aggregation: str) -> dict[str,dict[str,Any]]:
    # First calculate the selected aggregation for each baseline year, then average
    # equivalent calendar groups across years. Temperature is never summed.
    yearly_months: dict[str,list[dict[str,Any]]]={}
    yearly_years: dict[str,list[dict[str,Any]]]={}
    for row in rows:
        monthly= row["date"][:7]
        yearly_months.setdefault(monthly,[]).append(row)
        yearly_years.setdefault(row["date"][:4],[]).append(row)
    baseline_by_year_month={}
    for key,items in yearly_months.items():
        baseline_by_year_month[key]=_aggregate_group(items)
    baseline_by_year={key:_aggregate_group(items) for key,items in yearly_years.items()}
    source=baseline_by_year_month.values() if aggregation=="monthly" else baseline_by_year.values()
    if aggregation=="annual":
        return {"annual":_mean_group(list(baseline_by_year.values()),"annual")}
    cal_groups: dict[str,list[dict[str,Any]]]={}
    for item in source:
        key=item["date"][5:7] if aggregation=="monthly" else item["date"][:4] if aggregation=="annual" else item["date"][5:]
        cal_groups.setdefault(key,[]).append(item)
    if aggregation=="daily":
        daily_groups: dict[str,list[dict[str,Any]]]={}
        for row in rows: daily_groups.setdefault(row["date"][5:],[]).append(row)
        return {key:_mean_group(items,key) for key,items in daily_groups.items()}
    return {key:_mean_group(items,key) for key,items in cal_groups.items()}


def _aggregate_group(items: list[dict[str,Any]]) -> dict[str,Any]:
    means=[float(x["temperature_mean"]) for x in items if x["temperature_mean"] is not None]
    highs=[float(x["temperature_max"]) for x in items if x["temperature_max"] is not None]
    lows=[float(x["temperature_min"]) for x in items if x["temperature_min"] is not None]
    rain=[float(x["precipitation_sum"]) for x in items if x["precipitation_sum"] is not None]
    return {"date":items[0]["date"],"temperature_mean":sum(means)/len(means) if means else None,"temperature_max":max(highs) if highs else None,"temperature_min":min(lows) if lows else None,"precipitation_sum":sum(rain) if rain else None}


def _mean_group(items: list[dict[str,Any]], key: str) -> dict[str,Any]:
    keys=("temperature_mean","temperature_max","temperature_min","precipitation_sum")
    result={"date":key,"reference_years":len(items)}
    for field in keys:
        values=[float(x[field]) for x in items if x.get(field) is not None]
        result[field]=sum(values)/len(values) if values else None
        result[f"{field}_samples"]=len(values)
    return result


def _with_anomalies(selected: list[dict[str,Any]], reference: dict[str,dict[str,Any]], aggregation: str) -> list[dict[str,Any]]:
    result=[]
    for row in selected:
        key=row["date"][5:] if aggregation=="daily" else row["date"][5:7] if aggregation=="monthly" else "annual"
        ref=reference.get(key)
        item=dict(row)
        required_fields=("precipitation_sum","temperature_mean")
        if not ref or any(ref.get(f"{field}_samples",ref.get("reference_years",0))<10 for field in required_fields):
            item.update({"rainfall_anomaly_mm":None,"rainfall_anomaly_percent":None,"temperature_anomaly_c":None,"reference_note":"Insufficient matched historical reference data (minimum 10 years)."})
        else:
            base_rain=ref.get("precipitation_sum")
            rain=row.get("precipitation_sum")
            base_temp=ref.get("temperature_mean")
            temp=row.get("temperature_mean")
            item.update({"rainfall_anomaly_mm":round(rain-base_rain,2) if rain is not None and base_rain is not None else None,"rainfall_anomaly_percent":round((rain-base_rain)/base_rain*100,1) if rain is not None and base_rain not in (None,0) else None,"temperature_anomaly_c":round(temp-base_temp,2) if temp is not None and base_temp is not None else None,"reference_note":"Anomaly versus matched 1991–2020 Open-Meteo ERA5 daily reanalysis."})
        result.append(item)
    return result


async def _history_request(latitude: float,longitude: float,start_date: date,end_date: date,aggregation: str):
    if start_date>end_date: raise HTTPException(422,"start_date must be on or before end_date")
    if (end_date-start_date).days>3660: raise HTTPException(422,"The selected period cannot exceed ten years")
    if start_date<date(1940,1,1): raise HTTPException(422,"Historical archive data starts in 1940")
    if end_date>date.today()-timedelta(days=5): raise HTTPException(422,"Choose an end date at least five days in the past to allow for archive processing")
    try: payload=await weather_service.history(latitude,longitude,start_date,end_date)
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        log.warning("Historical Open-Meteo request failed: %s",exc)
        raise HTTPException(503,"Historical weather is currently unavailable") from exc
    return payload,_aggregate_history(payload["daily"],aggregation)


@app.get("/api/v1/climate/history")
async def climate_history(latitude: float=Query(-25.7479,ge=-90,le=90),longitude: float=Query(28.2293,ge=-180,le=180),start_date: date=Query(),end_date: date=Query(),aggregation: str=Query("daily",pattern="^(daily|monthly|annual)$")):
    payload,series=await _history_request(latitude,longitude,start_date,end_date,aggregation)
    return {"location":payload["location"],"series":series,"aggregation":aggregation,"source":payload["source"],"data_kind":payload["data_kind"],"updated_at":payload["updated_at"]}


@app.get("/api/v1/climate/analytics")
async def climate_analytics(latitude: float=Query(-25.7479,ge=-90,le=90),longitude: float=Query(28.2293,ge=-180,le=180),start_date: date=Query(),end_date: date=Query(),aggregation: str=Query("monthly",pattern="^(daily|monthly|annual)$")):
    payload,selected=await _history_request(latitude,longitude,start_date,end_date,aggregation)
    ref_start,ref_end=date(1991,1,1),date(2020,12,31)
    try:
        reference_raw=await weather_service.history(latitude,longitude,ref_start,ref_end)
        reference=_reference_groups(reference_raw["daily"],aggregation)
        series=_with_anomalies(selected,reference,aggregation)
        reference_period={"start":ref_start.isoformat(),"end":ref_end.isoformat(),"method":"Open-Meteo ERA5 reanalysis; matched calendar windows and years"}
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        log.warning("Historical reference request failed: %s",exc)
        series=[{**item,"rainfall_anomaly_mm":None,"rainfall_anomaly_percent":None,"temperature_anomaly_c":None,"reference_note":"Reference period is currently unavailable."} for item in selected]
        reference_period={"start":ref_start.isoformat(),"end":ref_end.isoformat(),"method":"unavailable"}
    daily=payload["daily"]
    observed=[x for x in daily if x.get("temperature_mean") is not None]
    rainfall=[x["precipitation_sum"] for x in daily if x.get("precipitation_sum") is not None]
    dry_streak=longest_dry_streak=0
    for row in daily:
        rain=row.get("precipitation_sum")
        if rain is not None and rain<1:
            dry_streak+=1; longest_dry_streak=max(longest_dry_streak,dry_streak)
        else: dry_streak=0
    summary={"average_temperature_c":round(sum(x["temperature_mean"] for x in observed)/len(observed),2) if observed else None,"maximum_temperature_c":max((x["temperature_max"] for x in daily if x.get("temperature_max") is not None),default=None),"minimum_temperature_c":min((x["temperature_min"] for x in daily if x.get("temperature_min") is not None),default=None),"rainfall_total_mm":round(sum(rainfall),2) if rainfall else None,"days_with_data":len(observed),"heat_days_35c":sum(1 for x in daily if x.get("temperature_max") is not None and x["temperature_max"]>=35),"longest_dry_spell_days":longest_dry_streak,"dry_day_basis":"daily precipitation below 1 mm; SAWS uses 1 mm as a rain-day threshold in its climate analysis"}
    return {"location":payload["location"],"series":series,"aggregation":aggregation,"summary":summary,"reference_period":reference_period,"source":payload["source"],"data_kind":payload["data_kind"],"updated_at":payload["updated_at"]}


@app.get("/api/v1/climate/locations")
async def climate_locations(search: str=Query(min_length=2,max_length=100)):
    try: results=await weather_service.search_locations(search)
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(503,"Location search is currently unavailable") from exc
    return {"items":results,"count":len(results),"source":"Open-Meteo Geocoding API; South Africa results"}


@app.get("/api/v1/climate/map")
async def climate_map(latitude: float=Query(-25.7479,ge=-90,le=90),longitude: float=Query(28.2293,ge=-180,le=180),layer: str=Query("temperature",pattern="^(temperature|rainfall|heat_risk|rain_risk|wind|advisories|community_incidents)$")):
    weather=await fetch_weather(latitude,longitude)
    risks=evaluate_forecast(weather)
    values={"temperature":weather["current"]["temperature"],"rainfall":weather["current"]["precipitation"],"wind":weather["current"]["wind_speed"],"heat_risk":next(x["level"] for x in risks["risks"] if x["risk"]=="heat"),"rain_risk":next(x["level"] for x in risks["risks"] if x["risk"]=="heavy_rain"),"advisories":"No official advisory integration configured","community_incidents":"Weather-related resident reports"}
    with db() as con:
        incidents=con.execute("SELECT id,title,category,location,latitude,longitude,created_at,status FROM incidents WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? AND lower(category) IN ('weather','storm','flood') ORDER BY created_at DESC LIMIT 100",(latitude-0.35,latitude+0.35,longitude-0.35,longitude+0.35)).fetchall()
    return {"markers":[{"latitude":weather["location"]["latitude"],"longitude":weather["location"]["longitude"],"label":"Selected location","layer":layer,"value":values[layer],"unit":"°C" if layer=="temperature" else "mm" if layer=="rainfall" else "km/h" if layer=="wind" else None,"source":weather["source"],"data_kind":weather["data_kind"],"updated_at":weather["updated_at"]}],"incidents":[dict(row) for row in incidents],"layer":layer,"note":"Only actual weather forecast points and coordinate-bearing resident reports are shown. No spatial interpolation is performed."}


@app.get("/api/v1/climate/advisories")
async def climate_advisories(latitude: float=Query(-25.7479,ge=-90,le=90),longitude: float=Query(28.2293,ge=-180,le=180),severity: str|None=Query(None,pattern="^(low|moderate|high|severe)$")):
    weather=await fetch_weather(latitude,longitude)
    assessment=evaluate_forecast(weather)
    generated=[]
    for risk in assessment["risks"]:
        if risk["level"] in ("low","unavailable"): continue
        generated.append({"id":f"ecopulse-{risk['risk']}-{weather['updated_at']}","title":f"{risk['risk'].replace('_',' ').title()} weather advisory","hazard_category":risk["risk"],"severity":risk["level"],"geographic_area":{"latitude":latitude,"longitude":longitude},"start_time":risk["expected_period"] or assessment["valid_from"],"expiry_time":assessment["valid_until"],"description":risk["explanation"],"supporting_indicators":[{"name":risk["indicator_name"],"value":risk["indicator_value"],"unit":risk["unit"]}],"recommended_actions":risk["precautions"],"source":weather["source"],"verification_status":"EcoPulse rule-based advisory; not official","is_official":False,"created_at":assessment["assessed_at"]})
    if severity: generated=[x for x in generated if x["severity"]==severity]
    return {"official_warnings":{"available":False,"items":[],"message":"No official warning provider is configured. Check South African Weather Service alerts."},"ecopulse_advisories":{"items":generated,"count":len(generated)},"assessment":assessment}


class AdvisorQuestion(BaseModel):
    question: str=Field(min_length=3,max_length=500)
    latitude: float=Field(default=-25.7479,ge=-90,le=90)
    longitude: float=Field(default=28.2293,ge=-180,le=180)


@app.post("/api/v1/climate/advisor")
async def climate_advisor(request: AdvisorQuestion):
    weather=await fetch_weather(request.latitude,request.longitude)
    assessment=evaluate_forecast(weather)
    from .services.climate_advisor import answer_question
    answer = await run_in_threadpool(answer_question, request.question, weather, assessment)
    return {"answer":answer,"source":weather["source"],"data_kind":weather["data_kind"],"location":weather["location"],"updated_at":weather["updated_at"],"engine_version":ENGINE_VERSION,"method":"Gemini response grounded in Open-Meteo forecast"}



@app.get("/api/v1/climate/preferences")
def climate_preferences():
    return {"available":False,"preferences":None,"reason":"Preferences require authenticated resident storage; Supabase authentication is not configured."}


@app.put("/api/v1/climate/preferences")
def update_climate_preferences():
    raise HTTPException(501,"Climate preferences require authenticated resident storage, which is not configured.")

@app.post("/api/v1/{kind}/readings")
def create_reading(kind: str, reading: Reading, user: dict[str, Any] | None = Depends(optional_current_user)):
    if kind not in ("water", "energy"): raise HTTPException(404,"Reading type not found")
    if database_url():
        if not user: raise HTTPException(401,"Sign in is required to record household readings.")
        if user["role"] != "resident": raise HTTPException(403,"Only residents can record household readings.")
        table = "water_readings" if kind == "water" else "energy_readings"
        try:
            with postgres_connection() as con:
                if kind == "water":
                    row = con.execute(f"""INSERT INTO public.{table}(resident_id,previous_value,current_value,previous_date,reading_date,household_size)
                        VALUES(%s,%s,%s,%s,%s,%s) RETURNING id::text,created_at""", (user["id"],reading.previous_reading,reading.current_reading,reading.previous_date,reading.current_date,reading.household_size)).fetchone()
                else:
                    row = con.execute(f"""INSERT INTO public.{table}(resident_id,previous_value,current_value,previous_date,reading_date)
                        VALUES(%s,%s,%s,%s,%s) RETURNING id::text,created_at""", (user["id"],reading.previous_reading,reading.current_reading,reading.previous_date,reading.current_date)).fetchone()
        except Exception as exc:
            log.exception("Could not save %s reading to the shared database", kind)
            raise HTTPException(503,"Could not save the reading to the shared database.") from exc
        return {"id":row["id"],"kind":kind,"consumption":reading.current_reading-reading.previous_reading,"unit":"kL" if kind=="water" else "kWh","created_at":row["created_at"]}
    require_local_demo_mode()
    with db() as con:
        cur = con.execute("INSERT INTO readings(kind,previous,current,previous_date,current_date,household_size,created_at) VALUES(?,?,?,?,?,?,?)",(kind,reading.previous_reading,reading.current_reading,reading.previous_date.isoformat(),reading.current_date.isoformat(),reading.household_size,datetime.now(timezone.utc).isoformat()))
    return {"id":cur.lastrowid,"kind":kind,"consumption":reading.current_reading-reading.previous_reading,"unit":"kL" if kind=="water" else "kWh","created_at":datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/{kind}/readings")
def list_readings(kind: str, limit: int = Query(50,ge=1,le=100), user: dict[str, Any] | None = Depends(optional_current_user)):
    if kind not in ("water","energy"): raise HTTPException(404,"Reading type not found")
    if database_url():
        if not user: raise HTTPException(401,"Sign in is required to view household readings.")
        if user["role"] != "resident": raise HTTPException(403,"Only residents can view household readings.")
        table = "water_readings" if kind == "water" else "energy_readings"
        try:
            with postgres_connection() as con:
                rows = con.execute(f"""SELECT id::text,previous_value as previous,current_value as current,previous_date,reading_date as current_date,
                    current_value-previous_value as consumption,created_at FROM public.{table} r
                    WHERE resident_id=%s ORDER BY r.reading_date DESC LIMIT %s""",(user["id"],limit)).fetchall()
        except Exception as exc:
            raise HTTPException(503,"Could not load household readings from the shared database.") from exc
        return {"items":[dict(row) for row in rows],"count":len(rows)}
    require_local_demo_mode()
    with db() as con: rows=con.execute("SELECT * FROM readings WHERE kind=? ORDER BY current_date DESC LIMIT ?",(kind,limit)).fetchall()
    items=[]
    for row in rows:
        item=dict(row); item["consumption"]=item["current"]-item["previous"]; items.append(item)
    return {"items":items,"count":len(items)}

@app.post("/api/v1/incidents")
def create_incident(incident: Incident, user: dict[str, Any] | None = Depends(optional_current_user)):
    if database_url():
        if not user: raise HTTPException(401,"Sign in is required to submit an incident.")
        if user["role"] != "resident": raise HTTPException(403,"Only residents can submit household reports.")
        try:
            with postgres_connection() as con:
                if incident.submission_key:
                    existing=con.execute("select id::text,status,created_at,public_reference from public.incidents where reporter_id=%s and submission_key=%s",(user["id"],str(incident.submission_key))).fetchone()
                    if existing:return {"id":existing["id"],"reference":existing["public_reference"],"category":incident.category,"status":existing["status"],"created_at":existing["created_at"],"classification":"unverified resident report"}
                municipality = resolve_municipality(con, incident.latitude, incident.longitude, incident.municipality_id)
                analysis = None
                if incident.analysis_id:
                    try: analysis_id=str(uuid.UUID(incident.analysis_id))
                    except ValueError as exc: raise HTTPException(status_code=422,detail="The selected AI analysis reference is invalid.") from exc
                    analysis=con.execute("select id,model_name,model_version,suggested_category from public.incident_ai_analyses where id=%s and requester_id=%s",(analysis_id,user["id"])).fetchone()
                    if not analysis: raise HTTPException(status_code=403,detail="That AI analysis does not belong to your account.")
                reference="ECO-"+str(datetime.now(timezone.utc).year)+"-"+uuid.uuid4().hex[:16].upper()
                row = con.execute("""insert into public.incidents(reporter_id,category,title,description,location,location_text,municipality_id,public_reference,submission_key,ai_suggested_category,resident_confirmed_category,ai_model_name,ai_model_version,training_consent,community_map_consent)
                    values(%s,%s,%s,%s,case when %s::double precision is null or %s::double precision is null then null else extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography end,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    returning id::text,status,created_at,public_reference""", (user["id"],incident.category,incident.title,incident.description,
                    incident.longitude,incident.latitude,incident.longitude,incident.latitude,incident.location,municipality,reference,
                    incident.submission_key,
                    analysis["suggested_category"] if analysis else None,incident.category,analysis["model_name"] if analysis else None,
                    analysis["model_version"] if analysis else None,incident.training_consent,incident.community_map_consent)).fetchone()
                con.execute("insert into public.incident_updates(incident_id,message,official,internal) values(%s,'Report submitted.',false,false)",(row["id"],))
                if analysis: con.execute("update public.incident_ai_analyses set incident_id=%s where id=%s",(row["id"],analysis["id"]))
                if municipality:
                    con.execute("""insert into public.notifications(resident_id,title,body,category,official)
                        select mm.user_id,%s,%s,'incident_report',false from public.municipal_memberships mm
                        where mm.municipality_id=%s and mm.status='active' and public.can_manage_incident(%s,mm.user_id)""",
                        (f"New community report: {incident.title}","A resident submitted a report in your assigned department.",municipality,row["id"]))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503,"Could not save this incident to the shared database.") from exc
        return {"id":row["id"],"reference":row["public_reference"],"category":incident.category,"status":row["status"],"created_at":row["created_at"],"classification":"unverified resident report"}
    require_local_demo_mode()
    now=datetime.now(timezone.utc).isoformat()
    reference="ECO-"+str(datetime.now(timezone.utc).year)+"-"+uuid.uuid4().hex[:16].upper()
    with db() as con:
        if incident.submission_key:
            existing=con.execute("select id,status,created_at,public_reference from incidents where submission_key=?",(str(incident.submission_key),)).fetchone()
            if existing:return {"id":existing["id"],"reference":existing["public_reference"],"category":incident.category,"status":existing["status"],"created_at":existing["created_at"],"classification":"unverified resident report"}
        cur=con.execute("INSERT INTO incidents(category,title,description,location,latitude,longitude,created_at,public_reference,submission_key,community_map_consent) VALUES(?,?,?,?,?,?,?,?,?,?)",(incident.category,incident.title,incident.description,incident.location,incident.latitude,incident.longitude,now,reference,str(incident.submission_key) if incident.submission_key else None,int(incident.community_map_consent)))
    return {"id":cur.lastrowid,"reference":reference,"category":incident.category,"status":"submitted","created_at":now,"classification":"unverified resident report"}

@app.get("/api/v1/incidents")
def list_incidents(limit: int = Query(50,ge=1,le=100), offset: int = Query(0,ge=0), user: dict[str, Any] | None = Depends(optional_current_user)):
    if database_url():
        if not user: raise HTTPException(401,"Sign in is required to view incident reports.")
        if user["role"] != "resident": raise HTTPException(403,"Only residents can view their report history.")
        try:
            with postgres_connection() as con:
                rows=con.execute("""select id::text,public_reference,category,title,description,location_text as location,
                    extensions.st_y(location::extensions.geometry) as latitude,extensions.st_x(location::extensions.geometry) as longitude,
                    created_at,status,community_map_consent,(select name from public.municipalities m where m.id=municipality_id) as municipality_name,(select department_name from public.municipal_departments d where d.id=department_id) as department_name from public.incidents where reporter_id=%s order by created_at desc limit %s offset %s""",
                    (user["id"],limit,offset)).fetchall()
        except Exception as exc:
            raise HTTPException(503,"Could not load your incident history.") from exc
        return {"items":[dict(row) for row in rows],"limit":limit,"offset":offset}
    require_local_demo_mode()
    with db() as con: rows=con.execute("SELECT id,public_reference,category,title,description,location,latitude,longitude,created_at,status,community_map_consent FROM incidents ORDER BY id DESC LIMIT ? OFFSET ?",(limit,offset)).fetchall()
    return {"items":[dict(r) for r in rows],"limit":limit,"offset":offset}

@app.get("/api/v1/incidents/{incident_id}")
def resident_incident(incident_id: str, user: dict[str, Any] | None = Depends(optional_current_user)):
    if database_url():
        if not user: raise HTTPException(401,"Sign in is required to view incident details.")
        if user["role"] != "resident": raise HTTPException(403,"Only residents can view their report details.")
        try:
            with postgres_connection() as con:
                incident=con.execute("""select id::text,public_reference,category,title,description,location_text as location,status,created_at,updated_at,(select name from public.municipalities m where m.id=municipality_id) as municipality_name,(select department_name from public.municipal_departments d where d.id=department_id) as department_name
                    from public.incidents where id=%s and reporter_id=%s""",(incident_id,user["id"])).fetchone()
                if not incident: raise HTTPException(404,"Incident not found.")
                updates=con.execute("select status,message,created_at from public.incident_updates where incident_id=%s and internal=false order by created_at",(incident_id,)).fetchall()
                media=con.execute("select storage_path,content_type from public.incident_media where incident_id=%s and owner_id=%s order by created_at",(incident_id,user["id"])).fetchall()
        except HTTPException: raise
        except Exception as exc: raise HTTPException(503,"Could not load incident updates.") from exc
        media_items=[]
        supabase_url=os.getenv("SUPABASE_URL","").strip().rstrip("/"); service_key=supabase_secret_key()
        for item in media:
            url=None
            if supabase_url and service_key:
                try:
                    signed=network.request('POST', f"{supabase_url}/storage/v1/object/sign/incident-media/{item['storage_path']}",headers=supabase_secret_headers(),json={"expiresIn":300},timeout=8)
                    signed.raise_for_status(); path=signed.json().get("signedURL"); url=f"{supabase_url}/storage/v1{path}" if path and path.startswith("/") else path
                except (httpx.HTTPError,ValueError): pass
            media_items.append({"content_type":item["content_type"],"url":url})
        return {"incident":dict(incident),"updates":[dict(row) for row in updates],"media":media_items}
    require_local_demo_mode()
    with db() as con:
        incident=con.execute("select id,category,title,description,location,status,created_at from incidents where id=?",(incident_id,)).fetchone()
    if not incident: raise HTTPException(404,"Incident not found.")
    return {"incident":dict(incident),"updates":[]}

@app.get("/api/v1/resident/dashboard")
def dashboard(user: dict[str, Any] | None = Depends(optional_current_user)):
    month = datetime.now(timezone.utc).date().strftime("%Y-%m")
    if database_url():
        if not user: raise HTTPException(401,"Sign in is required to view your dashboard.")
        if user["role"] != "resident": raise HTTPException(403,"This dashboard is only available to residents.")
        try:
            with postgres_connection() as con:
                active_incidents=con.execute("select count(*) as count from public.incidents where reporter_id=%s and status in ('submitted','acknowledged','under_review','verified','in_progress')",(user["id"],)).fetchone()["count"]
                recent=con.execute("select id::text,category,title,location_text as location,created_at,status,(select name from public.municipalities m where m.id=municipality_id) as municipality_name,(select department_name from public.municipal_departments d where d.id=department_id) as department_name from public.incidents where reporter_id=%s order by created_at desc limit 5",(user["id"],)).fetchall()
                water=con.execute("select coalesce(sum(w.current_value-w.previous_value),0) as total,count(*) as count from public.water_readings w where w.resident_id=%s and to_char(w.reading_date,'YYYY-MM')=%s",(user["id"],month)).fetchone()
                energy=con.execute("select coalesce(sum(e.current_value-e.previous_value),0) as total,count(*) as count from public.energy_readings e where e.resident_id=%s and to_char(e.reading_date,'YYYY-MM')=%s",(user["id"],month)).fetchone()
        except Exception as exc:
            raise HTTPException(503,"Could not load your dashboard from the shared database.") from exc
        return {"source":"your Supabase resident profile and submitted records","active_incidents":{"value":active_incidents,"updated_at":datetime.now(timezone.utc).isoformat()},
          "water":{"month":month,"consumption_kilolitres":round(water["total"],3) if water["count"] else None,"reading_count":water["count"],"estimated_cost":None},
          "energy":{"month":month,"consumption_kwh":round(energy["total"],2) if energy["count"] else None,"reading_count":energy["count"],"estimated_cost":None},
          "recent_incidents":[dict(row) for row in recent],"official_alerts":[],"timestamp":datetime.now(timezone.utc).isoformat()}
    require_local_demo_mode()
    with db() as con:
        active_incidents = con.execute("SELECT COUNT(*) FROM incidents WHERE status IN ('submitted','acknowledged','in_progress')").fetchone()[0]
        recent = con.execute("SELECT id,category,title,location,created_at,status FROM incidents ORDER BY id DESC LIMIT 5").fetchall()
        water = con.execute("SELECT COALESCE(SUM(current-previous),0), COUNT(*) FROM readings WHERE kind='water' AND substr(current_date,1,7)=?",(month,)).fetchone()
        energy = con.execute("SELECT COALESCE(SUM(current-previous),0), COUNT(*) FROM readings WHERE kind='energy' AND substr(current_date,1,7)=?",(month,)).fetchone()
    return {
        "source":"resident-submitted records in local development database",
        "active_incidents":{"value":active_incidents,"updated_at":datetime.now(timezone.utc).isoformat()},
        "water":{"month":month,"consumption_kilolitres":round(water[0],3) if water[1] else None,"reading_count":water[1],"estimated_cost":None},
        "energy":{"month":month,"consumption_kwh":round(energy[0],2) if energy[1] else None,"reading_count":energy[1],"estimated_cost":None},
        "recent_incidents":[dict(row) for row in recent],
        "official_alerts":[],
        "timestamp":datetime.now(timezone.utc).isoformat(),
    }
