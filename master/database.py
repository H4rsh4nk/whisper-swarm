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

            CREATE TABLE IF NOT EXISTS books (
                book_id TEXT PRIMARY KEY,
                original_filename TEXT,
                paused INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_logs_created ON activity_logs(created_at);
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

        # First try to get a pending task (excluding paused books)
        row = conn.execute("""
            SELECT t.* FROM tasks t
            LEFT JOIN books b ON t.book_id = b.book_id
            WHERE t.status = 'pending' AND COALESCE(b.paused, 0) = 0
            ORDER BY t.book_id, t.start_time LIMIT 1
        """).fetchone()

        if not row:
            # Try to recover a stale in-progress task (excluding paused books)
            row = conn.execute("""
                SELECT t.* FROM tasks t
                LEFT JOIN books b ON t.book_id = b.book_id
                WHERE t.status = 'in_progress' AND t.started_at < ?
                AND COALESCE(b.paused, 0) = 0
                ORDER BY t.book_id, t.start_time LIMIT 1
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
            SELECT t.book_id, t.original_filename,
                   COUNT(*) as total_chunks,
                   SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) as completed_chunks,
                   MIN(t.created_at) as created_at,
                   COALESCE(b.paused, 0) as paused
            FROM tasks t
            LEFT JOIN books b ON t.book_id = b.book_id
            GROUP BY t.book_id
            ORDER BY t.created_at DESC
        """).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def create_book(self, book_id: str, original_filename: str):
        """Create a book entry."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR IGNORE INTO books (book_id, original_filename)
            VALUES (?, ?)
        """, (book_id, original_filename))
        conn.commit()
        conn.close()

    def pause_book(self, book_id: str):
        """Pause processing of a book."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO books (book_id, paused) VALUES (?, 1)
            ON CONFLICT(book_id) DO UPDATE SET paused = 1
        """, (book_id,))
        conn.commit()
        conn.close()

    def resume_book(self, book_id: str):
        """Resume processing of a book."""
        conn = self._get_conn()
        conn.execute("UPDATE books SET paused = 0 WHERE book_id = ?", (book_id,))
        conn.commit()
        conn.close()

    def delete_book(self, book_id: str) -> list[str]:
        """Delete a book and all its tasks. Returns list of chunk paths to delete."""
        conn = self._get_conn()
        # Get chunk paths before deleting
        rows = conn.execute(
            "SELECT chunk_path FROM tasks WHERE book_id = ?", (book_id,)
        ).fetchall()
        chunk_paths = [row[0] for row in rows]
        
        # Delete tasks and book
        conn.execute("DELETE FROM tasks WHERE book_id = ?", (book_id,))
        conn.execute("DELETE FROM books WHERE book_id = ?", (book_id,))
        conn.commit()
        conn.close()
        return chunk_paths

    def is_book_paused(self, book_id: str) -> bool:
        """Check if a book is paused."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT paused FROM books WHERE book_id = ?", (book_id,)
        ).fetchone()
        conn.close()
        return row[0] == 1 if row else False

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

    def reset_in_progress_tasks(self):
        """Reset all in-progress tasks back to pending status."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE tasks SET status = 'pending', worker_id = NULL, started_at = NULL
            WHERE status = 'in_progress'
        """)
        conn.commit()
        conn.close()

    def add_log(self, log_type: str, message: str):
        """Add an activity log entry."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO activity_logs (log_type, message) VALUES (?, ?)
        """, (log_type, message))
        conn.commit()
        
        # Keep only last 500 logs to prevent database bloat
        conn.execute("""
            DELETE FROM activity_logs WHERE id NOT IN (
                SELECT id FROM activity_logs ORDER BY created_at DESC LIMIT 500
            )
        """)
        conn.commit()
        conn.close()

    def get_recent_logs(self, limit: int = 100) -> list[dict]:
        """Get recent activity logs."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT log_type, message, created_at FROM activity_logs
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(row) for row in rows]
