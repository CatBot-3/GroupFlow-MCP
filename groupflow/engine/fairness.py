"""Optional group-local accommodation history; never a substitute for consent."""
from __future__ import annotations

import json
from groupflow.core.errors import DomainError
from groupflow.core.util import new_id
from groupflow.repair.domain import burden, score
from groupflow.repair.validate import violations


class Fairness:
    def human_fairness_preference(self, member, group, enabled, reset=False):
        def run():
            if not getattr(member, 'id', None) or not self._group_role(group, member.id):
                raise DomainError('NOT_FOUND', 'No such active group of yours.')
            if type(enabled) is not bool or type(reset) is not bool:
                raise DomainError('INVALID_ARGUMENT', 'Choose whether to keep your own accommodation history.')
            self.db.run('INSERT INTO fairness_preferences(member_id,group_id,enabled,updated_at) VALUES(?,?,?,?) '
                        'ON CONFLICT(member_id,group_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at', member.id, group, enabled, self.ts())
            if reset or not enabled:
                self.db.run('DELETE FROM fairness_history WHERE member_id=? AND group_id=?', member.id, group)
            return {'outcome': 'saved', 'group': group, 'enabled': enabled, 'message': 'Your accommodation history preference was saved. Past choices never require you to consent.'}
        return self.db.tx(run)

    def human_fairness_status(self, member, group):
        def read():
            if not getattr(member, 'id', None) or not self._group_role(group, member.id):
                raise DomainError('NOT_FOUND', 'No such active group of yours.')
            pref = self.db.get('SELECT * FROM fairness_preferences WHERE member_id=? AND group_id=?', member.id, group)
            return {'group': group, 'enabled': bool(pref and pref['enabled']),
                    'message': 'Opt in to share accommodation scores on mutually visible plans in this group. You can stop or reset your history.'}
        return self.db.tx(read)

    def commit_plan(self, actor_id, plan=None, candidate=None, expected_version=None):
        def run():
            scope = self.scope_for(actor_id)
            if candidate:
                r = self._revision(candidate)
                p = scope.plans.get(r['plan_id']) if r else None
            else:
                p = self.resolve_plan(scope, plan, prefer=lambda r: r['status'] in ('READY_TO_COMMIT', 'AWAITING_CONFIRMATIONS'), purpose='something can be committed')
            old = self._revision(p['active_revision_id']) if p else None
            before = self._snapshot(old) if old else None
            problem = self._problem(p, before) if before else None
            result = super(Fairness, self).commit_plan(actor_id, plan=plan, candidate=candidate, expected_version=expected_version)
            outcome = result.get('outcome') if isinstance(result, dict) else result.outcome
            if outcome == 'committed' and before and p:
                fresh = self._plan_row(p['id'])
                rev = self._revision(fresh['active_revision_id'])
                per = burden(problem, before, self._snapshot(rev))
                for mid, value in per.items():
                    if self._group_role(p['group_id'], mid) and self.db.get('SELECT 1 AS x FROM fairness_preferences WHERE member_id=? AND group_id=? AND enabled=1', mid, p['group_id']):
                        self.db.run('INSERT OR IGNORE INTO fairness_history(id,group_id,plan_id,revision_id,member_id,burden,created_at) VALUES(?,?,?,?,?,?,?)',
                                    new_id('fair'), p['group_id'], p['id'], rev['id'], mid, value, self.ts())
            return result
        return self.db.tx(run)

    def _fairness_comparison(self, actor_id, p, snap, problem):
        scope = self.scope_for(actor_id)
        current = self._revision(p['candidate_revision_id'])
        job = self.db.get('SELECT * FROM repair_jobs WHERE id=?', current['repair_job_id']) if current and current.get('repair_job_id') else None
        if not job or job['status'] != 'done':
            return {'outcome': 'search_required', 'plan': p['id'], 'alternatives': [], 'read_only': True,
                    'message': 'Find feasible options first, then compare accommodation history. Nothing was changed.'}
        if json.loads(job['input_json'])['versions'] != self._input_versions(p):
            raise DomainError('STALE', 'The planning inputs changed; search again before comparing history.')
        opted = {r['member_id'] for r in self.db.all('SELECT member_id FROM fairness_preferences WHERE group_id=? AND enabled=1', p['group_id'])
                 if r['member_id'] in snap.members and self._group_role(p['group_id'], r['member_id'])}
        history = {mid: 0 for mid in sorted(opted)}
        # Never aggregate a hidden event. Room scope is the intersection of listener access.
        for r in self.db.all('SELECT * FROM fairness_history WHERE group_id=?', p['group_id']):
            if r['member_id'] in opted and r['plan_id'] in scope.plans:
                history[r['member_id']] += r['burden']
        alternatives = []
        for row in self.db.all('SELECT revision_id,rank FROM repair_candidates WHERE job_id=? ORDER BY rank LIMIT 4', job['id']):
            rev = self._revision(row['revision_id'])
            if not rev or rev['kind'] != 'candidate' or rev['plan_id'] != p['id']:
                continue
            choice = self._snapshot(rev)
            if violations(problem, choice) or self._stale_facts(p, choice):
                continue
            scored = score(problem, choice)
            totals = {mid: history[mid] + scored.burden.get(mid, 0) for mid in sorted(opted)}
            alternatives.append({'candidate': rev['id'], 'label': rev['label'], 'rank': row['rank'],
                                 'history': history, 'current_burden': {mid: scored.burden.get(mid, 0) for mid in sorted(opted)},
                                 'totals': totals, 'maximum_total': max(totals.values(), default=0), 'objective': list(scored.objective),
                                 'changes': self._diff(p, snap, choice), 'hard_constraints_checked': True})
        if not alternatives:
            return {'outcome': 'no_candidates', 'alternatives': [], 'read_only': True, 'message': 'No current candidate passes the hard checks. Search again.'}
        least = alternatives[0]
        fairer = min(alternatives, key=lambda c: (c['maximum_total'], c['objective'], c['rank'])) if opted else least
        return {'outcome': 'fairness', 'plan': p['id'], 'read_only': True, 'alternatives': alternatives, 'history': history,
                'least_change': least['candidate'], 'fairer': fairer['candidate'],
                'message': ('The history-balanced option differs from the least-change option. The group chooses the trade-off; everyone keeps their hard limits and consent rights.'
                            if fairer['candidate'] != least['candidate'] else 'The least-change option also has the smallest tested maximum accommodation total. Consent remains personal.'),
                'scope': 'Only opted-in people and mutually visible plans in this group; hidden plans do not affect this comparison.'}
