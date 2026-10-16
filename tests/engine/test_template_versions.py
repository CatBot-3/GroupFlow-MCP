import hashlib
import sqlite3

import pytest

from groupflow.core.db import Db, SchemaMismatch, _SCHEMAS
from groupflow.core.errors import DomainError
from groupflow.templates import get_template


def test_new_templates_freeze_policy_and_legacy_still_resolves(gf, monkeypatch):
    pid = gf.create_plan('alice','gathering','Picnic',group='friends').plan.id
    original = gf.template(gf._plan_row(pid)).model_dump()
    monkeypatch.setattr('groupflow.engine.core.get_template', lambda _: (_ for _ in ()).throw(AssertionError('YAML must not replace snapshots')))
    assert gf.template(gf._plan_row(pid)).model_dump() == original
    monkeypatch.undo()
    gf.db.run('DELETE FROM plan_extensions WHERE plan_id=?', 'party')
    assert gf.template(gf._plan_row('party')).id == 'surprise_party'
    assert get_template('weekend_trip').catalog.places


def test_generic_trip_is_not_a_cottage_itinerary_and_supports_ten_days(gf):
    view = gf.create_plan('dana','trip','Long trip',group='cottage',days=10).plan
    assert view.planning_window_days == 10 and view.working is None
    assert gf.db.scalar('SELECT catalog_json FROM plans WHERE id=?',view.id).find('pine_lake') == -1
    from datetime import date, timedelta
    day10 = (date.fromisoformat(gf._plan_row(view.id)['start_date']) + timedelta(days=9)).isoformat()
    r=gf.propose_change('dana',plan=view.id,new_type='activity',title='Return',day=day10,start='17:00',end='18:00')
    assert r.item
    preset=gf.create_plan('dana','trip','Cottage demo',group='cottage',preset='weekend_cottage').plan
    assert len(preset.working.activities)==6 and preset.planning_window_days==3


def test_surprise_preset_requires_and_enforces_explicit_audience(gf):
    with pytest.raises(DomainError):
        gf.create_plan('alice','gathering','Secret',group='family',preset='surprise')
    view=gf.create_plan('alice','gathering','Secret',group='family',preset='surprise',hidden_from=['Linda']).plan
    assert 'Linda' not in view.visible_to
    assert view.id not in {p.id for p in gf.context('linda').plans}


@pytest.mark.parametrize('days',[0,-1,367,True,1.5])
def test_invalid_planning_window_rejected(gf,days):
    with pytest.raises(DomainError):
        gf.create_plan('alice','trip','Bad window',group='friends',days=days)


def test_additive_migration_preserves_original_rows_and_refuses_unknown(tmp_path):
    dbpath=tmp_path/'legacy.db'
    Db().close()  # register schemas
    sql='\n'.join(s for n,s in _SCHEMAS if n!='collaboration_v1')
    legacy=hashlib.sha256(sql.encode()).hexdigest()[:16]
    c=sqlite3.connect(dbpath)
    c.executescript(sql)
    c.execute('CREATE TABLE gf_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    c.execute('INSERT INTO gf_meta VALUES(?,?)',('schema',legacy))
    c.execute('INSERT INTO meta VALUES(?,?)',('preserved','user content'))
    c.commit();c.close()
    upgraded=Db(str(dbpath))
    assert upgraded.scalar("SELECT value FROM meta WHERE key='preserved'")=='user content'
    assert upgraded.get("SELECT name FROM sqlite_master WHERE name='project_work'")
    upgraded.run("UPDATE gf_meta SET value='unknown' WHERE key='schema'")
    upgraded.close()
    with pytest.raises(SchemaMismatch):
        Db(str(dbpath))
    c=sqlite3.connect(dbpath)
    assert c.execute("SELECT value FROM meta WHERE key='preserved'").fetchone()[0]=='user content'
    c.close()
