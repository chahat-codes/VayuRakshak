"""Daily imported historical observations for Diwali comparisons."""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DB=Path(__file__).resolve().parents[2]/"data"/"historical.sqlite3"
DIWALI_DATES={2022:date(2022,10,24),2023:date(2023,11,12),2024:date(2024,10,31),2025:date(2025,10,20),2026:date(2026,11,8)}
DATE_SOURCES={2022:"https://www.timeanddate.com/holidays/india/diwali",2023:"https://www.timeanddate.com/holidays/india/diwali",2024:"https://cag.gov.in/uploads/media/List-of-holidays-2024-068c3d96130a564-11046917.pdf",2025:"https://www.timeanddate.com/holidays/india/diwali",2026:"https://www.indiapost.gov.in/holidays-list"}

def today(): return datetime.now(ZoneInfo("Asia/Kolkata")).date()

@contextmanager
def connect():
    DB.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DB,timeout=10); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db; db.commit()
    except Exception:
        db.rollback(); raise
    finally: db.close()

def init():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS daily_observations(
          id INTEGER PRIMARY KEY, city TEXT NOT NULL, station TEXT NOT NULL, observed_date TEXT NOT NULL,
          pm25 REAL, pm10 REAL, aqi INTEGER, source_name TEXT NOT NULL, source_url TEXT,
          imported_at TEXT NOT NULL, UNIQUE(city,station,observed_date))""")

def catalog():
    with connect() as db:
        rows=db.execute("SELECT city,station,count(*) records,min(observed_date) start,max(observed_date) end,group_concat(DISTINCT source_name) sources FROM daily_observations GROUP BY city,station ORDER BY city,station").fetchall()
        return [dict(r) for r in rows]

def compare(city,station,years,days=3):
    years=sorted(set(years))
    if len(years)<2: raise ValueError("Select at least two years")
    if any(y not in DIWALI_DATES for y in years): raise ValueError("Choose years from 2022 through 2026")
    as_of=today()
    results=[]
    with connect() as db:
        for year in years:
            festival=DIWALI_DATES[year]
            bounds={"before":(festival-timedelta(days=days),festival-timedelta(days=1)),"during":(festival,festival),"after":(festival+timedelta(days=1),festival+timedelta(days=days))}
            windows={}
            for key,(start,end) in bounds.items():
                if start>as_of:
                    windows[key]={"start":start.isoformat(),"end":end.isoformat(),"status":"upcoming","expected_days":(end-start).days+1,"observed_days":0,"missing_days":(end-start).days+1,"metrics":{},"records":[]}
                    continue
                effective_end=min(end,as_of)
                rows=db.execute("SELECT observed_date,pm25,pm10,aqi,source_name,source_url FROM daily_observations WHERE city=? AND station=? AND observed_date BETWEEN ? AND ? ORDER BY observed_date",(city,station,start.isoformat(),effective_end.isoformat())).fetchall()
                observed_dates={r["observed_date"] for r in rows}
                expected=(end-start).days+1
                metrics={}
                for key_metric in ("pm25","pm10","aqi"):
                    vals=sorted(float(r[key_metric]) for r in rows if r[key_metric] is not None)
                    metrics[key_metric]={"mean":sum(vals)/len(vals) if vals else None,"median":(vals[len(vals)//2] if len(vals)%2 else (vals[len(vals)//2-1]+vals[len(vals)//2])/2) if vals else None,"max":max(vals) if vals else None,"valid_days":len(vals)}
                sources=sorted({r["source_name"] for r in rows})
                urls=sorted({r["source_url"] for r in rows if r["source_url"]})
                missing=max(0,expected-len(observed_dates))
                windows[key]={"start":start.isoformat(),"end":end.isoformat(),"effective_end":effective_end.isoformat(),"status":"available" if rows else ("partial_period_no_data" if end>as_of else "no_data"),"expected_days":expected,"observed_days":len(observed_dates),"missing_days":missing,"metrics":metrics,"records":[dict(r) for r in rows],"sources":sources,"source_urls":urls,"provenance":"User-imported; source label is self-declared and not independently verified by this app."}
            results.append({"year":year,"festival_date":festival.isoformat(),"festival_status":"upcoming" if festival>as_of else "past","windows":windows})
    return {"city":city,"station":station,"years":years,"days_each_side":days,"as_of":as_of.isoformat(),"results":results,"summary":_summary(results)}

def _summary(results):
    summary={}
    if len(results)<2: return summary
    for metric in ("pm25","pm10","aqi"):
        pairs=[]
        for year in results:
            before=year["windows"]["before"]["metrics"].get(metric,{}).get("mean")
            during=year["windows"]["during"]["metrics"].get(metric,{}).get("mean")
            if before is not None and during is not None and before>0:
                pairs.append({"year":year["year"],"before_mean":before,"during_mean":during,"change_percent":(during-before)/before*100})
        summary[metric]={"comparisons":pairs,"message":_trend(pairs,metric)}
    return summary

def _trend(pairs,metric):
    if not pairs: return "Not enough measured data in both periods to compare."
    changes=[x["change_percent"] for x in pairs]
    mean=sum(changes)/len(changes)
    if abs(mean)<5: return f"Across {len(pairs)} comparable year(s), {metric.upper()} was broadly similar during Diwali to the before period (mean change {mean:+.1f}%)."
    direction="increased" if mean>0 else "decreased"
    return f"Across {len(pairs)} comparable year(s), {metric.upper()} {direction} by an average {abs(mean):.1f}% from the before period to Diwali day. This is descriptive, not proof of cause."

init()
