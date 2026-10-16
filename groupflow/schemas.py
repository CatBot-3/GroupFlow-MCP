"""Importing this module registers every module's database schema."""
from groupflow.core import identity  # noqa: F401
from groupflow.engine import schema as _engine  # noqa: F401
from groupflow.engine import collaboration_schema as _collaboration  # noqa: F401
from groupflow.engine import membership_schema as _membership  # noqa: F401
