import { useState } from 'react';
import type { BringItem, CostsView, PlanView, PollView, RsvpView, TogetherPlan } from '../api';

/* Votes, who is coming, the bring list and shared costs: on the plan board (read-only, as the viewer sees it) and on each person's own
   page (where they answer for themselves). A vote never confirms an activity, so none of this sits next to the confirmation buttons. */

function Tally({ poll }: { poll: PollView }) {
  const entries = Object.entries(poll.votes);
  const most = Math.max(1, ...entries.map(([, v]) => v.length));
  return (
    <ul className="tally">
      {entries.map(([option, who]) => (
        <li key={option}>
          <span className="tally-option">{poll.kind === 'vote' ? option : option === 'object' ? 'Objections' : 'Fine'}</span>
          <span className="tally-bar" style={{ ['--share' as string]: `${(who.length / most) * 100}%` }} aria-hidden="true" />
          <span className="tally-who">{who.length ? who.join(', ') : '–'}</span>
        </li>
      ))}
    </ul>
  );
}

function pollTitle(p: PollView) {
  return p.kind === 'vote' ? p.question : `${p.about ?? 'Proposal'}: ${p.proposal}, unless someone objects`;
}

function pollMeta(p: PollView) {
  const parts = [p.decides === 'you' ? 'you decide' : `${p.decides} decides`];
  if (p.closes) parts.push(`${p.status === 'open' ? 'closes' : 'closed'} ${p.closes}`);
  if (p.if_undecided) parts.push(`then ${p.if_undecided}`);
  return parts.join(' · ');
}

export function TogetherBoard({ plan }: { plan: PlanView }) {
  const polls = plan.polls ?? [];
  const rsvp = plan.rsvp;
  const bring = plan.bring_list ?? [];
  const costs = plan.costs;
  if (!polls.length && !rsvp && !bring.length && !costs) return null;
  return (
    <>
      {polls.length > 0 && (
        <>
          <h3 className="plan-section">Votes <span className="section-sub">suggestions to whoever decides</span></h3>
          <ul className="slots">
            {polls.map((p) => (
              <li key={p.id} className={`slot edge-plain poll status-${p.status}`}>
                <div className="slot-label">{pollMeta(p)}</div>
                <div className="slot-value">{pollTitle(p)}</div>
                {p.status === 'decided' || p.status === 'cancelled' ? <div className="slot-meta">{p.result}</div> : <Tally poll={p} />}
                {p.status === 'open' && p.not_voted.length > 0 && <div className="slot-meta">Not answered yet: {p.not_voted.join(', ')}</div>}
                {p.status === 'closed' && <div className="flag">Voting closed; waiting for {p.decides === 'you' ? 'you' : p.decides} to decide.</div>}
              </li>
            ))}
          </ul>
        </>
      )}
      {rsvp && <RsvpSummary rsvp={rsvp} />}
      {bring.length > 0 && <BringSummary items={bring} />}
      {costs && <CostsSummary costs={costs} />}
    </>
  );
}

function RsvpSummary({ rsvp }: { rsvp: RsvpView }) {
  return (
    <>
      <h3 className="plan-section">Who's coming <span className="section-sub">{rsvp.headcount} so far{rsvp.headcount_with_maybes > rsvp.headcount ? `, ${rsvp.headcount_with_maybes} with maybes` : ''}</span></h3>
      <ul className="events">
        {rsvp.coming.length > 0 && <li>Coming: {rsvp.coming.join(', ')}</li>}
        {rsvp.maybe.length > 0 && <li>Maybe: {rsvp.maybe.join(', ')}</li>}
        {rsvp.not_coming.length > 0 && <li>Not coming: {rsvp.not_coming.join(', ')}</li>}
        {rsvp.not_answered.length > 0 && <li className="muted">Not answered: {rsvp.not_answered.join(', ')}</li>}
        {rsvp.dietary_needs && <li>To cater for: {rsvp.dietary_needs.map((t) => t.replace(/_/g, ' ')).join(', ')} <span className="section-sub">from private conditions, no names</span></li>}
      </ul>
    </>
  );
}

function BringSummary({ items }: { items: BringItem[] }) {
  return (
    <>
      <h3 className="plan-section">Bring list</h3>
      <ul className="events bring">
        {items.map((b) => <li key={b.id} className={b.bringing ? '' : 'open'}>{b.thing}: {b.bringing ?? <strong>nobody yet</strong>}</li>)}
      </ul>
    </>
  );
}

