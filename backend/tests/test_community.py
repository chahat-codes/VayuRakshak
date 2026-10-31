import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.data import community
from app.main import app

class CoreAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); community.DB=Path(cls.tmp.name)/'test.sqlite3'; community.init(); cls.client=TestClient(app)
    def setUp(self):
        with community.connect() as c:
            c.execute('DELETE FROM environment_confirmations'); c.execute('DELETE FROM report_status_history'); c.execute('DELETE FROM environment_reports'); c.execute('DELETE FROM communities')
    def person(self, suffix): return {'user_id':f'profile_{suffix:0<10}','display_name':'Community member'}
    def report(self, person=None, **kwargs):
        payload={**(person or self.person('owner')),'category':'garbage','description':'A pile of rubbish blocks the footpath.','city':'Delhi',**kwargs}
        return self.client.post('/api/environment/reports',json=payload)
    def test_home_and_removed_features(self):
        r=self.client.get('/'); self.assertEqual(r.status_code,200)
        for label in ('AQI Intelligence','VAYU CONNECT','VAYU CAMPUS','My Reports','Settings','Understand Your Air.','Protect Your Community.'): self.assertIn(label,r.text)
        self.assertIn('hero-image',r.text); self.assertIn('pillar-grid',r.text)
        for photo in ('delhi-pollution.jpg','community-cleanup.jpg','campus-planting.jpg'): self.assertEqual(self.client.get('/static/images/photos/'+photo).status_code,200,photo)
        for old in ('Diwali Planner','What If','Green Community','My Progress'): self.assertNotIn(old,r.text)
        for path in ('/api/simulate','/api/alerts','/api/community/challenges','/api/community/feed','/api/community/impact'):
            self.assertEqual(self.client.get(path).status_code,404,path)
        self.assertEqual(self.client.get('/api/health').json()['status'],'ok')
    def test_groups_and_report_track_duplicate_confirm_dispute_and_resolution(self):
        group=self.client.post('/api/community/communities',json={'name':'Delhi Neighbours','city':'Delhi','description':'Neighbours help keep shared spaces clean.'})
        self.assertEqual(group.status_code,201)
        owner=self.person('owner'); helper=self.person('helper'); other=self.person('other')
        created=self.report(owner,latitude=28.6139,longitude=77.2090,community_id=group.json()['id'])
        self.assertEqual(created.status_code,201,created.text); item=created.json(); rid=item['id']
        self.assertEqual(item['reference'],f"VAYU-{rid:06d}"); self.assertEqual(item['ai_classification'],'unavailable')
        similar=self.client.post('/api/environment/reports/similar',json={'category':'garbage','description':'Rubbish pile blocks the footpath.','city':'Delhi','latitude':28.61,'longitude':77.21})
        self.assertTrue(similar.json()['matches']); self.assertFalse(similar.json()['automatic_merge'])
        self.assertEqual(self.client.post(f'/api/environment/reports/{rid}/confirm',json={**owner,'kind':'observed'}).status_code,409)
        for who,kind in ((helper,'observed'),(other,'dispute')):
            response=self.client.post(f'/api/environment/reports/{rid}/confirm',json={**who,'kind':kind}); self.assertEqual(response.status_code,200,response.text)
        summary=self.client.get('/api/environment/reports').json()['reports'][0]
        self.assertEqual((summary['observed_count'],summary['dispute_count']),(1,1))
        self.assertFalse(self.client.get(f'/api/environment/reports/{rid}').json().get('independently_verified',False))
        self.assertEqual(self.client.get('/api/environment/reports/mine',params={'user_id':owner['user_id']}).json()['count'],1)
        with tempfile.TemporaryDirectory() as folder:
            import app.main as main
            from unittest.mock import patch
            photo='a'*32+'.jpg'; (Path(folder)/photo).write_bytes(b'proof')
            with patch.object(main,'UPLOADS',Path(folder)):
                self.assertEqual(self.client.post(f'/api/environment/reports/{rid}/claim',json=helper).json()['status'],'under_review')
                self.assertEqual(self.client.post(f'/api/environment/reports/{rid}/start',json=helper).json()['status'],'action_reported')
                outcome=self.client.post(f'/api/environment/reports/{rid}/resolve',json={**helper,'resolution_photo_url':'/uploads/'+photo,'resolution_note':'Area cleared.'})
                self.assertEqual(outcome.status_code,200,outcome.text); self.assertFalse(outcome.json()['independently_verified'])
        mine=self.client.get('/api/environment/reports/mine',params={'user_id':owner['user_id']}).json()['reports'][0]
        self.assertEqual([x['status'] for x in mine['history']],['submitted','under_review','action_reported','awaiting_verification'])
    def test_missing_values_remain_unavailable_and_invalid_data_rejected(self):
        self.assertEqual(self.client.post('/api/environment/reports',json={**self.person('x'),'category':'smoke','description':'Smoke near a busy road.','city':'Delhi','latitude':28.6}).status_code,422)
        self.assertEqual(self.client.get('/api/air-quality/latest').status_code,200)
        self.assertEqual(self.client.get('/api/locations').json()[0]['name'],'Delhi')
