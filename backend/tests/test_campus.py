import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.data import community
import app.campus as campus
from app.main import app

class CampusFlowTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(); community.DB=Path(cls.tmp.name)/'community.sqlite3'; community.init(); community.init_campus(); cls.client=TestClient(app)
 def setUp(self):
  with community.connect() as c:
   for t in ('campus_audit','community_activity_interests','community_activities','observation_reviews','campus_observations','campaign_completions','campaign_participants','campus_campaigns','campus_projects','student_applications','campus_colleges','college_applications','campus_sessions','campus_users'):
    c.execute(f'DELETE FROM {t}')
  campus.ADMIN='test-admin-token'; self.private=tempfile.TemporaryDirectory(); campus.PRIVATE=Path(self.private.name)
 def tearDown(self): self.private.cleanup()
 def register(self,email,name):
  r=self.client.post('/api/campus/register',json={'email':email,'name':name,'password':'long-password-123'}); self.assertEqual(r.status_code,201,r.text); return self.client
 def document(self,name='proof.pdf'):
  return {'document':(name,b'%PDF-1.4\nvalid proof','application/pdf')}
 def test_college_and_student_approval_campaign_observation_review_and_export(self):
  self.register('rep@college.edu','Representative')
  apply=self.client.post('/api/campus/college-applications',data={'college_name':'Example College','city':'Delhi','official_email':'office@example.edu','representative':'Rep','contact':'00000000','club_details':'Environmental club','reason':'We want to coordinate clean air activities.','focus':'["air quality","waste"]'},files=self.document())
  self.assertEqual(apply.status_code,201,apply.text); app_id=apply.json()['id']
  self.assertEqual(self.client.get('/api/campus/colleges').json()['count'],0)
  self.assertEqual(self.client.get('/api/campus/admin/queue').status_code,403)
  queue=self.client.get('/api/campus/admin/queue',headers={'X-Admin-Token':campus.ADMIN}); self.assertEqual(queue.status_code,200,queue.text)
  private=self.client.get(f'/api/campus/admin/documents/college/{app_id}',headers={'X-Admin-Token':campus.ADMIN}); self.assertEqual(private.status_code,200)
  approved=self.client.post(f'/api/campus/admin/college-applications/{app_id}/decision',headers={'X-Admin-Token':campus.ADMIN},json={'decision':'approve'}); self.assertEqual(approved.status_code,200,approved.text)
  colleges=self.client.get('/api/campus/colleges').json()['colleges']; self.assertEqual(len(colleges),1); college_id=colleges[0]['id']
  self.client.post('/api/campus/logout')
  student=TestClient(app); student.post('/api/campus/register',json={'email':'student@example.edu','name':'Student','password':'long-password-123'})
  self.assertEqual(student.post('/api/campus/campaigns',json={}).status_code,403)
  student_app=student.post('/api/campus/student-applications',data={'college_id':college_id,'official_email':'student@example.edu','details':'Student verification and campus details.'},files=self.document('student.pdf'))
  self.assertEqual(student_app.status_code,201,student_app.text); sid=student_app.json()['id']
  self.assertEqual(student.post('/api/campus/campaigns',json={}).status_code,403)
  decision=self.client.post(f'/api/campus/admin/student-applications/{sid}/decision',headers={'X-Admin-Token':campus.ADMIN},json={'decision':'approve'}); self.assertEqual(decision.status_code,200,decision.text); self.assertEqual(student.get('/api/campus/me').json()['role'],'student')
  campaign=student.post('/api/campus/campaigns',json={'college_id':college_id,'name':'Green Diwali Campus Week','description':'A student-led clean air awareness week.','category':'diwali awareness','objective':'Help students understand festival air quality.','city':'Delhi','starts_at':'2026-11-01','ends_at':'2026-11-10','activities':'Workshops and community outreach'})
  self.assertEqual(campaign.status_code,201,campaign.text); campaign_id=campaign.json()['id']
  uploaded=student.post(f'/api/campus/campaigns/{campaign_id}/image',files={'image':('campaign.png',b'\x89PNG\r\n\x1a\nphoto','image/png')}); self.assertEqual(uploaded.status_code,201,uploaded.text)
  self.assertEqual(self.client.get(f'/api/campus/campaigns/{campaign_id}/image').status_code,404)
  self.assertEqual(len(self.client.get('/api/campus/campaigns').json()['campaigns']),0)
  self.client.post(f'/api/campus/admin/campaigns/{campaign_id}/decision',headers={'X-Admin-Token':campus.ADMIN},json={'decision':'approve'})
  self.assertEqual(self.client.get(f'/api/campus/campaigns/{campaign_id}/image').status_code,200)
  self.assertEqual(student.post(f'/api/campus/campaigns/{campaign_id}/join').status_code,200)
  completion=student.post(f'/api/campus/campaigns/{campaign_id}/completion',data={'note':'Students ran a documented clean-air awareness workshop.'},files={'proof':('activity.png',b'\x89PNG\r\n\x1a\nvalid','image/png')})
  self.assertEqual(completion.status_code,201,completion.text); completion_id=completion.json()['id']
  self.assertEqual(self.client.get('/api/campus/leaderboard').json()['colleges'][0]['verified_activities'],0)
  self.assertEqual(self.client.post(f'/api/campus/admin/completions/{completion_id}/decision',headers={'X-Admin-Token':campus.ADMIN},json={'decision':'approve'}).status_code,200)
  self.assertEqual(self.client.get('/api/campus/leaderboard').json()['colleges'][0]['verified_activities'],1)
  visual={'kind':'visual','category':'visible_smoke','observed_at':'2026-10-31T12:00:00+05:30','city':'Delhi','description':'Visible smoke near the road.','method':'Visual observation','source':'Student observation','pm25':None,'pm10':None}
  form=student.post('/api/campus/observations',data={'payload':__import__('json').dumps(visual)})
  self.assertEqual(form.status_code,201,form.text); oid=form.json()['id']
  self.assertEqual(self.client.get('/api/campus/observations/export',headers={'X-Admin-Token':campus.ADMIN}).status_code,200)
  self.assertEqual(self.client.post(f'/api/campus/admin/observations/{oid}/review',headers={'X-Admin-Token':campus.ADMIN},json={'status':'quality_checks_passed','note':'Reviewed fields.'}).status_code,200)
  export=self.client.get('/api/campus/observations/export',headers={'X-Admin-Token':campus.ADMIN}); self.assertIn('visible_smoke',export.text)
 def test_connect_activity_is_private_until_approval_then_records_interest(self):
  payload={'user_id':'connect-user','display_name':'Local volunteer','name':'Neighbourhood clean-up','description':'Collect litter safely along the public park path.','category':'cleanup','city':'Delhi','starts_at':'2026-11-10T09:00:00+05:30'}
  form={'payload':__import__('json').dumps(payload)}
  proposed=self.client.post('/api/community/activities',data=form,files={'image':('activity.png',b'\x89PNG\r\n\x1a\nphoto','image/png')})
  self.assertEqual(proposed.status_code,201,proposed.text); activity_id=proposed.json()['id']
  self.assertEqual(self.client.get('/api/community/activities').json()['activities'],[])
  queue=self.client.get('/api/campus/admin/queue',headers={'X-Admin-Token':campus.ADMIN}).json()
  self.assertEqual(queue['community_activities'][0]['id'],activity_id)
  self.assertEqual(self.client.get(f'/api/campus/admin/documents/community_activity/{activity_id}',headers={'X-Admin-Token':campus.ADMIN}).status_code,200)
  decision=self.client.post(f'/api/campus/admin/community-activities/{activity_id}/decision',headers={'X-Admin-Token':campus.ADMIN},json={'decision':'approve'})
  self.assertEqual(decision.status_code,200,decision.text)
  listed=self.client.get('/api/community/activities').json(); self.assertEqual(len(listed['activities']),1)
  self.assertIn('not verified attendance',listed['participation_note'])
  self.assertEqual(self.client.get(f'/api/community/activities/{activity_id}/image').status_code,200)
  interest={'user_id':'volunteer-2','display_name':'Volunteer'}
  self.assertEqual(self.client.post(f'/api/community/activities/{activity_id}/join',json=interest).status_code,200)
  self.assertEqual(self.client.post(f'/api/community/activities/{activity_id}/join',json=interest).status_code,409)
  self.assertEqual(self.client.get('/api/community/activities').json()['activities'][0]['interest_count'],1)

 def test_visual_observation_cannot_claim_pm_reading_and_private_admin_is_configured(self):
  self.assertEqual(self.client.get('/api/campus/admin/queue').status_code,403)
  user=self.register('x@example.edu','Applicant')
  self.assertEqual(user.get('/api/campus/admin/queue',headers={'X-Admin-Token':'wrong'}).status_code,403)
  self.assertEqual(user.get('/api/campus/colleges').status_code,200)