function CostsSummary({ costs }: { costs: CostsView }) {
  return (
    <>
      <h3 className="plan-section">Shared costs <span className="section-sub">{costs.total} so far; nothing is paid through GroupFlow</span></h3>
      <ul className="events">
        {costs.expenses.map((e) => (
          <li key={e.id} className={e.status === 'counted' ? '' : 'muted'}>
            {e.what}: {e.amount}, paid by {e.paid_by}{e.your_share ? ` (your share ${e.your_share})` : ''}{e.status !== 'counted' && <span className="tag warn">{e.status}</span>}
          </li>
        ))}
        {(costs.repayments_waiting ?? []).map((r, i) => <li key={`r${i}`} className="muted">{r}</li>)}
      </ul>
      {costs.settle_up.length > 0 && <p className="diff-meta">To settle up: {costs.settle_up.join('; ')}.</p>}
      {costs.your_balance && <p className="diff-meta">Your balance: {costs.your_balance}.</p>}
    </>
  );
}

interface PageProps {
  plans: TogetherPlan[];
  busy: boolean;
  showPlan: boolean;
  onVote: (poll: string, choice: string, decide?: boolean, reason?: string) => void;
  onRsvp: (plan: string, answer: 'yes' | 'no' | 'maybe', plusOnes?: number) => void;
  onBring: (plan: string, action: 'add' | 'bring' | 'unclaim', things: string[]) => void;
  onConfirmCost: (plan: string, id: string, answer: 'yes' | 'no') => void;
}

