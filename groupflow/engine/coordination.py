"""Human-owned agreement extensions; MCP cannot manufacture their consent."""
from __future__ import annotations

import json
from groupflow.core.errors import DomainError
from groupflow.core.util import digest
from .agreement import SATISFIED

from .agreement_review import AgreementReview
from .fallbacks import Fallbacks
from .planning_analysis import PlanningAnalysis
from .fairness import Fairness


class Coordination(AgreementReview, Fallbacks, PlanningAnalysis, Fairness):
    def retire_local_weather(self):
        """Retire old local rules without deleting historical data or human answers."""
        def run():
            affected = {row['plan_id'] for row in self.db.all('SELECT DISTINCT plan_id FROM weather_watches WHERE enabled=1')}
            self.db.run('UPDATE weather_watches SET enabled=0 WHERE enabled=1')
            self.db.run('DELETE FROM weather_refresh_claims')
            # Only derived dependency metadata changes; snapshot terms, content
            # digests, challenges, human receipts and external facts stay intact.
            for rev in self.db.all("SELECT id,plan_id,input_versions FROM plan_revisions WHERE input_versions LIKE '%weather_policy%'"):
                versions = json.loads(rev['input_versions'])
                if 'weather_policy' in versions:
                    versions.pop('weather_policy')
                    self.db.run('UPDATE plan_revisions SET input_versions=? WHERE id=?', json.dumps(versions), rev['id'])
                    affected.add(rev['plan_id'])
            # An old queued solve contains the retired constraints themselves.
            # Preserve its captured input, and require a fresh ordinary search.
            for job in self.db.all("SELECT id,input_json FROM repair_jobs WHERE status IN ('queued','running') AND input_json LIKE '%weather_policy%'"):
                if 'weather_policy' in json.loads(job['input_json']).get('versions', {}):
                    self.db.run("UPDATE repair_jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                                'STALE_INPUT: local weather rules were retired; request fresh alternatives.', self.ts(), job['id'])
            for pid in sorted(affected):
                p = self._plan_row(pid)
                if not p or p['status'] == 'CANCELLED':
                    continue
                candidate = self._revision(p['candidate_revision_id'])
                if candidate and p['status'] in ('DEGRADED', 'AWAITING_CONFIRMATIONS', 'READY_TO_COMMIT'):
                    if self._round_open(candidate):
                        status = 'READY_TO_COMMIT' if self._confirmations(candidate).complete and not self.gate(p, candidate) else 'AWAITING_CONFIRMATIONS'
                    else:
                        # Removing a dependency is not an organizer's decision
                        # to send an unasked proposal or open human scopes.
                        active = self._revision(p['active_revision_id'])
                        if (p['status'] != 'DEGRADED' or not active or not self._confirmations(active).complete
                                or self._active_review_problems(p, self._snapshot(active))):
                            continue
                        status = 'PROPOSED'
                    if status != p['status']:
                        self._bump(pid, status, 'Local weather rules were retired; current proposal and personal commitments were rechecked.')
                elif not candidate and p['active_revision_id'] and p['status'] == 'DEGRADED':
                    active = self._revision(p['active_revision_id'])
                    if self._confirmations(active).complete and not self._active_review_problems(p, self._snapshot(active)):
                        self._bump(pid, 'COMMITTED', 'Local weather rules were retired; the existing agreement remains valid.')
            return sorted(affected)
        return self.db.tx(run)

    def human_inbox(self, member):
        def read():
            result = super(Coordination, self).human_inbox(member)
            for p in result['plans']:
                if p.get('planning'):
                    p['agreement_version'] = self.state_token(p['id'])
            return result
        return self.db.tx(read)

    def reassess_coordination(self):
        """Reconcile time-dependent public status; gates always recheck immediately.

        The worker runs this at most one minute after expiry. Pure diagnostic
        reads remain read-only, including when time advances during a comparison.
        """
        def run():
            affected = []
            ids = {r['plan_id'] for r in self.db.all('SELECT DISTINCT a.plan_id FROM fallback_uses u JOIN fallback_authorizations a ON a.id=u.authorization_id')}
            for pid in sorted(ids):
                p = self._plan_row(pid)
                if not p or p['status'] == 'CANCELLED':
                    continue
                revision = self._revision(p['candidate_revision_id'] or p['active_revision_id'])
                if not revision:
                    continue
                snap = self._snapshot(revision)
                scopes = self.db.all('SELECT * FROM approval_scopes WHERE revision_id=?', revision['id'])
                invalid = any(self._scope_status(s, snap)[0] not in SATISFIED for s in scopes)
                if p['status'] == 'COMMITTED' and invalid:
                    self._bump(pid, 'DEGRADED', 'A time-limited commitment needs a fresh review.')
                    affected.append(pid)
                elif p['status'] == 'READY_TO_COMMIT' and invalid:
                    self._bump(pid, 'AWAITING_CONFIRMATIONS', 'A time-limited commitment needs a fresh review.')
                    affected.append(pid)
            return affected
        return self.db.tx(run)

    def _personal_plan(self, member, plan):
        # The human identity object comes exclusively from the human session.
        if not getattr(member, 'id', None):
            raise DomainError('UNAUTHENTICATED', 'Sign in on your own page.')
        return self.resolve_plan(self.scope_for(member.id), plan)

    def _expect_coordination(self, plan, expected):
        if not expected:
            raise DomainError('EXPECTED_VERSION_REQUIRED', 'Reload the agreement before changing it.')
        if expected != self.state_token(plan):
            raise DomainError('STALE', 'The plan changed. Review your current terms before answering.')

    def state_token(self, plan):
        p = self._plan_row(plan) if isinstance(plan, str) else plan
        ordinary = super().state_token(p['id'])
        parts = {'ordinary': ordinary}
        for table in ('agreement_contests', 'fallback_authorizations'):
            parts[table] = self.db.all(f'SELECT * FROM {table} WHERE plan_id=? ORDER BY id', p['id'])
        # Pending challenges and diagnostic counters never change the business version.
        return digest(parts)
