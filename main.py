"""
Frontier LLM Padlet — FastAPI Backend
SQLite-backed collaborative session board.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
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

DB_PATH = Path(__file__).parent / "padlet.db"
FRONTEND_PATH = Path(__file__).parent / "frontend" / "index.html"

# ── Database helpers ─────────────────────────────
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


def init_db():
    with get_db() as db:
        db.executescript("""
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
        """)

        # Seed default members if empty
        if db.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0:
            members = [
                (0, '멤버 1', '#39ff14'),
                (1, '멤버 2', '#00e5ff'),
                (2, '멤버 3', '#ff6e40'),
                (3, '멤버 4', '#7c4dff'),
                (4, '멤버 5', '#ffea00'),
                (5, '멤버 6', '#ff4081'),
            ]
            db.executemany("INSERT INTO members(id, name, color) VALUES(?,?,?)", members)

        # Seed default weeks if empty
        if db.execute("SELECT COUNT(*) FROM weeks").fetchone()[0] == 0:
            weeks = [
                ('Week 1 – Introduction to LLM', 'LLM 기초 개념과 Transformer 아키텍처 리뷰', 0),
                ('Week 2 – Scaling Laws & Pretraining', 'Chinchilla, Scaling Laws, 대규모 사전학습 기법', 1),
                ('Week 3 – RLHF & Alignment', 'RLHF, DPO, Constitutional AI 등 정렬 기법', 2),
                ('Week 4 – Prompting & Reasoning', 'Chain-of-Thought, Prompt Engineering, Reasoning 능력', 3),
                ('Week 5 – RAG & Retrieval', 'Retrieval-Augmented Generation, 벡터 DB, 검색 최적화', 4),
                ('Week 6 – Fine-tuning', 'LoRA, QLoRA, PEFT, 효율적 파인튜닝 기법', 5),
                ('Week 7 – Multimodal LLM', 'Vision-Language Models, GPT-4V, LLaVA', 6),
                ('Week 8 – Agents & Tool Use', 'LLM Agent 설계, Function Calling, Tool Use', 7),
                ('Week 9 – Evaluation & Benchmarks', 'LLM 평가 지표, MMLU, HumanEval, 벤치마크 분석', 8),
                ('Week 10 – Efficiency & Deployment', '양자화, Distillation, 추론 최적화', 9),
                ('Week 11 – Safety & Ethics', 'AI 안전성, 편향, 레드팀, Guardrails', 10),
                ('Week 12 – Open Source LLMs', 'LLaMA, Mistral, Gemma 등 오픈소스 모델 분석', 11),
                ('Week 13 – Long Context', 'Long Context Modeling, RoPE, Streaming', 12),
                ('Week 14 – Advanced Architectures', 'MoE, SSM, Mamba, 차세대 아키텍처', 13),
                ('Week 15 – Final Project', '최종 프로젝트 발표 및 토론', 14),
            ]
            for title, desc, order in weeks:
                db.execute("INSERT INTO weeks(title, description, sort_order) VALUES(?,?,?)", (title, desc, order))
                week_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                default_sections = ['📌 핵심 정리', '💡 자유 토론', '📎 자료 공유', '❓ 질문']
                for i, sec in enumerate(default_sections):
                    db.execute("INSERT INTO sections(week_id, name, sort_order) VALUES(?,?,?)", (week_id, sec, i))


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
    color: str = '#d4a843'

class CommentCreate(BaseModel):
    member_id: int
    text: str

class ReactionCreate(BaseModel):
    emoji: str


# ── API: Members ─────────────────────────────────
@app.get("/api/members")
def list_members():
    with get_db() as db:
        rows = db.execute("SELECT * FROM members ORDER BY id").fetchall()
        return [dict(r) for r in rows]


@app.put("/api/members/{member_id}")
def rename_member(member_id: int, body: MemberRename):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    with get_db() as db:
        r = db.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Member not found")
        db.execute("UPDATE members SET name=? WHERE id=?", (name, member_id))
        return {"ok": True, "name": name}


# ── API: Weeks ───────────────────────────────────
@app.get("/api/weeks")
def list_weeks():
    with get_db() as db:
        weeks = db.execute("SELECT * FROM weeks ORDER BY sort_order").fetchall()
        result = []
        for w in weeks:
            w_dict = dict(w)
            # Sections
            secs = db.execute("SELECT name FROM sections WHERE week_id=? ORDER BY sort_order", (w['id'],)).fetchall()
            w_dict['sections'] = [s['name'] for s in secs]
            # Posts
            posts = db.execute("""
                SELECT p.*, m.name as author, m.color as author_color
                FROM posts p JOIN members m ON p.member_id = m.id
                WHERE p.week_id=? ORDER BY p.created_at DESC
            """, (w['id'],)).fetchall()
            posts_list = []
            for p in posts:
                p_dict = dict(p)
                p_dict['tags'] = json.loads(p_dict['tags'])
                # Comments
                comments = db.execute("""
                    SELECT c.*, m.name as author, m.color as author_color
                    FROM comments c JOIN members m ON c.member_id = m.id
                    WHERE c.post_id=? ORDER BY c.created_at ASC
                """, (p['id'],)).fetchall()
                p_dict['comments'] = [dict(c) for c in comments]
                # Reactions
                reactions = db.execute("SELECT emoji FROM reactions WHERE post_id=?", (p['id'],)).fetchall()
                reaction_counts = {}
                for r in reactions:
                    reaction_counts[r['emoji']] = reaction_counts.get(r['emoji'], 0) + 1
                p_dict['reactions'] = reaction_counts
                posts_list.append(p_dict)
            w_dict['posts'] = posts_list
            result.append(w_dict)
        return result


@app.post("/api/weeks")
def create_week(body: WeekCreate):
    with get_db() as db:
        max_order = db.execute("SELECT COALESCE(MAX(sort_order),0) FROM weeks").fetchone()[0]
        db.execute("INSERT INTO weeks(title, description, sort_order) VALUES(?,?,?)",
                   (body.title, body.description, max_order + 1))
        week_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        default_sections = ['📌 핵심 정리', '💡 자유 토론', '📎 자료 공유', '❓ 질문']
        for i, sec in enumerate(default_sections):
            db.execute("INSERT INTO sections(week_id, name, sort_order) VALUES(?,?,?)", (week_id, sec, i))
        return {"ok": True, "id": week_id}


@app.put("/api/weeks/{week_id}")
def update_week(week_id: int, body: WeekUpdate):
    with get_db() as db:
        r = db.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Week not found")
        if body.title is not None:
            db.execute("UPDATE weeks SET title=? WHERE id=?", (body.title, week_id))
        if body.description is not None:
            db.execute("UPDATE weeks SET description=? WHERE id=?", (body.description, week_id))
        return {"ok": True}


@app.delete("/api/weeks/{week_id}")
def delete_week(week_id: int):
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM weeks").fetchone()[0]
        if count <= 1:
            raise HTTPException(400, "Cannot delete the last week")
        db.execute("DELETE FROM weeks WHERE id=?", (week_id,))
        return {"ok": True}


# ── API: Posts ───────────────────────────────────
@app.post("/api/posts")
def create_post(body: PostCreate):
    with get_db() as db:
        now = datetime.utcnow().isoformat()
        db.execute("""
            INSERT INTO posts(week_id, member_id, section, title, content, tags, color, created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (body.week_id, body.member_id, body.section, body.title, body.content,
              json.dumps(body.tags), body.color, now))
        post_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"ok": True, "id": post_id}


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int):
    with get_db() as db:
        db.execute("DELETE FROM posts WHERE id=?", (post_id,))
        return {"ok": True}


# ── API: Reactions ───────────────────────────────
@app.post("/api/posts/{post_id}/reactions")
def add_reaction(post_id: int, body: ReactionCreate):
    with get_db() as db:
        db.execute("INSERT INTO reactions(post_id, emoji) VALUES(?,?)", (post_id, body.emoji))
        return {"ok": True}


# ── API: Comments ────────────────────────────────
@app.post("/api/posts/{post_id}/comments")
def add_comment(post_id: int, body: CommentCreate):
    with get_db() as db:
        now = datetime.utcnow().isoformat()
        db.execute("INSERT INTO comments(post_id, member_id, text, created_at) VALUES(?,?,?,?)",
                   (post_id, body.member_id, body.text, now))
        comment_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"ok": True, "id": comment_id}


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int):
    with get_db() as db:
        db.execute("DELETE FROM comments WHERE id=?", (comment_id,))
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
