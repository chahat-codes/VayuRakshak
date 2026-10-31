"""VAYURAKSHAK AI: community hazard reporting and transparent air-quality data."""
import csv
import io
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.data import community as cm
from app.data.store import read_rows

BASE = Path(__file__).resolve().parent
UPLOADS = Path(__file__).resolve().parents[1] / "data" / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
LAT = float(os.getenv("VAYU_LATITUDE", "28.6139"))
LON = float(os.getenv("VAYU_LONGITUDE", "77.2090"))
LOCATION = os.getenv("VAYU_LOCATION", "Delhi")

app = FastAPI(title="VAYURAKSHAK AI", description="Community hazard reports and transparent air-quality information")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")

@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "static" / "index.html").read_text()

@app.get("/api/health")
def health():
    return {"status": "ok", "location": LOCATION, "data_mode": "Open-Meteo estimate + local observations"}

@app.get("/api/locations")
def locations():
    return [{"name": LOCATION, "latitude": LAT, "longitude": LON, "source": "server configuration"}]

@app.get("/api/stations")
def stations():
    rows = read_rows()
    return [
        {"station": station,
         "records": sum(1 for row in rows if row["station"] == station),
         "latitude": next((row["latitude"] for row in rows if row["station"] == station and row.get("latitude") is not None), None),
         "longitude": next((row["longitude"] for row in rows if row["station"] == station and row.get("longitude") is not None), None)}
        for station in sorted({row["station"] for row in rows})
    ]

