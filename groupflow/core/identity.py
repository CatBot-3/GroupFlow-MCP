"""People and their credentials.

Identity always comes from a credential: the MCP bearer token for the agent
side, a separate human login for confirmations. A person may belong to several
groups; which plans they can see is decided by the engine's spaces, never by
anything the model says.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import Db, register_schema
from .util import hash_secret, verify_passcode

register_schema("core", """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  color TEXT,
  token_hash TEXT NOT NULL UNIQUE,
  passcode_hash TEXT
);
""")


@dataclass(frozen=True)
class Member:
    id: str
    name: str


@dataclass(frozen=True)
class Person:
    """A person as described in the demo world file (credentials in clear, demo only)."""
    id: str
    name: str
    token: str
    passcode: str
    color: str


def seed_people(db: Db, people: list[Person]) -> None:
    def write() -> None:
        for p in people:
            db.run("INSERT OR IGNORE INTO members (id, name, color, token_hash, passcode_hash) VALUES (?, ?, ?, ?, ?)",
                   p.id, p.name, p.color, hash_secret(p.token), hash_secret(p.passcode))
    db.tx(write)


class Identity:
    def __init__(self, db: Db):
        self.db = db

    @staticmethod
    def _member(row: dict[str, Any] | None) -> Member | None:
        return Member(id=row["id"], name=row["name"]) if row else None

    def by_token(self, token: str) -> Member | None:
        """A demo member's own token, or a token the person issued to an assistant host on their page (account linking)."""
        hashed = hash_secret(token)
        row = self.db.get("SELECT * FROM members WHERE token_hash = ?", hashed)
        if row is None:
            row = self.db.get("SELECT m.* FROM linked_tokens l JOIN members m ON m.id = l.member_id WHERE l.token_hash = ? AND l.revoked_at IS NULL", hashed)
        return self._member(row)

    def by_passcode(self, member_id: str, passcode: str) -> Member | None:
        row = self.db.get("SELECT * FROM members WHERE id = ?", (member_id or "").strip().lower())
        return self._member(row) if row and verify_passcode(passcode or "", row["passcode_hash"]) else None

    def get(self, member_id: str) -> Member | None:
        return self._member(self.db.get("SELECT * FROM members WHERE id = ?", member_id))
