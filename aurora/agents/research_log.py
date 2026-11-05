"""
Research log: which projects you're working on, when you last worked on
each, and which papers you've already seen. Kept in the memory database
(new tables), so it can be inspected and deleted like other memories.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aurora.config import settings
from aurora.memory.manager import keywords


@dataclass
class Session:
    time: datetime
    summary: str
    papers: list[str] = field(default_factory=list)   # Paper URLs seen in that session.


@dataclass
class Project:
    id: int
    name: str
    created: datetime
    sessions: list[Session] = field(default_factory=list)

    @property
    def last_session(self) -> Session | None:
        return self.sessions[-1] if self.sessions else None

    @property
    def seen_papers(self) -> set[str]:
        return {url for session in self.sessions for url in session.papers}


class ResearchLog:

    def __init__(self, path: str | Path | None = None):
        self.path = path or settings.DATABASE_PATH
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS research_projects (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                       created TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS research_sessions (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       project_id INTEGER NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                       time TEXT NOT NULL,
                       summary TEXT NOT NULL,
                       papers TEXT NOT NULL)"""
            )

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def projects(self) -> list[Project]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, name, created FROM research_projects ORDER BY id").fetchall()
            projects = [Project(r[0], r[1], datetime.fromisoformat(r[2])) for r in rows]
            for project in projects:
                for time, summary, papers in connection.execute(
                    "SELECT time, summary, papers FROM research_sessions WHERE project_id = ? ORDER BY time",
                    (project.id,),
                ):
                    project.sessions.append(Session(datetime.fromisoformat(time), summary, json.loads(papers)))
        return projects

    def find(self, topic: str) -> Project | None:
        """The project whose name shares the most keywords with ``topic`` (all of the shorter one's)."""
        wanted = set(keywords(topic))
        best, best_score = None, 0
        for project in self.projects():
            have = set(keywords(project.name))
            common = len(wanted & have)
            if common and common >= min(len(wanted), len(have)) and common > best_score:
                best, best_score = project, common
        return best

    def record(self, topic: str, summary: str, papers: list[str], when: datetime | None = None) -> Project:
        when = when or datetime.now()
        project = self.find(topic)
        with self._connect() as connection:
            if project is None:
                cursor = connection.execute(
                    "INSERT INTO research_projects (name, created) VALUES (?, ?)", (topic, when.isoformat()),
                )
                project_id = cursor.lastrowid
            else:
                project_id = project.id
            connection.execute(
                "INSERT INTO research_sessions (project_id, time, summary, papers) VALUES (?, ?, ?, ?)",
                (project_id, when.isoformat(), summary, json.dumps(papers)),
            )
        return self.find(topic)
