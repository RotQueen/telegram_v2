"""Storage layer for project chat bindings.

Designed around SQLite but easily replaceable. The SQLite schema:
- slug (TEXT PRIMARY KEY)
- customer_chat_id (INTEGER, nullable)
- executor_chat_id (INTEGER, nullable)
- is_active (INTEGER as boolean)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class Project:
    slug: str
    customer_chat_id: Optional[int]
    executor_chat_id: Optional[int]
    is_active: bool = True


class ProjectStorage:
    """Abstract interface for project storage."""

    def create_project(self, slug: str, executor_chat_id: int) -> Project:
        raise NotImplementedError

    def bind_customer(self, slug: str, customer_chat_id: int) -> Project:
        raise NotImplementedError

    def unlink_chat(self, slug: str, chat_id: int) -> Project:
        raise NotImplementedError

    def find_by_chat(self, chat_id: int) -> Optional[Project]:
        raise NotImplementedError

    def get(self, slug: str) -> Optional[Project]:
        raise NotImplementedError

    def list_projects(self) -> Iterable[Project]:
        raise NotImplementedError


class SQLiteProjectStorage(ProjectStorage):
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    slug TEXT PRIMARY KEY,
                    customer_chat_id INTEGER,
                    executor_chat_id INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.commit()

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        return Project(
            slug=row["slug"],
            customer_chat_id=row["customer_chat_id"],
            executor_chat_id=row["executor_chat_id"],
            is_active=bool(row["is_active"]),
        )

    def create_project(self, slug: str, executor_chat_id: int) -> Project:
        with self._connect() as conn:
            existing = conn.execute("SELECT slug FROM projects WHERE slug = ?", (slug,)).fetchone()
            if existing:
                raise ValueError(f"Проект {slug} уже существует")
            conn.execute(
                "INSERT INTO projects (slug, executor_chat_id, is_active) VALUES (?, ?, 1)",
                (slug, executor_chat_id),
            )
            conn.commit()
        return Project(slug=slug, customer_chat_id=None, executor_chat_id=executor_chat_id, is_active=True)

    def bind_customer(self, slug: str, customer_chat_id: int) -> Project:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
            if not row:
                raise ValueError(f"Проект {slug} не найден")
            conn.execute(
                "UPDATE projects SET customer_chat_id = ?, is_active = 1 WHERE slug = ?",
                (customer_chat_id, slug),
            )
            conn.commit()
        return self.get(slug)  # type: ignore[return-value]

    def unlink_chat(self, slug: str, chat_id: int) -> Project:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
            if not row:
                raise ValueError(f"Проект {slug} не найден")
            project = self._row_to_project(row)
            updates = []
            if project.executor_chat_id == chat_id:
                updates.append(("executor_chat_id", None))
            if project.customer_chat_id == chat_id:
                updates.append(("customer_chat_id", None))
            if not updates:
                # If the chat is unrelated, deactivate the project instead.
                conn.execute("UPDATE projects SET is_active = 0 WHERE slug = ?", (slug,))
            else:
                for column, value in updates:
                    conn.execute(f"UPDATE projects SET {column} = ?, is_active = 1 WHERE slug = ?", (value, slug))
            conn.commit()
        return self.get(slug)  # type: ignore[return-value]

    def find_by_chat(self, chat_id: int) -> Optional[Project]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM projects
                WHERE executor_chat_id = ? OR customer_chat_id = ?
                """,
                (chat_id, chat_id),
            ).fetchone()
        return self._row_to_project(row) if row else None

    def get(self, slug: str) -> Optional[Project]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        return self._row_to_project(row) if row else None

    def list_projects(self) -> Iterable[Project]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY slug").fetchall()
        return [self._row_to_project(row) for row in rows]
