import sys, tempfile, unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.data import community
from app.main import app

class CommunityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); community.DB=Path(cls.tmp.name)/"test.sqlite3"; community.init(); cls.client=TestClient(app)

    def setUp(self):
        with community.connect() as c:
            for table in ("reports","comments","likes","posts","points","event_participants","events","completions","pledges","memberships","communities"):
                c.execute(f"DELETE FROM {table}")

    def test_home_and_challenge_catalog_apis_remain_available(self):
        home=self.client.get('/')
        self.assertEqual(home.status_code,200)
        self.assertIn('Explore Diwali Pollution',home.text)
        challenges=self.client.get('/api/community/challenges')
        self.assertEqual(challenges.status_code,200)
        self.assertTrue(challenges.json())
        self.assertTrue(all('points' in row for row in challenges.json()))

    def test_membership_and_challenge_points_are_unique(self):
        c=self.client; person={"user_id":"user_12345678","display_name":"Green Neighbour"}
        community=c.post('/api/community/communities',json={"name":"Test Green Group","city":"Patiala","description":"A place to share thoughtful celebration ideas."}).json()
        cid=community['id']
        self.assertEqual(c.post(f'/api/community/communities/{cid}/join',json=person).status_code,200)
        self.assertEqual(c.post(f'/api/community/communities/{cid}/join',json=person).json()['members'],1)
        body={**person,"challenge_id":"reusable-decor","community_id":cid}
        self.assertEqual(c.post('/api/community/challenges/complete',json=body).json()['new_points'],20)
        duplicate=c.post('/api/community/challenges/complete',json=body).json()
        self.assertTrue(duplicate['already_completed']); self.assertEqual(duplicate['new_points'],0)
        self.assertEqual(c.get('/api/community/progress',params={"user_id":person['user_id']}).json()['points'],20)

    def test_pledge_and_pledge_challenge_share_one_award(self):
        person={"user_id":"user_abcdefgh","display_name":"Green Neighbour"}
        self.assertEqual(self.client.post('/api/community/pledges',json=person).json()['new_points'],10)
        again=self.client.post('/api/community/challenges/complete',json={**person,"challenge_id":"pledge"}).json()
        self.assertEqual(again['new_points'],0)
        self.assertEqual(self.client.get('/api/community/pledges').json()['count'],1)

    def test_event_post_comments_likes_and_reports_persist(self):
        c=self.client; person={"user_id":"member_abcdefgh","display_name":"Green Neighbour"}
        group=c.post('/api/community/communities',json={"name":"Event Green Group","city":"Chandigarh","description":"A community for sharing greener local activities."}).json()
        c.post(f"/api/community/communities/{group['id']}/join",json=person)
        event=c.post('/api/community/events',json={**person,"community_id":group['id'],"name":"Neighbourhood clean-up","description":"Meet for a community litter clean-up.","starts_at":(datetime.now(timezone.utc)+timedelta(days=5)).isoformat(),"location":"Community park"})
        self.assertEqual(event.status_code,201,event.text); event_id=event.json()['id']
        self.assertEqual(c.post('/api/community/events/join',json={**person,"event_id":event_id}).json()['participants'],1)
        self.assertTrue(c.get('/api/community/events',params={"user_id":person['user_id']}).json()[0]['joined'])
        self.assertEqual(c.post('/api/community/events/leave',json={**person,"event_id":event_id}).status_code,200)
        post=c.post('/api/community/posts',json={**person,"community_id":group['id'],"content":"Try reusable decorations this year."})
        self.assertEqual(post.status_code,201,post.text); post_id=post.json()['id']
        self.assertEqual(c.post('/api/community/likes',json={**person,"post_id":post_id}).json()['likes'],1)
        self.assertEqual(c.post('/api/community/comments',json={**person,"post_id":post_id,"content":"Good idea."}).status_code,201)
        feed=c.get('/api/community/feed').json()[0]
        self.assertEqual(feed['comments_count'],1); self.assertEqual(feed['comments'][0]['content'],'Good idea.')
        report=c.post('/api/community/reports',json={**person,"kind":"post","item_id":post_id,"reason":"This post seems inappropriate."})
        self.assertEqual(report.status_code,201,report.text)

if __name__ == '__main__': unittest.main()
