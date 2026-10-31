"""Existing import and Diwali-aligned historical station observations API."""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from app.data import historical as hist
router=APIRouter(prefix='/api/historical',tags=['Historical observations'])

@router.get('/diwali-dates')
def diwali_dates():
    as_of=hist.today()
    return {'as_of':as_of.isoformat(),'dates':[{'year':y,'date':d.isoformat(),'status':'upcoming' if d>as_of else 'past','source':'Reference holiday calendar; local observance may differ','source_url':hist.DATE_SOURCES[y]} for y,d in hist.DIWALI_DATES.items()]}

@router.get('/coverage')
def coverage():
    observations=hist.catalog(); records=sum(x['records'] for x in observations)
    counts={metric:sum(x[f'{metric}_days'] for x in observations) for metric in hist.METRICS}
    years=sorted({year for item in observations for year in item['available_diwali_years']})
    return {'observations':observations,'records_loaded':records,'city_station_pairs':len(observations),'metric_days':counts,'available_diwali_years':years,'data_kind':'observed','pm_unit':hist.CONCENTRATION_UNIT,'status':'available' if records else 'empty','note':'Only imported daily station records appear here. Source labels are uploader-supplied, not independently verified.'}

@router.get('/diwali')
def compare(city:str,station:str,years:str,days:int=Query(default=3,ge=1,le=7)):
    try: selected=[int(x) for x in years.split(',') if x]
    except ValueError: raise HTTPException(422,'Years must be comma-separated numbers')
    if len(set(selected))<2:raise HTTPException(422,'Select at least two years')
    try:return hist.compare(city.strip(),station.strip(),selected,days)
    except ValueError as e:raise HTTPException(422,str(e))

def parse(raw,source_name,source_url,unit):
    try:parsed=hist.validate_csv(raw,source_name,source_url,unit)
    except ValueError as e:raise HTTPException(422,str(e))
    keys=hist.existing_keys()
    duplicate=sum(1 for r in parsed['rows'] if hist.observation_key(r['city'],r['station'],r['date']) in keys)
    return parsed,duplicate

async def read_csv(file):
    if not file.filename or not file.filename.lower().endswith('.csv'):raise HTTPException(415,'Upload a CSV file')
    raw=await file.read(10_000_001)
    if len(raw)>10_000_000:raise HTTPException(413,'Maximum file size is 10 MB')
    return raw

@router.post('/validate')
async def validate(file:UploadFile=File(...),source_name:str=Form(...),source_url:Optional[str]=Form(default=None),unit:str=Form(default='ug/m3')):
    parsed,dupes=parse(await read_csv(file),source_name,source_url,unit)
    return {'valid_rows':parsed['valid_rows'],'new_rows':parsed['valid_rows']-dupes,'rejected_rows':parsed['rejected_rows'],'duplicate_rows_in_file':parsed['duplicate_rows'],'duplicate_rows_already_saved':dupes,'date_field':parsed['date_field'],'date_from':parsed['date_from'],'date_to':parsed['date_to'],'stations':parsed['stations'],'provided_metrics':parsed['provided_metrics'],'valid_metric_days':parsed['metric_days'],'unit':parsed['unit'],'warning':'Station names and source labels are not checked against CPCB by this app.'}

@router.post('/import')
async def import_csv(file:UploadFile=File(...),source_name:str=Form(...),source_url:Optional[str]=Form(default=None),unit:str=Form(default='ug/m3')):
    parsed,dupes=parse(await read_csv(file),source_name,source_url,unit); imported=0
    with hist.connect() as c:
        for row in parsed['rows']:
            cur=c.execute('INSERT OR IGNORE INTO daily_observations(city,station,observed_date,pm25,pm10,aqi,pm_unit,source_name,source_url,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(row['city'],row['station'],row['date'],row['pm25'],row['pm10'],row['aqi'],row['pm_unit'],row['source_name'],row['source_url'],datetime.now(timezone.utc).isoformat()))
            imported+=cur.rowcount
        total=c.execute('SELECT count(*) FROM daily_observations').fetchone()[0]
    return {'imported':imported,'rejected':parsed['rejected_rows'],'duplicates':parsed['duplicate_rows']+dupes+(parsed['valid_rows']-dupes-imported),'records_loaded':total,'units':'PM₂.₅/PM₁₀ stored as µg/m³; AQI is unitless and source-reported.','provenance':'Source is uploader-supplied and not independently verified.','message':'Station-day records saved locally; compare dates, station and source coverage before interpretation.'}
