"""Readable personal comparisons and scoped, private objections."""
from __future__ import annotations

import hmac
import json
from datetime import date, timedelta
from groupflow.core.errors import DomainError
from groupflow.core.util import hash_secret, new_id, new_secret
from .reliability import instant
from .agreement import SATISFIED


class AgreementReview:
    def _active_review_problems(self, plan, snap):
        priced = self._with_current_prices(plan, snap)
        problems = self._public_problems(plan, priced)
        if priced != snap:
            problems.append('Verified venue prices changed. The organizer must prepare current terms before personal reaffirmation.')
        return problems

    def _active_personal_review(self, member_id, plan, scope):
        if not scope or scope['member_id'] != member_id or plan['candidate_revision_id'] or plan['active_revision_id'] != scope['revision_id']:
            return False
        return self._scope_status(scope, self._snapshot(self._revision(scope['revision_id'])))[0] not in SATISFIED

    def _agreement_terms(self, plan, snap, activity, member_id):
        if activity is None:
            return None
        day = (date.fromisoformat(snap.start_date) + timedelta(days=activity.day)).isoformat()
        return {'title': activity.title, 'date': day, 'time': self._when(plan, activity),
                'start': activity.start, 'end': activity.end, 'timezone': snap.timezone,
                'place_id': activity.place_id, 'place': self._place_name(plan, activity),
                'cost_share_minor': activity.share(member_id), 'currency': snap.currency,
                'participants': list(activity.participants), 'cancelled': activity.cancelled}

    def human_agreements(self, member, plan):
        def read():
            p = self._personal_plan(member, plan)
            rev = self._working_rev(p) or self._revision(p['active_revision_id'])
            if not rev:
                return {'plan': p['id'], 'title': p['title'], 'version_token': self.state_token(p), 'revision': None, 'items': [],
                        'message': 'Add activities before reviewing commitments.'}
            snap = self._snapshot(rev)
            old_rev = self._revision(p['active_revision_id'])
            if old_rev and old_rev['id'] == rev['id']:
                old_rev = self._revision(rev['parent_id']) if rev.get('parent_id') else None
            old = self._snapshot(old_rev) if old_rev else None
            scopes = self.db.all('SELECT * FROM approval_scopes WHERE revision_id=? AND member_id=?', rev['id'], member.id)
            out = []
            acts = {a.id: a for a in (old.activities if old else ())}
            acts.update({a.id: a for a in snap.activities})
            for aid, a in acts.items():
                before = old.activity(aid) if old and old.has(aid) else None
                after = snap.activity(aid) if snap.has(aid) else None
                if not any(member.id in x.participants for x in (before, after) if x):
                    continue
                sr = next((s for s in scopes if s['activity_id'] == aid or s['scope_kind'] == 'whole_plan'), None)
                status, via = self._scope_status(sr, snap) if sr else ('pending', None)
                why = {'carried_forward': 'Your earlier answer still counts: your terms match, your conditions and membership remain valid, and the answer has not expired.',
                       'fallback_approved': 'These exact terms are one of the finite fallback choices you approved on your own page.',
                       'approved': 'You approved these terms on your own page.',
                       'conditional_met': 'Your own stated condition is met by this version.',
                       'rejected': 'You declined this requirement.', 'revoked': 'You withdrew this commitment; a new personal answer is required.',
                       'expired': 'Your earlier answer expired; review these terms again.'}.get(status, 'Review these terms and answer on your own page.')
                contest = self.db.get('SELECT reason,created_at FROM agreement_contests WHERE plan_id=? AND member_id=? AND activity_id IN (?,?) '
                                      'AND resolved_at IS NULL ORDER BY created_at DESC,rowid DESC LIMIT 1', p['id'], member.id, aid, '*')
                out.append({'activity_id': aid, 'title': a.title, 'before': self._agreement_terms(p, old, before, member.id),
                            'after': self._agreement_terms(p, snap, after, member.id), 'status': status, 'via': via, 'why': why,
                            'scope_id': sr['id'] if sr else None, 'can_contest': bool(sr), 'contest': contest,
                            'can_answer': bool(sr and ((p['candidate_revision_id'] == rev['id'] and self._round_open(rev)) or
                                                       (self._active_personal_review(member.id, p, sr) and not self._active_review_problems(p, snap)))),
                            'confirmation_text': self._scope_text(sr) if sr else None,
                            'confirmation_mode': sr['scope_kind'] if sr else 'activity'})
            return {'plan': p['id'], 'title': p['title'], 'version_token': self.state_token(p), 'revision': rev['id'], 'items': out,
                    'group_id': p['group_id'], 'can_manage': self.is_organizer(p, member.id), 'start_date': snap.start_date, 'currency': snap.currency,
                    'activities': [{'id': a.id, 'title': a.title, 'kind': a.kind, 'place_id': a.place_id, 'participants': list(a.participants),
                                    'venues': [{'id': v.id, 'name': v.name, 'price_minor': v.price_minor} for v in self._catalog(p).places.values() if v.kind == a.kind]}
                                   for a in snap.live],
                    'people': [{'id': mid, 'name': self.name(mid)} for mid in snap.members],
                    'message': 'Only your commitments are shown. A private objection withdraws your matching commitment.'}
        return self.db.tx(read)

    def human_contest(self, member, plan, activity, reason, expected):
        def run():
            p = self._personal_plan(member, plan)
            self._expect_coordination(p, expected)
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
                raise DomainError('INVALID_ARGUMENT', 'Add a private reason of up to 1,000 characters.')
            rev = self._working_rev(p) or self._revision(p['active_revision_id'])
            if not rev:
                raise DomainError('NOT_FOUND', 'No agreement is available.')
            scopes = self.db.all('SELECT * FROM approval_scopes WHERE revision_id=? AND member_id=? AND (activity_id=? OR scope_kind=?)',
                                 rev['id'], member.id, activity, 'whole_plan')
            snap = self._snapshot(rev)
            if not scopes or not snap.has(activity) or member.id not in snap.activity(activity).participants:
                raise DomainError('NOT_FOUND', 'No such commitment of yours.')
            whole = any(s['scope_kind'] == 'whole_plan' for s in scopes)
            target = '*' if whole else activity
            # Revoke approving source receipts as well as the currently displayed scope.
            # This prevents an objection from being silently undone by a later carry.
            receipts = self.db.all('SELECT r.id FROM approval_receipts r JOIN approval_scopes s ON s.id=r.scope_id '
                                   "WHERE r.plan_id=? AND r.member_id=? AND r.status='valid' AND (s.activity_id=? OR ?=1 OR s.scope_kind='whole_plan')",
                                   p['id'], member.id, activity, whole)
            for r in receipts:
                self.db.run("UPDATE approval_receipts SET status='revoked',revoked_at=? WHERE id=?", self.ts(), r['id'])
            self.db.run('INSERT INTO agreement_contests(id,plan_id,member_id,activity_id,revision_id,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                        new_id('contest'), p['id'], member.id, target, rev['id'], reason.strip(), self.ts())
            status = 'DEGRADED' if p['active_revision_id'] and not p['candidate_revision_id'] else 'AWAITING_CONFIRMATIONS'
            fresh = self._bump(p['id'], status, 'A participant requested a new personal agreement review.')
            self._audit(member.id, 'agreement.contested', 'plan', p['id'], fresh, 'A participant withdrew their matching commitment.', private_to=member.id)
            return {'outcome': 'contested', 'plan': p['id'], 'activity_id': target, 'version_token': self.state_token(fresh),
                    'message': 'Your matching commitment was withdrawn. Your reason stays private. Review and answer again when ready.'}
        return self.db.tx(run)

    def _scope_status(self, scope_row, snap):
        if scope_row:
            pid = snap.plan_id
            if self.db.get('SELECT 1 AS x FROM agreement_contests WHERE plan_id=? AND member_id=? AND resolved_at IS NULL '
                           'AND (activity_id=? OR activity_id=? OR ?=?) LIMIT 1', pid, scope_row['member_id'], scope_row['activity_id'], '*',
                           scope_row['scope_kind'], 'whole_plan'):
                return 'revoked', 'personal_review'
        return super()._scope_status(scope_row, snap)

    def human_challenge(self, member, scope_id):
        def run():
            scope = self.db.get('SELECT s.*,r.plan_id FROM approval_scopes s JOIN plan_revisions r ON r.id=s.revision_id WHERE s.id=? AND s.member_id=?', scope_id, member.id)
            p = self._personal_plan(member, scope['plan_id']) if scope else None
            if not p or not self._active_personal_review(member.id, p, scope):
                return super(AgreementReview, self).human_challenge(member, scope_id)
            rev = self._revision(scope['revision_id'])
            if self._active_review_problems(p, self._snapshot(rev)):
                raise DomainError('INFEASIBLE', 'The agreed terms need a repair or source recheck before you can reaffirm them.')
            cid, nonce = new_id('ch'), new_secret(24)
            expires = (instant(self.ts()) + timedelta(minutes=10)).isoformat()
            self.db.run('INSERT INTO challenges(id,member_id,revision_id,scope_id,scope_digest,candidate_digest,nonce_hash,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        cid, member.id, rev['id'], scope_id, scope['scope_digest'], rev['content_digest'], hash_secret(nonce), self.ts(), expires)
            return {'challenge_id': cid, 'nonce': nonce, 'scope_digest': scope['scope_digest'], 'candidate_digest': rev['content_digest'],
                    'text': self._scope_text(scope), 'expires_at': expires}
        return self.db.tx(run)

    def _decide_active_review(self, member, scope, challenge_id, nonce, decision, condition=None, reason=None, new_limit=None):
        if decision not in ('approved', 'rejected', 'conditional') or (decision == 'conditional' and not condition):
            raise DomainError('INVALID_ARGUMENT', 'Choose approved, rejected, or a conditional answer with its condition.')
        if new_limit:
            raise DomainError('INVALID_ARGUMENT', 'Save a changed personal limit first, then review the current terms again.')
        ch = self.db.get('SELECT * FROM challenges WHERE id=? AND member_id=?', challenge_id, member.id)
        if not ch or not isinstance(nonce, str) or not hmac.compare_digest(hash_secret(nonce), ch['nonce_hash']):
            raise DomainError('FORBIDDEN', 'The personal review request does not match.')
        if ch['used_at']:
            raise DomainError('CHALLENGE_USED', 'This personal review request has already been answered.')
        if instant(ch['expires_at']) <= instant(self.ts()):
            raise DomainError('CHALLENGE_EXPIRED', 'This personal review request expired. Review your terms again.')
        p = self._personal_plan(member, scope['plan_id'])
        rev = self._revision(scope['revision_id'])
        snap = self._snapshot(rev)
        if not self._active_personal_review(member.id, p, scope) or ch['scope_digest'] != scope['scope_digest'] or ch['candidate_digest'] != rev['content_digest']:
            raise DomainError('STALE_CANDIDATE', 'The agreement changed; nothing was recorded.')
        if self._active_review_problems(p, snap):
            raise DomainError('INFEASIBLE', 'These terms need repair or source rechecking before you answer again.')
        rid = new_id('rcpt')
        self.db.run('UPDATE challenges SET used_at=? WHERE id=?', self.ts(), ch['id'])
        self.db.run("INSERT INTO approval_receipts(id,plan_id,revision_id,scope_id,member_id,decision,condition_json,reason,scope_digest,candidate_digest,challenge_id,fingerprint,status,created_at,expires_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'valid',?,?)", rid, p['id'], rev['id'], scope['id'], member.id, decision,
                    json.dumps(condition) if condition else None, reason, scope['scope_digest'], rev['content_digest'], ch['id'], self.fingerprint(p, member.id), self.ts(), self._expires_at(p))
        self._resolve_personal_contest(member, scope)
        complete = self._confirmations(rev).complete
        fresh = self._bump(p['id'], 'COMMITTED' if complete else 'DEGRADED', None if complete else 'A personal commitment still needs review.')
        self._audit(member.id, 'agreement.reanswered', 'plan', p['id'], fresh, 'Answered again for the exact existing participation terms.', private_to=member.id)
        return {'outcome': decision, 'receipt_id': rid, 'plan_status': fresh['status'], 'message': 'Your new answer was saved for the same agreement. No booking or other person’s answer changed.'}

    def _resolve_personal_contest(self, member, scope):
        if scope['scope_kind'] == 'whole_plan':
            self.db.run('UPDATE agreement_contests SET resolved_at=? WHERE plan_id=? AND member_id=? AND resolved_at IS NULL', self.ts(), scope['plan_id'], member.id)
        else:
            self.db.run('UPDATE agreement_contests SET resolved_at=? WHERE plan_id=? AND member_id=? AND activity_id=? AND resolved_at IS NULL', self.ts(), scope['plan_id'], member.id, scope['activity_id'])

    def human_decide(self, member, challenge_id, nonce, decision, *args, **kwargs):
        def run():
            # A new explicit human answer resolves only the corresponding objection.
            scope = self.db.get('SELECT s.*,r.plan_id FROM challenges c JOIN approval_scopes s ON s.id=c.scope_id '
                                'JOIN plan_revisions r ON r.id=s.revision_id WHERE c.id=? AND c.member_id=?', challenge_id, member.id)
            if scope:
                p = self._plan_row(scope['plan_id'])
                if self._active_personal_review(member.id, p, scope):
                    return self._decide_active_review(member, scope, challenge_id, nonce, decision, *args, **kwargs)
                self._resolve_personal_contest(member, scope)
            return super(AgreementReview, self).human_decide(member, challenge_id, nonce, decision, *args, **kwargs)
        return self.db.tx(run)
