"""Authenticated VAYU CAMPUS workflows using the existing community database."""
import csv, hashlib, hmac, io, json, os, secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from fastapi import APIRouter, Cookie, Depends, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.data import community as db

router=APIRouter(prefix='/api/campus',tags=['VAYU CAMPUS'])
PRIVATE=Path(__file__).resolve().parents[1]/'data'/'private-campus'
PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700)
os.chmod(PRIVATE,0o700)
ADMIN=os.getenv('VAYU_ADMIN_TOKEN','')

def now(): return datetime.now(timezone.utc)
def iso(): return now().isoformat()
def hash_token(value): return hashlib.sha256(value.encode()).hexdigest()
def hash_password(value,salt=None):
    salt=salt or secrets.token_bytes(16)
    import hashlib as _h
    return salt.hex()+':'+_h.scrypt(value.encode(),salt=salt,n=2**14,r=8,p=1).hex()
def verify_password(value, stored):
    try:
        salt_hex,digest=stored.split(':',1); return hmac.compare_digest(hash_password(value,bytes.fromhex(salt_hex)),stored)
    except Exception:return False

def audit(c,actor,action,entity,entity_id,details=None):
    c.execute('INSERT INTO campus_audit(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',(actor,action,entity,entity_id,json.dumps(details or {},ensure_ascii=False),iso()))

def current_user(vayu_session: Optional[str]=Cookie(default=None)):
    if not vayu_session: raise HTTPException(401,'Sign in to VAYU CAMPUS')
    with db.connect() as c:
        row=c.execute('SELECT u.* FROM campus_sessions s JOIN campus_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.status="active"',(hash_token(vayu_session),iso())).fetchone()
    if not row: raise HTTPException(401,'Session expired. Sign in again.')
    return dict(row)

def verified_student(user=Depends(current_user)):
    if user['role'] not in ('student','college_admin') or user['status']!='active': raise HTTPException(403,'An approved student membership is required')
    return user

def admin_required(x_admin_token: Optional[str]=Header(default=None)):
    if not ADMIN: raise HTTPException(503,'Admin review is unavailable until VAYU_ADMIN_TOKEN is configured on the server')
    if not x_admin_token or not hmac.compare_digest(x_admin_token,ADMIN): raise HTTPException(403,'Administrator access required')
    return 'configured-admin'

