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
CREATE TABLE IF NOT EXISTS curriculum (
    id SERIAL PRIMARY KEY,
    phase INTEGER NOT NULL DEFAULT 0,
    phase_title TEXT NOT NULL DEFAULT '',
    phase_subtitle TEXT NOT NULL DEFAULT '',
    phase_color TEXT NOT NULL DEFAULT '#39ff14',
    items TEXT NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS curriculum (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase INTEGER NOT NULL DEFAULT 0,
    phase_title TEXT NOT NULL DEFAULT '',
    phase_subtitle TEXT NOT NULL DEFAULT '',
    phase_color TEXT NOT NULL DEFAULT '#39ff14',
    items TEXT NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 0
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
        count = cur.fetchone()[0]
        if count == 0:
            members = [
                (0, '안예준', '#39ff14'),
                (1, '김현서', '#00e5ff'),
                (2, '노건준', '#ff6e40'),
                (3, '박찬진', '#7c4dff'),
                (4, '장예덕', '#ffea00'),
                (5, '장우혁', '#ff4081'),
            ]
            for mid, name, color in members:
                _exec(db, "INSERT INTO members(id, name, color) VALUES(?,?,?)", (mid, name, color))

        # Seed default weeks + posts
        cur = _exec(db, "SELECT COUNT(*) FROM weeks")
        count = cur.fetchone()[0]
        if count == 0:
            if USE_PG:
                c = db.cursor()
                c.execute("INSERT INTO weeks(title, description, sort_order) VALUES(%s,%s,%s) RETURNING id",
                          ('Week 1 – 왜 NN?', '오프닝 질문: NN이 왜 답인가?, NN은 왜 작동하는가?', 0))
                week_id = c.fetchone()[0]
            else:
                _exec(db, "INSERT INTO weeks(title, description, sort_order) VALUES(?,?,?)",
                      ('Week 1 – 왜 NN?', '오프닝 질문: NN이 왜 답인가?, NN은 왜 작동하는가?', 0))
                week_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

            default_sections = ['📌 핵심 정리', '💡 자유 토론', '📎 자료 공유', '❓ 질문']
            for i, sec in enumerate(default_sections):
                _exec(db, "INSERT INTO sections(week_id, name, sort_order) VALUES(?,?,?)", (week_id, sec, i))

            # Seed Week 1 posts
            now = datetime.utcnow().isoformat() + 'Z'
            seed_posts = [
                ('📌 핵심 정리', 'UAT (Universal Approximation Theorem)',
                 '다층 신경망구조(MLP)에 의해 충분히 많은 파라미터만 있다면 얼마든지 복잡한 함수도 근사가 가능함이 수학적으로 밝혀졌다.\n\n우리 실세계에서 마주하는 문제해결의 영역은 대부분 Input과 Output을 가지는 함수의 형태를 띄고 있다.\n\n하지만 과연 이러한 함수를 근사하는 방법은 신경망이 유일한 방법인 것일까? 만약 유일한 방법이 아니라면 우리는 왜 신경망을 채택하고 사용하는 것일까?',
                 '["UAT","MLP","함수근사"]', '#39ff14'),
                ('📌 핵심 정리', '매니폴드 가설 (Manifold Hypothesis)',
                 '아무리 높은 N차원의 공간에 인풋과 아웃풋이 존재하는 함수를 근사하려고 해도, 그 규칙이 유의미하다면, 그 벡터들은 N이하의 M차원에 존재하는 저차원의 Manifold(다양체)에 규칙적으로 분포한다.\n\nGPT-3의 은닉층 벡터는 12000차원인데, 이 벡터들이 무작위로 분포한다면 근사가 불가능하다 (차원의 저주).\n\n하지만 매니폴드 가설에 따르면 유의미한 데이터는 저차원 다양체 위에 놓이므로 NN이 학습 가능하다.',
                 '["매니폴드","차원의저주","오토인코더"]', '#00e676'),
                ('📌 핵심 정리', '수츠케버 가설 (Sutskever Hypothesis)',
                 '"텍스트의 다음 단어를 완벽하게 예측하려면, 모델은 필연적으로 그 텍스트를 생성한 기저의 현실, 즉 세계 모델(World Model)을 구축해야만 한다."\n\n추리소설의 마지막에서 진범을 예측하려면 모든 인물의 알리바이, 동기, 물리법칙까지 이해해야 한다.\n\n극한으로 고도화된 다음 단어 예측은 세상을 이해하는 행위 그 자체이다.',
                 '["수츠케버","WorldModel","NextToken"]', '#1b5e20'),
                ('💡 자유 토론', 'LLM은 지능을 가지고 있는가?',
                 'LLM은 철저히 확률 기반이다. 앞선 단어들을 바탕으로 다음에 올 단어의 확률을 예측하는 모델.\n\n이러한 특성이 인간의 지성과 동치될 수 있는 것일까?\nLLM은 그저 확률적 앵무새에 불과한 것이 아닐까?\n\n수츠케버 가설에 대한 본인의 생각을 자유롭게 공유해주세요! 🧠',
                 '["토론","지능","확률적앵무새"]', '#2e7d32'),
                ('💡 자유 토론', '차원의 저주 vs 매니폴드 가설',
                 '12000차원의 벡터들이 무작위에 가까운 규칙으로 Mapping되어 있다면, 우리는 그 규칙을 근사해내기 어려울 것이다.\n\n고차원 공간에서는 초구면체 가까이에 대부분의 벡터들이 분포하게 되는 등, 거리개념이 사실상 소멸한다.\n\n그렇다면 매니폴드 가설이 깨지는 경우는 없을까? 여러분의 생각을 공유해주세요! 💡',
                 '["토론","차원의저주","매니폴드"]', '#4caf50'),
                ('📎 자료 공유', '📺 3Blue1Brown - Neural Networks 시리즈',
                 'LLM의 구조에 대한 직관적 이해를 위한 필수 영상!\n\nhttps://youtube.com/playlist?list=PLZHQObOWTQDNU6R1_67000Dx_ZCJB-3pi\n\n이 영상들을 보고 Attention, Transformer에 대한 직관적 이해를 갖추어 봅시다.',
                 '["영상","3b1b","Transformer"]', '#81c784'),
                ('📎 자료 공유', '📄 참고 논문 (선택 사항)',
                 '1. "Emergent World Representations" (Kenneth Li et al., ICLR 2023)\n\n2. "Contractive Auto-Encoders" (Rifai et al., 2011)\n\n3. "Auto-Encoding Variational Bayes" (Kingma & Welling, 2013)\n\n※ 필수 아님! Gemini한테 던지고 요약해달라고 하는 식으로 간단하게 읽어보셔도 좋습니다.',
                 '["논문","VAE","WorldModel"]', '#00c853'),
                ('❓ 질문', '📋 이번 주 과제',
                 '1️⃣ 3b1b의 영상을 보고, LLM의 구조에 대해 이해하기\n\n2️⃣ 매니폴드 가설에 대해 설명할 수 있는 자기 자신만의 설명문 작성해보기 (발표 예정, 간단하게 해도 됩니다!)\n\n3️⃣ 수츠케버 가설에 대한 본인의 생각을 정리해보기\n\n4️⃣ Attention, Transformer에 대한 직관적 이해 가능하게 하기\n\n💡 논문은 깊게 안 읽어도 됨. 우리가 어떻게 차원의 저주를 극복할 수 있는지에 초점!',
                 '["과제","발표","필수"]', '#39ff14'),
            ]
            for sec, title, content, tags, color in seed_posts:
                _exec(db, "INSERT INTO posts(week_id, member_id, section, title, content, tags, color, created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (week_id, 0, sec, title, content, tags, color, now))

        # Seed curriculum
        cur = _exec(db, "SELECT COUNT(*) FROM curriculum")
        count = cur.fetchone()[0]
        if count == 0:
            phases = [
                (1, 'Phase 1', 'The Physics: Engine Architecture & Control', '#39ff14',
                 json.dumps([
                     'UAT와 매니폴드 가설을 통해 LLM이 입력을 가치 있는 출력으로 변환하는 "강력한 함수"임을 이해합니다.',
                     'DeepSeek-V3/R1 등 최신 MoE 구조를 분석하며 현재 인공지능이 도달한 지능의 한계치를 탐구합니다.',
                     '블랙박스 상태인 모델 내부의 작동 원리를 파헤치고, 정교하게 제어하기 위한 최신 연구 방법론을 학습합니다.'
                 ], ensure_ascii=False), 0),
                (2, 'Phase 2', 'The System: Pipeline & Interface Engineering', '#00e5ff',
                 json.dumps([
                     'GraphRAG와 이벤트 기반 파이프라인을 통해 LLM의 추론 성능을 극대화하는 데이터 맥락(Context) 설계를 배웁니다.',
                     'MCP와 IoT 기술을 활용하여 LLM의 출력을 실제 소프트웨어와 현실 세계의 액션으로 연결하는 표준 인터페이스를 구축합니다.',
                     '단순 챗봇을 넘어 스스로 사고하고 도구를 사용하는 지능형 시스템의 엔진으로서 LLM을 활용하는 전략을 습득합니다.'
                 ], ensure_ascii=False), 1),
                (3, 'Phase 3', 'The Build: Frontier Agent with Antigravity', '#ff6e40',
                 json.dumps([
                     'Antigravity와 Claude Code 등 최신 툴을 활용하여 실제 문제를 해결하는 자율형 에이전트(Autonomous Agent)를 직접 개발합니다.',
                     '인간의 데이터가 AI를 거쳐 가치 있는 액션으로 변하는 과정을 통해 공학적 완성도와 철학적 통찰을 동시에 추구합니다.',
                     '개인 프로젝트를 넘어 논문 투고나 실제 프로젝트 등 실제 산업과 학계에서 영향력을 발휘할 수 있는 결과물을 도출합니다.'
                 ], ensure_ascii=False), 2),
            ]
            for phase, title, subtitle, color, items, sort in phases:
                _exec(db, "INSERT INTO curriculum(phase, phase_title, phase_subtitle, phase_color, items, sort_order) VALUES(?,?,?,?,?,?)",
                      (phase, title, subtitle, color, items, sort))

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

class CurriculumUpdate(BaseModel):
    phase_title: Optional[str] = None
    phase_subtitle: Optional[str] = None
    phase_color: Optional[str] = None
    items: Optional[list[str]] = None

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
        now = datetime.utcnow().isoformat() + 'Z'
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
        now = datetime.utcnow().isoformat() + 'Z'
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


# ── API: Curriculum ──────────────────────────────
@app.get("/api/curriculum")
def list_curriculum():
    with get_db() as db:
        cur = _exec(db, "SELECT * FROM curriculum ORDER BY sort_order")
        rows = _fetchall(cur)
        for r in rows:
            r['items'] = json.loads(r['items'])
        return rows


@app.put("/api/curriculum/{phase_id}")
def update_curriculum(phase_id: int, body: CurriculumUpdate):
    with get_db() as db:
        cur = _exec(db, "SELECT * FROM curriculum WHERE id=?", (phase_id,))
        if not _fetchone(cur):
            raise HTTPException(404, "Phase not found")
        if body.phase_title is not None:
            _exec(db, "UPDATE curriculum SET phase_title=? WHERE id=?", (body.phase_title, phase_id))
        if body.phase_subtitle is not None:
            _exec(db, "UPDATE curriculum SET phase_subtitle=? WHERE id=?", (body.phase_subtitle, phase_id))
        if body.phase_color is not None:
            _exec(db, "UPDATE curriculum SET phase_color=? WHERE id=?", (body.phase_color, phase_id))
        if body.items is not None:
            _exec(db, "UPDATE curriculum SET items=? WHERE id=?", (json.dumps(body.items, ensure_ascii=False), phase_id))
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
