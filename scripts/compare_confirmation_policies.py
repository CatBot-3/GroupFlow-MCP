"""Execute A+B vs disabled approval reuse on identical synthetic repair inputs.

This is an isolated policy ablation, not a competitor benchmark or user study.
The baseline deliberately disables _carry_forward in its in-memory instance.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from groupflow.core.settings import ServerSettings
from groupflow.runtime import build_runtime
from groupflow.world import approve_all


def execute(reuse: bool) -> dict:
    rt = build_runtime(ServerSettings(db_path=':memory:'), stages={'friday_dinner': 'degraded'})
    try:
        engine = rt.services.engine
        repair = engine.find_alternatives('alice', plan='friday_dinner')
        if not reuse:
            engine._carry_forward = lambda *_: 0
        sent = engine.request_confirmations('alice', plan='friday_dinner')
        plan = engine._plan_row('friday_dinner')
        revision = engine._revision(plan['candidate_revision_id'])
        view = engine.context('alice', plan='friday_dinner', mark_read=False).plans[0]
        before = rt.db.scalar('SELECT count(*) FROM approval_receipts')
        gate = engine.commit_plan('alice', plan='friday_dinner')
        assert gate.outcome == 'blocked'
        for person in ('alice', 'bob', 'chen', 'dana'):
            approve_all(engine, engine.identity.get(person), 'friday_dinner')
        answers = rt.db.scalar('SELECT count(*) FROM approval_receipts') - before
        committed = engine.commit_plan('alice', plan='friday_dinner')
        assert committed.outcome == 'committed'
        final = engine.context('alice', plan='friday_dinner', mark_read=False).plans[0]
        assert final.agreement_summary['carried_forward'] == sent.carried_forward
        return {'policy': 'scoped_reuse' if reuse else 'reconfirm_every_scope',
                'candidate_digest': revision['content_digest'], 'changes': view.working.changes,
                'solver_status': repair.proof.status, 'required_scopes': view.confirmations.required,
                'carried_forward': sent.carried_forward, 'new_human_receipts': answers,
                'unanswered_gate': gate.outcome, 'final_gate': committed.outcome,
                'committed_summary': final.agreement_summary}
    finally:
        rt.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('docs/confirmation_policy_comparison.json'))
    args = parser.parse_args()
    baseline, combined = execute(False), execute(True)
    assert baseline['candidate_digest'] == combined['candidate_digest']
    assert baseline['changes'] == combined['changes']
    assert combined['new_human_receipts'] == combined['required_scopes'] - combined['carried_forward']
    result = {'fixture': 'synthetic Friday dinner; same minimal repair in both isolated instances',
              'baseline_intervention': 'Disable carry-forward only; all other engine/solver/gate behavior unchanged',
              'not_measured': ['real customer time savings', 'competitor behavior', 'market demand'],
              'policies': [baseline, combined],
              'fewer_confirmation_requests_percent': 100 * (1 - combined['new_human_receipts'] / baseline['new_human_receipts'])}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
