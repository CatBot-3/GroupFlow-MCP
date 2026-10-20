"""Read-only simulations, recoverable operations and explainable planning aids."""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from groupflow.core.errors import DomainError
from groupflow.repair.domain import changed_activities
from groupflow.repair.solver import repair
from groupflow.repair.validate import violations
from .agreement import snapshot_to_dict, SATISFIED
from .product import fingerprint, named


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise DomainError('INVALID_ARGUMENT', 'Fact times require ISO dates with a timezone.') from None


class Reliability:
    def create_plan(self, actor_id, *args, **kwargs):
        self._rate_limit(actor_id)
        def run():
            result=super(Reliability,self).create_plan(actor_id,*args,**kwargs)
            plan=self._plan_row(result.plan.id)
            snap=self._latest_snapshot(plan)
            if snap:
                from groupflow.repair.local_time import validate_interval
                for a in snap.live:
                    validate_interval(snap.start_date,a.start,a.end,snap.timezone,a.start_fold,a.end_fold)
            return result
        return self.db.tx(run)

    def action_status(self, actor_id: str, request_key: str) -> dict:
        def run():
            if not isinstance(request_key, str) or not 1 <= len(request_key) <= 120:
                raise DomainError('INVALID_ARGUMENT', 'Use the original request key (1 to 120 characters).')
            scope = self.scope_for(actor_id)
            row = self.db.get('SELECT * FROM action_receipts WHERE actor_id=? AND request_key=?', actor_id, request_key)
            if not row:
                return {'outcome': 'unknown', 'request_key': request_key, 'message': 'No saved result is visible. The outcome is unknown; recover using the original key and arguments.'}
            if row['audience'] != fingerprint(sorted(scope.listeners or [])) or any(not scope.sees_plan(p) for p in json.loads(row['plan_ids_json'])):
                raise DomainError('FORBIDDEN', 'The saved result is outside this conversation scope.')
            receipt = self._receipt_view(row)
            result = json.loads(row['result_json'])
            result['action_receipt'] = receipt
            return {'outcome': 'saved', 'request_key': request_key, 'result': result, 'action_receipt': receipt,
                    'message': receipt['message']}
        return self.db.tx(run)

    def correction_guard(self, undo: dict) -> str:
        plan = self._plan_row(undo['plan'])
        table, ident = ('bring_items', undo.get('_record_id') or undo['things'][0]) if undo['tool'] == 'bring_list' else ('expenses', undo['expense'])
        row = self.db.get(f'SELECT * FROM {table} WHERE id=? AND plan_id=?', ident, plan['id'])
        if not row:
            return fingerprint({'missing': True})
        label = row.get('label') or row.get('description') or ''
        state = {'record': row, 'plan_status': plan['status'] == 'CANCELLED'}
        # Durable per-record epochs also detect release/reclaim and short labels.
        state['epoch'] = self.db.scalar('SELECT version FROM record_epochs WHERE record_type=? AND record_id=?',table,ident)
        if table == 'expenses':
            people = set(json.loads(row['shares_json'])) | {row['payer_id']}
            state['settlements'] = [s for s in self.db.all('SELECT * FROM settlements WHERE plan_id=? ORDER BY id', plan['id']) if s['from_id'] in people or s['to_id'] in people]
        related = [i for i in self.db.all("SELECT * FROM items WHERE plan_id=? AND shape='activity'", plan['id']) if named(label, i['label'])]
        state['related_items'] = related
        ids = {i['id'] for i in related}
        if ids:
            from dataclasses import asdict
            state['related_activities']=sorted({json.dumps(asdict(a),sort_keys=True) for ref in ('active_revision_id','candidate_revision_id','draft_revision_id')
                if plan[ref] for a in self._snapshot(self._revision(plan[ref])).activities if a.id in ids})
            linked=set(ids)
            edges=self.db.all('SELECT * FROM links ORDER BY item_id,depends_on_id')
            while True:
                expanded=linked | {e['depends_on_id'] for e in edges if e['item_id'] in linked} | {e['item_id'] for e in edges if e['depends_on_id'] in linked}
                if expanded==linked:
                    break
                linked=expanded
            state['links']=[e for e in edges if e['item_id'] in linked or e['depends_on_id'] in linked]
            state['linked_items']=[self._item(i) for i in sorted(linked-ids)]
            scopes = [s for s in self.db.all('SELECT s.* FROM approval_scopes s JOIN plan_revisions r ON r.id=s.revision_id WHERE r.plan_id=? ORDER BY s.id', plan['id']) if s['activity_id'] in ids or s['scope_kind'] == 'whole_plan']
            state['scopes'] = scopes
            state['approvals'] = [r for r in self.db.all('SELECT * FROM approval_receipts WHERE plan_id=? ORDER BY id', plan['id']) if r['scope_id'] in {s['id'] for s in scopes}]
        return fingerprint(state)

    def _action_plan(self, actor_id, arguments):
        scope = self.scope_for(actor_id)
        if arguments.get('plan'):
            return self.resolve_plan(scope, arguments['plan'])
        if arguments.get('item'):
            return scope.plans[self.resolve_item(scope, arguments['item'])['plan_id']]
        if arguments.get('request'):
            req = self.db.get('SELECT * FROM requests WHERE id=?', arguments['request'])
            if req and scope.sees_plan(req['plan_id']):
                return scope.plans[req['plan_id']]
        raise DomainError('INVALID_ARGUMENT', 'Preview requires an exact visible plan or item/request.')

    def preview_action(self, actor_id: str, tool: str, arguments: dict, expected: str | None = None) -> dict:
        if tool not in {'propose_change','decide_change','link_items','costs','bring_list','rsvp'}:
            raise DomainError('INVALID_ARGUMENT', 'Preview supports item edits, request decisions, dependencies, expenses, bring items and attendance.')
        def run():
            p = self._action_plan(actor_id, arguments)
            token = self.state_token(p['id'])
            if not expected:
                raise DomainError('EXPECTED_VERSION_REQUIRED', 'Read the current version before previewing.')
            if token != expected:
                raise DomainError('STALE', 'The record changed. Read it again before previewing.')
            # A savepoint also rolls back nested calls, generated IDs, counters and clarification rows.
            self.db.run('SAVEPOINT action_preview')
            try:
                before = self._latest_snapshot(p) or self._empty_snapshot(p)
                old_items = {i['id']: i for i in self._visible_items(self.scope_for(actor_id), p['id'])}
                old_records={table:{str(r.get('id') or r.get('member_id')):r for r in self.db.all(f'SELECT * FROM {table} WHERE plan_id=?',p['id'])} for table in ('bring_items','expenses','rsvps')}
                result = self.apply_action(actor_id, tool, arguments, 'preview:' + uuid.uuid4().hex, expected)
                result.pop('action_receipt', None)
                fresh = self._plan_row(p['id'])
                after = self._latest_snapshot(fresh)
                changes, affected = [], set()
                if before and after:
                    old, new = {a.id:a for a in before.activities}, {a.id:a for a in after.activities}
                    for aid in changed_activities(before, after):
                        a, b = old.get(aid), new.get(aid)
                        for act in (a, b):
                            if act:
                                affected.update(act.participants)
                        def public(act):
                            if not act:
                                return None
                            return {'title': act.title, 'start': act.start, 'end': act.end, 'timezone': after.timezone,
                                    'start_fold':act.start_fold, 'end_fold':act.end_fold,
                                    'place':self._place_name(fresh, act), 'participants':[self.name(m) for m in act.participants], 'cancelled':act.cancelled}
                        changes.append({'plan':p['id'], 'item':aid, 'before':public(a), 'after':public(b)})
                new_items = {i['id']:i for i in self._visible_items(self.scope_for(actor_id), p['id'])}
                activity_ids = {c['item'] for c in changes}
                for ident in old_items.keys() | new_items.keys():
                    a,b = old_items.get(ident),new_items.get(ident)
                    if a != b and ident not in activity_ids:
                        def item_view(i):
                            return None if not i else {'label':i['label'],'status':i['status'],'owner':self.name(i['owner_id']), 'value':self._current_value(i) if i['shape']=='detail' else None}
                        changes.append({'plan':p['id'],'item':ident,'before':item_view(a),'after':item_view(b)})
                        affected.update(i['owner_id'] for i in (a,b) if i and i['owner_id'])
                impact = {'carried':[], 'needs_confirmation':[]}
                for table,old_rows in old_records.items():
                    new_rows={str(r.get('id') or r.get('member_id')):r for r in self.db.all(f'SELECT * FROM {table} WHERE plan_id=?',p['id'])}
                    for ident in sorted(old_rows.keys() | new_rows.keys()):
                        a,b=old_rows.get(ident),new_rows.get(ident)
                        if a==b:
                            continue
                        def record_view(row):
                            if not row:
                                return None
                            if table=='bring_items':
                                if row.get('claimed_by'):
                                    affected.add(row['claimed_by'])
                                return {'label':row['label'],'bringing':self.name(row['claimed_by']) if row.get('claimed_by') else None,'status':row['status']}
                            if table=='expenses':
                                affected.update(json.loads(row['shares_json']))
                                affected.add(row['payer_id'])
                                return {'label':row['description'],'amount_minor':row['amount_minor'],'currency':row['currency'],'payer':self.name(row['payer_id']),'status':row['status']}
                            affected.add(row['member_id'])
                            return {'label':self.name(row['member_id']),'answer':row['answer'],'plus_ones':row['plus_ones']}
                        changes.append({'plan':p['id'],'item':ident,'before':record_view(a),'after':record_view(b)})
                if after:
                    rev = self._new_revision(fresh, after, 'Preview', 'candidate', 'edit', actor_id, fresh['active_revision_id'])
                    self._scopes_for(rev)
                    self._carry_forward(fresh, rev)
                    for req,status,_ in self._requirements(rev):
                        dest = 'carried' if status in SATISFIED else 'needs_confirmation'
                        impact[dest].append({'member':self.name(req['member_id']), 'activity':self._activity_title(rev,req['activity_id'])})
                return {'outcome':'preview','expected':token,'tool':tool,'arguments':arguments,'result':result,
                        'changes':changes,'affected_participants':sorted(self.name(m) for m in affected), 'approval_impact':impact,
                        'message':'Preview only. Apply the same arguments and expected version to save; personal confirmations remain separate.'}
            finally:
                self.db.run('ROLLBACK TO action_preview')
                self.db.run('RELEASE action_preview')
        return self.db.tx(run)

    def _next_actions(self, actor_id, plan):
        result = []
        token = self.state_token(plan['id'])
        def recipe(tool, arguments, reason):
            return {'tool':tool,'arguments':arguments,'expected':token,'reason':reason}
        for req in self.db.all("SELECT id FROM requests WHERE plan_id=? AND assignee_id=? AND status='open' ORDER BY id LIMIT 4", plan['id'], actor_id):
            for decision in ('accept','reject'):
                result.append(recipe('decide_change',{'request':req['id'],'decision':decision},'You decide this request. Activity approval remains separate.'))
        for item in self._visible_items(self.scope_for(actor_id), plan['id'], ('task',)):
            if item['owner_id']==actor_id and item['status']!='done' and len(result)<8:
                result.append(recipe('propose_change',{'plan':plan['id'],'item':item['id'],'status':'done'},'Report your assigned work complete.'))
        for question in self._open_questions(self.scope_for(actor_id)):
            if question['plan']==plan['title']:
                result.extend(recipe('answer_followup',{'followup':question['id'],'choice':option},'Choose the stated follow-up action.') for option in question['options'])
        cand = self._revision(plan['candidate_revision_id'])
        if cand and self._round_open(cand):
            for req,status,_ in self._requirements(cand):
                if req['member_id']==actor_id and status not in SATISFIED:
                    result.append({'human_page':'#decision-'+req['id'],'reason':'Only you can answer this confirmation on your own page.'})
        if self.is_organizer(plan,actor_id) and self._latest_snapshot(plan) and self._latest_snapshot(plan).activities:
            if cand and not self.gate(plan,cand):
                result.append(recipe('commit_plan',{'plan':plan['id']},'All current confirmations and feasibility checks are satisfied.'))
            elif not cand or not self._round_open(cand):
                if not self._public_problems(plan,self._latest_snapshot(plan)):
                    result.append(recipe('request_confirmations',{'plan':plan['id']},'Ask each affected person to review this version.'))
        return result[:12]

    def resolve_action(self, actor_id, query, plan=None, shape=None):
        def run():
            data=super(Reliability,self).resolve_action(actor_id,query,plan,shape)
            ids={t['plan'] for t in data['targets']}
            if data.get('plan'):
                ids.add(data['plan'])
            data['next_actions']=[a for pid in sorted(ids) for a in self._next_actions(actor_id,self._plan_row(pid))][:12]
            return data
        return self.db.tx(run)

    def decision_inbox(self, actor_id):
        def run():
            data=super(Reliability,self).decision_inbox(actor_id)
            for card in data['cards']:
                card['next_actions']=self._next_actions(actor_id,self._plan_row(card['plan']))
            return data
        return self.db.tx(run)

    def _observation_freshness(self, row):
        payload=json.loads(row['payload_json'])
        if row['verification']!='verified':
            return 'unverified'
        until=payload.get('valid_until')
        if not until:
            return 'unknown'
        try:
            return 'fresh' if instant(until)>instant(self.ts()) else 'stale'
        except DomainError:
            return 'stale'

    def _fact_rows(self, plan):
        # Group facts are public only when the referenced place belongs to this plan.
        places=set(self._catalog(plan).places)
        return [r for r in self.db.all("SELECT * FROM observations WHERE group_id=? AND kind IN ('price_changed','place_unavailable') ORDER BY rowid",plan['group_id'])
                if (not r['plan_id'] or r['plan_id']==plan['id']) and json.loads(r['payload_json']).get('place_id') in places]

    def _stale_facts(self, plan, snap):
        used={a.place_id for a in snap.live}
        latest={}
        for row in self._fact_rows(plan):
            if row['verification']=='verified':
                latest[(row['kind'],json.loads(row['payload_json'])['place_id'])]=row
        return [r for (_,place),r in latest.items() if place in used and self._observation_freshness(r)=='stale']

    def _input_versions(self, plan):
        data=super()._input_versions(plan)
        rows=[(r['id'],self._observation_freshness(r)) for r in self._fact_rows(plan) if json.loads(r['payload_json']).get('valid_until')]
        if rows:
            data['fact_freshness']=fingerprint(rows)
        return data

    def _public_problems(self, plan, snap):
        problems=super()._public_problems(plan,snap)
        if self._stale_facts(plan,snap):
            problems.append('A verified place fact expired. Recheck its source before asking for confirmation or committing.')
        return problems

    def gate(self, plan, rev):
        result=super().gate(plan,rev)
        if self._stale_facts(plan,self._snapshot(rev)):
            result.append('A verified place fact expired; recheck it before committing.')
        return list(dict.fromkeys(result))

    def human_decide(self, member, challenge_id, nonce, decision, *args, **kwargs):
        def run():
            ch=self.db.get('SELECT * FROM challenges WHERE id=?',challenge_id)
            if ch:
                scope=self.db.get('SELECT * FROM approval_scopes WHERE id=?',ch['scope_id'])
                rev=self._revision(scope['revision_id']) if scope else None
                if scope and rev and scope['member_id']==member.id and not self.scope_for(member.id).sees_plan(rev['plan_id']):
                    raise DomainError('FORBIDDEN','You no longer have access to this plan. Nothing was recorded.')
                if scope and scope['member_id']==member.id and rev and decision in ('approve','approved','conditional'):
                    p=self._plan_row(rev['plan_id'])
                    from groupflow.repair.local_time import validate_interval
                    snap=self._snapshot(rev)
                    for a in snap.live:
                        validate_interval(snap.start_date,a.start,a.end,snap.timezone,a.start_fold,a.end_fold)
                    if self._stale_facts(p,snap):
                        raise DomainError('STALE','A verified fact expired. Ask for a fresh version before confirming.')
            return super(Reliability,self).human_decide(member,challenge_id,nonce,decision,*args,**kwargs)
        return self.db.tx(run)

    def inject_observation(self, group_id, kind, payload, source, source_event_id, verified=True, actor_id=None, valid_until=None):
        body=dict(payload)
        until=valid_until if valid_until is not None else body.get('valid_until')
        if until is not None:
            if instant(until)<=instant(self.ts()):
                raise DomainError('INVALID_ARGUMENT','A new fact expiry must be in the future.')
            body['valid_until']=instant(until).isoformat()
        body['verified_at']=self.ts() if verified else None
        return super().inject_observation(group_id,kind,body,source,source_event_id,verified,actor_id)

    def planning_facts(self, actor_id, plan):
        p=self.resolve_plan(self.scope_for(actor_id),plan)
        rows=[]
        for place in self._catalog(p).places.values():
            rows.append({'id':'catalog:'+place.id,'kind':'catalog','place_id':place.id,'source':'template catalog (static supplied estimate)',
                         'verified_at':None,'valid_until':None,'freshness':'unverified','verification':'unverified',
                         'value':{'price_minor':place.price_minor,'open_min':place.open_min,'close_min':place.close_min,'capacity':place.capacity}})
        for r in self._fact_rows(p):
            payload=json.loads(r['payload_json'])
            rows.append({'id':r['id'],'kind':r['kind'],'place_id':payload['place_id'],'source':r['source'],
                         'verified_at':payload.get('verified_at') or (r['received_at'] if r['verification']=='verified' else None),
                         'valid_until':payload.get('valid_until'),'freshness':self._observation_freshness(r),'verification':r['verification'],'value':payload})
        return {'outcome':'facts','plan':p['id'],'facts':rows,'message':'Static catalog values are estimates. No live provider is connected; stale or unknown freshness needs source rechecking.'}

    def planning_relaxations(self, actor_id, plan):
        p=self.resolve_plan(self.scope_for(actor_id),plan)
        snap=self._latest_snapshot(p)
        if not snap:
            raise DomainError('INVALID_ARGUMENT','Add activities before exploring alternatives.')
        problem=self._problem(p,snap)
        ordinary=repair(problem,time_budget_s=min(self.solver_budget_s,0.5),explain=False)
        if ordinary.candidates:
            return {'outcome':'feasible','suggestions':[],'message':'Feasible choices exist under the current rules; no relaxation is needed.','search_scope':'current finite domain'}
        proposals=[]
        # Each hypothesis changes exactly one PUBLIC domain/catalog input. Personal hard limits are retained.
        expanded=replace(problem,shifts=tuple(dict.fromkeys((*problem.shifts,-60,-30,60,90,120))),max_options=12)
        proposals.append(('Consider a wider start-time range, subject to everyone confirming.',expanded))
        for place in sorted(problem.catalog.places.values(),key=lambda x:x.id)[:12]:
            need=max((len(a.participants) for a in snap.live if a.place_id==place.id),default=0)
            if place.capacity<need:
                places=dict(problem.catalog.places)
                places[place.id]=replace(place,capacity=need)
                proposals.append((f'Ask {place.name} to verify capacity for {need}, or find an equivalent larger venue. This fact must be verified before use.',replace(problem,catalog=replace(problem.catalog,places=places))))
        suggestions=[]
        for text,hypothesis in proposals:
            outcome=repair(hypothesis,time_budget_s=min(self.solver_budget_s,0.4),max_candidates=1,explain=False)
            if outcome.candidates:
                best=outcome.candidates[0]
                if not violations(hypothesis,best.snapshot):
                    suggestions.append({'hypothetical':True,'changes':[text],'snapshot':snapshot_to_dict(best.snapshot),
                                        'proof':{'status':outcome.status,'scope':'one public input within the tested finite domain'},'requires_verification':True})
            if len(suggestions)==3:
                break
        return {'outcome':'hypotheses' if suggestions else 'no_public_relaxation','suggestions':suggestions,
                'search_scope':'At most one public schedule-domain or capacity input; all personal hard conditions remain enforced. Bounded search, no global minimality claim.',
                'message':'Hypothetical only; nothing was changed. Verify facts and ask affected people before adoption.' if suggestions else 'No tested public change sufficed. Invite people to voluntarily review their own conditions; no private owner or threshold is disclosed.'}

    def planning_options(self, actor_id, plan, candidate=None, mode='options'):
        if mode=='facts':
            return self.db.tx(lambda:self.planning_facts(actor_id,plan))
        if mode=='relaxations':
            return self.db.tx(lambda:self.planning_relaxations(actor_id,plan))
        if mode!='options':
            raise DomainError('INVALID_ARGUMENT','mode is options, facts or relaxations.')
        return super().planning_options(actor_id,plan,candidate)
