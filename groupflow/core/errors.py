"""Business errors with stable codes.

Tools turn these into MCP tool error results (`isError: true`) whose text is
`{"error": {"code", "message", "details"}}`, so a model or client can react to
the code instead of parsing prose.
"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DomainError({self.code!r}, {self.message!r})"
