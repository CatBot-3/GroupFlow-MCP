from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.engine import GroupFlow
from groupflow.world import load_world, seed_world
from tests.engine.test_project_collaboration import project, work, sent, answer_all


def test_separate_connections_cannot_commit_the_same_person_twice(tmp_path):
    path=str(tmp_path/'atomic.db')
    first=GroupFlow(Db(path))
    seed_world(first,load_world())
    a,b=project(first,'Concurrent A'),project(first,'Concurrent B')
    work(first,a);work(first,b)
    sent(first,a);sent(first,b)
    answer_all(first,a);answer_all(first,b)
    second=GroupFlow(Db(path))
    barrier=Barrier(2)
    def commit(engine,pid):
        barrier.wait()
        return engine.commit_project('priya',pid)['outcome']
    try:
        with ThreadPoolExecutor(2) as pool:
            fa=pool.submit(commit,first,a);fb=pool.submit(commit,second,b)
            assert sorted([fa.result(),fb.result()])==['blocked','committed']
        assert first.db.scalar('SELECT count(DISTINCT plan_id) FROM resource_reservations')==1
    finally:
        first.db.close();second.db.close()


def test_stale_project_search_does_not_overwrite_a_new_edit(gf,monkeypatch):
    from groupflow.engine import collaboration
    pid=project(gf);iid=work(gf,pid)
    real=collaboration.solve
    def intervening(*args,**kwargs):
        result=real(*args,**kwargs)
        gf.upsert_work('priya',pid,{'duration_minutes':180},item=iid)
        return result
    monkeypatch.setattr(collaboration,'solve',intervening)
    with pytest.raises(DomainError) as err:
        gf.repair_project('priya',pid)
    assert err.value.code=='STALE_INPUT'
    assert gf.get_project('priya',pid)['revision'] is None
    assert gf.get_project('priya',pid)['work'][0]['spec']['duration_minutes']==180


def test_project_scopes_appear_in_focused_and_global_personal_todos(gf):
    pid=project(gf);work(gf,pid);sent(gf,pid)
    assert any(t.plan=='Release' and t.kind=='confirm' for t in gf.context('marcus',detail='summary').your_todos)
    assert gf.context('marcus',plan=pid).plans[0].project['pending_by_person']['marcus']==1
