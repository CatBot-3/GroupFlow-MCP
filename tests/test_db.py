"""The database refuses a file made by another schema version instead of failing later on an old column or check."""
import sqlite3
import hashlib

import pytest

from groupflow.core.db import Db, SchemaMismatch
from groupflow.core.db import _SCHEMAS


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


def test_product_additive_upgrade_preserves_older_rows_and_relations(tmp_path):
    path = str(tmp_path / 'older.db')
    from groupflow.engine import GroupFlow
    from groupflow.world import load_world, seed_world
    old = Db(path)
    seed_world(GroupFlow(old), load_world())
    before = {table: old.all(f'SELECT * FROM {table} ORDER BY rowid') for table in ('plans', 'items', 'facts', 'members', 'approval_receipts')}
    # Recreate the immediately preceding additive schema on this synthetic file.
    for table in ('calendar_events', 'calendar_links', 'delivery_preferences', 'action_receipts'):
        old.run(f'DROP TABLE {table}')
    legacy_sql = '\n'.join(sql for name, sql in _SCHEMAS if name != 'product_v1')
    old.run("UPDATE gf_meta SET value=? WHERE key='schema'", hashlib.sha256(legacy_sql.encode()).hexdigest()[:16])
    old.close()
    upgraded = Db(path)
    assert all(upgraded.all(f'SELECT * FROM {table} ORDER BY rowid') == rows for table, rows in before.items())
    assert upgraded.get("SELECT name FROM sqlite_master WHERE name='action_receipts'")
    assert upgraded.all('PRAGMA foreign_key_check') == []
    upgraded.close()
