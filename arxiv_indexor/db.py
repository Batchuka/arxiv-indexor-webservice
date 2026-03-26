import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "arxiv.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT,
            abstract TEXT,
            category TEXT,
            published TEXT,
            link TEXT,
            score REAL,
            summary TEXT,
            read INTEGER DEFAULT 0,
            fetched_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT DEFAULT (datetime('now')),
            status TEXT,
            articles_fetched INTEGER DEFAULT 0,
            articles_classified INTEGER DEFAULT 0,
            error TEXT
        );
    """)
    # Migration: add token tracking columns if not yet present
    for col, typedef in [("input_tokens", "INTEGER DEFAULT 0"), ("output_tokens", "INTEGER DEFAULT 0")]:
        try:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()


def insert_article(conn: sqlite3.Connection, article: dict) -> bool:
    """Returns True if the article was newly inserted, False if it already existed."""
    cur = conn.execute("""
        INSERT OR IGNORE INTO articles (id, title, authors, abstract, category, published, link)
        VALUES (:id, :title, :authors, :abstract, :category, :published, :link)
    """, article)
    return cur.rowcount == 1


def update_score(conn: sqlite3.Connection, article_id: str, score: float, summary: str | None = None):
    conn.execute(
        "UPDATE articles SET score = ?, summary = ? WHERE id = ?",
        (score, summary, article_id),
    )


def get_today_articles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM articles WHERE date(fetched_at) = date('now') ORDER BY score DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_unscored_articles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM articles WHERE score IS NULL ORDER BY fetched_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_top_articles(conn: sqlite3.Connection, n: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM articles WHERE date(fetched_at) = date('now') AND score IS NOT NULL ORDER BY score DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_articles(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM articles ORDER BY fetched_at DESC, score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO runs (status) VALUES ('running')")
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, fetched: int, classified: int, error: str | None = None, input_tokens: int = 0, output_tokens: int = 0):
    conn.execute(
        "UPDATE runs SET status = ?, articles_fetched = ?, articles_classified = ?, error = ?, input_tokens = ?, output_tokens = ? WHERE id = ?",
        (status, fetched, classified, error, input_tokens, output_tokens, run_id),
    )
    conn.commit()


def get_last_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
