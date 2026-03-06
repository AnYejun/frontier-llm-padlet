"""
Frontier LLM Padlet — FastAPI Backend
PostgreSQL (Railway) + SQLite (local fallback) backend.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

# ── App ──────────────────────────────────────────
app = FastAPI(title="Frontier LLM Padlet")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = DATABASE_URL.startswith("postgres")
FRONTEND_PATH = Path(__file__).parent / "frontend" / "index.html"


# ── Database helpers ─────────────────────────────
if USE_PG:
    import psycopg2
    import psycopg2.extras

    def _fix_url(url):
        """Railway uses postgres:// but psycopg2 needs postgresql://"""
        if url.startswith("postgres://"):
            return "postgresql://" + url[len("postgres://"):]
        return url

    @contextmanager
    def get_db():
        conn = psycopg2.connect(_fix_url(DATABASE_URL))
        conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _fetchall(cur):
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _fetchone(cur):
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None

    PH = "%s"  # placeholder

else:  # SQLite fallback for local dev
    import sqlite3
    DB_PATH = Path(__file__).parent / "padlet.db"

    @contextmanager
    def get_db():
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _fetchall(cur):
        return [dict(r) for r in cur.fetchall()]

    def _fetchone(cur):
        row = cur.fetchone()
        return dict(row) if row else None

    PH = "?"  # placeholder


def _exec(conn, sql, params=()):
    """Execute SQL with correct placeholder style."""
    if USE_PG:
        sql = sql.replace("?", "%s")
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    else:
        return conn.execute(sql, params)


