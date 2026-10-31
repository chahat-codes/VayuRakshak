"""Compatibility routes for existing local observations and model artifacts."""
import csv, io
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from app.data.store import read_rows, validate_rows, save_rows
from app.ml.pipeline import load_artifacts
router=APIRouter(tags=['Air data and model compatibility'])

@router.get('/api/air-quality/history')
def observation_history(station:Optional[str]=None):
    rows=read_rows(); selected=[r for r in rows if not station or r.get('station')==station]
    return {'source':'User-imported observations','records':selected,'count':len(selected)}

@router.get('/api/reports/export')
def export_observations():
    rows=read_rows(); buf=io.StringIO(); fields=['timestamp','pm25','pm10','temperature','humidity','wind_speed','rainfall','station','latitude','longitude']; writer=csv.DictWriter(buf,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=vayurakshak-observations.csv'})

@router.post('/api/data/import')
async def import_observations(file:UploadFile=File(...)):
    if not file.filename or not file.filename.lower().endswith('.csv'):raise HTTPException(415,'Upload a CSV file')
    raw=await file.read(5_000_001)
    if len(raw)>5_000_000:raise HTTPException(413,'Maximum CSV size is 5 MB')
    try:parsed=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    except Exception:raise HTTPException(400,'Could not read CSV as UTF-8')
    if not parsed or not {'timestamp','pm25','pm10'}.issubset(parsed[0]):raise HTTPException(422,'Required headers: timestamp,pm25,pm10')
    valid,rejected=validate_rows(parsed); total=save_rows(valid)
    return {'imported':len(valid),'rejected':rejected,'total_records':total,'message':'CSV validated and stored locally'}

@router.get('/api/model/performance')
def performance():
    model,metrics=load_artifacts();return {'status':'evaluated' if metrics else 'not evaluated','model':'Random Forest Regressor','metrics':metrics}

@router.get('/api/model/explanations')
def explanations():
    model,metrics=load_artifacts()
    if not model:return {'status':'unavailable','reason':'Train a model from sufficient imported observations first.','features':[]}
    names=metrics.get('features',[]);return {'status':'available','method':'Random Forest impurity-based feature importance','caveat':'Associations only; not causal explanations.','features':[{'name':n,'importance':float(v)} for n,v in zip(names,model.feature_importances_)]}

@router.get('/api/data-sources')
def sources():
    rows=read_rows();return {'providers':[{'name':'Open-Meteo Air Quality API','type':'modelled grid forecast','configured':True},{'name':'Local CSV import','type':'user-supplied observations','records':len(rows),'coverage':{'start':rows[0]['timestamp'] if rows else None,'end':rows[-1]['timestamp'] if rows else None}}]}

@router.get('/api/diwali-analysis')
def diwali_analysis(year:int,festival_date:date,station:Optional[str]=None):
    rows=[r for r in read_rows() if not station or r.get('station')==station]; windows={'before':(festival_date-timedelta(days=3),festival_date-timedelta(days=1)),'during':(festival_date,festival_date),'after':(festival_date+timedelta(days=1),festival_date+timedelta(days=3))};result={}
    for name,(start,end) in windows.items():
        picked=[r for r in rows if start<=datetime.fromisoformat(r['timestamp'].replace('Z','+00:00')).date()<=end];result[name]={'start':start.isoformat(),'end':end.isoformat(),'records':len(picked)}
        for metric in ('pm25','pm10'):
            vals=sorted(float(r[metric]) for r in picked if r.get(metric) not in (None,''));result[name][metric]={'mean':sum(vals)/len(vals) if vals else None,'max':max(vals) if vals else None,'valid_count':len(vals)}
    has_data=any(result[w][m]['valid_count'] for w in result for m in ('pm25','pm10'))
    return {'year':year,'festival_date':festival_date.isoformat(),'station':station,'status':'calculated' if has_data else 'insufficient_data','windows':result,'records':len(rows),'message':'Descriptive user-imported observations; period completeness depends on supplied data and no causal attribution is implied.'}
