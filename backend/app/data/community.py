"""Small local community store. Public display names are self-selected, not verified."""
import json, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "community.sqlite3"
CHALLENGES = [
    {"id":"pledge","name":"Take the Green Diwali pledge","description":"Commit to thoughtful choices, less unnecessary waste, and encouraging your community.","points":10,"kind":"pledge"},
    {"id":"reusable-decor","name":"Choose reusable decorations","description":"Decorate with items you can use again next year.","points":20,"kind":"individual"},
    {"id":"traditional-diya","name":"Choose reusable lighting","description":"Consider traditional diyas or other reusable decorative alternatives.","points":20,"kind":"individual"},
    {"id":"no-burning-waste","name":"Avoid burning waste","description":"Dispose of household waste safely. Never burn rubbish.","points":20,"kind":"individual"},
    {"id":"community-cleanup","name":"Join a community cleanup","description":"Take part in a local cleanup and invite a neighbour.","points":30,"kind":"community"},
    {"id":"share-green-tip","name":"Share a green celebration tip","description":"Post one practical idea that others can try.","points":20,"kind":"individual"},
    {"id":"awareness-event","name":"Organise an awareness activity","description":"Bring people together for an environmental awareness session.","points":50,"kind":"event"},
]

@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(DB, timeout=10); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS communities(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, city TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memberships(community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE, user_id TEXT NOT NULL, display_name TEXT NOT NULL, joined_at TEXT NOT NULL, PRIMARY KEY(community_id,user_id));
        CREATE TABLE IF NOT EXISTS pledges(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL, community_id INTEGER REFERENCES communities(id) ON DELETE SET NULL, created_at TEXT NOT NULL, UNIQUE(user_id));
        CREATE TABLE IF NOT EXISTS completions(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL, challenge_id TEXT NOT NULL, community_id INTEGER REFERENCES communities(id) ON DELETE SET NULL, status TEXT NOT NULL DEFAULT 'complete', created_at TEXT NOT NULL, UNIQUE(user_id,challenge_id));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE, name TEXT NOT NULL, description TEXT NOT NULL, starts_at TEXT NOT NULL, location TEXT NOT NULL, organiser_id TEXT NOT NULL, organiser_name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event_participants(event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,user_id TEXT NOT NULL,display_name TEXT NOT NULL,joined_at TEXT NOT NULL,PRIMARY KEY(event_id,user_id));
        CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, community_id INTEGER REFERENCES communities(id) ON DELETE SET NULL,user_id TEXT NOT NULL,display_name TEXT NOT NULL,content TEXT NOT NULL,image_url TEXT,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'visible');
        CREATE TABLE IF NOT EXISTS likes(post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,user_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(post_id,user_id));
        CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY,post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,user_id TEXT NOT NULL,display_name TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'visible');
        CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY,reporter_id TEXT NOT NULL,kind TEXT NOT NULL,item_id INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',UNIQUE(reporter_id,kind,item_id));
        CREATE TABLE IF NOT EXISTS points(id INTEGER PRIMARY KEY,user_id TEXT NOT NULL,display_name TEXT NOT NULL,community_id INTEGER REFERENCES communities(id) ON DELETE SET NULL,source_type TEXT NOT NULL,source_id TEXT NOT NULL,points INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(user_id,source_type,source_id));
        """)

def now(): return datetime.now(timezone.utc).isoformat()
def pack(row): return dict(row) if row else None
def award(c,user,name,community,source_type,source_id,points):
    c.execute("INSERT OR IGNORE INTO points(user_id,display_name,community_id,source_type,source_id,points,created_at) VALUES(?,?,?,?,?,?,?)",(user,name,community,source_type,str(source_id),points,now()))

init()