/** The person's own answers: their vote, whether they are coming, what they bring, and payments recorded for them. */
export function TogetherPage({ plans, busy, showPlan, onVote, onRsvp, onBring, onConfirmCost }: PageProps) {
  const [extra, setExtra] = useState<Record<string, number>>({});
  const [thing, setThing] = useState<Record<string, string>>({});
  const [why, setWhy] = useState<Record<string, string>>({});
  if (!plans.length) return null;
  return (
    <>
      <h3 className="phone-h">Your say <span className="phone-sub">votes are suggestions; activities still need your confirmation above</span></h3>
      {plans.map((p) => {
        const plus = extra[p.plan_id] ?? Number((p.rsvp?.your_answer ?? '').split('+')[1] ?? 0);
        return (
          <div key={p.plan_id} className="together">
            {showPlan && <p className="phone-plan">{p.title}</p>}
            <ul className="phone-list">
              {p.to_confirm.map((c) => (
                <li key={c.id} className="phone-item pending">
                  <p className="phone-text">{c.text} Is that right?</p>
                  <div className="phone-row">
                    <button type="button" className="pbtn primary-p" disabled={busy} onClick={() => onConfirmCost(p.plan_id, c.id, 'yes')}>Yes, right</button>
                    <button type="button" className="pbtn" disabled={busy} onClick={() => onConfirmCost(p.plan_id, c.id, 'no')}>No</button>
                  </div>
                </li>
              ))}
              {p.polls.map((v) => (
                <li key={v.id} className={`phone-item ${v.can_vote && !v.your_vote ? 'pending' : ''}`}>
                  <p className="phone-text">{pollTitle(v)} <span className="phone-sub">{pollMeta(v)}</span></p>
                  {v.kind === 'vote' && (v.can_vote || v.can_decide) && (
                    <div className="phone-row" role="group" aria-label={v.can_decide ? 'Decide' : 'Your vote'}>
                      {(v.options ?? []).map((o) => (
                        <button key={o} type="button" className={`pbtn ${v.your_vote === o ? 'primary-p' : ''}`} aria-pressed={v.your_vote === o} disabled={busy}
                          onClick={() => onVote(v.id, o, v.can_decide)}>{o}{v.can_decide ? ` (${v.votes[o]?.length ?? 0})` : ''}</button>
                      ))}
                      {v.can_vote && <button type="button" className="pbtn link" disabled={busy} onClick={() => onVote(v.id, 'no preference')}>No preference</button>}
                    </div>
                  )}
                  {v.kind !== 'vote' && v.can_vote && (
                    <div className="phone-row">
                      <label className="sr-only" htmlFor={`why-${v.id}`}>Why</label>
                      <input id={`why-${v.id}`} value={why[v.id] ?? ''} onChange={(e) => setWhy((w) => ({ ...w, [v.id]: e.target.value }))} placeholder="Why (optional)" />
                      <button type="button" className={`pbtn ${v.your_vote === 'object' ? 'primary-p' : ''}`} disabled={busy} onClick={() => onVote(v.id, 'object', false, why[v.id])}>Object</button>
                      <button type="button" className={`pbtn ${v.your_vote === 'fine' ? 'primary-p' : ''}`} disabled={busy} onClick={() => onVote(v.id, 'fine')}>Fine by me</button>
                    </div>
                  )}
                  {v.kind !== 'vote' && v.can_decide && (
                    <div className="phone-row">
                      <button type="button" className="pbtn primary-p" disabled={busy} onClick={() => onVote(v.id, 'apply', true)}>Apply it</button>
                      <button type="button" className="pbtn" disabled={busy} onClick={() => onVote(v.id, 'drop', true)}>Drop it</button>
                    </div>
                  )}
                </li>
              ))}
              {p.offer_rsvp && (
                <li className={`phone-item ${p.rsvp && !p.rsvp.your_answer ? 'pending' : ''}`}>
                  <p className="phone-text">Coming?{p.rsvp ? <span className="phone-sub"> {p.rsvp.headcount} coming so far</span> : null}</p>
                  <div className="phone-row" role="group" aria-label="Your answer">
                    {(['yes', 'maybe', 'no'] as const).map((a) => (
                      <button key={a} type="button" className={`pbtn ${(p.rsvp?.your_answer ?? '').startsWith(a) ? 'primary-p' : ''}`} disabled={busy}
                        onClick={() => onRsvp(p.plan_id, a, a === 'no' ? 0 : plus)}>{a === 'yes' ? 'Yes' : a === 'maybe' ? 'Maybe' : 'No'}</button>
                    ))}
                    <label htmlFor={`plus-${p.plan_id}`} className="phone-sub">guests</label>
                    <input id={`plus-${p.plan_id}`} className="short" type="number" min={0} max={10} value={plus}
                      onChange={(e) => setExtra((x) => ({ ...x, [p.plan_id]: Math.max(0, Math.min(10, Number(e.target.value) || 0)) }))} />
                  </div>
                  <p className="phone-sub">If you come, dietary needs you confirmed show to this plan as tags, never with your name, so the host can cater.</p>
                </li>
              )}
              {p.bring_list.length > 0 && (
                <li className="phone-item">
                  <p className="phone-text">Bring list</p>
                  <ul className="phone-list compact">
                    {p.bring_list.map((b) => (
                      <li key={b.id}>{b.thing}: {b.bringing ?? 'nobody yet'}
                        {!b.bringing && <button type="button" className="pbtn link" disabled={busy} onClick={() => onBring(p.plan_id, 'bring', [b.id])}>I'll bring it</button>}
                        {b.bringing === 'you' && <button type="button" className="pbtn link" disabled={busy} onClick={() => onBring(p.plan_id, 'unclaim', [b.id])}>Can't after all</button>}
                      </li>
                    ))}
                  </ul>
                </li>
              )}
              {(p.bring_list.length > 0 || p.offer_rsvp) && (
                <li>
                  <form className="phone-row" onSubmit={(e) => { e.preventDefault(); const t = (thing[p.plan_id] ?? '').trim(); if (t) { onBring(p.plan_id, 'bring', [t]); setThing((x) => ({ ...x, [p.plan_id]: '' })); } }}>
                    <label className="sr-only" htmlFor={`bring-${p.plan_id}`}>I'll bring</label>
                    <input id={`bring-${p.plan_id}`} value={thing[p.plan_id] ?? ''} onChange={(e) => setThing((x) => ({ ...x, [p.plan_id]: e.target.value }))} placeholder="I'll bring…" />
                    <button type="submit" className="pbtn" disabled={busy || !(thing[p.plan_id] ?? '').trim()}>Add</button>
                  </form>
                </li>
              )}
              {p.costs && p.costs.your_balance && (
                <li className="phone-item"><p className="phone-text">Shared costs: {p.costs.your_balance}.</p>
                  {p.costs.settle_up.filter((s) => s.startsWith('you ') || s.includes(' you ')).map((s, i) => <p key={i} className="phone-sub">{s}</p>)}
                </li>
              )}
            </ul>
          </div>
        );
      })}
    </>
  );
}