async def private_upload(file: UploadFile, label: str):
    allowed={'application/pdf':'.pdf','image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}
    if file.content_type not in allowed: raise HTTPException(415,'Use a PDF, JPG, PNG, or WebP verification document')
    content=await file.read(5_000_001)
    if not content or len(content)>5_000_000: raise HTTPException(413,'Document must be between 1 byte and 5 MB')
    ext=allowed[file.content_type]
    valid=(ext=='.pdf' and content.startswith(b'%PDF-')) or (ext=='.jpg' and content.startswith(b'\xff\xd8\xff')) or (ext=='.png' and content.startswith(b'\x89PNG\r\n\x1a\n')) or (ext=='.webp' and content[:4]==b'RIFF' and content[8:12]==b'WEBP')
    if not valid: raise HTTPException(415,'The uploaded file does not match its declared type')
    name=f'{label}_{secrets.token_hex(20)}{ext}'; fd=os.open(PRIVATE/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as saved:saved.write(content)
    return name

class Register(BaseModel):
    email:str=Field(min_length=5,max_length=254,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    name:str=Field(min_length=2,max_length=80)
    password:str=Field(min_length=12,max_length=128)
class Login(BaseModel): email:str; password:str
class Decision(BaseModel): decision:Literal['approve','reject','suspend','deactivate']; reason:str=Field(default='',max_length=500)
class Campaign(BaseModel):
    college_id:int
    name:str=Field(min_length=4,max_length=100); description:str=Field(min_length=15,max_length=1200)
    category:str=Field(min_length=2,max_length=40); objective:str=Field(min_length=8,max_length=500)
    city:str=Field(min_length=2,max_length=80); latitude:Optional[float]=Field(default=None,ge=-90,le=90); longitude:Optional[float]=Field(default=None,ge=-180,le=180)
    starts_at:str; ends_at:str; activities:str=Field(min_length=5,max_length=1200)
class Observation(BaseModel):
    kind:Literal['visual','sensor','external']; category:str=Field(min_length=2,max_length=60); observed_at:str
    city:str=Field(min_length=2,max_length=80); latitude:Optional[float]=Field(default=None,ge=-90,le=90); longitude:Optional[float]=Field(default=None,ge=-180,le=180)
    description:str=Field(min_length=8,max_length=1000); method:str=Field(min_length=3,max_length=300); source:str=Field(min_length=2,max_length=200)
    pm25:Optional[float]=Field(default=None,ge=0,le=5000); pm10:Optional[float]=Field(default=None,ge=0,le=10000); instrument:Optional[str]=Field(default=None,max_length=200); calibration:Optional[str]=Field(default=None,max_length=500)
class Review(BaseModel): status:Literal['reviewed','quality_checks_passed','rejected']; note:str=Field(default='',max_length=500)
class Project(BaseModel): title:str=Field(min_length=4,max_length=120); description:str=Field(min_length=20,max_length=2000); topic:str=Field(min_length=2,max_length=80); url:Optional[str]=Field(default=None,max_length=300)

@router.post('/register',status_code=201)
def register(body:Register,response:Response):
    email=body.email.strip().lower(); name=' '.join(body.name.split())
    with db.connect() as c:
        try: cur=c.execute('INSERT INTO campus_users(email,name,password_hash,created_at) VALUES(?,?,?,?)',(email,name,hash_password(body.password),iso()))
        except Exception as e:
            if 'UNIQUE' in str(e): raise HTTPException(409,'An account already exists for this email')
            raise
        token=secrets.token_urlsafe(32); c.execute('INSERT INTO campus_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',(hash_token(token),cur.lastrowid,(now()+timedelta(days=7)).isoformat(),iso()))
    response.set_cookie('vayu_session',token,httponly=True,samesite='lax',secure=os.getenv('VAYU_SECURE_COOKIE','0')=='1',max_age=604800)
    return {'name':name,'email':email,'role':'applicant','message':'Account created. Submit a college or student application.'}

@router.post('/login')
def login(body:Login,response:Response):
    with db.connect() as c: row=c.execute('SELECT * FROM campus_users WHERE email=?',(body.email.strip().lower(),)).fetchone()
    if not row or not verify_password(body.password,row['password_hash']): raise HTTPException(401,'Email or password is incorrect')
    token=secrets.token_urlsafe(32)
    with db.connect() as c:c.execute('INSERT INTO campus_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',(hash_token(token),row['id'],(now()+timedelta(days=7)).isoformat(),iso()))
    response.set_cookie('vayu_session',token,httponly=True,samesite='lax',secure=os.getenv('VAYU_SECURE_COOKIE','0')=='1',max_age=604800)
    return {'name':row['name'],'email':row['email'],'role':row['role'],'status':row['status']}

@router.post('/logout')
def logout(response:Response,vayu_session:Optional[str]=Cookie(default=None)):
    if vayu_session:
        with db.connect() as c:c.execute('DELETE FROM campus_sessions WHERE token_hash=?',(hash_token(vayu_session),))
    response.delete_cookie('vayu_session'); return {'ok':True}

@router.get('/me')
def me(user=Depends(current_user)):
    return {k:user[k] for k in ('id','email','name','role','status','created_at')}

@router.post('/college-applications',status_code=201)
async def apply_college(college_name:str=Form(min_length=3,max_length=150),city:str=Form(min_length=2,max_length=100),official_email:str=Form(default='',max_length=254),representative:str=Form(min_length=2,max_length=100),contact:str=Form(min_length=5,max_length=80),club_details:str=Form(default='',max_length=1000),reason:str=Form(min_length=15,max_length=1000),focus:str=Form(default='[]',max_length=1000),document:UploadFile=File(...),user=Depends(current_user)):
    try: parsed=json.loads(focus)
    except Exception: raise HTTPException(422,'Focus areas must be a JSON array')
    if not isinstance(parsed,list) or len(parsed)>10 or not all(isinstance(x,str) and len(x)<=60 for x in parsed): raise HTTPException(422,'Focus areas must be a list of up to 10 short labels')
    filename=await private_upload(document,'college')
    with db.connect() as c:
        cur=c.execute('INSERT INTO college_applications(college_name,city,official_email,representative,contact,club_details,document_path,reason,focus,submitted_by,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(college_name.strip(),city.strip(),official_email.strip().lower(),representative.strip(),contact.strip(),club_details.strip(),filename,reason.strip(),json.dumps(parsed),user['id'],iso()))
        audit(c,str(user['id']),'college_application_submitted','college_application',cur.lastrowid,{'college':college_name.strip()})
    return {'id':cur.lastrowid,'status':'pending','message':'Application saved. It will not appear publicly until approved.'}

@router.get('/colleges')
def colleges(q:Optional[str]=None,city:Optional[str]=None,focus:Optional[str]=None):
    sql="SELECT c.id,c.name,c.city,c.description,c.focus,c.approved_at,(SELECT count(*) FROM campus_campaigns p WHERE p.college_id=c.id AND p.status='approved') approved_campaigns,(SELECT count(*) FROM campus_observations o WHERE o.college_id=c.id AND o.status='quality_checks_passed') verified_observations FROM campus_colleges c WHERE c.status='approved'"; args=[]
    if q: sql+=' AND (lower(c.name) LIKE ? OR lower(c.city) LIKE ?)'; args += [f'%{q.lower()}%',f'%{q.lower()}%']
    if city: sql+=' AND lower(c.city)=lower(?)'; args.append(city.strip())
    with db.connect() as c: rows=c.execute(sql+' ORDER BY c.name',args).fetchall()
    result=[]
    for r in rows:
        x=dict(r); x['focus']=json.loads(x['focus'] or '[]')
        if focus and focus.casefold() not in ' '.join(x['focus']).casefold():continue
        result.append(x)
    return {'colleges':result,'count':len(result)}

@router.post('/student-applications',status_code=201)
async def apply_student(college_id:int=Form(),official_email:str=Form(default='',max_length=254),details:str=Form(min_length=8,max_length=1000),document:UploadFile=File(...),user=Depends(current_user)):
    # Private identity proof is reviewed by an administrator; email is supplementary.
    filename=await private_upload(document,'student')
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM campus_colleges WHERE id=? AND status='approved'",(college_id,)).fetchone(): raise HTTPException(404,'Approved college not found')
        try: cur=c.execute('INSERT INTO student_applications(user_id,college_id,official_email,document_path,details,submitted_at) VALUES(?,?,?,?,?,?)',(user['id'],college_id,official_email.strip().lower(),filename,details.strip(),iso()))
        except Exception as e:
            if 'UNIQUE' in str(e): raise HTTPException(409,'An application already exists for this college')
            raise
        audit(c,str(user['id']),'student_application_submitted','student_application',cur.lastrowid,{'college_id':college_id})
    return {'id':cur.lastrowid,'status':'pending'}

@router.get('/campaigns')
def campaigns(college_id:Optional[int]=None,city:Optional[str]=None):
    sql="SELECT p.id,p.name,p.description,p.category,p.objective,p.city,p.latitude,p.longitude,p.starts_at,p.ends_at,p.activities,p.college_id,(p.image_path IS NOT NULL) has_image,c.name college_name,(SELECT count(*) FROM campaign_participants x WHERE x.campaign_id=p.id) participating_colleges FROM campus_campaigns p LEFT JOIN campus_colleges c ON c.id=p.college_id WHERE p.status='approved' AND (p.college_id IS NULL OR c.status='approved')"; args=[]
    if college_id:sql+=' AND (p.college_id=? OR p.id IN (SELECT campaign_id FROM campaign_participants WHERE college_id=?))';args += [college_id,college_id]
    if city:sql+=' AND lower(p.city)=lower(?)';args.append(city.strip())
    with db.connect() as c:rows=c.execute(sql+' ORDER BY p.starts_at',args).fetchall()
    return {'campaigns':[dict(r) for r in rows]}

@router.post('/campaigns',status_code=201)
def propose_campaign(body:Campaign,user=Depends(verified_student)):
    if body.starts_at>body.ends_at:raise HTTPException(422,'Campaign end must be after its start')
    if (body.latitude is None)!=(body.longitude is None):raise HTTPException(422,'Provide both coordinates or neither')
    with db.connect() as c:
        if not c.execute('SELECT 1 FROM campus_colleges WHERE id=? AND status="approved"',(body.college_id,)).fetchone():raise HTTPException(404,'Approved college not found')
        if not c.execute('SELECT 1 FROM student_applications WHERE user_id=? AND college_id=? AND status="approved"',(user['id'],body.college_id)).fetchone():raise HTTPException(403,'You are not an approved member of this college')
        cur=c.execute('INSERT INTO campus_campaigns(college_id,name,description,category,objective,city,latitude,longitude,starts_at,ends_at,activities,submitted_by,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(body.college_id,body.name.strip(),body.description.strip(),body.category.strip(),body.objective.strip(),body.city.strip(),round(body.latitude,2) if body.latitude is not None else None,round(body.longitude,2) if body.longitude is not None else None,body.starts_at,body.ends_at,body.activities.strip(),user['id'],iso()))
        audit(c,str(user['id']),'campaign_submitted','campaign',cur.lastrowid)
    return {'id':cur.lastrowid,'status':'pending','message':'Campaign is private until admin approval.'}

@router.post('/campaigns/{campaign_id}/join')
def join_campaign(campaign_id:int,user=Depends(verified_student)):
    with db.connect() as c:
        row=c.execute("SELECT p.id FROM campus_campaigns p LEFT JOIN campus_colleges c ON c.id=p.college_id WHERE p.id=? AND p.status='approved' AND (p.college_id IS NULL OR c.status='approved')",(campaign_id,)).fetchone()
        if not row:raise HTTPException(404,'Approved campaign not found')
        membership=c.execute("SELECT college_id FROM student_applications WHERE user_id=? AND status='approved'",(user['id'],)).fetchone()
        if not membership:raise HTTPException(403,'Approved student membership required')
        try:c.execute('INSERT INTO campaign_participants(campaign_id,college_id,joined_at) VALUES(?,?,?)',(campaign_id,membership['college_id'],iso()))
        except Exception as e:
            if 'UNIQUE' in str(e):raise HTTPException(409,'Your college has already joined this campaign')
            raise
        audit(c,str(user['id']),'campaign_joined','campaign',campaign_id,{'college_id':membership['college_id']})
    return {'campaign_id':campaign_id,'college_id':membership['college_id'],'status':'joined'}

@router.post('/observations',status_code=201)
async def submit_observation(payload:str=Form(),photo:Optional[UploadFile]=File(default=None),user=Depends(verified_student)):
    try:body=Observation.model_validate_json(payload)
    except Exception as e:raise HTTPException(422,'Observation details are invalid') from e
    if body.kind=='visual' and (body.pm25 is not None or body.pm10 is not None):raise HTTPException(422,'Visual observations cannot contain sensor measurements')
    if body.kind=='sensor' and (body.pm25 is None and body.pm10 is None):raise HTTPException(422,'Sensor observation needs at least one instrument reading')
    if (body.latitude is None)!=(body.longitude is None):raise HTTPException(422,'Provide both coordinates or neither')
    filename=await private_upload(photo,'observation') if photo else None
    with db.connect() as c:
        member=c.execute("SELECT college_id FROM student_applications WHERE user_id=? AND status='approved' ORDER BY id DESC LIMIT 1",(user['id'],)).fetchone()
        if not member:raise HTTPException(403,'Approved student membership required')
        cur=c.execute('INSERT INTO campus_observations(user_id,college_id,kind,category,observed_at,city,latitude,longitude,description,method,source,pm25,pm10,instrument,calibration,photo_path,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(user['id'],member['college_id'],body.kind,body.category,body.observed_at,body.city.strip(),round(body.latitude,2) if body.latitude is not None else None,round(body.longitude,2) if body.longitude is not None else None,body.description.strip(),body.method.strip(),body.source.strip(),body.pm25,body.pm10,body.instrument,body.calibration,filename,iso()))
        c.execute('INSERT INTO observation_reviews(observation_id,status,reviewer,note,created_at) VALUES(?,?,?,?,?)',(cur.lastrowid,'unreviewed','system','Awaiting review; not a certified measurement',iso()))
        audit(c,str(user['id']),'observation_submitted','observation',cur.lastrowid,{'kind':body.kind})
    return {'id':cur.lastrowid,'status':'unreviewed','data_kind':body.kind,'message':'This is a student-submitted observation, not an official monitoring result.'}

@router.get('/observations/export')
def export_observations(admin=Depends(admin_required)):
    with db.connect() as c: rows=c.execute("SELECT o.id,o.kind,o.category,o.observed_at,o.city,o.latitude,o.longitude,o.description,o.method,o.source,o.pm25,o.pm10,o.instrument,o.calibration,o.status,c.name college FROM campus_observations o LEFT JOIN campus_colleges c ON c.id=o.college_id WHERE o.status IN ('reviewed','quality_checks_passed') ORDER BY o.observed_at").fetchall()
    out=io.StringIO();w=csv.writer(out);w.writerow(['id','type','category','observed_at','city','latitude_approx','longitude_approx','description','method','source','pm25_ug_m3','pm10_ug_m3','instrument','calibration','quality_status','college']);w.writerows([tuple(r) for r in rows])
    with db.connect() as c:audit(c,admin,'observation_export','dataset',0,{'rows':len(rows)})
    return StreamingResponse(iter([out.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="vayu-campus-reviewed-observations.csv"'})

@router.post('/projects',status_code=201)
def submit_project(body:Project,user=Depends(verified_student)):
    with db.connect() as c:
        member=c.execute("SELECT college_id FROM student_applications WHERE user_id=? AND status='approved' ORDER BY id DESC LIMIT 1",(user['id'],)).fetchone()
        cur=c.execute('INSERT INTO campus_projects(user_id,college_id,title,description,topic,url,submitted_at) VALUES(?,?,?,?,?,?,?)',(user['id'],member['college_id'] if member else None,body.title.strip(),body.description.strip(),body.topic.strip(),body.url,iso()))
        audit(c,str(user['id']),'project_submitted','project',cur.lastrowid)
    return {'id':cur.lastrowid,'status':'pending'}

@router.get('/projects')
def public_projects():
    with db.connect() as c:rows=c.execute("SELECT p.id,p.title,p.description,p.topic,p.url,p.submitted_at,c.name college FROM campus_projects p LEFT JOIN campus_colleges c ON c.id=p.college_id WHERE p.status='approved' AND (p.college_id IS NULL OR c.status='approved') ORDER BY p.submitted_at DESC").fetchall()
    return {'projects':[dict(r) for r in rows]}

@router.get('/admin/queue')
def admin_queue(admin=Depends(admin_required)):
    with db.connect() as c:
        colleges=[dict(x) for x in c.execute("SELECT id,college_name,city,official_email,representative,contact,club_details,document_path,reason,focus,status,submitted_at FROM college_applications WHERE status IN ('pending','under_review') ORDER BY submitted_at").fetchall()]
        active_colleges=[dict(x) for x in c.execute("SELECT c.id,c.application_id,c.name,c.city,c.status FROM campus_colleges c WHERE c.status='approved' ORDER BY c.name").fetchall()]
        students=[dict(x) for x in c.execute("SELECT a.id,a.official_email,a.document_path,a.details,a.status,a.submitted_at,u.name student_name,u.email account_email,c.name college_name FROM student_applications a JOIN campus_users u ON u.id=a.user_id JOIN campus_colleges c ON c.id=a.college_id WHERE a.status IN ('pending','under_review') ORDER BY a.submitted_at").fetchall()]
        campaigns=[dict(x) for x in c.execute("SELECT p.*,c.name college_name FROM campus_campaigns p LEFT JOIN campus_colleges c ON c.id=p.college_id WHERE p.status='pending' ORDER BY p.submitted_at").fetchall()]
        completions=[dict(x) for x in c.execute("SELECT x.id,x.campaign_id,x.college_id,x.proof_path,x.note,x.submitted_at,c.name college_name,p.name campaign_name FROM campaign_completions x JOIN campus_colleges c ON c.id=x.college_id JOIN campus_campaigns p ON p.id=x.campaign_id WHERE x.status='pending' ORDER BY x.submitted_at").fetchall()]
        observations=[dict(x) for x in c.execute("SELECT o.id,o.kind,o.category,o.observed_at,o.city,o.description,o.method,o.source,o.pm25,o.pm10,o.instrument,o.calibration,o.status,o.submitted_at,(o.photo_path IS NOT NULL) photo_available,c.name college_name FROM campus_observations o LEFT JOIN campus_colleges c ON c.id=o.college_id WHERE o.status='unreviewed' ORDER BY o.submitted_at").fetchall()]
        projects=[dict(x) for x in c.execute("SELECT p.id,p.title,p.description,p.topic,p.url,p.submitted_at,c.name college_name FROM campus_projects p LEFT JOIN campus_colleges c ON c.id=p.college_id WHERE p.status='pending' ORDER BY p.submitted_at").fetchall()]
        reports=[dict(x) for x in c.execute("SELECT id,category,description,city,status,created_at,resolution_photo_url,resolution_note FROM environment_reports WHERE status IN ('submitted','under_review','action_reported','awaiting_verification') ORDER BY created_at").fetchall()]
        community_activities=[dict(x) for x in c.execute("SELECT id,name,description,category,city,starts_at,image_path,submitted_at FROM community_activities WHERE status='pending' ORDER BY submitted_at").fetchall()]
    for row in colleges:row['focus']=json.loads(row['focus'] or '[]')
    return {'college_applications':colleges,'approved_colleges':active_colleges,'student_applications':students,'campaigns':campaigns,'completions':completions,'observations':observations,'projects':projects,'reports':reports,'community_activities':community_activities}

@router.get('/admin/documents/{kind}/{item_id}')
def admin_document(kind:str,item_id:int,admin=Depends(admin_required)):
    table,col={'college':('college_applications','document_path'),'student':('student_applications','document_path'),'observation':('campus_observations','photo_path'),'campaign':('campus_campaigns','image_path'),'completion':('campaign_completions','proof_path'),'community_activity':('community_activities','image_path')}.get(kind,(None,None))
    if not table:raise HTTPException(404,'Document not found')
    with db.connect() as c:row=c.execute(f'SELECT {col} path FROM {table} WHERE id=?',(item_id,)).fetchone()
    if not row or not row['path']:raise HTTPException(404,'Private document not found')
    path=PRIVATE/Path(row['path']).name
    if not path.is_file():raise HTTPException(404,'Private document is unavailable')
    with db.connect() as c:audit(c,admin,'private_document_viewed',kind,item_id)
    from fastapi.responses import FileResponse
    return FileResponse(path,filename=path.name,content_disposition_type='inline')

def decide(table,id,body,admin,entity):
    if table not in {'college_applications','student_applications','campus_campaigns','campus_projects'}:raise HTTPException(404,'Item not found')
    with db.connect() as c:
        row=c.execute(f'SELECT * FROM {table} WHERE id=?',(id,)).fetchone()
        if not row:raise HTTPException(404,'Item not found')
        if table=='college_applications':
            status={'approve':'approved','reject':'rejected','suspend':'suspended','deactivate':'deactivated'}[body.decision]
            c.execute('UPDATE college_applications SET status=?,rejection_reason=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,body.reason if status!='approved' else None,admin,iso(),id))
            if status=='approved':
                if row['status']!='pending': raise HTTPException(409,'This college application has already been reviewed')
                cur=c.execute('INSERT INTO campus_colleges(application_id,name,city,description,focus,approved_at) VALUES(?,?,?,?,?,?)',(id,row['college_name'],row['city'],row['club_details'] or row['reason'],row['focus'],iso()))
                c.execute('UPDATE college_applications SET community_id=? WHERE id=?',(cur.lastrowid,id))
                if row['submitted_by'] is not None:c.execute("UPDATE campus_users SET role='college_admin' WHERE id=?",(row['submitted_by'],))
            elif status in ('suspended','deactivated') and row['community_id']:
                c.execute('UPDATE campus_colleges SET status=? WHERE id=?',(status,row['community_id']))
        elif table=='student_applications':
            if body.decision not in ('approve','reject'):raise HTTPException(422,'Student applications can only be approved or rejected')
            status='approved' if body.decision=='approve' else 'rejected'
            c.execute('UPDATE student_applications SET status=?,rejection_reason=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,body.reason if status=='rejected' else None,admin,iso(),id))
            if status=='approved':c.execute("UPDATE campus_users SET role=CASE WHEN role='college_admin' THEN role ELSE 'student' END WHERE id=?",(row['user_id'],))
        elif table=='campus_campaigns':
            if body.decision not in ('approve','reject'):raise HTTPException(422,'Campaigns can only be approved or rejected')
            status='approved' if body.decision=='approve' else 'rejected';c.execute('UPDATE campus_campaigns SET status=?,rejection_reason=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,body.reason if status=='rejected' else None,admin,iso(),id))
        else:
            if body.decision not in ('approve','reject'):raise HTTPException(422,'Projects can only be approved or rejected')
            status='approved' if body.decision=='approve' else 'rejected';c.execute('UPDATE campus_projects SET status=?,rejection_reason=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,body.reason if status=='rejected' else None,admin,iso(),id))
        audit(c,admin,f'{entity}_{body.decision}',entity,id,{'reason':body.reason})
    return {'id':id,'status':status}

@router.post('/admin/college-applications/{item_id}/decision')
def decide_college(item_id:int,body:Decision,admin=Depends(admin_required)):return decide('college_applications',item_id,body,admin,'college_application')
@router.post('/admin/student-applications/{item_id}/decision')
def decide_student(item_id:int,body:Decision,admin=Depends(admin_required)):return decide('student_applications',item_id,body,admin,'student_application')
@router.post('/admin/campaigns/{item_id}/decision')
def decide_campaign(item_id:int,body:Decision,admin=Depends(admin_required)):return decide('campus_campaigns',item_id,body,admin,'campaign')
@router.post('/admin/projects/{item_id}/decision')
def decide_project(item_id:int,body:Decision,admin=Depends(admin_required)):return decide('campus_projects',item_id,body,admin,'project')
@router.post('/admin/observations/{item_id}/review')
def review_observation(item_id:int,body:Review,admin=Depends(admin_required)):
    with db.connect() as c:
        if not c.execute('SELECT id FROM campus_observations WHERE id=?',(item_id,)).fetchone():raise HTTPException(404,'Observation not found')
        c.execute('UPDATE campus_observations SET status=? WHERE id=?',(body.status,item_id));c.execute('INSERT INTO observation_reviews(observation_id,status,reviewer,note,created_at) VALUES(?,?,?,?,?)',(item_id,body.status,admin,body.note,iso()));audit(c,admin,'observation_reviewed','observation',item_id,{'status':body.status,'note':body.note})
    return {'id':item_id,'status':body.status}

@router.get('/impact')
def impact():
    with db.connect() as c:
        return {'approved_colleges':c.execute("SELECT count(*) FROM campus_colleges WHERE status='approved'").fetchone()[0], 'approved_campaigns':c.execute("SELECT count(*) FROM campus_campaigns WHERE status='approved' AND (college_id IS NULL OR college_id IN (SELECT id FROM campus_colleges WHERE status='approved'))").fetchone()[0], 'verified_activities':c.execute("SELECT count(*) FROM campaign_completions WHERE status='approved'").fetchone()[0], 'approved_student_memberships':c.execute("SELECT count(*) FROM student_applications WHERE status='approved'").fetchone()[0], 'quality_checked_observations':c.execute("SELECT count(*) FROM campus_observations WHERE status='quality_checks_passed'").fetchone()[0], 'approved_projects':c.execute("SELECT count(*) FROM campus_projects WHERE status='approved'").fetchone()[0], 'methodology':'Counts include administrator-approved records only. Verified activities require evidence approval. Counts do not measure pollution reduction or prove emissions impact.'}

class ReportDecision(BaseModel):
    decision:Literal['verify','close']
    reason:str=Field(default='',max_length=500)
@router.post('/admin/reports/{report_id}/decision')
def decide_report(report_id:int,body:ReportDecision,admin=Depends(admin_required)):
    status='verified_resolved' if body.decision=='verify' else 'closed_without_verification'
    with db.connect() as c:
        row=c.execute('SELECT status FROM environment_reports WHERE id=?',(report_id,)).fetchone()
        if not row:raise HTTPException(404,'Report not found')
        if body.decision=='verify' and row['status']!='awaiting_verification':raise HTTPException(409,'Only reports awaiting verification can be marked resolved')
        if row['status'] in ('verified_resolved','closed_without_verification'):raise HTTPException(409,'This report is already closed')
        c.execute('UPDATE environment_reports SET status=? WHERE id=?',(status,report_id))
        c.execute('INSERT INTO report_status_history(report_id,status,actor_id,actor_name,note,created_at) VALUES(?,?,?,?,?,?)',(report_id,status,None,'VAYURAKSHAK Admin',body.reason or ('Evidence verified by administrator' if status=='verified_resolved' else 'Closed without verification'),iso()))
        audit(c,admin,f'report_{body.decision}','environment_report',report_id,{'reason':body.reason})
    return {'id':report_id,'status':status}

@router.post('/campaigns/{campaign_id}/completion',status_code=201)
async def submit_completion(campaign_id:int,note:str=Form(min_length=8,max_length=1000),proof:UploadFile=File(...),user=Depends(verified_student)):
    if proof.content_type not in ('image/jpeg','image/png','image/webp'):raise HTTPException(415,'Activity proof must be a JPG, PNG, or WebP photo')
    filename=await private_upload(proof,'completion')
    with db.connect() as c:
        campaign=c.execute("SELECT college_id,status FROM campus_campaigns WHERE id=? AND status='approved'",(campaign_id,)).fetchone()
        if not campaign:raise HTTPException(404,'Approved campaign not found')
        membership=c.execute("SELECT college_id FROM student_applications WHERE user_id=? AND status='approved' ORDER BY id DESC LIMIT 1",(user['id'],)).fetchone()
        if not membership:raise HTTPException(403,'Approved student membership required')
        college_id=membership['college_id']
        if campaign['college_id'] is not None and campaign['college_id']!=college_id:
            if not c.execute('SELECT 1 FROM campaign_participants WHERE campaign_id=? AND college_id=?',(campaign_id,college_id)).fetchone():raise HTTPException(403,'Your college must join this campaign first')
        try:
            cur=c.execute('INSERT INTO campaign_completions(campaign_id,college_id,user_id,proof_path,note,submitted_at) VALUES(?,?,?,?,?,?)',(campaign_id,college_id,user['id'],filename,note.strip(),iso()))
        except Exception as e:
            if 'UNIQUE' in str(e):raise HTTPException(409,'Your college already submitted evidence for this campaign')
            raise
        audit(c,str(user['id']),'campaign_completion_submitted','campaign_completion',cur.lastrowid,{'campaign_id':campaign_id,'college_id':college_id})
    return {'id':cur.lastrowid,'status':'pending','message':'Evidence is private until administrator review.'}

@router.post('/admin/completions/{item_id}/decision')
def decide_completion(item_id:int,body:Decision,admin=Depends(admin_required)):
    if body.decision not in ('approve','reject'):raise HTTPException(422,'Completion evidence can only be approved or rejected')
    status='approved' if body.decision=='approve' else 'rejected'
    with db.connect() as c:
        row=c.execute("SELECT status FROM campaign_completions WHERE id=?",(item_id,)).fetchone()
        if not row:raise HTTPException(404,'Completion submission not found')
        if row['status']!='pending':raise HTTPException(409,'Completion evidence has already been reviewed')
        c.execute('UPDATE campaign_completions SET status=?,rejection_reason=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,body.reason if status=='rejected' else None,admin,iso(),item_id))
        audit(c,admin,'campaign_completion_'+body.decision,'campaign_completion',item_id,{'reason':body.reason})
    return {'id':item_id,'status':status}

@router.get('/leaderboard')
def leaderboard():
    with db.connect() as c:
        rows=c.execute("""SELECT c.id,c.name,c.city,
          (SELECT count(*) FROM campaign_completions x WHERE x.college_id=c.id AND x.status='approved') verified_activities,
          (SELECT count(*) FROM campus_observations o WHERE o.college_id=c.id AND o.status='quality_checks_passed') quality_observations,
          (SELECT count(*) FROM campus_campaigns p WHERE p.college_id=c.id AND p.status='approved') approved_campaigns
          FROM campus_colleges c WHERE c.status='approved'
          ORDER BY verified_activities DESC,quality_observations DESC,approved_campaigns DESC,c.name""").fetchall()
    return {'colleges':[dict(r) for r in rows],'methodology':'Ranked lexicographically by administrator-approved activity evidence, quality-check-passed observations, then approved campaigns. Counts are separate; no pollution reduction or arbitrary points are calculated.'}

@router.post('/campaigns/{campaign_id}/image',status_code=201)
async def upload_campaign_image(campaign_id:int,image:UploadFile=File(...),user=Depends(verified_student)):
    if image.content_type not in ('image/jpeg','image/png','image/webp'):raise HTTPException(415,'Campaign image must be a JPG, PNG, or WebP')
    filename=await private_upload(image,'campaign')
    with db.connect() as c:
        row=c.execute('SELECT submitted_by,status FROM campus_campaigns WHERE id=?',(campaign_id,)).fetchone()
        if not row:raise HTTPException(404,'Campaign not found')
        if row['submitted_by']!=user['id'] or row['status']!='pending':raise HTTPException(403,'Only the proposing student can add an image before review')
        c.execute('UPDATE campus_campaigns SET image_path=? WHERE id=?',(filename,campaign_id))
        audit(c,str(user['id']),'campaign_image_submitted','campaign',campaign_id)
    return {'campaign_id':campaign_id,'stored_privately':True}

@router.get('/campaigns/{campaign_id}/image')
def public_campaign_image(campaign_id:int):
    with db.connect() as c:row=c.execute("SELECT image_path FROM campus_campaigns WHERE id=? AND status='approved'",(campaign_id,)).fetchone()
    if not row or not row['image_path']:raise HTTPException(404,'Approved campaign image not found')
    path=PRIVATE/Path(row['image_path']).name
    if not path.is_file():raise HTTPException(404,'Campaign image unavailable')
    from fastapi.responses import FileResponse
    return FileResponse(path)

connect_router=APIRouter(prefix='/api/community',tags=['VAYU CONNECT initiatives'])
class ActivityJoin(BaseModel):
    user_id:str=Field(min_length=8,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    display_name:str=Field(min_length=2,max_length=40)
class ActivityProposal(BaseModel):
    user_id:str=Field(min_length=8,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    display_name:str=Field(min_length=2,max_length=40)
    name:str=Field(min_length=4,max_length=100)
    description:str=Field(min_length=15,max_length=1000)
    category:Literal['cleanup','plantation','awareness','other']
    city:str=Field(min_length=2,max_length=80)
    latitude:Optional[float]=Field(default=None,ge=-90,le=90)
    longitude:Optional[float]=Field(default=None,ge=-180,le=180)
    starts_at:str=Field(min_length=10,max_length=50)

@connect_router.get('/activities')
def list_activities(city:Optional[str]=None):
    sql="""SELECT a.id,a.name,a.description,a.category,a.city,a.latitude,a.longitude,a.starts_at,a.submitted_at,
      (a.image_path IS NOT NULL) has_image,(SELECT count(*) FROM community_activity_interests p WHERE p.activity_id=a.id) interest_count
      FROM community_activities a WHERE a.status='approved'"""
    args=[]
    if city:sql+=' AND lower(a.city)=lower(?)';args.append(city.strip())
    with db.connect() as c:rows=c.execute(sql+' ORDER BY a.starts_at LIMIT 100',args).fetchall()
    return {'activities':[dict(r) for r in rows],'participation_note':'Join counts are self-reported interest, not verified attendance or environmental impact.'}

@connect_router.post('/activities',status_code=201)
async def propose_activity(payload:str=Form(),image:Optional[UploadFile]=File(default=None)):
    try:body=ActivityProposal.model_validate_json(payload)
    except Exception as e:raise HTTPException(422,'Activity details are invalid') from e
    if (body.latitude is None)!=(body.longitude is None):raise HTTPException(422,'Provide both approximate coordinates or neither')
    try:dt=datetime.fromisoformat(body.starts_at.replace('Z','+00:00'))
    except ValueError:raise HTTPException(422,'Activity start must use ISO 8601')
    if dt.replace(tzinfo=dt.tzinfo or timezone.utc)<now()-timedelta(days=1):raise HTTPException(422,'Activity start time cannot be in the past')
    path=None
    if image:
        if image.content_type not in ('image/jpeg','image/png','image/webp'):raise HTTPException(415,'Activity photo must be JPG, PNG, or WebP')
        path=await private_upload(image,'connect_activity')
    with db.connect() as c:
        cur=c.execute('INSERT INTO community_activities(user_id,display_name,name,description,category,city,latitude,longitude,starts_at,image_path,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(body.user_id,body.display_name.strip(),body.name.strip(),body.description.strip(),body.category,body.city.strip(),round(body.latitude,2) if body.latitude is not None else None,round(body.longitude,2) if body.longitude is not None else None,body.starts_at,path,iso()))
        audit(c,body.user_id,'community_activity_proposed','community_activity',cur.lastrowid)
    return {'id':cur.lastrowid,'status':'pending','message':'Proposal is private until administrator approval.'}

@connect_router.get('/activities/{activity_id}/image')
def activity_image(activity_id:int):
    with db.connect() as c:row=c.execute("SELECT image_path FROM community_activities WHERE id=? AND status='approved'",(activity_id,)).fetchone()
    if not row or not row['image_path']:raise HTTPException(404,'Approved activity image not found')
    path=PRIVATE/Path(row['image_path']).name
    if not path.is_file():raise HTTPException(404,'Activity photo is unavailable')
    from fastapi.responses import FileResponse
    return FileResponse(path)

@connect_router.post('/activities/{activity_id}/join')
def join_activity(activity_id:int,body:ActivityJoin):
    with db.connect() as c:
        if not c.execute("SELECT id FROM community_activities WHERE id=? AND status='approved'",(activity_id,)).fetchone():raise HTTPException(404,'Approved community activity not found')
        try:c.execute('INSERT INTO community_activity_interests(activity_id,user_id,display_name,joined_at) VALUES(?,?,?,?)',(activity_id,body.user_id,body.display_name.strip(),iso()))
        except Exception as e:
            if 'UNIQUE' in str(e):raise HTTPException(409,'This browser profile already showed interest')
            raise
    return {'activity_id':activity_id,'participation':'self-reported interest','attendance_verified':False}

@router.post('/admin/community-activities/{item_id}/decision')
def decide_community_activity(item_id:int,body:Decision,admin=Depends(admin_required)):
    if body.decision not in ('approve','reject'):raise HTTPException(422,'Community activities can only be approved or rejected')
    status='approved' if body.decision=='approve' else 'rejected'
    with db.connect() as c:
        row=c.execute('SELECT status FROM community_activities WHERE id=?',(item_id,)).fetchone()
        if not row:raise HTTPException(404,'Activity proposal not found')
        if row['status']!='pending':raise HTTPException(409,'Activity proposal has already been reviewed')
        c.execute('UPDATE community_activities SET status=?,rejection_reason=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,body.reason if status=='rejected' else None,admin,iso(),item_id))
        audit(c,admin,'community_activity_'+body.decision,'community_activity',item_id,{'reason':body.reason})
    return {'id':item_id,'status':status}
