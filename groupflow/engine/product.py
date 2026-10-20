"""Resolved actions, canonical receipts and a current personal decision inbox.

The guarded action envelope is atomic. Legacy tools also receive receipts; their
existing contracts remain usable. Corrections are deliberately limited to a
person's own bring claims and own recorded expenses, never human consent.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel
from groupflow.core.errors import DomainError
from .core import AUDIT_CAPTURE

CAPTURING = ContextVar('receipt_capture', default=False)
ACTION_TOOLS = {'create_plan', 'propose_change', 'decide_change', 'link_items', 'set_my_conditions', 'report_observation',
    'request_confirmations', 'commit_plan', 'notify_people', 'ask_followup', 'answer_followup', 'set_reminder',
    'poll', 'rsvp', 'bring_list', 'costs'}
RECEIPT_TOOLS = ACTION_TOOLS | {'find_alternatives', 'configure_project', 'upsert_work', 'manage_recurrence',
    'repair_project', 'create_group', 'invite_to_group', 'review_plan', 'planning_options',
    'my_part', 'find_options', 'invite_people'}  # the voice profile's own multi-step operations
WORKFLOW_TOOLS = {'find_alternatives', 'repair_project', 'planning_options', 'find_options'}
# The same member sending the same write with the same arguments within this window is treated as a retry, not a new request:
# hosts resend after timeouts, and money, tasks and reminders must not double. The person can still say "record it again".
REPEAT_WINDOW_S = 120
REPEAT_GUARDED = (ACTION_TOOLS - {'answer_followup', 'ask_followup', 'decide_change', 'commit_plan'}) | {'create_group', 'invite_to_group',
                                                                                                    'my_part', 'invite_people'}


def encoded(value: Any) -> Any:
    return value.model_dump(mode='json') if isinstance(value, BaseModel) else value


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def named(text: str, label: str) -> bool:
    return len(label.strip()) >= 3 and bool(re.search(r'(?<!\w)' + re.escape(label.strip()) + r'(?!\w)', text, re.I))


class Product:
    def state_token(self, plan: str) -> str:
        """Opaque state token includes ledger/attendance edits that do not bump a plan version."""
        tables = ('plans', 'items', 'facts', 'requests', 'conditions', 'polls', 'poll_votes', 'rsvps', 'bring_items', 'expenses', 'settlements',
                  'approval_scopes', 'approval_receipts', 'confirmation_rounds', 'followups', 'reminders', 'observations', 'group_members', 'record_epochs')
        state = {}
        for table in tables:
            if table == 'plans':
                rows = self.db.all('SELECT * FROM plans WHERE id = ?', plan)
            elif table == 'facts':
                rows = self.db.all('SELECT f.* FROM facts f JOIN items i ON i.id=f.item_id WHERE i.plan_id=?', plan)
            elif table == 'poll_votes':
                rows = self.db.all('SELECT v.* FROM poll_votes v JOIN polls p ON p.id=v.poll_id WHERE p.plan_id=?', plan)
            elif table == 'conditions':
                rows = self.db.all('SELECT * FROM conditions WHERE plan_id=? OR (plan_id IS NULL AND group_id=(SELECT group_id FROM plans WHERE id=?))', plan, plan)
            elif table == 'approval_scopes':
                rows = self.db.all('SELECT s.* FROM approval_scopes s JOIN plan_revisions r ON r.id=s.revision_id WHERE r.plan_id=?', plan)
            elif table in {'observations', 'group_members'}:
                rows = self.db.all(f'SELECT * FROM {table} WHERE group_id=(SELECT group_id FROM plans WHERE id=?)', plan)
            else:
                rows = self.db.all(f'SELECT * FROM {table} WHERE plan_id=?', plan)
            state[table] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
        return fingerprint(state)

    def resolve_action(self, actor_id: str, query: str, plan: str | None = None, shape: str | None = None) -> dict:
        def run():
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else None
            items = self._visible_items(scope, p['id'] if p else None)
            if shape and shape not in {'detail', 'task', 'activity'}:
                raise DomainError('INVALID_ARGUMENT', 'shape is detail, task or activity.')
            items = [i for i in items if not shape or i['shape'] == shape]
            scored = []
            for i in items:
                score = 4 if query.casefold().strip() == i['id'].casefold() else 3 if named(query, i['label']) else 0
                if not score and any(named(query, a) for a in json.loads(i['aliases'] or '[]')):
                    score = 2
                if score:
                    scored.append((score, i))
            best = max((score for score, _ in scored), default=0)
            hits = [i for score, i in scored if score == best]
            # Named detail labels outrank a related activity, never a second exact label.
            if best == 3 and any(i['shape'] == 'detail' for i in hits):
                hits = [i for i in hits if i['shape'] == 'detail']
            targets = [{'plan': i['plan_id'], 'plan_title': scope.plans[i['plan_id']]['title'], 'item': i['id'],
                        'label': i['label'], 'shape': i['shape'], 'owner': self.name(i['owner_id']),
                        'version': self.state_token(i['plan_id'])} for i in hits[:12]]
            if not p and not shape:
                named_plans = [row for row in scope.plans.values() if row['id'] == query.strip()]
                named_plans = named_plans or [row for row in scope.plans.values() if row['title'].casefold() == query.strip().casefold()]
                if named_plans:
                    hits = named_plans
                    targets = [{'plan': row['id'], 'plan_title': row['title'], 'item': None, 'label': row['title'],
                                'shape': 'plan', 'owner': self.name(row['organizer_id']), 'version': self.state_token(row['id'])} for row in named_plans[:12]]
            return {'outcome': 'resolved' if len(hits) == 1 else 'ambiguous' if hits else 'unmatched', 'targets': targets,
                    'plan': p['id'] if p else None, 'version': self.state_token(p['id']) if p else None,
                    'message': 'Use the exact target and version.' if len(hits) == 1 else 'Ask which record; nothing changed.' if hits else
                    'No named record matched. Search visible plans or create separate work; do not replace a related task.'}
        return self.db.tx(run)

    def propose_change(self, actor_id: str, *args: Any, **kwargs: Any):
        quote, item = kwargs.get('quote'), kwargs.get('item') or (args[0] if args else None)
        if quote and item:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, kwargs['plan']) if kwargs.get('plan') else None
            target = self.resolve_item(scope, item, p['id'] if p else None, required=False)
            resolved = self.resolve_action(actor_id, quote)
            exact = [t for t in resolved['targets'] if named(quote, t['label'])]
            if target and target['shape'] == 'activity' and exact and not any(t['item'] == target['id'] for t in exact):
                explicit_plan = named(quote, scope.plans[target['plan_id']]['title'])
                if not explicit_plan and not named(quote, target['label']):
                    raise DomainError('TARGET_MISMATCH', 'The words name another record. Resolve it before writing.', {'targets': exact})
        return super().propose_change(actor_id, *args, **kwargs)

    def poll(self, actor_id: str, action: str, *args: Any, **kwargs: Any):
        if action == 'start' and not kwargs.get('item') and (len(kwargs.get('options') or []) == 1 or kwargs.get('fallback')):
            p = self.resolve_plan(self.scope_for(actor_id), kwargs.get('plan'))
            quote = kwargs.get('quote') or ''
            activities = self._visible_items(self.scope_for(actor_id), p['id'], ('activity',))
            if any(named(quote, i['label']) for i in activities):
                raise DomainError('CONSENT_REQUIRED', 'An activity cannot be settled by silence, including a poll without item. Each person confirms on their page.')
        return super().poll(actor_id, action, *args, **kwargs)

    def capture_workflow(self, actor_id: str, operation: str, fn, *, arguments: dict | None = None) -> dict:
        """Observe a workflow across its own short transactions; never lock through a solve.

        These tools already guard snapshot/claim/publication inside the engine.
        Unlike the atomic action envelope, their receipt records the observed
        start/end states and only audit events emitted by this invocation.
        """
        self._rate_limit(actor_id)
        def snapshot():
            scope = self.scope_for(actor_id)
            return {'before': {pid: self.state_token(pid) for pid in scope.plans},
                    'audience': fingerprint(sorted(scope.listeners or [])), 'events': []}
        initial = self.db.tx(snapshot)
        capture = AUDIT_CAPTURE.set(initial['events'])
        try:
            data = encoded(fn())
        finally:
            AUDIT_CAPTURE.reset(capture)
        args = dict(arguments or {})
        def finish():
            scope = self.scope_for(actor_id)
            if fingerprint(sorted(scope.listeners or [])) != initial['audience']:
                raise DomainError('FORBIDDEN', 'The conversation scope changed during this action.')
            # Legacy searches may resolve an omitted plan. Their durable job
            # identifies the same target even when no new audit event occurred.
            if not args.get('plan') and isinstance(data, dict) and data.get('job'):
                job = self.db.get('SELECT plan_id FROM repair_jobs WHERE id=?', data['job'])
                if job:
                    args['plan'] = job['plan_id']
            if args.get('plan'):
                self.resolve_plan(scope, args['plan'])  # Recheck current membership/listeners before exposing a result.
            return self.capture_action(actor_id, operation, lambda: data, arguments=args, _snapshot=initial)
        return self.db.tx(finish)

    def capture_action(self, actor_id: str, operation: str, fn, *, request_key: str | None = None,
                       arguments: dict | None = None, expected: str | None = None, _snapshot: dict | None = None) -> dict:
        if CAPTURING.get():
            return fn()
        if _snapshot is None:
            self._rate_limit(actor_id)
        clarification = {}
        def run():
            scope = self.scope_for(actor_id)
            audience = fingerprint(sorted(scope.listeners or []))
            request_hash = fingerprint({'operation': operation, 'arguments': arguments or {}, 'expected': expected})
            if request_key:
                if not 1 <= len(request_key) <= 120:
                    raise DomainError('INVALID_ARGUMENT', 'Use a request key of 1 to 120 characters.')
                old = self.db.get('SELECT * FROM action_receipts WHERE actor_id=? AND request_key=?', actor_id, request_key)
                if old:
                    if old['audience'] != audience or any(not scope.sees_plan(p) for p in json.loads(old['plan_ids_json'])):
                        raise DomainError('FORBIDDEN', 'The saved result is outside this conversation scope.')
                    legacy_hash = fingerprint({'operation': operation, 'arguments': arguments or {}})
                    if old['request_hash'] not in {request_hash, legacy_hash}:
                        raise DomainError('KEY_REUSED', 'This request key was used for different arguments.')
                    data = json.loads(old['result_json'])
                    data['action_receipt'] = self._receipt_view(old)
                    data['replayed'] = True
                    return data
            if (not request_key and _snapshot is None and arguments and operation in REPEAT_GUARDED
                    and (repeat := self._recent_repeat(actor_id, operation, request_hash, audience, scope, arguments))):
                return repeat
            target_plan = None
            if arguments and arguments.get('plan'):
                target_plan = self.resolve_plan(scope, arguments['plan'])['id']
            elif arguments and arguments.get('item'):
                target_plan = self.resolve_item(scope, arguments['item'])['plan_id']
            elif arguments and arguments.get('request'):
                req = self.db.get('SELECT plan_id FROM requests WHERE id=?', arguments['request'])
                if req and scope.sees_plan(req['plan_id']):
                    target_plan = req['plan_id']
            elif arguments and arguments.get('followup'):
                question = self.db.get('SELECT plan_id FROM followups WHERE id=? AND member_id=?', arguments['followup'], actor_id)
                if question and scope.sees_plan(question['plan_id']):
                    target_plan = question['plan_id']
            if request_key and not target_plan and operation != 'create_plan':
                raise DomainError('INVALID_ARGUMENT', 'Guarded plan actions require an explicit visible plan or target id and its current state token.')
            if request_key and target_plan and not expected:
                raise DomainError('EXPECTED_VERSION_REQUIRED', 'Read the record with resolve_action and supply its state token.')
            if expected and (not target_plan or self.state_token(target_plan) != expected):
                raise DomainError('STALE', 'The record changed. Read it again before applying this action.')
            before = _snapshot['before'] if _snapshot is not None else {pid: self.state_token(pid) for pid in scope.plans}
            bring_before = {r['id']: r for r in self.db.all('SELECT * FROM bring_items WHERE claimed_by=? OR claimed_by IS NULL', actor_id)}
            expenses_before = {r['id'] for r in self.db.all('SELECT id FROM expenses')}
            seq = self.db.scalar('SELECT coalesce(max(seq),0) FROM audit')
            token = CAPTURING.set(True)
            try:
                data = encoded(fn())
            except DomainError as err:
                offer = err.details.get('followup')
                fid = offer.get('id') if isinstance(offer, dict) else None
                if fid:
                    row = self.db.get('SELECT * FROM followups WHERE id=? AND member_id=?', fid, actor_id)
                    if row and (not row['plan_id'] or scope.sees_plan(row['plan_id'])):
                        clarification['row'] = row
                raise
            finally:
                CAPTURING.reset(token)
            if not isinstance(data, dict):
                data = {'result': data}
            after_scope = self.scope_for(actor_id)
            emitted = ([self.db.get('SELECT * FROM audit WHERE seq=?', n) for n in _snapshot['events']]
                       if _snapshot is not None else self.db.all('SELECT * FROM audit WHERE seq>? ORDER BY seq', seq))
            events = [a for a in emitted if a is not None
                      if a['plan_id'] in after_scope.plans and a['private_to'] in (None, actor_id)]
            plans = list(dict.fromkeys(a['plan_id'] for a in events))
            if target_plan and after_scope.sees_plan(target_plan) and target_plan not in plans:
                plans.append(target_plan)  # No-op/read retries still depend on visibility of their target.
            subjects = {actor_id}
            for a in events:
                row = self._item(a['target_id']) if a['target_type'] == 'item' else None
                if row and row['owner_id']:
                    subjects.add(row['owner_id'])
            undo = None
            if operation in {'bring_list', 'human/bring'}:
                changes = [r for r in self.db.all('SELECT * FROM bring_items WHERE claimed_by=?', actor_id)
                           if r['plan_id'] in plans and r != bring_before.get(r['id'])]
                if len(changes) == 1 and (not bring_before.get(changes[0]['id']) or bring_before[changes[0]['id']]['claimed_by'] is None):
                    undo = {'tool': 'bring_list', 'plan': changes[0]['plan_id'], 'things': [changes[0]['id']], 'action': 'unclaim',
                            '_record_id': changes[0]['id']}
            if operation == 'costs':
                new = [r for r in self.db.all('SELECT * FROM expenses WHERE payer_id=? AND created_by=? AND status=\'active\'', actor_id, actor_id)
                       if r['id'] not in expenses_before and r['plan_id'] in plans]
                if len(new) == 1:
                    undo = {'tool': 'costs', 'plan': new[0]['plan_id'], 'expense': new[0]['id'], 'action': 'remove'}
            if undo:
                undo['_guard'] = self.correction_guard(undo)
            receipt = {'id': self.new_id('action'), 'actor': self.name(actor_id), 'subjects': sorted(self.name(m) for m in subjects),
                'operation': operation, 'plans': plans, 'before': {p: before.get(p) for p in plans},
                'after': {p: self.state_token(p) for p in plans}, 'effects': list(dict.fromkeys(a['action'] for a in events)),
                'outcome': data.get('outcome') or data.get('status') or 'completed',
                'message': data.get('message') or 'The server recorded this action.', 'reversible': bool(undo), 'created_at': self.ts()}
            self.db.run('INSERT INTO action_receipts (id,actor_id,operation,request_key,request_hash,audience,plan_ids_json,receipt_json,result_json,undo_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        receipt['id'], actor_id, operation, request_key, request_hash, audience, json.dumps(plans), json.dumps(receipt), json.dumps(data), json.dumps(undo), self.ts())
            data['action_receipt'] = receipt
            if not request_key and _snapshot is None and arguments and operation in REPEAT_GUARDED:
                self.db.run('INSERT INTO recent_writes (actor_id, request_hash, receipt_id, wall_at) VALUES (?,?,?,?) '
                            'ON CONFLICT(actor_id, request_hash) DO UPDATE SET receipt_id=excluded.receipt_id, wall_at=excluded.wall_at',
                            actor_id, request_hash, receipt['id'], time.time())
            return data
        try:
            return self.db.tx(run)
        except DomainError as err:
            # Roll back the failed write completely. Only its authenticated,
            # server-generated clarification is allowed to survive the failure.
            row = clarification.get('row')
            if row and not self.db.get('SELECT id FROM followups WHERE id=?', row['id']):
                def restore():
                    scope = self.scope_for(actor_id)
                    if row['plan_id'] and not scope.sees_plan(row['plan_id']):
                        return
                    plan = scope.plans.get(row['plan_id'])
                    ident = self._store_followup(actor_id, row['kind'], row['question'], json.loads(row['options_json']), plan, row['status'])
                    err.details['followup']['id'] = ident
                self.db.tx(restore)
            raise

    def _recent_repeat(self, actor_id: str, operation: str, request_hash: str, audience: str, scope: Any, arguments: dict) -> dict | None:
        """The saved result of the same write seconds ago, with an offer to record it again, or None."""
        now = time.time()
        self.db.run('DELETE FROM recent_writes WHERE wall_at < ?', now - REPEAT_WINDOW_S)
        recent = self.db.get('SELECT * FROM recent_writes WHERE actor_id=? AND request_hash=? AND wall_at >= ?', actor_id, request_hash, now - REPEAT_WINDOW_S)
        old = self.db.get('SELECT * FROM action_receipts WHERE id=?', recent['receipt_id']) if recent else None
        if not old or old['audience'] != audience or any(not scope.sees_plan(p) for p in json.loads(old['plan_ids_json'])):
            return None
        # Only a true retry: the plans are exactly as that write left them. Anything since (anyone's write, a new outside report)
        # means the same words may now mean something else, so the request runs normally.
        after = json.loads(old['receipt_json']).get('after') or {}
        if any(self.state_token(p) != token for p, token in after.items()):
            return None
        # Facts expire with the server's clock (a verified price, a hold): if that clock moved on, the same words may mean more.
        try:
            moved = (datetime.fromisoformat(self.ts().replace('Z', '+00:00')) - datetime.fromisoformat(old['created_at'].replace('Z', '+00:00'))).total_seconds()
        except ValueError:
            moved = 0
        if abs(moved) > REPEAT_WINDOW_S:
            return None
        data = json.loads(old['result_json'])
        data['action_receipt'] = self._receipt_view(old)
        data['replayed'] = True
        seconds = max(1, int(now - recent['wall_at']))
        data['repeat'] = {'of': old['id'], 'seconds_ago': seconds}
        data['message'] = (f'This exact request was already recorded {seconds} seconds ago, so nothing new was added. '
                           + str(data.get('message') or ''))[:600]
        from .notices import FOLLOWUP_TOOLS
        if operation in FOLLOWUP_TOOLS:
            plans = json.loads(old['plan_ids_json'])
            plan = scope.plans.get(plans[0]) if plans else None
            question = 'That was already recorded a moment ago. Should I record it again as a second one?'
            options = [{'label': 'yes, again', 'action': {'tool': operation, 'arguments': dict(arguments)}}, {'label': 'no', 'action': None}]
            try:
                fid = self._store_followup(actor_id, 'offer', question, options, plan, 'suggested')
                data['suggested_followup'] = {'id': fid, 'question': question, 'options': ['yes, again', 'no']}
            except DomainError:
                pass
        return data

    def _receipt_view(self, row: dict) -> dict:
        receipt = json.loads(row['receipt_json'])
        receipt['reversible'] = bool(receipt['reversible'] and not row['corrected_by'])
        receipt['corrected_by'] = row['corrected_by']
        return receipt

    def action_history(self, actor_id: str) -> dict:
        scope = self.scope_for(actor_id)
        audience = fingerprint(sorted(scope.listeners or []))
        rows = self.db.all('SELECT * FROM action_receipts WHERE actor_id=? ORDER BY rowid DESC LIMIT 50', actor_id)
        return {'receipts': [self._receipt_view(r) for r in rows if r['audience'] == audience and all(scope.sees_plan(p) for p in json.loads(r['plan_ids_json']))]}

    def apply_action(self, actor_id: str, tool: str, arguments: dict, request_key: str, expected: str | None = None) -> dict:
        if tool not in ACTION_TOOLS:
            raise DomainError('INVALID_ARGUMENT', 'This tool is not available through the action envelope.')
        if not isinstance(arguments, dict) or any(k in arguments for k in ('actor_id', 'member_id', 'as', 'self')):
            raise DomainError('INVALID_ARGUMENT', 'Arguments cannot override the authenticated identity.')
        args = dict(arguments)
        def apply():
            try:
                return getattr(self, tool)(actor_id, **args)
            except TypeError:
                raise DomainError('INVALID_ARGUMENT', 'Check the arguments against the named tool schema.')
        return self.capture_action(actor_id, tool, apply,
                                   request_key=request_key, arguments=arguments, expected=expected)

    def correct_action(self, actor_id: str, receipt_id: str) -> dict:
        def run():
            scope = self.scope_for(actor_id)
            row = self.db.get('SELECT * FROM action_receipts WHERE id=? AND actor_id=?', receipt_id, actor_id)
            if not row or row['audience'] != fingerprint(sorted(scope.listeners or [])) or any(not scope.sees_plan(p) for p in json.loads(row['plan_ids_json'])):
                raise DomainError('NOT_FOUND', 'No correctable action is available here.')
            if row['corrected_by']:
                return {'outcome': 'already_corrected', 'receipt': row['corrected_by']}
            undo = json.loads(row['undo_json'] or 'null')
            if not undo:
                raise DomainError('INVALID_ARGUMENT', 'This action has no automatic correction. Consent and external actions cannot be undone here.')
            guard = undo.pop('_guard', None)
            if guard:
                if self.correction_guard(undo) != guard:
                    raise DomainError('STALE', 'The record or dependent work changed. Correct the current record explicitly.')
            else:
                receipt = json.loads(row['receipt_json'])
                if any(self.state_token(p) != v for p, v in receipt['after'].items()):
                    raise DomainError('STALE', 'This older action has no dependency snapshot. Correct the current record explicitly.')
            undo.pop('_record_id', None)
            tool = undo.pop('tool')
            result = self.apply_action(actor_id, tool, undo, 'correct:' + receipt_id, self.state_token(undo['plan']))
            self.db.run('UPDATE action_receipts SET corrected_by=? WHERE id=?', result['action_receipt']['id'], receipt_id)
            return result
        return self.db.tx(run)

    def delivery_preferences(self, actor_id: str, plan: str, *, muted: bool = False, digest: bool = False,
                             quiet_start: int | None = None, quiet_end: int | None = None, timezone: str = 'America/Toronto') -> dict:
        p = self.resolve_plan(self.scope_for(actor_id), plan)
        if (quiet_start is None) != (quiet_end is None) or any(h is not None and (type(h) is not int or not 0 <= h <= 23) for h in (quiet_start, quiet_end)):
            raise DomainError('INVALID_ARGUMENT', 'Give both quiet-hour endpoints, from 0 to 23, or neither.')
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise DomainError('INVALID_ARGUMENT', 'Use a valid time zone.')
        self.db.run('INSERT INTO delivery_preferences VALUES (?,?,?,?,?,?,?) ON CONFLICT(member_id,plan_id) DO UPDATE SET muted=excluded.muted,digest=excluded.digest,quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end,timezone=excluded.timezone',
                    actor_id, p['id'], muted, digest, quiet_start, quiet_end, timezone)
        return {'outcome': 'saved', 'message': 'Notification preferences saved. Pending decisions remain visible.'}

    def notification_available(self, actor_id: str, plan_id: str | None, kind: str | None) -> bool:
        if kind not in {'nudge', 'reminder', 'notice'} or not plan_id:
            return True
        pref = self.db.get('SELECT * FROM delivery_preferences WHERE member_id=? AND plan_id=?', actor_id, plan_id)
        if not pref:
            return True
        if pref['muted']:
            return False
        start, end = pref['quiet_start'], pref['quiet_end']
        hour = datetime.fromisoformat(self.ts().replace('Z', '+00:00')).astimezone(ZoneInfo(pref['timezone'])).hour
        return start is None or start == end or not (start <= hour < end if start < end else hour >= start or hour < end)

    def decision_inbox(self, actor_id: str) -> dict:
        def run():
            scope = self.scope_for(actor_id)
            cards = []
            for p in scope.plans.values():
                actions = []
                cand = self._revision(p['candidate_revision_id'])
                if cand and self._round_open(cand):
                    for req, status, _ in self._requirements(cand):
                        if req['member_id'] == actor_id and status == 'pending':
                            actions.append({'kind': 'confirmation', 'id': req['id'], 'text': self._scope_text(req),
                                'why': 'Your own answer is required for this version.', 'effect': 'Confirm or decline on your page; nobody else can answer for you.'})
                for req in self.db.all("SELECT * FROM requests WHERE plan_id=? AND assignee_id=? AND status='open'", p['id'], actor_id):
                    actions.append({'kind': 'request', 'id': req['id'], 'text': req['summary'], 'why': 'You decide this item.', 'effect': 'Accepting adopts the request; activity consent remains separate.'})
                for task in self._visible_items(scope, p['id'], ('task',)):
                    if task['owner_id'] == actor_id and task['status'] != 'done':
                        actions.append({'kind': 'task', 'id': task['id'], 'text': task['label'], 'why': 'This work is assigned to you.', 'effect': 'Report progress or completion.'})
                questions = [q for q in self._open_questions(scope) if q['plan'] == p['title']]
                actions += [{'kind': 'question', 'id': q['id'], 'text': q['question'], 'options': q['options'], 'why': 'Your clarification is needed.', 'effect': 'Your chosen option runs its stated action.'} for q in questions]
                notes = self.db.all("SELECT * FROM notifications WHERE plan_id=? AND member_id=? AND read_at IS NULL ORDER BY created_at DESC,rowid DESC", p['id'], actor_id)
                notes = [n for n in notes if (n['space_id'] is None or n['space_id'] in scope.spaces) and self.notification_available(actor_id, p['id'], n['kind'])]
                unique = list(dict.fromkeys(n['text'] for n in notes))
                pref = self.db.get('SELECT * FROM delivery_preferences WHERE member_id=? AND plan_id=?', actor_id, p['id']) or {}
                if actions or notes:
                    cards.append({'plan': p['id'], 'title': p['title'], 'version': p['aggregate_version'], 'version_token': self.state_token(p['id']), 'actions': actions,
                        'updates': unique[:1] if pref.get('digest') else unique[:4], 'update_count': len(unique),
                        'changed_since': notes[0]['created_at'] if notes else None, 'preferences': {k: pref.get(k) for k in ('muted','digest','quiet_start','quiet_end','timezone')}})
            return {'cards': cards, 'unresolved': sum(len(c['actions']) for c in cards), 'message': 'One current card per plan; confirmations are never inferred from a reminder.'}
        return self.db.tx(run)

    def human_inbox(self, member: Any) -> dict:
        data = super().human_inbox(member)
        data['decision_inbox'] = self.decision_inbox(member.id)
        data['action_receipts'] = self.action_history(member.id)['receipts']
        data['calendar_links'] = self.db.all('SELECT id,created_at FROM calendar_links WHERE member_id=? AND revoked_at IS NULL', member.id)
        data['groups'] = self.db.all("SELECT g.id,g.name FROM groups g JOIN group_members m ON m.group_id=g.id WHERE m.member_id=? AND m.status='active'", member.id)
        for p in data['plans']:
            row = self._plan_row(p['id'])
            p['group_id'] = row['group_id']
            p['planning'] = self._has_activities(row)
            p['you_organize'] = self.is_organizer(row, member.id)
        data['notifications'] = [n for n in data['notifications'] if self.notification_available(member.id, n['plan_id'], n.get('kind'))]
        return data

    def pilot_metrics(self, actor_id: str) -> dict:
        scope = self.scope_for(actor_id)
        receipts = [r for r in self.db.all('SELECT plan_ids_json,corrected_by FROM action_receipts WHERE actor_id=?', actor_id)
                    if all(scope.sees_plan(p) for p in json.loads(r['plan_ids_json']))]
        cards = self.decision_inbox(actor_id)
        notes = [n for n in self.db.all('SELECT plan_id,text FROM notifications WHERE member_id=?', actor_id) if n['plan_id'] in scope.plans]
        return {'visible_plans': len(scope.plans), 'committed_plans': sum(p['status']=='COMMITTED' for p in scope.plans.values()),
                'your_open_actions': cards['unresolved'], 'your_recorded_actions': len(receipts),
                'your_corrections': sum(bool(r['corrected_by']) for r in receipts), 'your_notifications': len(notes),
                'your_unique_notifications': len({(n['plan_id'], n['text']) for n in notes}),
                'scope': 'Local counters only; no independent users or time savings are established.'}

    def planning_options(self, actor_id: str, plan: str, candidate: str | None = None) -> dict:
        if candidate is None:
            p = self.resolve_plan(self.scope_for(actor_id), plan)
            result = self.find_alternatives(actor_id, plan=plan, include_current=not bool(p['active_revision_id']))
            data = encoded(result)
            data['next_step'] = ('Discuss or poll on the candidate labels; the organizer then selects an exact candidate. No consent is inferred.'
                                 if data.get('alternatives') else 'No new selectable option was produced. Read the search outcome; no consent is inferred.')
            return data
        def run():
            p = self.resolve_plan(self.scope_for(actor_id), plan)
            self._require_organizer(p, actor_id, 'select a candidate')
            rev = self._revision(candidate)
            job = self.db.get('SELECT * FROM repair_jobs WHERE id=?', rev['repair_job_id']) if rev and rev.get('repair_job_id') else None
            if not rev or rev['plan_id'] != p['id'] or rev['kind'] != 'candidate' or not job or job['status'] != 'done':
                raise DomainError('NOT_FOUND', 'No current selectable candidate.')
            current = self._revision(p['candidate_revision_id'])
            if not current or current.get('repair_job_id') != job['id']:
                raise DomainError('STALE', 'The plan changed after these options were produced; search again.')
            inputs = json.loads(job['input_json'])
            if inputs['versions'] != self._input_versions(p):
                raise DomainError('STALE', 'The planning inputs changed; search again.')
            problems = self._public_problems(p, self._snapshot(rev))
            if problems:
                raise DomainError('INFEASIBLE', 'This choice no longer meets the hard constraints.', {'problems': problems})
            if current['id'] != candidate:
                self.db.run("UPDATE confirmation_rounds SET status='superseded',closed_at=? WHERE plan_id=? AND status='open'", self.ts(), p['id'])
                self.db.run('UPDATE plans SET candidate_revision_id=? WHERE id=?', candidate, p['id'])
                fresh = self._bump(p['id'], 'PROPOSED', 'organizer selected a feasible option')
                self._audit(actor_id, 'candidate.selected', 'plan', p['id'], fresh, f'{self.name(actor_id)} selected {rev["label"]}; personal confirmations are still required.')
            return {'outcome': 'selected', 'plan': p['id'], 'candidate': candidate, 'label': rev['label'], 'message': 'Candidate selected. Describe it, then ask whether to send confirmation requests. No approvals were created.'}
        return self.db.tx(run)
