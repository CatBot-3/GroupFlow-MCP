"""Finite, exact-branch participation permission, issued only by a human."""
from __future__ import annotations

import hmac
import json
from datetime import date, timedelta

from groupflow.core.errors import DomainError
from groupflow.core.util import canonical_json, digest, hash_secret, new_id, new_secret
from groupflow.repair.domain import make_activity
from groupflow.repair.validate import violations
from .reliability import instant
from .agreement import SATISFIED
from .text import money, parse_time_of_day


class Fallbacks:
    def _branch_canonical(self, p, snap, a, mid):
        return {'member': mid, 'plan_id': p['id'], 'start_date': snap.start_date, 'activity_id': a.id, 'title': a.title,
                'status': 'cancelled' if a.cancelled else 'planned', 'start': a.start, 'end': a.end,
                'place_id': a.place_id, 'place_name': self._place_name(p, a), 'cost_share_minor': a.share(mid), 'currency': snap.currency,
                'participants': sorted(a.participants), 'guards': ['arrive_in_time', 'within_my_budget', 'fits_my_conditions'],
                'timezone': snap.timezone, 'start_fold': a.start_fold, 'end_fold': a.end_fold}

    def _fallback_dependencies(self, p, mid):
        members = []
        for m in self._members_of(p):
            role = self._group_role(p['group_id'], m['id'])
            members.append((m['id'], role['membership_version'] if role else None, role['confirmation_mode'] if role else None))
        return {'personal': self.fingerprint(p, mid), 'members': members}

    def human_fallback_challenge(self, member, plan, activity, places, expires_at, max_total_minor=None, latest_end=None):
        def run():
            p = self._personal_plan(member, plan)
            mode = (self._group_role(p['group_id'], member.id) or {}).get('confirmation_mode', 'scoped')
            if mode != 'scoped':
                raise DomainError('INVALID_ARGUMENT', 'Finite fallbacks currently require activity-level confirmation.')
            snap = self._latest_snapshot(p)
            if not snap or not snap.has(activity) or member.id not in snap.activity(activity).participants or snap.activity(activity).cancelled:
                raise DomainError('NOT_FOUND', 'No such participating activity of yours.')
            if not isinstance(places, list) or not 1 <= len(places) <= 3 or any(not isinstance(v, str) for v in places) or len(set(places)) != len(places):
                raise DomainError('INVALID_ARGUMENT', 'Choose one to three different named venues.')
            end = instant(expires_at)
            clock = instant(self.ts())
            if not clock < end <= min(clock + timedelta(days=30), instant(self._expires_at(p))):
                raise DomainError('INVALID_ARGUMENT', 'Choose an expiry in the future, within 30 days and before this plan ends.')
            if max_total_minor is not None and (type(max_total_minor) is not int or not 0 <= max_total_minor <= 10**8):
                raise DomainError('INVALID_ARGUMENT', 'Use a non-negative maximum total in minor currency units.')
            if latest_end is not None and (not isinstance(latest_end, str) or parse_time_of_day(latest_end) is None):
                raise DomainError('INVALID_ARGUMENT', 'Use a latest end time such as 22:00.')
            a = snap.activity(activity)
            problem = self._problem(p, snap)
            branches = []
            for place_id in places:
                place = problem.catalog.places.get(place_id)
                if not place or place.kind != a.kind:
                    raise DomainError('INVALID_ARGUMENT', 'Choose a compatible venue from this plan catalog.')
                branch = snap.with_activity(make_activity(problem, a, (a.start, place_id)))
                bounds = {k: v for k, v in {'max_total_minor': max_total_minor, 'latest_end': latest_end}.items() if v is not None}
                if violations(problem, branch) or not self._condition_holds(bounds, member.id, branch):
                    raise DomainError('INFEASIBLE', 'A chosen branch does not meet the current hard conditions or your bounds.')
                ca = self._branch_canonical(p, branch, branch.activity(activity), member.id)
                day = (date.fromisoformat(snap.start_date) + timedelta(days=a.day)).isoformat()
                names = ', '.join(self.name(mid) for mid in a.participants)
                branches.append({'terms': ca, 'scope_digest': digest(ca), 'text': f"{p['title']}: {a.title} on {day}, {self._when(p, a)} {snap.timezone} at {place.name}; "
                                 f"your share {money(ca['cost_share_minor'], snap.currency)}, your total {money(branch.member_total(member.id), snap.currency)}. With {names}."})
            existing_answers = self.db.all('SELECT r.id,r.status FROM approval_receipts r JOIN approval_scopes s ON s.id=r.scope_id '
                                            "WHERE r.plan_id=? AND r.member_id=? AND (s.activity_id=? OR s.scope_kind='whole_plan')", p['id'], member.id, activity)
            grant = {'activity_id': activity, 'branches': branches, 'dependencies': self._fallback_dependencies(p, member.id),
                     'bounds': bounds, 'expires_at': end.isoformat(), 'receipt_watermark': self.db.scalar('SELECT COALESCE(MAX(rowid),0) FROM approval_receipts'),
                     'receipt_states': {r['id']: r['status'] for r in existing_answers}}
            cid, nonce = new_id('fbch'), new_secret(24)
            self.db.run('INSERT INTO fallback_challenges(id,member_id,plan_id,grant_json,nonce_hash,version_token,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                        cid, member.id, p['id'], canonical_json(grant), hash_secret(nonce), self.state_token(p), self.ts(),
                        min(clock + timedelta(minutes=10), end).isoformat())
            return {'challenge_id': cid, 'nonce': nonce, 'branches': branches, 'expires_at': grant['expires_at'],
                    'challenge_expires_at': min(clock + timedelta(minutes=10), end).isoformat(), 'bounds': bounds,
                    'message': 'Review every named choice. Approval covers only these exact participation terms; bookings and payments are separate.'}
        return self.db.tx(run)

    def human_fallback_decide(self, member, challenge_id, nonce, decision):
        def run():
            if decision not in ('approve', 'decline'):
                raise DomainError('INVALID_ARGUMENT', 'Choose approve or decline.')
            ch = self.db.get('SELECT * FROM fallback_challenges WHERE id=? AND member_id=?', challenge_id, member.id)
            if not ch:
                raise DomainError('FORBIDDEN', 'That fallback request is not yours.')
            if ch['used_at']:
                raise DomainError('CHALLENGE_USED', 'This fallback request has already been answered.')
            if instant(ch['expires_at']) <= instant(self.ts()):
                raise DomainError('CHALLENGE_EXPIRED', 'Review the choices again; this request expired.')
            if not isinstance(nonce, str) or not hmac.compare_digest(hash_secret(nonce), ch['nonce_hash']):
                raise DomainError('FORBIDDEN', 'The fallback request does not match.')
            p = self._personal_plan(member, ch['plan_id'])
            self._expect_coordination(p, ch['version_token'])
            grant = json.loads(ch['grant_json'])
            if grant['dependencies'] != json.loads(canonical_json(self._fallback_dependencies(p, member.id))):
                raise DomainError('STALE', 'Your planning dependencies changed; review again.')
            self.db.run('UPDATE fallback_challenges SET used_at=? WHERE id=?', self.ts(), ch['id'])
            if decision == 'decline':
                return {'outcome': 'declined', 'message': 'No fallback permission was granted.'}
            aid = new_id('fallback')
            self.db.run('INSERT INTO fallback_authorizations(id,member_id,plan_id,activity_id,grant_json,challenge_id,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                        aid, member.id, p['id'], grant['activity_id'], ch['grant_json'], ch['id'], self.ts(), grant['expires_at'])
            self._audit(member.id, 'fallback.approved', 'plan', p['id'], p, 'Approved finite participation choices on their own page.', private_to=member.id)
            current = self._revision(p['candidate_revision_id'])
            if current and self._round_open(current):
                complete = self._confirmations(current).complete
                if complete and not self.gate(p, current):
                    self._bump(p['id'], 'READY_TO_COMMIT', None)
                else:
                    self._bump(p['id'], 'AWAITING_CONFIRMATIONS', None)
                self._record_fallback_uses(current)
            return {'outcome': 'approved', 'authorization_id': aid, 'message': 'Your exact fallback choices were saved. You can withdraw them on this page.'}
        return self.db.tx(run)

    def human_fallbacks(self, member, plan):
        def read():
            p = self._personal_plan(member, plan)
            rows = []
            for r in self.db.all('SELECT * FROM fallback_authorizations WHERE plan_id=? AND member_id=? ORDER BY created_at DESC,rowid DESC', p['id'], member.id):
                g = json.loads(r['grant_json'])
                status = 'revoked' if r['revoked_at'] else 'expired' if instant(r['expires_at']) <= instant(self.ts()) else 'active'
                if status == 'active' and g['dependencies'] != json.loads(canonical_json(self._fallback_dependencies(p, member.id))):
                    status = 'stale'
                rows.append({'id': r['id'], 'authorization_id': r['id'], 'activity_id': r['activity_id'], 'status': status,
                             'branches': g['branches'], 'bounds': g['bounds'], 'expires_at': r['expires_at'],
                             'uses': self.db.all('SELECT revision_id,scope_id,used_at FROM fallback_uses WHERE authorization_id=?', r['id'])})
            return {'plan': p['id'], 'authorizations': rows, 'message': 'These are only your own finite participation permissions.'}
        return self.db.tx(read)

    def human_fallback_revoke(self, member, authorization_id):
        def run():
            r = self.db.get('SELECT * FROM fallback_authorizations WHERE id=? AND member_id=?', authorization_id, member.id)
            if not r:
                raise DomainError('NOT_FOUND', 'No such permission of yours.')
            p = self._personal_plan(member, r['plan_id'])
            if r['revoked_at']:
                return {'outcome': 'already_revoked', 'message': 'This permission was already withdrawn.'}
            revisions = [self._revision(p[key]) for key in ('active_revision_id', 'candidate_revision_id') if p[key]]
            dependent = set()
            for rev in revisions:
                snap = self._snapshot(rev)
                for scope in self.db.all('SELECT * FROM approval_scopes WHERE revision_id=? AND member_id=? AND activity_id=?', rev['id'], member.id, r['activity_id']):
                    if self._scope_status(scope, snap) == ('fallback_approved', 'fallback:' + r['id']):
                        dependent.add(rev['id'])
            self.db.run('UPDATE fallback_authorizations SET revoked_at=? WHERE id=?', self.ts(), r['id'])
            invalid = set()
            for rev in revisions:
                if rev['id'] in dependent:
                    snap = self._snapshot(rev)
                    scopes = self.db.all('SELECT * FROM approval_scopes WHERE revision_id=? AND member_id=? AND activity_id=?', rev['id'], member.id, r['activity_id'])
                    if any(self._scope_status(s, snap)[0] not in SATISFIED for s in scopes):
                        invalid.add(rev['id'])
            status = ('DEGRADED' if p['active_revision_id'] in invalid else 'AWAITING_CONFIRMATIONS' if p['candidate_revision_id'] in invalid else p['status'])
            self._bump(p['id'], status, 'A participant withdrew a fallback permission.')
            self._audit(member.id, 'fallback.revoked', 'plan', p['id'], p, 'Withdrew their finite participation permission.', private_to=member.id)
            return {'outcome': 'revoked', 'message': 'Your fallback permission was withdrawn. Any matching requirement needs a current personal answer.'}
        return self.db.tx(run)

    def _matching_fallback(self, scope_row, snap):
        if scope_row['scope_kind'] != 'activity':
            return None
        grants = self.db.all('SELECT * FROM fallback_authorizations WHERE plan_id=? AND member_id=? AND activity_id=? AND revoked_at IS NULL '
                             'ORDER BY created_at DESC,rowid DESC', snap.plan_id, scope_row['member_id'], scope_row['activity_id'])
        if not grants:
            return None
        p = self._plan_row(snap.plan_id)
        mid = scope_row['member_id']
        if mid not in self.plan_audience(p) or p['status'] == 'CANCELLED' or not self._in_space(mid, p['space_id']):
            return None
        if violations(self._problem(p, snap), snap) or self._stale_facts(p, snap):
            return None
        for r in grants:
            g = json.loads(r['grant_json'])
            if instant(r['expires_at']) <= instant(self.ts()) or g['dependencies'] != json.loads(canonical_json(self._fallback_dependencies(p, mid))):
                continue
            if not self._condition_holds(g['bounds'], mid, snap):
                continue
            # A later refusal/withdrawal takes precedence over an earlier general branch permission.
            later = self.db.all("SELECT r.id,r.rowid AS receipt_order,r.status,r.decision FROM approval_receipts r JOIN approval_scopes s ON s.id=r.scope_id WHERE r.plan_id=? AND r.member_id=? "
                                "AND (s.activity_id=? OR s.scope_kind='whole_plan') AND (r.decision='rejected' OR r.status='revoked')",
                                p['id'], mid, scope_row['activity_id'])
            # Durable sequence/state, never comparisons between two clocks. A
            # withdrawal can update an old receipt after this grant was issued.
            refusal = any(x['receipt_order'] > g.get('receipt_watermark', -1) or
                          (x['status'] == 'revoked' and g.get('receipt_states', {}).get(x['id']) != 'revoked') for x in later)
            if refusal:
                continue
            for branch in g['branches']:
                terms = branch['terms']
                place = self._catalog(p).places.get(terms['place_id'])
                if place and place.price_minor == terms['cost_share_minor'] and digest(terms) == scope_row['scope_digest']:
                    return r
        return None

    def _scope_status(self, scope_row, snap):
        status, via = super()._scope_status(scope_row, snap)
        if status == 'pending':
            grant = self._matching_fallback(scope_row, snap)
            if grant:
                return 'fallback_approved', 'fallback:' + grant['id']
        return status, via

    def _record_fallback_uses(self, rev):
        if not rev:
            return
        snap = self._snapshot(rev)
        for scope in self.db.all('SELECT * FROM approval_scopes WHERE revision_id=?', rev['id']):
            status, via = self._scope_status(scope, snap)
            if status == 'fallback_approved':
                self.db.run('INSERT OR IGNORE INTO fallback_uses(authorization_id,revision_id,scope_id,used_at) VALUES(?,?,?,?)',
                            via.removeprefix('fallback:'), rev['id'], scope['id'], self.ts())

    def request_confirmations(self, *args, **kwargs):
        def run():
            actor = args[0] if args else kwargs['actor_id']
            selector = kwargs.get('plan', args[1] if len(args) > 1 else None)
            target = self.resolve_plan(self.scope_for(actor), selector)
            result = super(Fallbacks, self).request_confirmations(*args, **kwargs)
            p = self._plan_row(target['id'])
            if p['candidate_revision_id']:
                self._record_fallback_uses(self._revision(p['candidate_revision_id']))
            return result
        return self.db.tx(run)

    def commit_plan(self, *args, **kwargs):
        def run():
            actor = args[0] if args else kwargs['actor_id']
            candidate = kwargs.get('candidate')
            rev = self._revision(candidate) if candidate else None
            selector = rev['plan_id'] if rev else kwargs.get('plan', args[1] if len(args) > 1 else None)
            target = self.resolve_plan(self.scope_for(actor), selector) if selector else None
            result = super(Fallbacks, self).commit_plan(*args, **kwargs)
            outcome = result.get('outcome') if isinstance(result, dict) else result.outcome
            if outcome == 'committed' and target:
                p = self._plan_row(target['id'])
                if p['active_revision_id']:
                    self._record_fallback_uses(self._revision(p['active_revision_id']))
            return result
        return self.db.tx(run)
