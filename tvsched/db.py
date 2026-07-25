import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "data/tv_scheduler.db")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shows (
                id INTEGER PRIMARY KEY,
                run_date TEXT,
                date TEXT,
                weekday TEXT,
                time TEXT,
                channel TEXT,
                title TEXT,
                href TEXT,
                Country TEXT DEFAULT '',
                Year TEXT DEFAULT '',
                Genre TEXT DEFAULT '',
                Rating TEXT DEFAULT '',
                Description TEXT DEFAULT '',
                Quote TEXT DEFAULT '',
                Cast TEXT DEFAULT '',
                Crew TEXT DEFAULT '',
                UNIQUE(run_date, date, time, channel, title)
            );
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY,
                show_id INTEGER REFERENCES shows(id),
                run_date TEXT,
                slot TEXT,
                final_score REAL,
                rank_in_group INTEGER,
                is_must_watch INTEGER,
                UNIQUE(show_id, run_date)
            );
            CREATE TABLE IF NOT EXISTS selections (
                id INTEGER PRIMARY KEY,
                show_id INTEGER REFERENCES shows(id),
                selected INTEGER,
                run_date TEXT,
                UNIQUE(show_id, run_date)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                run_date TEXT PRIMARY KEY,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS training (
                id INTEGER PRIMARY KEY,
                title TEXT,
                description TEXT,
                "cast" TEXT,
                crew TEXT,
                channel TEXT,
                selected INTEGER,
                run_date TEXT
            );
        """)
        _migrate(conn)


def _migrate(conn):
    existing = {r[1] for r in conn.execute("PRAGMA table_info(shows)")}
    for column in ("start_utc", "end_utc"):
        if column not in existing:
            conn.execute(f"ALTER TABLE shows ADD COLUMN {column} TEXT DEFAULT ''")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_shows_run_date ON shows(run_date);
        CREATE INDEX IF NOT EXISTS idx_scores_run_date ON scores(run_date);
        CREATE INDEX IF NOT EXISTS idx_selections_run_date ON selections(run_date);
    """)


def save_shows(shows, run_date):
    with _conn() as conn:
        for s in shows:
            conn.execute("""
                INSERT OR IGNORE INTO shows
                    (run_date, date, weekday, time, channel, title, href,
                     Country, Year, Genre, Rating, Description, Quote, Cast, Crew,
                     start_utc, end_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_date,
                s.get("date", ""), s.get("weekday", ""), s.get("time", ""),
                s.get("channel", ""), s.get("title", ""), s.get("href", "") or "",
                s.get("Country", ""), s.get("Year", ""), s.get("Genre", ""),
                s.get("Rating", ""), s.get("Description", ""), s.get("Quote", ""),
                s.get("Cast", ""), s.get("Crew", ""),
                s.get("start_utc", ""), s.get("end_utc", ""),
            ))


def get_run_shows(run_date):
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM shows WHERE run_date = ?", (run_date,)
        ).fetchall()]


def save_scores(scored_shows, run_date):
    with _conn() as conn:
        for s in scored_shows:
            conn.execute("""
                INSERT OR REPLACE INTO scores
                    (show_id, run_date, slot, final_score, rank_in_group, is_must_watch)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                s["id"], run_date, s.get("slot", ""),
                float(s.get("final_score") or 0.0),
                int(s.get("rank_in_group") or 0),
                int(bool(s.get("is_must_watch", False))),
            ))


def get_run_shows_with_scores(run_date):
    with _conn() as conn:
        rows = conn.execute("""
            SELECT sh.*, sc.slot, sc.final_score, sc.rank_in_group, sc.is_must_watch
            FROM shows sh
            JOIN scores sc ON sh.id = sc.show_id AND sc.run_date = sh.run_date
            WHERE sh.run_date = ?
        """, (run_date,)).fetchall()
        return [dict(r) for r in rows]


def session_done(run_date):
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM sessions WHERE run_date = ?", (run_date,)
        ).fetchone() is not None


def get_session_selections(run_date):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT show_id FROM selections WHERE run_date = ? AND selected = 1", (run_date,)
        ).fetchall()
        return {r["show_id"] for r in rows}


def save_session(run_date, selected_ids, all_show_ids):
    selected_set = {str(i) for i in selected_ids}
    with _conn() as conn:
        for sid in all_show_ids:
            sel = 1 if str(sid) in selected_set else 0
            conn.execute(
                "INSERT OR IGNORE INTO selections (show_id, selected, run_date) VALUES (?, ?, ?)",
                (sid, sel, run_date),
            )
            row = conn.execute(
                'SELECT title, Description, "Cast", Crew, channel FROM shows WHERE id = ?', (sid,)
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO training
                       (title, description, "cast", crew, channel, selected, run_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (row["title"], row["Description"], row["Cast"], row["Crew"],
                     row["channel"], sel, run_date),
                )
        conn.execute(
            "INSERT OR REPLACE INTO sessions (run_date, completed_at) VALUES (?, datetime('now'))",
            (run_date,),
        )


def get_training_data():
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT title, description, "cast", crew, channel, selected FROM training'
        ).fetchall()]


def get_training_stats():
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM training").fetchone()[0]
        selected = conn.execute("SELECT COUNT(*) FROM training WHERE selected = 1").fetchone()[0]
        return total, selected


def clear_run(run_date: str) -> int:
    """Drop the stored schedule and scores for one run date, so it can be recollected.

    Only ever touches the given run date. Selections, training rows and every other
    run are left alone.

    Returns:
        How many show rows were removed.
    """
    with _conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM shows WHERE run_date = ?", (run_date,)).fetchone()[0]
        conn.execute("DELETE FROM scores WHERE run_date = ?", (run_date,))
        conn.execute("DELETE FROM shows WHERE run_date = ?", (run_date,))
        return n


def last_scored_run_date() -> str | None:
    """The most recent run_date that produced scores, i.e. the last successful run."""
    with _conn() as conn:
        row = conn.execute("SELECT MAX(run_date) FROM scores").fetchone()
        return row[0] if row and row[0] else None


def get_titles_from_previous_runs(run_date: str) -> set:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT sh.title
            FROM shows sh JOIN scores sc ON sh.id = sc.show_id
            WHERE sh.run_date < ?
        """, (run_date,)).fetchall()
        return {r["title"] for r in rows}
