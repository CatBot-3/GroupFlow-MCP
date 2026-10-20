"""A guarded follow-up queues expensive work without solving inside its transaction."""
import groupflow.engine.agreement as agreement

from tests.shared_workload import build_world


def test_guarded_search_answer_is_durable_and_retry_does_not_queue_twice(tmp_path, monkeypatch):
    world = build_world(tmp_path / 'followup.sqlite')
    engine = world.engine
    try:
        question = engine.ask_followup('avery', question='Search dinner alternatives?', plan='dinner', options=[
            {'label': 'Search', 'action': {'tool': 'find_alternatives', 'arguments': {'plan': 'dinner'}}},
            {'label': 'Keep'}])
        args = {'followup': question['id'], 'choice': 'Search'}
        expected = engine.state_token('dinner')
        approvals_before = engine.db.all('SELECT * FROM approval_receipts ORDER BY id')
        calls = []
        original = agreement.repair

        def observed(*args, **kwargs):
            calls.append(engine.db.in_transaction)
            return original(*args, **kwargs)

        monkeypatch.setattr(agreement, 'repair', observed)
        answer = engine.apply_action('avery', 'answer_followup', args, 'shared-search-answer', expected)
        assert answer['action']['ok']
        job = answer['action']['result']['job']
        assert answer['action']['result']['status'] == 'queued'
        assert answer['action_receipt']['plans'] == ['dinner']
        assert not calls, 'Guarded action must commit its queue entry before any solve'
        assert engine.db.scalar('SELECT status FROM repair_jobs WHERE id=?', job) == 'queued'
        assert engine.db.scalar('SELECT status FROM followups WHERE id=?', question['id']) == 'answered'

        engine.run_queued_jobs()
        assert calls == [False], 'The queued solver must run outside a database transaction'
        assert engine.job_status(job) == 'done'
        replay = engine.apply_action('avery', 'answer_followup', args, 'shared-search-answer', expected)
        assert replay['replayed']
        assert replay['action_receipt']['id'] == answer['action_receipt']['id']
        assert engine.db.scalar('SELECT count(*) FROM repair_jobs') == 1
        assert engine.db.all('SELECT * FROM approval_receipts ORDER BY id') == approvals_before
    finally:
        engine.db.close()
