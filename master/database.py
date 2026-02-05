"""
SQLite database for task management.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class Database:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = Path(__file__).parent.parent / "stt_tasks.db"
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                chunk_path TEXT NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                original_filename TEXT,
                status TEXT DEFAULT 'pending',
                worker_id TEXT,
                transcript TEXT,
                processing_time REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS workers (
                worker_id TEXT PRIMARY KEY,
                hostname TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_heartbeat TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_book ON tasks(book_id);
        """)
        conn.commit()
        conn.close()

    def create_task(self, book_id: str, chunk_id: str, chunk_path: str,
                    start_time: float, end_time: float, original_filename: str):
        conn = self._get_conn()
        task_id = f"{book_id}_{chunk_id}"
        conn.execute("""
            INSERT INTO tasks (id, book_id, chunk_id, chunk_path, start_time, end_time, original_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (task_id, book_id, chunk_id, chunk_path, start_time, end_time, original_filename))
        conn.commit()
        conn.close()
        return task_id

    def get_next_pending_task(self) -> Optional[dict]:
        conn = self._get_conn()
        # Also recover stale tasks (assigned but not completed for > 10 min)
        stale_threshold = datetime.now() - timedelta(minutes=10)

        # First try to get a pending task
        row = conn.execute("""
            SELECT * FROM tasks WHERE status = 'pending'
            ORDER BY book_id, start_time LIMIT 1
        """).fetchone()

        if not row:
            # Try to recover a stale in-progress task
            row = conn.execute("""
                SELECT * FROM tasks
                WHERE status = 'in_progress' AND started_at < ?
                ORDER BY book_id, start_time LIMIT 1
            """, (stale_threshold,)).fetchone()

        conn.close()
        return dict(row) if row else None

    def assign_task(self, task_id: str, worker_id: str):
        conn = self._get_conn()
        conn.execute("""
            UPDATE tasks SET status = 'in_progress', worker_id = ?, started_at = ?
            WHERE id = ?
        """, (worker_id, datetime.now(), task_id))
        conn.commit()
        conn.close()

    def complete_task(self, task_id: str, worker_id: str, transcript: dict, processing_time: float):
        conn = self._get_conn()
        conn.execute("""
            UPDATE tasks SET
                status = 'completed',
                transcript = ?,
                processing_time = ?,
                completed_at = ?
            WHERE id = ?
        """, (json.dumps(transcript), processing_time, datetime.now(), task_id))
        conn.commit()
        conn.close()

    def get_task(self, task_id: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_tasks(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM tasks ORDER BY book_id, start_time").fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_book_tasks(self, book_id: str) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE book_id = ? ORDER BY start_time",
            (book_id,)
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_book_status(self, book_id: str) -> dict:
        conn = self._get_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE book_id = ?", (book_id,)
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE book_id = ? AND status = 'completed'",
            (book_id,)
        ).fetchone()[0]
        in_progress = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE book_id = ? AND status = 'in_progress'",
            (book_id,)
        ).fetchone()[0]
        conn.close()
        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "pending": total - completed - in_progress,
            "percent": round(completed / total * 100, 1) if total > 0 else 0
        }

    def get_status_summary(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'completed'").fetchone()[0]
        in_progress = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'in_progress'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]
        conn.close()
        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "pending": pending,
            "percent": round(completed / total * 100, 1) if total > 0 else 0
        }

    def get_all_books(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT book_id, original_filename,
                   COUNT(*) as total_chunks,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_chunks,
                   MIN(created_at) as created_at
            FROM tasks
            GROUP BY book_id
            ORDER BY created_at DESC
        """).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def register_worker(self, worker_id: str, hostname: str):
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO workers (worker_id, hostname, last_heartbeat)
            VALUES (?, ?, ?)
        """, (worker_id, hostname, datetime.now()))
        conn.commit()
        conn.close()

    def worker_heartbeat(self, worker_id: str):
        conn = self._get_conn()
        conn.execute("""
            UPDATE workers SET last_heartbeat = ? WHERE worker_id = ?
        """, (datetime.now(), worker_id))
        conn.commit()
        conn.close()

    def get_active_workers(self) -> list[dict]:
        conn = self._get_conn()
        # Workers with heartbeat in last 2 minutes
        threshold = datetime.now() - timedelta(minutes=2)
        rows = conn.execute("""
            SELECT * FROM workers WHERE last_heartbeat > ?
        """, (threshold,)).fetchall()
        conn.close()
        return [dict(row) for row in rows]
