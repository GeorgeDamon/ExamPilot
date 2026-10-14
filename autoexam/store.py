import json
import os
import sqlite3
from pathlib import Path

from .models import TaskState, utcnow


def private_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def private_json(path: Path, data):
    private_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(0o600)
    tmp.replace(path)


class Store:
    """State/events only. Does not store questions, answer sets, cookies or API keys."""

    def __init__(self, path: Path):
        private_dir(path.parent)
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
          scope TEXT NOT NULL, exam_id TEXT NOT NULL, name TEXT NOT NULL,
          state TEXT NOT NULL, updated TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(scope, exam_id));
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, at TEXT NOT NULL, scope TEXT NOT NULL,
          exam_id TEXT NOT NULL, stage TEXT NOT NULL, elapsed REAL);
        """)
        self.db.commit()
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(tasks)")}
        if "deadline" not in columns:
            self.db.execute("ALTER TABLE tasks ADD COLUMN deadline TEXT")
            self.db.commit()

    def close(self):
        self.db.close()

    def get(self, exam):
        row = self.db.execute(
            "SELECT state FROM tasks WHERE scope=? AND exam_id=?", (exam.target.key, exam.exam_id)
        ).fetchone()
        return TaskState(row[0]) if row else None

    def set(self, exam, state: TaskState, detail=""):
        if self.get(exam) == TaskState.SUBMITTED and state != TaskState.SUBMITTED:
            raise ValueError("不能回退已确认提交状态")
        with self.db:
            self.db.execute(
                """INSERT INTO tasks(scope,exam_id,name,state,updated,detail,deadline) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, exam_id) DO UPDATE SET state=excluded.state,
                updated=excluded.updated, detail=excluded.detail,
                deadline=COALESCE(excluded.deadline,tasks.deadline)""",
                (
                    exam.target.key,
                    exam.exam_id,
                    exam.name,
                    state.value,
                    utcnow().isoformat(),
                    detail,
                    exam.deadline.isoformat() if exam.deadline else None,
                ),
            )

    def event(self, exam, stage, elapsed=None):
        with self.db:
            self.db.execute(
                "INSERT INTO events(at,scope,exam_id,stage,elapsed) VALUES(?,?,?,?,?)",
                (utcnow().isoformat(), exam.target.key, exam.exam_id, stage, elapsed),
            )

    def has_unfinished(self, scope):
        from datetime import datetime

        rows = self.db.execute(
            "SELECT state,deadline FROM tasks WHERE scope=?", (scope,)
        ).fetchall()
        return any(
            r[0]
            in {
                TaskState.ANSWERING,
                TaskState.READY,
                TaskState.SAVING,
                TaskState.SUBMITTING,
                TaskState.SUBMISSION_UNKNOWN,
            }
            and (not r[1] or datetime.fromisoformat(r[1]) > utcnow())
            for r in rows
        )

    def rows(self):
        return self.db.execute(
            "SELECT scope,exam_id,name,state,updated,detail FROM tasks"
        ).fetchall()
