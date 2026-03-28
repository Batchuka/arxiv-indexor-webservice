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
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # Migrations: add columns if not yet present
    for col, typedef in [
        ("input_tokens", "INTEGER DEFAULT 0"),
        ("output_tokens", "INTEGER DEFAULT 0"),
        ("operation", "TEXT DEFAULT 'fetch_classify'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Mark any runs left in RUNNING state (e.g. server crash) as interrupted
    conn.execute("UPDATE runs SET status = 'interrupted' WHERE status = 'running'")
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


def get_today_new_articles_page(
    conn: sqlite3.Connection, page: int = 1, per_page: int = 20
) -> tuple[list[dict], int]:
    page = max(1, page)
    per_page = max(1, per_page)

    total = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE date(fetched_at) = date('now') AND score IS NULL"
    ).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    rows = conn.execute(
        """
        SELECT * FROM articles
        WHERE date(fetched_at) = date('now') AND score IS NULL
        ORDER BY fetched_at DESC
        LIMIT ? OFFSET ?
        """,
        (per_page, offset),
    ).fetchall()
    return [dict(r) for r in rows], total_pages


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


def get_top_unsummarized(conn: sqlite3.Connection, n: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM articles WHERE score >= 9.0 AND summary IS NULL ORDER BY score DESC, fetched_at DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_articles(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM articles ORDER BY fetched_at DESC, score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_ranked_articles_page(
    conn: sqlite3.Connection,
    query: str = "",
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[dict], int]:
    page = max(1, page)
    per_page = max(1, per_page)
    q = (query or "").strip()
    like = f"%{q}%"

    where = "score IS NOT NULL"
    params: tuple[object, ...]

    if q:
        where += " AND (id LIKE ? OR title LIKE ? OR authors LIKE ? OR abstract LIKE ? OR category LIKE ?)"
        params = (like, like, like, like, like)
    else:
        params = ()

    total = conn.execute(f"SELECT COUNT(*) FROM articles WHERE {where}", params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    rows = conn.execute(
        f"""
        SELECT * FROM articles
        WHERE {where}
        ORDER BY score DESC, fetched_at DESC
        LIMIT ? OFFSET ?
        """,
        params + (per_page, offset),
    ).fetchall()
    return [dict(r) for r in rows], total_pages


def get_today_fetch_stats(conn: sqlite3.Connection) -> dict:
    """Returns count of articles fetched today and the time of the last fetch."""
    row = conn.execute(
        "SELECT COUNT(*) as count, MAX(fetched_at) as last_at FROM articles WHERE date(fetched_at) = date('now')"
    ).fetchone()
    return {"count": row["count"] or 0, "last_at": row["last_at"]}


def get_last_run_by_op(conn: sqlite3.Connection, operation: str) -> dict | None:
    """Returns the most recent run for a given operation type."""
    row = conn.execute(
        "SELECT * FROM runs WHERE operation = ? AND status != 'interrupted' ORDER BY id DESC LIMIT 1",
        (operation,),
    ).fetchone()
    return dict(row) if row else None


def insert_run(conn: sqlite3.Connection, operation: str = "fetch_classify") -> int:
    cur = conn.execute("INSERT INTO runs (status, operation) VALUES ('running', ?)", (operation,))
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


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def clear_all_scores(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE articles SET score = NULL, summary = NULL")
    conn.commit()
