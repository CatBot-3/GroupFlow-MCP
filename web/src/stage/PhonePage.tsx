import { useMemo, useState } from 'react';
import type { DecisionExtra, Inbox, InboxPlan, Member, ProjectHumanPlan } from '../api';
import { STATUS_WORDS } from './PlanBoard';
import { ProjectHumanPanel } from './ProjectHumanPanel';
import { TogetherPage } from './Together';
import { DecisionHub } from './DecisionHub';
import type { HumanClient } from '../api';

interface Props {
  client?: HumanClient;
  onInbox?: (inbox: Inbox) => void;
  member: Member;
  signedIn: boolean;
  inbox: Inbox | null;
  groupPlanIds: Set<string>;
  groupId?: string;
  busy: boolean;
  error: string | null;
  message: string | null;
  onLogin: (passcode: string) => void;
  onLogout: () => void;
  onDecide: (scopeId: string, decision: 'approved' | 'rejected' | 'conditional', extra?: DecisionExtra) => void;
  onVerify: (conditionId: string, digest: string) => void;
  onRevoke: (receiptId: string) => void;
  onSetCondition: (planId: string, kind: string, value: Record<string, unknown>) => void;
  onProjectAnswer: (id: string, decision: 'approve' | 'decline') => void;
  onProjectRevoke: (id: string) => void;
  onProjectCalendar: (plan: string, calendar: ProjectHumanPlan['calendar']) => void;
  onWorkspaceRole: (group: string, target: string, role: string, grant: boolean) => void;
  onVote?: (poll: string, choice: string, decide?: boolean, reason?: string) => void;
  onRsvp?: (plan: string, answer: 'yes' | 'no' | 'maybe', plusOnes?: number) => void;
  onBring?: (plan: string, action: 'add' | 'bring' | 'unclaim', things: string[]) => void;
  onConfirmCost?: (plan: string, id: string, answer: 'yes' | 'no') => void;
}

const DECISION_WORDS: Record<string, string> = { approved: 'Confirmed', rejected: 'Declined', conditional: 'Confirmed with a condition' };
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const NOTE_KIND: Record<string, string> = { reminder: 'Reminder', nudge: 'Check-in', notice: 'Update', poll: 'Vote', rsvp: 'Coming', bring: 'Bring list', costs: 'Costs' };
const noop = () => undefined;

type Spec = InboxPlan['conditions'][number];

function Lock() {
  return <svg className="lock" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10V8a5 5 0 0 1 10 0v2h1a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h1Zm2 0h6V8a3 3 0 0 0-6 0v2Z" /></svg>;
}

/** The checks a person can type on their own page; the rest (must attend, keep this) are the organizer's. */
const TYPED = new Set(['latest_end', 'earliest_start', 'max_total_cost', 'needs', 'unavailable_window']);