@app.get("/api/air-quality/latest")
async def latest():
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {"latitude": LAT, "longitude": LON, "hourly": "pm2_5,pm10", "past_days": 2, "forecast_days": 2, "timezone": "auto"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        hourly = payload.get("hourly", {})
        zone = ZoneInfo(payload.get("timezone") or "Asia/Kolkata")
        now = datetime.now(zone)
        def local_time(stamp):
            value = datetime.fromisoformat(stamp)
            return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
        values = []
        pm25 = hourly.get("pm2_5", [])
        pm10 = hourly.get("pm10", [])
        for index, stamp in enumerate(hourly.get("time", [])):
            if index < len(pm25) and index < len(pm10) and pm25[index] is not None and pm10[index] is not None:
                kind = "forecast" if local_time(stamp) >= now else "estimated"
                values.append({"timestamp": stamp, "pm25": pm25[index], "pm10": pm10[index], "kind": kind,
                               "source": "Open-Meteo Air Quality API", "location": LOCATION, "aqi": None})
        past = [item for item in values if local_time(item["timestamp"]) <= now]
        current = max(past, key=lambda item: local_time(item["timestamp"])) if past else None
        future = [item for item in values if local_time(item["timestamp"]) > now]
        tomorrow = (now.date() + timedelta(days=1)).isoformat()
        next_day = [float(item["pm25"]) for item in future if local_time(item["timestamp"]).date().isoformat() == tomorrow]
        summary = {"date": tomorrow, "pm25_mean": sum(next_day) / len(next_day), "pm25_min": min(next_day),
                   "pm25_max": max(next_day), "hours": len(next_day)} if next_day else None
        return {"provider": "Open-Meteo Air Quality API", "source": "Open-Meteo Air Quality API", "data_kind": "estimated",
                "location": LOCATION, "latitude": LAT, "longitude": LON, "timezone": str(zone),
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "provider_status": "available", "current": current,
                "series": past[-48:], "forecast": future[:48], "tomorrow_forecast": summary, "status": "available",
                "label": "Estimated · Open-Meteo modelled grid; not a monitoring station measurement", "aqi": None}
    except Exception:
        rows = read_rows()
        latest_row = rows[-1] if rows else None
        if latest_row:
            latest_row = {**latest_row, "kind": "observed", "source": "User-imported observations",
                          "location": latest_row.get("station") or LOCATION, "aqi": latest_row.get("aqi")}
        return {"provider": "Open-Meteo Air Quality API", "source": "User-imported observations" if latest_row else None,
                "data_kind": "observed" if latest_row else "unavailable", "location": LOCATION, "current": latest_row,
                "series": [], "forecast": [], "tomorrow_forecast": None, "status": "available" if latest_row else "unavailable",
                "provider_status": "unavailable", "error": "Provider request failed. Check connection and retry.",
                "aqi": latest_row.get("aqi") if latest_row else None,
                "label": "Observed · imported CSV" if latest_row else "No provider data or imported observations available"}

@app.get("/api/forecast")
async def forecast(hours: int = Query(default=24, ge=1, le=48)):
    data = await latest()
    if not data.get("forecast"):
        raise HTTPException(503, "No provider forecast available")
    return {"location": data["location"], "horizon_hours": hours, "series": data["forecast"][:hours],
            "source": data["source"], "kind": "forecast", "aqi": None,
            "reliability": "Provider forecast; no validated local prediction-accuracy metric is available"}

# Report profiles are pseudonymous browser IDs. They are not authenticated identities.
class Person(BaseModel):
    user_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str = Field(min_length=2, max_length=40)

class CommunityCreate(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    city: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=10, max_length=500)

Category = Literal["garbage", "waste_burning", "construction_dust", "smoke", "other"]

class EnvironmentReportCreate(Person):
    category: Category
    description: str = Field(min_length=8, max_length=500)
    city: str = Field(min_length=2, max_length=80)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    photo_url: Optional[str] = Field(default=None, pattern=r"^/uploads/[a-f0-9]{32}\.(jpg|png|webp)$", max_length=50)
    community_id: Optional[int] = None

class EnvironmentSimilar(BaseModel):
    category: Category
    description: str = Field(min_length=8, max_length=500)
    city: str = Field(min_length=2, max_length=80)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

class EnvironmentResolve(Person):
    resolution_photo_url: str = Field(pattern=r"^/uploads/[a-f0-9]{32}\.(jpg|png|webp)$", max_length=50)
    resolution_note: Optional[str] = Field(default=None, max_length=300)

class EnvironmentConfirmation(Person):
    kind: Literal["observed", "dispute"]


def clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _record_status(c, report_id, status, actor_id=None, actor_name=None, note=""):
    c.execute("INSERT INTO report_status_history(report_id,status,actor_id,actor_name,note,created_at) VALUES(?,?,?,?,?,?)",
              (report_id, status, actor_id, actor_name, clean_text(note), cm.now()))


_REPORT_SELECT = """SELECT r.*,
  (SELECT count(*) FROM environment_confirmations x WHERE x.report_id=r.id AND x.kind='observed') AS observed_count,
  (SELECT count(*) FROM environment_confirmations x WHERE x.report_id=r.id AND x.kind='dispute') AS dispute_count
  FROM environment_reports r"""


def _report(row, with_history=False, private=False):
    item = dict(row)
    item["reference"] = f"VAYU-{item['id']:06d}"
    for key in ("latitude", "longitude"):
        if item.get(key) is not None:
            item[key] = round(float(item[key]), 2)
    if not private:
        for key in ("reporter_id", "reporter_name", "claimed_by_name"):
            item.pop(key, None)
    if with_history:
        with cm.connect() as c:
            entries = c.execute("SELECT status,actor_id,actor_name,note,created_at FROM report_status_history WHERE report_id=? ORDER BY id", (item["id"],)).fetchall()
        item["history"] = [dict(entry) if private else {k: v for k, v in dict(entry).items() if k not in ("actor_id", "actor_name")} for entry in entries]
    return item


def _require_uploaded_image(url):
    if not re.fullmatch(r"/uploads/([a-f0-9]{32}\.(?:jpg|png|webp))", url or "") or not (UPLOADS / url.rsplit("/", 1)[-1]).is_file():
        raise HTTPException(422, "Upload a JPG, PNG, or WebP photo first")

@app.get("/api/community/communities")
def community_list(q: Optional[str] = None):
    with cm.connect() as c:
        rows = c.execute("SELECT id,name,city,description,created_at FROM communities ORDER BY name").fetchall()
    result = [dict(row) for row in rows]
    if q:
        term = q.casefold().strip()
        result = [row for row in result if term in " ".join((row["name"], row["city"], row["description"])).casefold()]
    return {"groups": result, "note": "Groups are user-created, not independently verified, and matched by city only."}

@app.post("/api/community/communities", status_code=201)
def community_create(body: CommunityCreate):
    with cm.connect() as c:
        try:
            cur = c.execute("INSERT INTO communities(name,city,description,created_at) VALUES(?,?,?,?)",
                            (clean_text(body.name), clean_text(body.city), clean_text(body.description), cm.now()))
        except Exception as error:
            if "UNIQUE" in str(error):
                raise HTTPException(409, "A group with this name already exists")
            raise
        row = c.execute("SELECT id,name,city,description,created_at FROM communities WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

@app.post("/api/environment/reports/similar")
def similar_reports(body: EnvironmentSimilar):
    city = clean_text(body.city).casefold()
    incoming = {token for token in re.findall(r"[a-z0-9]+", body.description.casefold()) if len(token) > 2}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with cm.connect() as c:
        rows = c.execute("SELECT id,description,status,created_at,latitude,longitude FROM environment_reports WHERE lower(city)=? AND category=? AND created_at>=? AND status NOT IN ('resolution_submitted','resolved') ORDER BY created_at DESC LIMIT 100",
                         (city, body.category, cutoff)).fetchall()
    matches = []
    for row in rows:
        existing = {token for token in re.findall(r"[a-z0-9]+", row["description"].casefold()) if len(token) > 2}
        overlap = len(incoming & existing) / max(1, len(incoming | existing))
        near = False
        if body.latitude is not None and body.longitude is not None and row["latitude"] is not None and row["longitude"] is not None:
            distance_km = math.hypot((body.latitude-row["latitude"])*111,
                                     (body.longitude-row["longitude"])*111*math.cos(math.radians(body.latitude)))
            near = distance_km <= 1.0
        if overlap >= 0.24 or near:
            matches.append({"reference": f"VAYU-{row['id']:06d}", "status": row["status"], "created_at": row["created_at"]})
    return {"matches": matches[:3], "automatic_merge": False,
            "basis": "same city and category, recent description overlap, or approximate coordinates within 1 km"}

@app.post("/api/environment/reports", status_code=201)
def create_report(body: EnvironmentReportCreate):
    if (body.latitude is None) != (body.longitude is None):
        raise HTTPException(422, "Provide both coordinates or leave both blank")
    if body.photo_url:
        _require_uploaded_image(body.photo_url)
    city = clean_text(body.city)
    with cm.connect() as c:
        if body.community_id is not None:
            group = c.execute("SELECT city FROM communities WHERE id=?", (body.community_id,)).fetchone()
            if not group:
                raise HTTPException(404, "Community not found")
            if group["city"].casefold() != city.casefold():
                raise HTTPException(422, "Choose a group in the reported city")
        cur = c.execute("""INSERT INTO environment_reports(reporter_id,reporter_name,category,description,city,latitude,longitude,photo_url,community_id,created_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (body.user_id, clean_text(body.display_name), body.category, clean_text(body.description), city,
                         round(body.latitude, 2) if body.latitude is not None else None, round(body.longitude, 2) if body.longitude is not None else None, body.photo_url or "", body.community_id, cm.now()))
        report_id = cur.lastrowid
        _record_status(c, report_id, "submitted", body.user_id, clean_text(body.display_name), "Report submitted")
        row = c.execute(_REPORT_SELECT + " WHERE r.id=?", (report_id,)).fetchone()
        result = _report(row, private=True)
        result.update({"ai_classification": "unavailable", "message": "Category selected by reporter; AI image classification is not connected."})
        return result

@app.get("/api/environment/reports")
def list_reports(city: Optional[str] = None):
    with cm.connect() as c:
        if city:
            rows = c.execute(_REPORT_SELECT + " WHERE lower(r.city)=lower(?) ORDER BY r.created_at DESC LIMIT 100", (city.strip(),)).fetchall()
        else:
            rows = c.execute(_REPORT_SELECT + " ORDER BY r.created_at DESC LIMIT 100").fetchall()
    return {"reports": [_report(row) for row in rows], "count": len(rows),
            "identity_note": "Profile confirmations are self-reported; identities are not verified."}

@app.get("/api/environment/reports/mine")
def my_reports(user_id: str = Query(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")):
    with cm.connect() as c:
        rows = c.execute(_REPORT_SELECT + " WHERE r.reporter_id=? ORDER BY r.created_at DESC LIMIT 100", (user_id,)).fetchall()
    return {"reports": [_report(row, with_history=True, private=True) for row in rows], "count": len(rows),
            "identity_note": "Tracking uses this browser’s pseudonymous profile ID; no account authentication is available."}

@app.get("/api/environment/reports/{report_id}")
def report_detail(report_id: int):
    with cm.connect() as c:
        row = c.execute(_REPORT_SELECT + " WHERE r.id=?", (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Report not found")
    return _report(row, with_history=True)

@app.post("/api/environment/reports/{report_id}/confirm")
def confirm_report(report_id: int, body: EnvironmentConfirmation):
    with cm.connect() as c:
        row = c.execute("SELECT reporter_id FROM environment_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        if row["reporter_id"] == body.user_id:
            raise HTTPException(409, "The reporting profile cannot confirm its own issue")
        try:
            c.execute("INSERT INTO environment_confirmations(report_id,user_id,kind,created_at) VALUES(?,?,?,?)",
                      (report_id, body.user_id, body.kind, cm.now()))
        except Exception as error:
            if "UNIQUE" in str(error):
                raise HTTPException(409, "This profile already responded to the report")
            raise
        counts = c.execute("SELECT sum(kind='observed'),sum(kind='dispute') FROM environment_confirmations WHERE report_id=?", (report_id,)).fetchone()
    return {"report_id": report_id, "observed_count": counts[0] or 0, "dispute_count": counts[1] or 0,
            "independently_verified": False, "note": "These are self-reported profile responses, not independent verification."}

@app.post("/api/environment/reports/{report_id}/claim")
def claim_report(report_id: int, body: Person):
    with cm.connect() as c:
        row = c.execute("SELECT status FROM environment_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        if row["status"] != "submitted":
            raise HTTPException(409, "This report is no longer open")
        c.execute("UPDATE environment_reports SET status='claimed',claimed_by_id=?,claimed_by_name=?,claimed_at=? WHERE id=? AND status='submitted'",
                  (body.user_id, clean_text(body.display_name), cm.now(), report_id))
        _record_status(c, report_id, "claimed", body.user_id, clean_text(body.display_name), "Volunteer accepted report")
    return {"id": report_id, "status": "claimed"}

@app.post("/api/environment/reports/{report_id}/start")
def start_action(report_id: int, body: Person):
    with cm.connect() as c:
        row = c.execute("SELECT status,claimed_by_id FROM environment_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        if row["status"] != "claimed":
            raise HTTPException(409, "Only an accepted report can be started")
        if row["claimed_by_id"] != body.user_id:
            raise HTTPException(403, "Only the accepting profile can start the action")
        c.execute("UPDATE environment_reports SET status='action_started',action_started_at=? WHERE id=?",
                  (cm.now(), report_id))
        _record_status(c, report_id, "action_started", body.user_id, clean_text(body.display_name), "Action reported as started")
    return {"id": report_id, "status": "action_started"}

@app.post("/api/environment/reports/{report_id}/resolve")
def submit_resolution(report_id: int, body: EnvironmentResolve):
    _require_uploaded_image(body.resolution_photo_url)
    with cm.connect() as c:
        row = c.execute("SELECT status,claimed_by_id FROM environment_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        if row["status"] != "action_started":
            raise HTTPException(409, "Start the action before submitting evidence")
        if row["claimed_by_id"] != body.user_id:
            raise HTTPException(403, "Only the accepting profile can submit resolution evidence")
        note = clean_text(body.resolution_note or "")
        c.execute("UPDATE environment_reports SET status='resolution_submitted',resolution_photo_url=?,resolution_note=?,resolved_at=? WHERE id=?",
                  (body.resolution_photo_url, note, cm.now(), report_id))
        _record_status(c, report_id, "resolution_submitted", body.user_id, clean_text(body.display_name), "Evidence submitted; awaiting independent review")
    return {"id": report_id, "status": "resolution_submitted", "independently_verified": False,
            "message": "Evidence was submitted; no authority integration or independent review is connected."}

@app.post("/api/community/uploads", status_code=201)
async def upload_image(file: UploadFile = File(...)):
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if file.content_type not in allowed:
        raise HTTPException(415, "Use a JPG, PNG, or WebP image")
    content = await file.read(3_000_001)
    if len(content) > 3_000_000:
        raise HTTPException(413, "Images must be 3 MB or smaller")
    name = uuid.uuid4().hex + allowed[file.content_type]
    (UPLOADS / name).write_bytes(content)
    return {"image_url": "/uploads/" + name, "stored_locally": True}
