"""Bounded counterfactual diagnostics, with audience checks before private analysis."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from groupflow.core.errors import DomainError
from groupflow.repair.domain import changed_activities, default_reconfirm, assemble, option_of
from groupflow.repair.model import DAY, Constraint, Unavailability
from groupflow.repair.solver import repair
from groupflow.repair.validate import violations
from .agreement import snapshot_to_dict
from .reliability import instant

DIAGNOSTIC_LIMIT = 5
DIAGNOSTIC_WINDOW_SECONDS = 600


class PlanningAnalysis:
    def _diagnostic_admission(self, actor_id, plan):
        p = self.resolve_plan(self.scope_for(actor_id), plan)
        clock = instant(self.ts())
        since = (clock - timedelta(seconds=DIAGNOSTIC_WINDOW_SECONDS)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        used = self.db.scalar('SELECT COUNT(*) FROM diagnostic_queries WHERE actor_id=? AND plan_id=? AND at>?', actor_id, p['id'], since)
        if used >= DIAGNOSTIC_LIMIT:
            raise DomainError('RATE_LIMITED', 'This private-planning diagnostic budget is used up. Try again after ten minutes.')
        self.db.run('INSERT INTO diagnostic_queries(actor_id,plan_id,at) VALUES(?,?,?)', actor_id, p['id'], clock.isoformat(timespec='milliseconds').replace('+00:00', 'Z'))
        # No argument, room, key, version or session changes can reset this budget.
        private = any(r['owner_id'] != actor_id and r['visibility'] == 'private' and r['strength'] == 'hard' for r in self._condition_rows(p))
        room = bool(self._listeners(actor_id))
        return p, bool(private and room), DIAGNOSTIC_LIMIT - used - 1

    def human_private_review(self, member, plan):
        def read():
            p = self._personal_plan(member, plan)
            return {'plan': p['id'], 'conditions': [dict(self._condition_view(c).model_dump(), value=__import__('json').loads(c['value_json']))
                                                  for c in self._condition_rows(p, member.id, ('active', 'unverified'))],
                    'message': 'Review only your own limits here. You may keep them, edit them, or discuss an alternative privately. No one is asked to weaken a hard limit.'}
        return self.db.tx(read)

    def _scenario_inputs(self, problem, scenarios):
        if scenarios is None:
            place = next((a.place_id for a in problem.base.live if a.place_id), None)
            scenarios = [{'kind': 'place_unavailable', 'place_id': place}] if place else []
        if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 6:
            raise DomainError('INVALID_ARGUMENT', 'Declare one to six future scenarios.')
        checked = []
        for raw in scenarios:
            if not isinstance(raw, dict):
                raise DomainError('INVALID_ARGUMENT', 'Each scenario needs a supported kind and exact public input.')
            kind = raw.get('kind')
            allowed = {'kind', 'place_id', 'price_minor'} if kind == 'price_changed' else {'kind', 'place_id', 'start', 'end'} if kind == 'place_unavailable' else {'kind', 'member_id', 'start', 'end'}
            if kind not in ('place_unavailable', 'price_changed', 'participant_unavailable') or set(raw) - allowed:
                raise DomainError('INVALID_ARGUMENT', 'Use place_unavailable, price_changed, or participant_unavailable with the documented fields.')
            if kind in ('place_unavailable', 'price_changed') and (not isinstance(raw.get('place_id'), str) or raw['place_id'] not in problem.catalog.places):
                raise DomainError('NOT_FOUND', 'No such venue in this plan catalog.')
            if kind == 'participant_unavailable' and (not isinstance(raw.get('member_id'), str) or raw['member_id'] not in problem.base.members):
                raise DomainError('NOT_FOUND', 'No such participant in this plan.')
            item = dict(raw)
            if kind == 'price_changed':
                value = raw.get('price_minor')
                if type(value) is not int or not 0 <= value <= 10**8:
                    raise DomainError('INVALID_ARGUMENT', 'Use a bounded non-negative integer price in minor currency units.')
            else:
                lo, hi = raw.get('start', 0), raw.get('end', problem.base.days * DAY)
                if type(lo) is not int or type(hi) is not int or not 0 <= lo < hi <= problem.base.days * DAY:
                    raise DomainError('INVALID_ARGUMENT', 'Use a non-empty minute window within the plan horizon.')
                item.update(start=lo, end=hi)
            checked.append(item)
        return checked

    def _hypothesis_problem(self, problem, base, scenario):
        result = replace(problem, base=base)
        if scenario['kind'] == 'price_changed':
            places = dict(problem.catalog.places)
            places[scenario['place_id']] = replace(places[scenario['place_id']], price_minor=scenario['price_minor'])
            result = replace(result, catalog=replace(problem.catalog, places=places))
        # Immutable committed snapshots can predate a verified price update.
        # Every hypothesis starts from current catalog shares, including outages
        # and busy windows that do not themselves change a price.
        result = replace(result, base=assemble(result, {a.id: option_of(a) for a in base.activities}))
        if scenario['kind'] == 'place_unavailable':
            return replace(result, unavailable=problem.unavailable + (Unavailability(scenario['place_id'], scenario['start'], scenario['end'], 'hypothetical'),))
        if scenario['kind'] == 'participant_unavailable':
            busy = Constraint.make('hypothetical-busy', scenario['member_id'], 'unavailable_window', {'windows': [(scenario['start'], scenario['end'])]}, visibility='group')
            return replace(result, constraints=problem.constraints + (busy,))
        return result

    def _resilience_analysis(self, p, problem, bases, scenarios):
        results = []
        for index, scenario in enumerate(scenarios):
            comparisons = []
            for rid, label, base in bases:
                hypothesis = self._hypothesis_problem(problem, base, scenario)
                outcome = repair(hypothesis, method=self.method, time_budget_s=min(self.solver_budget_s, 0.3), max_candidates=2, explain=False)
                repairs = []
                for scored in outcome.candidates:
                    if violations(hypothesis, scored.snapshot):
                        continue
                    repairs.append({'snapshot': snapshot_to_dict(scored.snapshot), 'changed_activities': list(changed_activities(base, scored.snapshot)),
                                    'changes': self._diff(p, base, scored.snapshot),
                                    'reconfirmation_members': sorted(default_reconfirm(hypothesis, base, scored.snapshot)),
                                    'reconfirmation_count': len(default_reconfirm(hypothesis, base, scored.snapshot)),
                                    'hard_constraints_checked': True})
                comparisons.append({'candidate': rid, 'label': label, 'status': outcome.status, 'currently_feasible_in_scenario': not violations(hypothesis, hypothesis.base),
                                    'repairs': repairs, 'search_scope': 'bounded finite domain; every returned repair independently validated'})
            first = comparisons[0]
            results.append({'id': index + 1, 'input': scenario, 'status': first['status'], 'repairs': first['repairs'], 'comparisons': comparisons})
        return {'outcome': 'resilience', 'plan': p['id'], 'hypothetical': True, 'read_only': True, 'scenarios': results,
                'message': 'Only the declared future scenarios were tested. Nothing was changed or approved; no failure probabilities are assumed.'}

    def _public_relaxation_analysis(self, problem):
        ordinary = repair(problem, method=self.method, time_budget_s=min(self.solver_budget_s, 0.3), explain=False)
        if ordinary.candidates:
            return {'outcome': 'feasible', 'suggestions': [], 'read_only': True, 'message': 'Feasible choices exist under the current rules; no relaxation is needed.'}
        proposals = [('Consider a wider start-time range; affected people must still confirm.', replace(problem, shifts=tuple(dict.fromkeys((*problem.shifts, -60, -30, 60, 90, 120))), max_options=12))]
        for place in sorted(problem.catalog.places.values(), key=lambda x: x.id)[:12]:
            need = max((len(a.participants) for a in problem.base.live if a.place_id == place.id), default=0)
            if place.capacity < need:
                places = dict(problem.catalog.places)
                places[place.id] = replace(place, capacity=need)
                proposals.append((f'Ask {place.name} to verify capacity for {need}; this is a hypothetical public input.', replace(problem, catalog=replace(problem.catalog, places=places))))
        suggestions = []
        for text, hypothesis in proposals:
            result = repair(hypothesis, method=self.method, time_budget_s=min(self.solver_budget_s, 0.2), max_candidates=1, explain=False)
            if result.candidates and not violations(hypothesis, result.candidates[0].snapshot):
                suggestions.append({'hypothetical': True, 'changes': [text], 'snapshot': snapshot_to_dict(result.candidates[0].snapshot),
                                    'proof': {'status': result.status, 'scope': 'one public input within the tested finite domain'}, 'requires_verification': True})
            if len(suggestions) == 3:
                break
        return {'outcome': 'hypotheses' if suggestions else 'no_public_relaxation', 'read_only': True, 'suggestions': suggestions,
                'search_scope': 'At most one public input; all personal hard limits retained. Bounded search, no global minimality claim.',
                'message': 'Hypothetical only. People may voluntarily review their own conditions in private; no private owner or threshold is disclosed.'}

    def planning_options(self, actor_id, plan, candidate=None, mode='options', scenarios=None):
        if mode == 'agreement':
            result = self.human_agreements(self.identity.get(actor_id), plan)
            # Free-text objections stay on the human page, including in a personal MCP session.
            for item in result['items']:
                item.pop('contest', None)
            return result
        if mode not in ('resilience', 'fairness', 'relaxations'):
            if scenarios is not None:
                raise DomainError('INVALID_ARGUMENT', 'Scenarios are accepted only in resilience mode.')
            return super().planning_options(actor_id, plan, candidate, mode)
        if scenarios is not None and mode != 'resilience':
            raise DomainError('INVALID_ARGUMENT', 'Scenarios are accepted only in resilience mode.')
        def capture():
            p, private, remaining = self._diagnostic_admission(actor_id, plan)
            if private:
                return (p, None, None, None, None, remaining)
            snap = self._latest_snapshot(p)
            if not snap:
                raise DomainError('INVALID_ARGUMENT', 'Add activities before comparing future changes.')
            problem = self._problem(p, snap)
            bases = [(p['candidate_revision_id'] or p['active_revision_id'] or p['draft_revision_id'], 'Current plan', snap)]
            if candidate:
                rev = self._revision(candidate)
                current = self._revision(p['candidate_revision_id'])
                valid = rev and rev['plan_id'] == p['id'] and (rev['id'] == bases[0][0] or
                          (rev['kind'] == 'candidate' and current and current.get('repair_job_id') and current['repair_job_id'] == rev.get('repair_job_id')))
                if not valid:
                    raise DomainError('NOT_FOUND', 'No such current candidate in this plan.')
                bases = [(rev['id'], rev['label'], self._snapshot(rev))]
            elif p['candidate_revision_id']:
                current = self._revision(p['candidate_revision_id'])
                if current and current.get('repair_job_id'):
                    for r in self.db.all("SELECT * FROM plan_revisions WHERE repair_job_id=? AND kind='candidate' AND id<>? ORDER BY created_at,id LIMIT 3", current['repair_job_id'], current['id']):
                        bases.append((r['id'], r['label'], self._snapshot(r)))
            checked = self._scenario_inputs(problem, scenarios) if mode == 'resilience' else None
            if mode == 'fairness':
                baseline = self._revision(p['active_revision_id'])
                snap = self._snapshot(baseline) if baseline else snap
                problem = self._problem(p, snap)
                result = self._fairness_comparison(actor_id, p, snap, problem)
                return p, result, None, None, None, remaining
            return p, problem, bases, checked, (self.state_token(p), self._input_versions(p)), remaining
        p, problem, bases, checked, token, remaining = self.db.tx(capture)
        if problem is None:
            return {'outcome': 'private_review_required', 'plan': p['id'], 'read_only': True, 'budget_remaining': remaining,
                    'message': 'Review personal limits privately on each person’s own page, or discuss a public alternative. No one needs to disclose a private reason.'}
        if mode == 'fairness':
            result = problem
        else:
            result = self._resilience_analysis(p, problem, bases, checked) if mode == 'resilience' else self._public_relaxation_analysis(problem)
            def validate_current():
                current = self.resolve_plan(self.scope_for(actor_id), p['id'])
                if (self.state_token(current), self._input_versions(current)) != token:
                    raise DomainError('STALE', 'The plan or source freshness changed while the comparison ran; review a fresh comparison.')
            self.db.tx(validate_current)
        result['budget_remaining'] = remaining
        result['privacy_scope'] = 'Bounded diagnostic policy; shared outcomes can still reveal information. No cryptographic or differential-privacy guarantee.'
        return result
