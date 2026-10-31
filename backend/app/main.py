import csv, io, os, math
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, Query, Form
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.data import community as cm
from app.data import historical as hist
from app.data.store import read_rows, validate_rows, save_rows
from app.ml.pipeline import load_artifacts, predict

BASE = Path(__file__).resolve().parent
app = FastAPI(title="VAYURAKSHAK AI", description="Transparent air-quality intelligence MVP")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
UPLOADS=Path(__file__).resolve().parents[1]/"data"/"uploads"; UPLOADS.mkdir(parents=True,exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
LAT=float(os.getenv("VAYU_LATITUDE", "28.6139")); LON=float(os.getenv("VAYU_LONGITUDE", "77.2090")); LOCATION=os.getenv("VAYU_LOCATION", "Delhi")

@app.get("/", response_class=HTMLResponse)
def home(): return (BASE / "static" / "index.html").read_text()
@app.get("/api/health")
def health(): return {"status":"ok","location":LOCATION,"data_mode":"provider + local CSV"}
@app.get("/api/locations")
def locations(): return [{"name":LOCATION,"latitude":LAT,"longitude":LON,"source":"environment configuration"}]
@app.get("/api/stations")
def stations():
    return [{"station":s,"records":len([r for r in read_rows() if r["station"]==s]),"latitude":next((r["latitude"] for r in read_rows() if r["station"]==s and r["latitude"]),None),"longitude":next((r["longitude"] for r in read_rows() if r["station"]==s and r["longitude"]),None)} for s in sorted(set(r["station"] for r in read_rows()))]
@app.get("/api/air-quality/history")
def history(station:Optional[str]=None):
    rows=read_rows(); return {"source":"user imported observations","records":[r for r in rows if not station or r["station"]==station],"count":len(rows)}
@app.get("/api/reports/export")
def export_csv():
    rows=read_rows(); buf=io.StringIO(); writer=csv.DictWriter(buf,fieldnames=["timestamp","pm25","pm10","temperature","humidity","wind_speed","rainfall","station","latitude","longitude"]); writer.writeheader(); writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=vayurakshak-observations.csv"})
@app.get("/api/air-quality/latest")
async def latest():
    url="https://air-quality-api.open-meteo.com/v1/air-quality"
    params={"latitude":LAT,"longitude":LON,"hourly":"pm2_5,pm10","past_days":2,"forecast_days":2,"timezone":"auto"}
    try:
        async with httpx.AsyncClient(timeout=8) as client: res=await client.get(url,params=params); res.raise_for_status(); payload=res.json()
        hourly=payload.get("hourly",{}); vals=[]
        local_zone=ZoneInfo(payload.get("timezone") or "Asia/Kolkata")
        now=datetime.now(local_zone)
        def local_time(stamp):
            value=datetime.fromisoformat(stamp)
            return value.replace(tzinfo=local_zone) if value.tzinfo is None else value.astimezone(local_zone)
        for i,stamp in enumerate(hourly.get("time",[])):
            a=hourly.get("pm2_5",[]); b=hourly.get("pm10",[])
            if i<len(a) and i<len(b) and a[i] is not None and b[i] is not None:
                kind="forecast" if local_time(stamp)>=now else "estimated"
                vals.append({"timestamp":stamp,"pm25":a[i],"pm10":b[i],"kind":kind,"source":"Open-Meteo Air Quality API","location":LOCATION,"aqi":None})
        previous=[x for x in vals if local_time(x["timestamp"])<=now]
        current=max(previous,key=lambda x:local_time(x["timestamp"])) if previous else (min(vals,key=lambda x:abs((local_time(x["timestamp"])-now).total_seconds())) if vals else None)
        future=[x for x in vals if local_time(x["timestamp"])>=now]
        tomorrow_date=(now.date()+timedelta(days=1)).isoformat()
        tomorrow_values=[float(x["pm25"]) for x in future if local_time(x["timestamp"]).date().isoformat()==tomorrow_date]
        tomorrow_forecast={"date":tomorrow_date,"pm25_mean":sum(tomorrow_values)/len(tomorrow_values),"pm25_min":min(tomorrow_values),"pm25_max":max(tomorrow_values),"hours":len(tomorrow_values)} if tomorrow_values else None
        return {"provider":"Open-Meteo Air Quality API","source":"Open-Meteo Air Quality API","data_kind":"estimated","location":LOCATION,"latitude":LAT,"longitude":LON,"timezone":str(local_zone),"retrieved_at":datetime.now(timezone.utc).isoformat(),"provider_status":"available","current":current,"series":previous[-48:],"forecast":future,"tomorrow_forecast":tomorrow_forecast,"status":"available","label":"Estimated · Open-Meteo modelled grid; not a monitoring station measurement","aqi":None}
    except Exception:
        rows=read_rows(); latest_row=rows[-1] if rows else None
        if latest_row:
            latest_row={**latest_row,"kind":"observed","source":"User-imported observations","location":LOCATION}
        return {"provider":"Open-Meteo Air Quality API","source":"User-imported observations" if latest_row else None,"data_kind":"observed" if latest_row else "unavailable","location":LOCATION,"current":latest_row,"series":[],"forecast":[],"tomorrow_forecast":None,"status":"available" if latest_row else "unavailable","provider_status":"unavailable","error":"Provider request failed. Check connection and try again.","aqi":None,"label":"Observed · imported CSV" if latest_row else "No provider data or imported observations available"}

@app.get("/api/forecast")
async def forecast(hours:int=Query(default=24,ge=1,le=48)):
    data=await latest(); source=data.get("forecast") or []
    if not source: raise HTTPException(503,"No provider forecast available")
    rows=read_rows(); ml=[]
    if rows:
        for r in rows[-1:]:
            try: ml.append(predict(r))
            except Exception: pass
    return {"location":LOCATION,"horizon_hours":hours,"series":source[:hours],"model":"Open-Meteo numerical model","ml_model_status":"trained" if any(ml) else "not trained; provider forecast shown","aqi":None,"reliability":"Provider forecast; local ML evaluation status shown separately"}

class Scenario(BaseModel):
    wind_delta:float=Field(default=0,ge=-3,le=3)
    temperature_delta:float=Field(default=0,ge=-5,le=5)
    humidity_delta:float=Field(default=0,ge=-15,le=15)
    rainfall_delta:float=Field(default=0,ge=0,le=30)
    emission_reduction:float=Field(default=0,ge=0,le=100)
    hours:int=Field(default=24,ge=1,le=48)

@app.post("/api/simulate")
async def simulate(s:Scenario):
    if s.emission_reduction: raise HTTPException(422,"Emission interventions require a validated causal model, which is not available.")
    if any((s.wind_delta,s.temperature_delta,s.humidity_delta,s.rainfall_delta)): raise HTTPException(422,"Weather perturbation is unsupported until a trained model with validated weather features is available.")
    data=await forecast(s.hours)
    return {"baseline":data["series"],"scenario":data["series"],"assumptions":"No parameter changes; baseline repeated for transparent comparison.","label":"Hypothetical modelled scenario — not a validated real-world intervention estimate.","status":"baseline_only"}

@app.get("/api/model/performance")
def performance():
    model,metrics=load_artifacts(); return {"status":"evaluated" if metrics else "not evaluated","model":"Random Forest Regressor","metrics":metrics}
@app.get("/api/model/explanations")
def explanations():
    model,metrics=load_artifacts()
    if not model: return {"status":"unavailable","reason":"Train a model from sufficient imported observations first.","features":[]}
    names=metrics.get("features",[]); return {"status":"available","method":"Random Forest impurity-based feature importance","caveat":"Associations only; not causal explanations.","features":[{"name":n,"importance":float(v)} for n,v in zip(names,model.feature_importances_)]}
@app.get("/api/data-sources")
def sources():
    rows=read_rows(); return {"providers":[{"name":"Open-Meteo Air Quality API","type":"modelled grid forecast","location":LOCATION,"configured":True},{"name":"Local CSV import","type":"user-supplied observations","records":len(rows),"coverage":{"start":rows[0]["timestamp"] if rows else None,"end":rows[-1]["timestamp"] if rows else None}}]}
@app.post("/api/data/import")
async def import_csv(file:UploadFile=File(...)):
    if not file.filename.lower().endswith(".csv"): raise HTTPException(415,"Upload a CSV file")
    raw=await file.read(5_000_000)
    if len(raw)>=5_000_000: raise HTTPException(413,"Maximum CSV size is 5 MB")
    try: parsed=list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except Exception: raise HTTPException(400,"Could not read CSV as UTF-8")
    if not parsed or not {"timestamp","pm25","pm10"}.issubset(parsed[0]): raise HTTPException(422,"Required headers: timestamp,pm25,pm10")
    valid,rejected=validate_rows(parsed); total=save_rows(valid)
    return {"imported":len(valid),"rejected":rejected,"total_records":total,"message":"CSV validated and stored locally"}
@app.get("/api/diwali-analysis")
def diwali_analysis(year:int, festival_date:date, station:Optional[str]=None):
    rows=[r for r in read_rows() if (not station or r["station"]==station)]
    windows={"pre_diwali":(festival_date-timedelta(days=3),festival_date-timedelta(days=1)),"diwali_day":(festival_date,festival_date),"post_diwali":(festival_date+timedelta(days=1),festival_date+timedelta(days=3))}
    result={}
    for name,(start,end) in windows.items():
        selected=[r for r in rows if start<=datetime.fromisoformat(r["timestamp"]).date()<=end]
        result[name]={"start":start.isoformat(),"end":end.isoformat(),"records":len(selected)}
        for pollutant in ("pm25","pm10"):
            values=sorted(float(r[pollutant]) for r in selected if r.get(pollutant) not in (None,""))
            result[name][pollutant]={"mean":sum(values)/len(values) if values else None,"median":(values[len(values)//2] if len(values)%2 else (values[len(values)//2-1]+values[len(values)//2])/2) if values else None,"max":max(values) if values else None,"valid_count":len(values)}
    has_values=any(result[w][p]["valid_count"] for w in result for p in ("pm25","pm10"))
    return {"year":year,"festival_date":festival_date.isoformat(),"station":station,"status":"calculated" if has_values else "insufficient_data","windows":result,"records":len(rows),"message":"Descriptive station observation summary. Period completeness depends on available imported records; no causal attribution is implied."}

@app.get("/api/historical/diwali-dates")
def historical_diwali_dates():
    as_of=hist.today()
    return {"as_of":as_of.isoformat(),"dates":[{"year":y,"date":d.isoformat(),"status":"upcoming" if d>as_of else "past","source":"Reference holiday calendar; local observance may differ","source_url":hist.DATE_SOURCES[y]} for y,d in hist.DIWALI_DATES.items()]}

@app.get("/api/historical/coverage")
def historical_coverage():
    observations=hist.catalog()
    records=sum(item["records"] for item in observations)
    metric_counts={metric:sum(item[f"{metric}_days"] for item in observations) for metric in hist.METRICS}
    available_years=sorted({year for item in observations for year in item["available_diwali_years"]})
    return {"observations":observations,"records_loaded":records,"city_station_pairs":len(observations),"metric_days":metric_counts,"available_diwali_years":available_years,"data_kind":"observed","pm_unit":hist.CONCENTRATION_UNIT,"status":"available" if records else "empty","note":"Only user-imported daily station records appear here. Source labels are uploader-supplied, not independently verified."}

@app.get("/api/historical/diwali")
def historical_diwali(city:str,station:str,years:str,days:int=Query(default=3,ge=1,le=7)):
    try: selected=[int(x) for x in years.split(",") if x]
    except ValueError: raise HTTPException(422,"Years must be comma-separated numbers")
    if len(set(selected))<2: raise HTTPException(422,"Select at least two years")
    try: return hist.compare(city.strip(),station.strip(),selected,days)
    except ValueError as e: raise HTTPException(422,str(e))

def _read_historical_upload(file_bytes,source_name,source_url,unit):
    try:
        parsed=hist.validate_csv(file_bytes,source_name,source_url,unit)
    except ValueError as e:
        raise HTTPException(422,str(e))
    database_keys=hist.existing_keys()
    duplicate_existing=sum(1 for row in parsed["rows"] if hist.observation_key(row["city"],row["station"],row["date"]) in database_keys)
    return parsed,duplicate_existing

@app.post("/api/historical/validate")
async def validate_historical_csv(file:UploadFile=File(...),source_name:str=Form(...),source_url:Optional[str]=Form(default=None),unit:str=Form(default="ug/m3")):
    if not file.filename or not file.filename.lower().endswith(".csv"): raise HTTPException(415,"Upload a CSV file")
    raw=await file.read(10_000_001)
    if len(raw)>10_000_000: raise HTTPException(413,"Maximum file size is 10 MB")
    parsed,duplicate_existing=_read_historical_upload(raw,source_name,source_url,unit)
    return {"valid_rows":parsed["valid_rows"],"new_rows":parsed["valid_rows"]-duplicate_existing,"rejected_rows":parsed["rejected_rows"],"duplicate_rows_in_file":parsed["duplicate_rows"],"duplicate_rows_already_saved":duplicate_existing,"date_field":parsed["date_field"],"date_from":parsed["date_from"],"date_to":parsed["date_to"],"stations":parsed["stations"],"provided_metrics":parsed["provided_metrics"],"valid_metric_days":parsed["metric_days"],"unit":parsed["unit"],"warning":"Station names and source labels are not checked against CPCB by this app."}

@app.post("/api/historical/import")
async def import_historical_csv(file:UploadFile=File(...),source_name:str=Form(...),source_url:Optional[str]=Form(default=None),unit:str=Form(default="ug/m3")):
    if not file.filename or not file.filename.lower().endswith(".csv"): raise HTTPException(415,"Upload a CSV file")
    raw=await file.read(10_000_001)
    if len(raw)>10_000_000: raise HTTPException(413,"Maximum file size is 10 MB")
    parsed,duplicate_existing=_read_historical_upload(raw,source_name,source_url,unit)
    imported=0
    with hist.connect() as db:
        for row in parsed["rows"]:
            cur=db.execute("""INSERT OR IGNORE INTO daily_observations
                (city,station,observed_date,pm25,pm10,aqi,pm_unit,source_name,source_url,imported_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (row["city"],row["station"],row["date"],row["pm25"],row["pm10"],row["aqi"],row["pm_unit"],row["source_name"],row["source_url"],datetime.now(timezone.utc).isoformat()))
            if cur.rowcount: imported+=1
        total=db.execute("SELECT count(*) FROM daily_observations").fetchone()[0]
    duplicates=parsed["duplicate_rows"]+duplicate_existing+(parsed["valid_rows"]-duplicate_existing-imported)
    return {"imported":imported,"rejected":parsed["rejected_rows"],"duplicates":duplicates,"records_loaded":total,"units":"PM₂.₅/PM₁₀ stored as µg/m³; AQI is unitless and source-reported.","provenance":"Source is uploader-supplied and not independently verified by the app.","message":"Daily station records saved locally. Check station, source, dates, and daily aggregation method before comparing."}
@app.get("/api/alerts")
async def alerts():
    d=await latest(); return {"alerts":[],"status":"no active rule triggers evaluated" if d.get("status")!="available" else "no threshold rules configured","data_status":d.get("status")}

# Green Community: a local MVP. Profiles are pseudonymous and self-declared; there is no login.
class Person(BaseModel):
    user_id:str=Field(min_length=8,max_length=80,pattern=r"^[a-zA-Z0-9_-]+$")
    display_name:str=Field(min_length=2,max_length=40)
class CommunityCreate(BaseModel):
    name:str=Field(min_length=3,max_length=80); city:str=Field(min_length=2,max_length=80); description:str=Field(min_length=10,max_length=500)
class Pledge(Person): community_id:Optional[int]=None
class Complete(Person): challenge_id:str; community_id:Optional[int]=None
class EventCreate(Person): community_id:int; name:str=Field(min_length=4,max_length=100); description:str=Field(min_length=10,max_length=800); starts_at:datetime; location:str=Field(min_length=3,max_length=180)
class EventJoin(Person): event_id:int
class PostCreate(Person): community_id:Optional[int]=None; content:str=Field(min_length=2,max_length=1000); image_url:Optional[str]=Field(default=None,max_length=250)
class CommentCreate(Person): post_id:int; content:str=Field(min_length=2,max_length=500)
class LikeCreate(Person): post_id:int
class ReportCreate(Person): kind:str=Field(pattern=r"^(post|comment)$"); item_id:int; reason:str=Field(min_length=4,max_length=240)

def community_exists(c,cid):
    if cid is None: return
    if not c.execute("SELECT 1 FROM communities WHERE id=?",(cid,)).fetchone(): raise HTTPException(404,"Community not found")
def require_membership(c,cid,user_id):
    community_exists(c,cid)
    if cid is not None and not c.execute("SELECT 1 FROM memberships WHERE community_id=? AND user_id=?",(cid,user_id)).fetchone():
        raise HTTPException(403,"Join this community before adding community activity")
def clean_text(value): return " ".join(value.strip().split())

@app.get("/api/community/communities")
def community_list(q:Optional[str]=None):
    with cm.connect() as c:
        rows=c.execute("SELECT c.*, (SELECT count(*) FROM memberships m WHERE m.community_id=c.id) members FROM communities c WHERE (? IS NULL OR c.name LIKE ? OR c.city LIKE ?) ORDER BY c.name",(q,f"%{q}%",f"%{q}%")).fetchall()
        return [dict(r) for r in rows]

@app.get("/api/community/memberships")
def my_memberships(user_id:str):
    with cm.connect() as c: return [dict(r) for r in c.execute("SELECT c.id,c.name,c.city,m.joined_at FROM memberships m JOIN communities c ON c.id=m.community_id WHERE m.user_id=? ORDER BY c.name",(user_id,)).fetchall()]

@app.get("/api/community/progress")
def my_progress(user_id:str):
    with cm.connect() as c:
        p=c.execute("SELECT coalesce(sum(points),0) points,count(*) actions FROM points WHERE user_id=?",(user_id,)).fetchone()
        return {"points":p["points"],"activities":p["actions"],"pledged":bool(c.execute("SELECT 1 FROM pledges WHERE user_id=?",(user_id,)).fetchone()),"completed_challenges":[r[0] for r in c.execute("SELECT challenge_id FROM completions WHERE user_id=?",(user_id,)).fetchall()],"joined_communities":[dict(r) for r in c.execute("SELECT c.id,c.name,c.city FROM memberships m JOIN communities c ON c.id=m.community_id WHERE m.user_id=?",(user_id,)).fetchall()]}

@app.post("/api/community/communities",status_code=201)
def community_create(body:CommunityCreate):
    try:
        with cm.connect() as c:
            cur=c.execute("INSERT INTO communities(name,city,description,created_at) VALUES(?,?,?,?)",(clean_text(body.name),clean_text(body.city),clean_text(body.description),cm.now())); return {"id":cur.lastrowid,"name":clean_text(body.name),"city":clean_text(body.city),"description":clean_text(body.description)}
    except Exception as e:
        if "UNIQUE" in str(e): raise HTTPException(409,"A community with this name already exists")
        raise

@app.get("/api/community/communities/{cid}")
def community_detail(cid:int):
    with cm.connect() as c:
        row=c.execute("SELECT c.*,(SELECT count(*) FROM memberships WHERE community_id=c.id) members FROM communities c WHERE c.id=?",(cid,)).fetchone()
        if not row: raise HTTPException(404,"Community not found")
        return dict(row)

@app.post("/api/community/communities/{cid}/join")
def community_join(cid:int,body:Person):
    with cm.connect() as c:
        community_exists(c,cid); c.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?,?)",(cid,body.user_id,clean_text(body.display_name),cm.now()))
        return {"joined":True,"members":c.execute("SELECT count(*) FROM memberships WHERE community_id=?",(cid,)).fetchone()[0]}

@app.post("/api/community/communities/{cid}/leave")
def community_leave(cid:int,body:Person):
    with cm.connect() as c:
        community_exists(c,cid); c.execute("DELETE FROM memberships WHERE community_id=? AND user_id=?",(cid,body.user_id))
        return {"joined":False,"members":c.execute("SELECT count(*) FROM memberships WHERE community_id=?",(cid,)).fetchone()[0]}

@app.get("/api/community/challenges")
def challenges(user_id:Optional[str]=None):
    with cm.connect() as c:
        done={r[0] for r in c.execute("SELECT challenge_id FROM completions WHERE user_id=?",(user_id,)).fetchall()} if user_id else set()
        return [{**x,"completed":x["id"] in done} for x in cm.CHALLENGES]

@app.post("/api/community/challenges/complete")
def challenge_complete(body:Complete):
    challenge=next((x for x in cm.CHALLENGES if x["id"]==body.challenge_id),None)
    if not challenge: raise HTTPException(404,"Challenge not found")
    with cm.connect() as c:
        require_membership(c,body.community_id,body.user_id)
        if challenge["kind"]=="pledge":
            cur=c.execute("INSERT OR IGNORE INTO pledges(user_id,display_name,community_id,created_at) VALUES(?,?,?,?)",(body.user_id,clean_text(body.display_name),body.community_id,cm.now()))
            c.execute("INSERT OR IGNORE INTO completions(user_id,display_name,challenge_id,community_id,created_at) VALUES(?,?,?,?,?)",(body.user_id,clean_text(body.display_name),body.challenge_id,body.community_id,cm.now()))
            awarded=cur.rowcount==1
            if awarded: cm.award(c,body.user_id,clean_text(body.display_name),body.community_id,"pledge","green-diwali",challenge["points"])
        else:
            cur=c.execute("INSERT OR IGNORE INTO completions(user_id,display_name,challenge_id,community_id,created_at) VALUES(?,?,?,?,?)",(body.user_id,clean_text(body.display_name),body.challenge_id,body.community_id,cm.now()))
            awarded=cur.rowcount==1
            if awarded: cm.award(c,body.user_id,clean_text(body.display_name),body.community_id,"challenge",body.challenge_id,challenge["points"])
        return {"completed":True,"new_points":challenge["points"] if awarded else 0,"already_completed":not awarded,"points_are_participation_not_pollution_reduction":True}

@app.post("/api/community/pledges")
def pledge(body:Pledge):
    with cm.connect() as c:
        require_membership(c,body.community_id,body.user_id)
        cur=c.execute("INSERT OR IGNORE INTO pledges(user_id,display_name,community_id,created_at) VALUES(?,?,?,?)",(body.user_id,clean_text(body.display_name),body.community_id,cm.now()))
        awarded=cur.rowcount==1
        c.execute("INSERT OR IGNORE INTO completions(user_id,display_name,challenge_id,community_id,created_at) VALUES(?,?,?,?,?)",(body.user_id,clean_text(body.display_name),"pledge",body.community_id,cm.now()))
        if awarded: cm.award(c,body.user_id,clean_text(body.display_name),body.community_id,"pledge","green-diwali",10)
        count=c.execute("SELECT count(*) FROM pledges").fetchone()[0]
        return {"pledged":True,"new_points":10 if awarded else 0,"already_pledged":not awarded,"pledge_count":count}

@app.get("/api/community/pledges")
def pledge_count():
    with cm.connect() as c: return {"count":c.execute("SELECT count(*) FROM pledges").fetchone()[0]}

@app.get("/api/community/leaderboard")
def leaderboard(period:str="overall",scope:str="individual",community_id:Optional[int]=None):
    if period not in ("weekly","overall") or scope not in ("individual","community"): raise HTTPException(422,"Choose a supported period and leaderboard type")
    since=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    with cm.connect() as c:
        if scope=="individual":
            rows=c.execute("SELECT p.user_id,p.display_name,sum(p.points) points,count(DISTINCT p.source_type||':'||p.source_id) activities FROM points p WHERE (?='overall' OR p.created_at>=?) AND (? IS NULL OR p.community_id=?) GROUP BY p.user_id ORDER BY points DESC,activities DESC LIMIT 50",(period,since,community_id,community_id)).fetchall()
        else:
            rows=c.execute("SELECT c.id community_id,c.name,sum(p.points) points,count(DISTINCT p.user_id) members,count(p.id) activities FROM points p JOIN communities c ON c.id=p.community_id WHERE (?='overall' OR p.created_at>=?) AND (? IS NULL OR c.id=?) GROUP BY c.id ORDER BY points DESC,activities DESC LIMIT 50",(period,since,community_id,community_id)).fetchall()
        return [dict(rank=i+1,**dict(r)) for i,r in enumerate(rows)]

@app.get("/api/community/events")
def events(community_id:Optional[int]=None,user_id:Optional[str]=None):
    with cm.connect() as c:
        return [dict(r) for r in c.execute("SELECT e.*,c.name community_name,(SELECT count(*) FROM event_participants p WHERE p.event_id=e.id) participants,EXISTS(SELECT 1 FROM event_participants p WHERE p.event_id=e.id AND p.user_id=?) joined FROM events e JOIN communities c ON c.id=e.community_id WHERE (? IS NULL OR e.community_id=?) ORDER BY e.starts_at",(user_id,community_id,community_id)).fetchall()]

@app.post("/api/community/events",status_code=201)
def event_create(body:EventCreate):
    if body.starts_at.tzinfo is None: raise HTTPException(422,"Please include a time zone in the event date and time")
    if body.starts_at <= datetime.now(timezone.utc): raise HTTPException(422,"Event date and time must be in the future")
    with cm.connect() as c:
        require_membership(c,body.community_id,body.user_id)
        cur=c.execute("INSERT INTO events(community_id,name,description,starts_at,location,organiser_id,organiser_name,created_at) VALUES(?,?,?,?,?,?,?,?)",(body.community_id,clean_text(body.name),clean_text(body.description),body.starts_at.isoformat(),clean_text(body.location),body.user_id,clean_text(body.display_name),cm.now()))
        return {"id":cur.lastrowid,"message":"Event created. Organiser identity is self-declared in this local mode."}

@app.post("/api/community/events/join")
def event_join(body:EventJoin):
    with cm.connect() as c:
        event=c.execute("SELECT id,community_id FROM events WHERE id=?",(body.event_id,)).fetchone()
        if not event: raise HTTPException(404,"Event not found")
        cur=c.execute("INSERT OR IGNORE INTO event_participants VALUES(?,?,?,?)",(body.event_id,body.user_id,clean_text(body.display_name),cm.now()))
        if cur.rowcount==1: cm.award(c,body.user_id,clean_text(body.display_name),event["community_id"],"event",body.event_id,30)
        return {"joined":True,"already_joined":cur.rowcount==0,"participants":c.execute("SELECT count(*) FROM event_participants WHERE event_id=?",(body.event_id,)).fetchone()[0]}

@app.post("/api/community/events/leave")
def event_leave(body:EventJoin):
    with cm.connect() as c:
        if not c.execute("SELECT 1 FROM events WHERE id=?",(body.event_id,)).fetchone(): raise HTTPException(404,"Event not found")
        c.execute("DELETE FROM event_participants WHERE event_id=? AND user_id=?",(body.event_id,body.user_id))
        return {"joined":False,"message":"Your RSVP was removed. Participation points already earned remain recorded as a past participation action."}

@app.post("/api/community/posts",status_code=201)
def post_create(body:PostCreate):
    content=clean_text(body.content)
    if any(x in content.lower() for x in ("<script", "javascript:")): raise HTTPException(422,"Please remove unsafe content")
    with cm.connect() as c:
        require_membership(c,body.community_id,body.user_id)
        cur=c.execute("INSERT INTO posts(community_id,user_id,display_name,content,image_url,created_at) VALUES(?,?,?,?,?,?)",(body.community_id,body.user_id,clean_text(body.display_name),content,body.image_url,cm.now()))
        return {"id":cur.lastrowid,"status":"visible","message":"Published in local community mode."}

@app.get("/api/community/feed")
def feed(community_id:Optional[int]=None):
    with cm.connect() as c:
        rows=c.execute("SELECT p.*,coalesce(c.name,'Green Community') community_name,(SELECT count(*) FROM likes l WHERE l.post_id=p.id) likes,(SELECT count(*) FROM comments x WHERE x.post_id=p.id AND x.status='visible') comments_count FROM posts p LEFT JOIN communities c ON c.id=p.community_id WHERE p.status='visible' AND (? IS NULL OR p.community_id=?) ORDER BY p.created_at DESC LIMIT 100",(community_id,community_id)).fetchall()
        result=[]
        for r in rows:
            item=dict(r); item["comments"]=[dict(x) for x in c.execute("SELECT display_name,content,created_at FROM comments WHERE post_id=? AND status='visible' ORDER BY created_at",(r["id"],)).fetchall()]; result.append(item)
        return result

@app.post("/api/community/likes")
def like(body:LikeCreate):
    with cm.connect() as c:
        if not c.execute("SELECT 1 FROM posts WHERE id=? AND status='visible'",(body.post_id,)).fetchone(): raise HTTPException(404,"Post not found")
        exists=c.execute("SELECT 1 FROM likes WHERE post_id=? AND user_id=?",(body.post_id,body.user_id)).fetchone()
        if exists: c.execute("DELETE FROM likes WHERE post_id=? AND user_id=?",(body.post_id,body.user_id)); liked=False
        else: c.execute("INSERT INTO likes VALUES(?,?,?)",(body.post_id,body.user_id,cm.now())); liked=True
        count=c.execute("SELECT count(*) FROM likes WHERE post_id=?",(body.post_id,)).fetchone()[0]
        return {"liked":liked,"likes":count}

@app.post("/api/community/comments",status_code=201)
def comment(body:CommentCreate):
    with cm.connect() as c:
        if not c.execute("SELECT 1 FROM posts WHERE id=? AND status='visible'",(body.post_id,)).fetchone(): raise HTTPException(404,"Post not found")
        cur=c.execute("INSERT INTO comments(post_id,user_id,display_name,content,created_at) VALUES(?,?,?,?,?)",(body.post_id,body.user_id,clean_text(body.display_name),clean_text(body.content),cm.now()))
        return {"id":cur.lastrowid,"status":"visible"}

@app.post("/api/community/reports",status_code=201)
def report(body:ReportCreate):
    with cm.connect() as c:
        table="posts" if body.kind=="post" else "comments"
        if not c.execute(f"SELECT 1 FROM {table} WHERE id=?",(body.item_id,)).fetchone(): raise HTTPException(404,"Content not found")
        try: c.execute("INSERT INTO reports(reporter_id,kind,item_id,reason,created_at) VALUES(?,?,?,?,?)",(body.user_id,body.kind,body.item_id,clean_text(body.reason),cm.now()))
        except Exception as e:
            if "UNIQUE" in str(e): raise HTTPException(409,"You already reported this item")
            raise
        return {"received":True,"status":"pending review","message":"Reports are recorded locally. There is no staffed moderation service in this MVP."}

@app.get("/api/community/impact")
def impact(community_id:Optional[int]=None):
    with cm.connect() as c:
        filt=" AND community_id=?" if community_id else ""; args=(community_id,) if community_id else ()
        members=c.execute("SELECT count(*) FROM memberships WHERE 1=1"+filt,args).fetchone()[0]
        pledges=c.execute("SELECT count(*) FROM pledges WHERE 1=1"+filt,args).fetchone()[0]
        challenges=c.execute("SELECT count(*) FROM completions WHERE status='complete'"+filt,args).fetchone()[0]
        eventcount=c.execute("SELECT count(*) FROM events WHERE 1=1"+filt,args).fetchone()[0]
        participants=c.execute("SELECT count(*) FROM event_participants p JOIN events e ON e.id=p.event_id WHERE 1=1"+(" AND e.community_id=?" if community_id else ""),args).fetchone()[0]
        points=c.execute("SELECT coalesce(sum(points),0) FROM points WHERE 1=1"+filt,args).fetchone()[0]
        return {"members":members,"pledges":pledges,"completed_challenges":challenges,"events_organised":eventcount,"event_participations":participants,"points_earned":points,"note":"Participation counts only; these figures do not measure or imply pollution reduction."}

@app.post("/api/community/uploads",status_code=201)
async def upload_post_image(file:UploadFile=File(...)):
    import uuid
    allowed={"image/jpeg":".jpg","image/png":".png","image/webp":".webp"}
    if file.content_type not in allowed: raise HTTPException(415,"Use a JPG, PNG, or WebP image")
    data=await file.read(3_000_001)
    if len(data)>3_000_000: raise HTTPException(413,"Images must be 3 MB or smaller")
    name=uuid.uuid4().hex+allowed[file.content_type]; (UPLOADS/name).write_bytes(data)
    return {"image_url":"/uploads/"+name,"stored_locally":True}