def _lastid(conn, cur):
    """Get last inserted ID."""
    if USE_PG:
        return None  # We handle RETURNING separately
    else:
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Schema ───────────────────────────────────────
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weeks (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sections (
    id SERIAL PRIMARY KEY,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    section TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    color TEXT NOT NULL DEFAULT '#39ff14',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reactions (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    emoji TEXT NOT NULL
);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weeks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    section TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    color TEXT NOT NULL DEFAULT '#39ff14',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    emoji TEXT NOT NULL
);
"""


def init_db():
    with get_db() as db:
        if USE_PG:
            cur = db.cursor()
            for stmt in PG_SCHEMA.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt + ";")
        else:
            db.executescript(SQLITE_SCHEMA)

        # Seed default members
        cur = _exec(db, "SELECT COUNT(*) FROM members")
        count = cur.fetchone()[0] if USE_PG else cur.fetchone()[0]
        if count == 0:
            members = [
                (0, '멤버 1', '#39ff14'),
                (1, '멤버 2', '#00e5ff'),
                (2, '멤버 3', '#ff6e40'),
                (3, '멤버 4', '#7c4dff'),
                (4, '멤버 5', '#ffea00'),
                (5, '멤버 6', '#ff4081'),
            ]
            for mid, name, color in members:
                _exec(db, "INSERT INTO members(id, name, color) VALUES(?,?,?)", (mid, name, color))

        # Seed default weeks
        cur = _exec(db, "SELECT COUNT(*) FROM weeks")
        count = cur.fetchone()[0] if USE_PG else cur.fetchone()[0]
        if count == 0:
            _exec(db, "INSERT INTO weeks(title, description, sort_order) VALUES(?,?,?)",
                  ('Week 1 – Introduction to LLM', 'LLM 기초 개념과 Transformer 아키텍처 리뷰', 0))
            if USE_PG:
                cur = db.cursor()
                cur.execute("SELECT id FROM weeks ORDER BY id DESC LIMIT 1")
                week_id = cur.fetchone()[0]
            else:
                week_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            default_sections = ['📌 핵심 정리', '💡 자유 토론', '📎 자료 공유', '❓ 질문']
            for i, sec in enumerate(default_sections):
                _exec(db, "INSERT INTO sections(week_id, name, sort_order) VALUES(?,?,?)", (week_id, sec, i))


# ── Pydantic Models ──────────────────────────────
class MemberRename(BaseModel):
    name: str

class WeekCreate(BaseModel):
    title: str
    description: str = ''

class WeekUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None

class PostCreate(BaseModel):
    week_id: int
    member_id: int
    section: str
    title: str = ''
    content: str = ''
    tags: list[str] = []
    color: str = '#39ff14'

class CommentCreate(BaseModel):
    member_id: int
    text: str

class ReactionCreate(BaseModel):
    emoji: str


# ── API: Members ─────────────────────────────────
@app.get("/api/members")
def list_members():
    with get_db() as db:
        cur = _exec(db, "SELECT * FROM members ORDER BY id")
        return _fetchall(cur)


@app.put("/api/members/{member_id}")
def rename_member(member_id: int, body: MemberRename):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    with get_db() as db:
        cur = _exec(db, "SELECT * FROM members WHERE id=?", (member_id,))
        if not _fetchone(cur):
            raise HTTPException(404, "Member not found")
        _exec(db, "UPDATE members SET name=? WHERE id=?", (name, member_id))
        return {"ok": True, "name": name}


# ── API: Weeks ───────────────────────────────────
@app.get("/api/weeks")
def list_weeks():
    with get_db() as db:
        cur = _exec(db, "SELECT * FROM weeks ORDER BY sort_order")
        weeks = _fetchall(cur)
        result = []
        for w in weeks:
            # Sections
            cur = _exec(db, "SELECT name FROM sections WHERE week_id=? ORDER BY sort_order", (w['id'],))
            w['sections'] = [s['name'] for s in _fetchall(cur)]
            # Posts
            cur = _exec(db, """
                SELECT p.*, m.name as author, m.color as author_color
                FROM posts p JOIN members m ON p.member_id = m.id
                WHERE p.week_id=? ORDER BY p.created_at DESC
            """, (w['id'],))
            posts_list = []
            for p in _fetchall(cur):
                p['tags'] = json.loads(p['tags'])
                # Comments
                cur2 = _exec(db, """
                    SELECT c.*, m.name as author, m.color as author_color
                    FROM comments c JOIN members m ON c.member_id = m.id
                    WHERE c.post_id=? ORDER BY c.created_at ASC
                """, (p['id'],))
                p['comments'] = _fetchall(cur2)
                # Reactions
                cur3 = _exec(db, "SELECT emoji FROM reactions WHERE post_id=?", (p['id'],))
                reaction_counts = {}
                for r in _fetchall(cur3):
                    reaction_counts[r['emoji']] = reaction_counts.get(r['emoji'], 0) + 1
                p['reactions'] = reaction_counts
                posts_list.append(p)
            w['posts'] = posts_list
            result.append(w)
        return result


@app.post("/api/weeks")
def create_week(body: WeekCreate):
    with get_db() as db:
        cur = _exec(db, "SELECT COALESCE(MAX(sort_order),0) FROM weeks")
        row = cur.fetchone()
        max_order = row[0] if USE_PG else row[0]

        if USE_PG:
            c = db.cursor()
            c.execute("INSERT INTO weeks(title, description, sort_order) VALUES(%s,%s,%s) RETURNING id",
                      (body.title, body.description, max_order + 1))
            week_id = c.fetchone()[0]
        else:
            _exec(db, "INSERT INTO weeks(title, description, sort_order) VALUES(?,?,?)",
                  (body.title, body.description, max_order + 1))
            week_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        default_sections = ['📌 핵심 정리', '💡 자유 토론', '📎 자료 공유', '❓ 질문']
        for i, sec in enumerate(default_sections):
            _exec(db, "INSERT INTO sections(week_id, name, sort_order) VALUES(?,?,?)", (week_id, sec, i))
        return {"ok": True, "id": week_id}


@app.put("/api/weeks/{week_id}")
def update_week(week_id: int, body: WeekUpdate):
    with get_db() as db:
        cur = _exec(db, "SELECT * FROM weeks WHERE id=?", (week_id,))
        if not _fetchone(cur):
            raise HTTPException(404, "Week not found")
        if body.title is not None:
            _exec(db, "UPDATE weeks SET title=? WHERE id=?", (body.title, week_id))
        if body.description is not None:
            _exec(db, "UPDATE weeks SET description=? WHERE id=?", (body.description, week_id))
        return {"ok": True}


@app.delete("/api/weeks/{week_id}")
def delete_week(week_id: int):
    with get_db() as db:
        cur = _exec(db, "SELECT COUNT(*) FROM weeks")
        count = cur.fetchone()[0] if USE_PG else cur.fetchone()[0]
        if count <= 1:
            raise HTTPException(400, "Cannot delete the last week")
        _exec(db, "DELETE FROM weeks WHERE id=?", (week_id,))
        return {"ok": True}


# ── API: Posts ───────────────────────────────────
@app.post("/api/posts")
def create_post(body: PostCreate):
    with get_db() as db:
        now = datetime.utcnow().isoformat()
        if USE_PG:
            c = db.cursor()
            c.execute("""
                INSERT INTO posts(week_id, member_id, section, title, content, tags, color, created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (body.week_id, body.member_id, body.section, body.title, body.content,
                  json.dumps(body.tags), body.color, now))
            post_id = c.fetchone()[0]
        else:
            _exec(db, """
                INSERT INTO posts(week_id, member_id, section, title, content, tags, color, created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (body.week_id, body.member_id, body.section, body.title, body.content,
                  json.dumps(body.tags), body.color, now))
            post_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"ok": True, "id": post_id}


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM posts WHERE id=?", (post_id,))
        return {"ok": True}


# ── API: Reactions ───────────────────────────────
@app.post("/api/posts/{post_id}/reactions")
def add_reaction(post_id: int, body: ReactionCreate):
    with get_db() as db:
        _exec(db, "INSERT INTO reactions(post_id, emoji) VALUES(?,?)", (post_id, body.emoji))
        return {"ok": True}


# ── API: Comments ────────────────────────────────
@app.post("/api/posts/{post_id}/comments")
def add_comment(post_id: int, body: CommentCreate):
    with get_db() as db:
        now = datetime.utcnow().isoformat()
        if USE_PG:
            c = db.cursor()
            c.execute("INSERT INTO comments(post_id, member_id, text, created_at) VALUES(%s,%s,%s,%s) RETURNING id",
                      (post_id, body.member_id, body.text, now))
            comment_id = c.fetchone()[0]
        else:
            _exec(db, "INSERT INTO comments(post_id, member_id, text, created_at) VALUES(?,?,?,?)",
                  (post_id, body.member_id, body.text, now))
            comment_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"ok": True, "id": comment_id}


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM comments WHERE id=?", (comment_id,))
        return {"ok": True}


# ── Serve Frontend ───────────────────────────────
@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    if FRONTEND_PATH.exists():
        return HTMLResponse(FRONTEND_PATH.read_text(encoding='utf-8'))
    return HTMLResponse("<h1>Frontend not found. Place index.html in frontend/ directory.</h1>")


# ── Startup ──────────────────────────────────────
init_db()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
