"""Importing this module registers every module's database schema."""
from groupflow.core import identity  # noqa: F401
from groupflow.engine import schema as _engine  # noqa: F401
from groupflow.engine import collaboration_schema as _collaboration  # noqa: F401
from groupflow.engine import membership_schema as _membership  # noqa: F401
from groupflow.engine import followups_schema as _followups  # noqa: F401
from groupflow.engine import momentum_schema as _momentum  # noqa: F401
from groupflow.engine import together_schema as _together  # noqa: F401
from groupflow.engine import product_schema as _product  # noqa: F401
from groupflow.engine import reliability_schema as _reliability  # noqa: F401
from groupflow.engine import coordination_schema as _coordination  # noqa: F401
from groupflow.engine import hardening_schema as _hardening  # noqa: F401
from groupflow.engine import joins_schema as _joins  # noqa: F401
