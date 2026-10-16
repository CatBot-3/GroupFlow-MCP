"""The database refuses a file made by another schema version instead of failing later on an old column or check."""
import sqlite3

import pytest

from groupflow.core.db import Db, SchemaMismatch


def test_a_database_from_another_version_is_refused(tmp_path):
    path = str(tmp_path / "gf.db")
    Db(path).raw.close()
    Db(path).raw.close()  # the same version opens again without complaint
    raw = sqlite3.connect(path)
    raw.execute("UPDATE gf_meta SET value = 'older' WHERE key = 'schema'")
    raw.commit()
    raw.close()
    with pytest.raises(SchemaMismatch, match="Preserve the file"):
        Db(path)


def test_a_database_without_a_recorded_version_is_refused(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE plans (id TEXT PRIMARY KEY)")
    raw.commit()
    raw.close()
    with pytest.raises(SchemaMismatch):
        Db(path)