function ConditionForm({ id, plans, busy, onSet }: { id: string; plans: InboxPlan[]; busy: boolean; onSet: (planId: string, kind: string, value: Record<string, unknown>) => void }) {
  const choices = useMemo(() => plans.flatMap((p) => p.conditions.filter((c) => TYPED.has(c.check)).map((c) => ({ plan: p, spec: c }))), [plans]);
  const [pick, setPick] = useState(0);
  const [a, setA] = useState('');
  const [day, setDay] = useState('');
  const [b, setB] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  if (!choices.length) return null;
  const cur = choices[Math.min(pick, choices.length - 1)];
  const spec: Spec = cur.spec;
  const many = plans.filter((p) => p.conditions.some((c) => TYPED.has(c.check))).length > 1;

  const value = (): Record<string, unknown> | null => {
    if (spec.check === 'latest_end' || spec.check === 'earliest_start') return /^\d{1,2}:\d{2}$/.test(a.trim()) ? { time: a.trim().padStart(5, '0') } : null;
    if (spec.check === 'max_total_cost') return Number(a) > 0 ? { amount: Number(a) } : null;
    if (spec.check === 'needs') return tags.length ? { tags } : null;
    if (spec.check === 'unavailable_window') {
      if (!/^\d{1,2}:\d{2}$/.test(a.trim()) || !/^\d{1,2}:\d{2}$/.test(b.trim())) return null;
      return { ...(day ? { day } : {}), from: a.trim().padStart(5, '0'), to: b.trim().padStart(5, '0') };
    }
    return null;
  };
  const v = value();

  return (
    <form className="phone-add" onSubmit={(e) => {
      e.preventDefault();
      if (!v) return;
      onSet(cur.plan.id, spec.name, v);
      setA(''); setB(''); setDay(''); setTags([]);
    }}>
      <div className="phone-row">
        <label className="sr-only" htmlFor={`kind-${id}`}>Condition</label>
        <select id={`kind-${id}`} value={pick} onChange={(e) => { setPick(Number(e.target.value)); setA(''); setB(''); setTags([]); }}>
          {choices.map((c, i) => <option key={`${c.plan.id}.${c.spec.name}`} value={i}>{c.spec.label.charAt(0).toUpperCase() + c.spec.label.slice(1)}{many ? ` (${c.plan.title})` : ''}</option>)}
        </select>
      </div>
      <div className="phone-row">
        {(spec.check === 'latest_end' || spec.check === 'earliest_start') && (
          <><label className="sr-only" htmlFor={`val-${id}`}>Time</label><input id={`val-${id}`} value={a} onChange={(e) => setA(e.target.value)} placeholder={spec.check === 'latest_end' ? '21:00' : '09:30'} inputMode="numeric" /></>
        )}
        {spec.check === 'max_total_cost' && (
          <><label className="sr-only" htmlFor={`val-${id}`}>Amount</label><input id={`val-${id}`} value={a} onChange={(e) => setA(e.target.value)} placeholder="40" inputMode="decimal" /></>
        )}
        {spec.check === 'needs' && (
          <fieldset className="tag-picks">
            <legend className="sr-only">{spec.label}</legend>
            {(spec.tags.length ? spec.tags : []).map((t) => (
              <label key={t} className="tag-pick">
                <input type="checkbox" checked={tags.includes(t)} onChange={(e) => setTags((x) => (e.target.checked ? [...x, t] : x.filter((y) => y !== t)))} /> {t.replace(/_/g, ' ')}
              </label>
            ))}
            {!spec.tags.length && <input aria-label="Tags, comma separated" value={a} onChange={(e) => { setA(e.target.value); setTags(e.target.value.split(',').map((x) => x.trim()).filter(Boolean)); }} placeholder="vegetarian" />}
          </fieldset>
        )}
        {spec.check === 'unavailable_window' && (
          <>
            <label className="sr-only" htmlFor={`day-${id}`}>Day</label>
            <select id={`day-${id}`} value={day} onChange={(e) => setDay(e.target.value)}>
              <option value="">Any day</option>
              {DAYS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <label className="sr-only" htmlFor={`from-${id}`}>From</label>
            <input id={`from-${id}`} className="short" value={a} onChange={(e) => setA(e.target.value)} placeholder="12:00" />
            <label className="sr-only" htmlFor={`to-${id}`}>To</label>
            <input id={`to-${id}`} className="short" value={b} onChange={(e) => setB(e.target.value)} placeholder="13:00" />
          </>
        )}
        <button type="submit" className="pbtn" disabled={busy || !v}>Set</button>
      </div>
    </form>
  );
}

export function PhonePage({ client, onInbox, member, signedIn, inbox, groupPlanIds: shownPlanIds, groupId, busy, error, message, onLogin, onLogout, onDecide, onVerify, onRevoke, onSetCondition, onProjectAnswer, onProjectRevoke, onProjectCalendar, onWorkspaceRole, onVote = noop, onRsvp = noop, onBring = noop, onConfirmCost = noop }: Props) {
  const [passcode, setPasscode] = useState(member.demoPasscode ?? '');
  const [limit, setLimit] = useState<Record<string, string>>({});
  const [openItem, setOpenItem] = useState<string | null>(null);
  const groupPlanIds = useMemo(() => new Set([...shownPlanIds,
    ...(inbox?.plans ?? []).filter(p => groupId && p.group_id === groupId).map(p => p.id)]), [shownPlanIds, inbox, groupId]);

  const head = (
    <p className="phone-head"><Lock /> {member.name}'s own page <span className="phone-sub">human-only API, not reachable by Alexa</span></p>
  );

  if (!signedIn) {
    return (
      <div className="phone">
        {head}
        <form className="phone-login" onSubmit={(e) => { e.preventDefault(); onLogin(passcode); }}>
          <label htmlFor={`pass-${member.id}`}>Passcode</label>
          <div className="phone-row">
            <input id={`pass-${member.id}`} value={passcode} onChange={(e) => setPasscode(e.target.value)} autoComplete="off" />
            <button type="submit" className="pbtn primary-p" disabled={busy || !passcode}>Sign in</button>
          </div>
          <p className="phone-note">Signs in to the GroupFlow server's human-only API. Alexa's MCP token is refused there, so only {member.name} can confirm {member.name}'s part.{member.demoPasscode ? ' The demo passcode is filled in.' : ''}</p>
        </form>
        {error && <p className="phone-error" role="alert">{error}</p>}
      </div>
    );
  }

  // The page lists everything the person can see; this card shows the current group's plans only.
  const inGroup = (planId: string | null | undefined) => !planId || groupPlanIds.has(planId);
  const plans = (inbox?.plans ?? []).filter((p) => groupPlanIds.has(p.id));
  const pending = (inbox?.pending ?? []).filter((it) => inGroup(it.plan_id));
  const conds = (inbox?.conditions ?? []).filter((c) => inGroup(c.plan_id));
  const drafts = conds.filter((c) => c.status === 'unverified');
  const active = conds.filter((c) => c.status === 'active');
  const titles = new Set(plans.map((p) => p.title));
  const receipts = (inbox?.receipts ?? []).filter((r) => titles.has(r.plan));
  const currentReceipts = receipts.filter((r) => r.current !== false);
  const oldReceipts = receipts.filter((r) => r.current === false);
  const budgetOf = (planId: string) => plans.find((p) => p.id === planId)?.conditions.find((c) => c.check === 'max_total_cost') ?? null;
  const busyPlans = plans.filter((p) => p.status !== 'ACTIVE');
  const notes = (inbox?.notifications ?? []).filter((n) => inGroup(n.plan_id));
  const together = (inbox?.together ?? []).filter((t) => groupPlanIds.has(t.plan_id));

  return (
    <div className={`phone ${busy ? 'busy' : ''}`}>
      {head}
      {message && <p className="phone-ok" role="status">{message}</p>}
      {client && inbox && <DecisionHub client={client} inbox={inbox} planIds={groupPlanIds} onRefresh={onInbox} />}
      <ProjectHumanPanel items={(inbox?.project_items || []).filter((i) => groupPlanIds.has(i.plan_id))} projects={(inbox?.project_plans || []).filter((p) => groupPlanIds.has(p.id))} busy={busy} onAnswer={onProjectAnswer} onRevoke={onProjectRevoke} onCalendar={onProjectCalendar} onRole={onWorkspaceRole} />
      {busyPlans.map((p) => (
        <p key={p.id} className="phone-plan">{p.title}: <strong>{STATUS_WORDS[p.status] ?? p.status}</strong></p>
      ))}

      <h3 className="phone-h">Waiting for you</h3>
      {inbox && pending.length === 0 && <p className="phone-empty">Nothing to confirm right now.</p>}
      <ul className="phone-list">
        {pending.map((item) => {
          const budget = budgetOf(item.plan_id);
          return (
            <li key={item.scope_id} id={`decision-${item.scope_id}`} className="phone-item pending">
              <p className="phone-text"><span className="rev">{item.revision}</span> {item.text}{plans.length > 1 && <span className="phone-sub"> {item.plan}</span>}</p>
              <div className="phone-row">
                <button type="button" className="pbtn primary-p" disabled={busy} onClick={() => onDecide(item.scope_id, 'approved')}>Confirm</button>
                <button type="button" className="pbtn" disabled={busy} onClick={() => onDecide(item.scope_id, 'rejected')}>Decline</button>
                {budget && <button type="button" className="pbtn link" aria-expanded={openItem === item.scope_id} onClick={() => setOpenItem(openItem === item.scope_id ? null : item.scope_id)}>More</button>}
              </div>
              {budget && openItem === item.scope_id && (
                <div className="phone-more">
                  <label htmlFor={`lim-${item.scope_id}`}>My total must stay under</label>
                  <div className="phone-row">
                    <input id={`lim-${item.scope_id}`} inputMode="decimal" value={limit[item.scope_id] ?? ''} onChange={(e) => setLimit((l) => ({ ...l, [item.scope_id]: e.target.value }))} placeholder="45" />
                    <button type="button" className="pbtn" disabled={busy || !(Number(limit[item.scope_id]) > 0)}
                      onClick={() => onDecide(item.scope_id, 'conditional', { condition: { max_total_minor: Math.round(Number(limit[item.scope_id]) * 100) } })}>Confirm if so</button>
                    <button type="button" className="pbtn" disabled={busy || !(Number(limit[item.scope_id]) > 0)}
                      onClick={() => onDecide(item.scope_id, 'rejected', { reason: 'over my budget', new_limit: { kind: budget.name, value: { amount: Number(limit[item.scope_id]) } } })}>Decline, set as my limit</button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <TogetherPage plans={together} busy={busy} showPlan={together.length > 1 || plans.length > 1} onVote={onVote} onRsvp={onRsvp} onBring={onBring} onConfirmCost={onConfirmCost} />

      {drafts.length > 0 && (
        <>
          <h3 className="phone-h">Check before it counts</h3>
          <ul className="phone-list">
            {drafts.map((c) => (
              <li key={c.id} className="phone-item pending">
                <p className="phone-text">{c.description} <span className="tag">drafted by Alexa</span></p>
                <div className="phone-row">
                  <button type="button" className="pbtn primary-p" disabled={busy} onClick={() => onVerify(c.id, c.verify_digest)}>Yes, count it</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {plans.some((p) => p.conditions.length > 0) && (
        <>
          <h3 className="phone-h">My conditions <span className="phone-sub">private to me</span></h3>
          <ul className="phone-list compact">
            {active.map((c) => <li key={c.id}>{c.description}{plans.length > 1 && <span className="phone-sub"> {c.plan}</span>}</li>)}
            {active.length === 0 && <li className="phone-empty">None.</li>}
          </ul>
          <ConditionForm id={member.id} plans={plans} busy={busy} onSet={onSetCondition} />
        </>
      )}

      {receipts.length > 0 && (
        <>
          <h3 className="phone-h">My answers</h3>
          <ul className="phone-list compact">
            {currentReceipts.slice(0, 4).map((r) => (
              <li key={r.receipt_id} className={r.status !== 'valid' ? 'muted' : ''}>
                <span className="rev">{r.revision}</span> {DECISION_WORDS[r.decision] ?? r.decision}: {r.text.split(' on ')[0]}
                {r.status !== 'valid' ? <span className="tag">{r.status}</span> : (
                  <button type="button" className="pbtn link" disabled={busy} onClick={() => onRevoke(r.receipt_id)}>Withdraw</button>
                )}
              </li>
            ))}
            {currentReceipts.length === 0 && <li className="phone-empty">None that count right now.</li>}
          </ul>
          {oldReceipts.length > 0 && (
            <details className="phone-older">
              <summary>{oldReceipts.length} earlier {oldReceipts.length === 1 ? 'answer' : 'answers'} for replaced versions</summary>
              <ul className="phone-list compact">
                {oldReceipts.slice(0, 6).map((r) => <li key={r.receipt_id} className="muted"><span className="rev">{r.revision}</span> {DECISION_WORDS[r.decision] ?? r.decision}: {r.text.split(' on ')[0]}</li>)}
              </ul>
            </details>
          )}
        </>
      )}

      {notes.length > 0 && (
        <>
          <h3 className="phone-h">Notifications</h3>
          <ul className="phone-list compact">
            {notes.slice(-3).map((n) => <li key={n.id}>{NOTE_KIND[n.kind ?? ''] && <span className="note-kind">{NOTE_KIND[n.kind ?? '']}</span>}{n.text}</li>)}
          </ul>
        </>
      )}
      {error && <p className="phone-error" role="alert">{error}</p>}
      <button type="button" className="pbtn link signout" onClick={onLogout}>Sign out</button>
    </div>
  );
}
