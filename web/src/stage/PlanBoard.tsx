import type { ReactNode } from 'react';
import type { BoardModel, ConditionView, DetailView, Member, PlanStatus, PlanView, ScopeStatus } from '../api';
import { TaskGraph } from './TaskGraph';
import { Timeline } from './Timeline';
import { ProjectPanel } from './ProjectPanel';

export const STATUS_WORDS: Record<PlanStatus, string> = {
  ACTIVE: 'Open plan, owners decide their items',
  DRAFT: 'Draft, not shared yet',
  PROPOSED: 'Proposed, not yet sent for confirmation',
  AWAITING_CONFIRMATIONS: 'Waiting for confirmations',
  READY_TO_COMMIT: 'Everyone confirmed, ready to commit',
  COMMITTED: 'Agreed',
  DEGRADED: 'Agreed plan no longer works',
  REPAIRING: 'Looking for alternatives',
  BLOCKED: 'No option fits everyone',
  NEEDS_ATTENTION: 'Needs attention',
  CANCELLED: 'Cancelled',
};

const STATUS_TONE: Record<PlanStatus, 'ok' | 'warn' | 'bad' | 'plain'> = {
  ACTIVE: 'plain', DRAFT: 'plain', PROPOSED: 'plain', AWAITING_CONFIRMATIONS: 'warn', READY_TO_COMMIT: 'ok', COMMITTED: 'ok',
  DEGRADED: 'bad', REPAIRING: 'warn', BLOCKED: 'bad', NEEDS_ATTENTION: 'bad', CANCELLED: 'plain',
};

const CELL: Record<ScopeStatus, { label: string; tone: string; title: string }> = {
  approved: { label: 'Confirmed', tone: 'ok', title: 'Confirmed by the person on their own page' },
  carried_forward: { label: 'Kept', tone: 'kept', title: 'Unchanged part: the earlier approval still covers it (digest, conditions and membership re-checked)' },
  pending: { label: 'Waiting', tone: 'wait', title: 'Not answered yet' },
  rejected: { label: 'Declined', tone: 'bad', title: 'Declined on their own page' },
  conditional_met: { label: 'Yes, if', tone: 'ok', title: 'Confirmed with a condition that this version meets' },
  conditional_unmet: { label: 'Condition fails', tone: 'bad', title: 'Confirmed with a condition this version does not meet' },
  revoked: { label: 'Withdrawn', tone: 'bad', title: 'The person withdrew their answer' },
  expired: { label: 'Expired', tone: 'bad', title: 'The answer expired' },
};

const PROOF_WORDS: Record<string, string> = {
  optimal_within_domain: 'Best possible among the options searched',
  best_found: 'Best found before the time limit',
  infeasible_within_domain: 'Nothing among the options fits everyone',
  unknown: 'Search stopped without an answer',
};

const DETAIL_WORDS: Record<DetailView['status'], string> = {
  confirmed: 'Set',
  disputed: 'Change waiting for the owner',
  has_suggestions: 'Set, with an idea on the table',
  unset: 'Not set yet',
};

export interface PlanTab { id: string; title: string; hiddenFrom: string[] }

function Dot({ color }: { color?: string }) {
  return <span className="dot" style={{ background: color ?? 'var(--ink-3)' }} aria-hidden="true" />;
}

function fmtTime(iso: string) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} ${time}`;
}

/** Missing confirmations are already in the table above, so the gate counts them and lists only the other blockers. */
function GateList({ gate }: { gate: string[] }) {
  const missing = gate.filter((g) => / has not confirmed /.test(g));
  const other = gate.filter((g) => !missing.includes(g));
  return (
    <ul>
      {other.map((g, i) => <li key={i}>{g}</li>)}
      {missing.length > 0 && <li>{missing.length} {missing.length === 1 ? 'confirmation' : 'confirmations'} still missing (see the table above).</li>}
    </ul>
  );
}

function Details({ plan, colorOf, edge }: { plan: PlanView; colorOf: (name: string) => string | undefined; edge: string }) {
  if (!plan.details.length) return null;
  return (
    <>
      <h3 className="plan-section">Details</h3>
      <ul className="slots">
        {plan.details.map((d) => (
          <li key={d.id} className={`slot edge-${edge} status-${d.status}`}>
            <div className="slot-label">{d.label}</div>
            <div className="slot-value">{d.value ?? 'Not set'}</div>
            <div className="slot-meta">
              <span className="who"><Dot color={colorOf(d.owner)} />{d.decided_by === 'anyone' ? 'Anyone can change it' : `${d.owner} decides`}</span>
              <span className="state">{DETAIL_WORDS[d.status]}</span>
            </div>
            {d.pending.map((p, i) => (
              <div key={p.request ?? i} className={`proposal ${p.firm ? 'committed' : 'tentative'}`}>
                {p.firm
                  ? <><strong>{p.by}</strong> asked to change this to <strong>{p.value}</strong>. Waiting for {d.owner}.</>
                  : <><strong>{p.by}</strong>'s idea: {p.value}. Not applied.</>}
                {p.said && <q className="spoken">{p.said}</q>}
              </div>
            ))}
            {d.flags.map((f, i) => <div key={i} className="flag">{f}</div>)}
          </li>
        ))}
      </ul>
    </>
  );
}

function Matrix({ plan, colorOf }: { plan: PlanView; colorOf: (name: string) => string | undefined }) {
  const c = plan.confirmations;
  if (!c || !c.rows.length) return null;
  // People confirm only the activities they take part in, so the columns are the union across rows.
  const cols: string[] = [];
  for (const r of c.rows) for (const k of Object.keys(r.items)) if (!cols.includes(k)) cols.push(k);
  const waiting = c.rows.filter((r) => !r.done).map((r) => r.person);
  return (
    <>
      <h3 className="plan-section">Who has confirmed {c.version}</h3>
      <p className="matrix-sum">
        {c.satisfied} of {c.required} parts confirmed or kept.{' '}
        {c.complete ? 'Everyone needed has confirmed.' : `Waiting for ${waiting.join(', ')} on their own page.`}
      </p>
      <div className="matrix-wrap">
        <table className="matrix">
          <thead>
            <tr>
              <th scope="col"><span className="sr-only">Person</span></th>
              {cols.map((t) => <th key={t} scope="col">{t}</th>)}
            </tr>
          </thead>
          <tbody>
            {c.rows.map((r) => (
              <tr key={r.person}>
                <th scope="row"><Dot color={colorOf(r.person)} />{r.person}</th>
                {cols.map((t) => {
                  const st = r.items[t] as ScopeStatus | undefined;
                  if (!st) return <td key={t} className="cell none" title="Not taking part">·</td>;
                  const cell = CELL[st] ?? { label: st, tone: 'wait', title: st };
                  return <td key={t} className={`cell ${cell.tone}`} title={cell.title}>{cell.label}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Repair({ plan }: { plan: PlanView }) {
  const r = plan.repair;
  const ex = plan.explain;
  if (!r && !ex) return null;
  const first = r?.alternatives[0] ?? null;
  // A fallback that reads the same as the first choice adds nothing for the viewer.
  const fallbacks = (r?.alternatives.slice(1) ?? []).filter((a) => a.changes.join('|') !== first?.changes.join('|'));
  return (
    <>
      <h3 className="plan-section">Why this version</h3>
      {r?.proof && (
        <p className="proof">
          <span className={`proof-chip ${r.proof.status}`}>{PROOF_WORDS[r.proof.status] ?? r.proof.status}</span>{' '}
          {r.proof.method === 'cp-sat'
            ? <>CP-SAT over {r.proof.domain_size.toLocaleString('en-US')} combinations in {Math.max(1, Math.round(r.proof.runtime_ms))} ms.</>
            : <>Exact enumeration, {r.proof.explored.toLocaleString('en-US')} of {r.proof.domain_size.toLocaleString('en-US')} combinations checked in {Math.max(1, Math.round(r.proof.runtime_ms))} ms.</>}
        </p>
      )}
      {ex && ex.triggers.length > 0 && <p className="explain-trigger">Because: {ex.triggers.join('; ')}.</p>}
      {first && (
        <p className="diff-meta">
          {first.unchanged.length > 0 && <>Keeps {first.unchanged.join(', ')}. </>}
          {first.cost_each_before !== first.cost_each_after && <>Per person {first.cost_each_before} → {first.cost_each_after}. </>}
          {first.reconfirm.length > 0 && <>Asks {first.reconfirm.join(', ')} again about the changed part only.</>}
        </p>
      )}
      {fallbacks.length > 0 && (
        <ul className="rejected">
          {fallbacks.map((a) => <li key={a.candidate}><strong>{a.label}</strong> (fallback): {a.changes.join('; ')}</li>)}
        </ul>
      )}
      {r && r.conflict.length > 0 && <ul className="conflict">{r.conflict.map((c, i) => <li key={i}>{c}</li>)}</ul>}
      {ex && ex.options.length > 0 && (
        <ul className="rejected">
          {ex.options.map((o, i) => <li key={i}><strong>{o.option}</strong>: {o.reason}</li>)}
        </ul>
      )}
    </>
  );
}

function Conditions({ list, viewer, colorOf, members }: { list: ConditionView[]; viewer: Member | null; colorOf: (name: string) => string | undefined; members: Member[] }) {
  if (!list.length) return null;
  const order = (c: ConditionView) => members.findIndex((m) => m.id === c.owner_id || m.name === c.owner);
  return (
    <>
      <h3 className="plan-section">
        {viewer ? `${viewer.name}'s conditions` : 'Conditions'}{' '}
        <span className="section-sub">{viewer ? 'private to them' : 'narrator only; never sent to other members'}</span>
      </h3>
      <ul className="conditions">
        {[...list].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name)).map((c) => (
          <li key={c.id} className={c.status}>
            {c.owner && <span className="who"><Dot color={colorOf(c.owner)} />{c.owner}</span>}
            <span>{c.description}</span>
            {!c.private && <span className="tag">shared</span>}
            {c.status === 'unverified' && <span className="tag warn">draft, not counted until confirmed</span>}
          </li>
        ))}
      </ul>
    </>
  );
}

interface Props {
  model: BoardModel | null;
  planId: string;
  plans: PlanTab[];
  onPlan: (id: string) => void;
  viewAs: string;
  onViewAs: (v: string) => void;
  members: Member[];
  console?: ReactNode;
  memberOnly?: boolean;
}

export function PlanBoard({ model, planId, plans, onPlan, viewAs, onViewAs, members, console: demoConsole, memberOnly = false }: Props) {
  const colorOf = (name: string) => members.find((m) => m.name === name || m.id === name)?.color;
  const viewer = viewAs === 'all' ? null : members.find((m) => m.id === viewAs) ?? null;
  const tab = plans.find((p) => p.id === planId) ?? plans[0] ?? null;
  const plan = model?.plans.find((p) => p.id === tab?.id) ?? null;
  const ctx = model?.source === 'mcp' ? model.context : null;
  const hiddenFrom = plan ? members.filter((m) => !plan.visible_to.includes(m.name)).map((m) => m.name) : tab?.hiddenFrom ?? [];
  const edge = hiddenFrom.length ? 'hatch' : 'plain';
  const conditions = viewer ? ctx?.your_conditions ?? [] : plan?.conditions ?? [];
  const showGate = !viewer && plan?.gate && plan.working && plan.working.kind === 'candidate';
  const guidance = (text: string) => memberOnly ? text.replace(/ with (find_alternatives|request_confirmations|commit_plan)\b/g, '') : text;
  // While the agreed version no longer works, mark the activities a verified outside fact hit.
  const broken = new Set<string>();
  if (plan?.agreed && plan.status !== 'COMMITTED') {
    for (const a of plan.agreed.activities) {
      if (a.place && plan.observations.some((o) => o.startsWith(a.place!) && o.endsWith('(verified)'))) broken.add(a.id);
    }
  }

  return (
    <section className={`plan board ${!model ? 'loading' : ''}`} id="plan" aria-label="Group plans">
      <header className="plan-head">
        <div className="seg plan-tabs" role="tablist" aria-label="Plan">
          {plans.map((p) => {
            const blind = !!viewer && p.hiddenFrom.includes(viewer.name);
            return (
              <button key={p.id} type="button" role="tab" aria-selected={p.id === tab?.id} className={`${p.id === tab?.id ? 'on' : ''} ${blind ? 'blind' : ''}`}
                onClick={() => onPlan(p.id)} title={p.hiddenFrom.length ? `Hidden from ${p.hiddenFrom.join(' and ')}` : undefined}>
                {p.hiddenFrom.length > 0 && <span className="swatch hatch tab-swatch" aria-hidden="true" />}{p.title}
                {p.hiddenFrom.length > 0 && <span className="sr-only">, hidden from {p.hiddenFrom.join(' and ')}</span>}
              </button>
            );
          })}
        </div>
        {!memberOnly && <div className="seg" role="radiogroup" aria-label="Show the plan as">
          <button type="button" role="radio" aria-checked={viewAs === 'all'} className={viewAs === 'all' ? 'on' : ''} onClick={() => onViewAs('all')}>Everything</button>
          {members.map((m) => (
            <button key={m.id} type="button" role="radio" aria-checked={viewAs === m.id} className={viewAs === m.id ? 'on' : ''} onClick={() => onViewAs(m.id)} style={{ ['--member' as string]: m.color }}>
              {m.name}
            </button>
          ))}
        </div>}
      </header>
      <p className={`plan-caption ${viewer ? 'as-member' : ''}`} style={viewer ? { ['--member' as string]: viewer.color } : undefined}>
        {viewer
          ? memberOnly ? <>{viewer.name}'s live view. Shared arrangements and your own private limits.</> : <>Exactly what get_context returns to {viewer.name}'s Echo over MCP for this plan: {viewer.name}'s own conditions only, nobody else's.</>
          : <>Inspector view for narrating the demo, including everyone's private conditions. Only the demo backend can see this; it is not exposed over MCP.</>}
      </p>

      {!model && <p className="plan-empty">Loading the plan…</p>}

      {model && viewer && !plan && (
        <div className="refused" role="status">
          <p><strong>{viewer.name}'s Echo receives nothing about this plan.</strong></p>
          <p>get_context does not list it, and asking for it by name is refused the same way as a plan that does not exist{model.refused ? `: "${model.refused}"` : '.'}</p>
        </div>
      )}
      {model && !viewer && !plan && <p className="plan-empty">This plan is not in the group any more.</p>}

      {model && plan && (
        <>
          <h2 className="plan-title">{plan.title}</h2>
          <div className={`status-strip tone-${STATUS_TONE[plan.status]}`}>
            <span className="status-chip">{STATUS_WORDS[plan.status] ?? plan.status}</span>
            <span className="status-meta">{plan.dates} · {plan.kind} · organizer {plan.organizer} · {memberOnly ? plan.working?.label ?? plan.agreed?.label : `version ${plan.version}`}</span>
            {plan.status_reason && <p className="status-reason">{plan.status_reason}</p>}
            {plan.next_step && <p className="status-next">{plan.project ? 'Review work and resource availability, prepare a schedule, then request individual commitments and stage authorization.' : guidance(plan.next_step)}</p>}
          </div>
          <p className={`audience edge-${edge}`}>
            <span className={`swatch ${edge}`} aria-hidden="true" />
            <span>Visible to {plan.visible_to.join(', ')}{hiddenFrom.length > 0 ? '.' : ''}</span>
            {hiddenFrom.length > 0 && <strong>Hidden from {hiddenFrom.join(' and ')}.</strong>}
            {plan.space && !viewer && <span className="section-sub">Space: {plan.space}</span>}
          </p>

          {ctx && (ctx.decisions_for_you.length > 0 || ctx.your_todos.length > 0 || ctx.updates.length > 0 || ctx.affected.length > 0) && (
            <div className="for-you">
              <h3 className="plan-section">For {viewer?.name}</h3>
              <ul className="todo-list">
                {ctx.decisions_for_you.map((d) => (
                  <li key={d.request}><strong>{d.by}</strong> {d.firm ? 'asks' : 'suggests'}: {d.what}{d.current ? ` (now ${d.current})` : ''} → {d.proposed}</li>
                ))}
                {ctx.your_todos.map((t, i) => <li key={`t${i}`}>{t.text} <span className="where">({t.where})</span></li>)}
                {ctx.affected.map((f) => <li key={f.item}>{f.label}: {f.message}</li>)}
                {memberOnly && ctx.updates.length > 0 ? <li className="update"><details><summary>Recent updates</summary><ul>{ctx.updates.map((u, i) => <li key={`u${i}`}>{u}</li>)}</ul></details></li> : ctx.updates.map((u, i) => <li key={`u${i}`} className="update">{u}</li>)}
              </ul>
            </div>
          )}

          <Details plan={plan} colorOf={colorOf} edge={edge} />
          {plan.project && <ProjectPanel key={plan.id} plan={plan} member={viewer?.id} members={members} />}

          {(plan.agreed || plan.working) && (
            <>
              <h3 className="plan-section">Schedule</h3>
              <Timeline agreed={plan.agreed} working={plan.working} broken={broken} />
              {plan.working && plan.working.changes.length > 0 && (
                <div className="diff">
                  <ul className="diff-list">{plan.working.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
                  {viewer && plan.agreed?.your_total && plan.working.your_total && (
                    <p className="diff-meta">Your total {plan.agreed.your_total} → {plan.working.your_total}.</p>
                  )}
                </div>
              )}
            </>
          )}

          <Matrix plan={plan} colorOf={colorOf} />

          {showGate && (
            <div className={`gate ${plan.gate!.length ? 'blocked' : 'clear'}`}>
              <h3 className="plan-section">Commit gate</h3>
              {plan.gate!.length === 0
                ? <p>All checks pass: current version, every hard condition, unchanged inputs, every confirmation.</p>
                : <GateList gate={plan.gate!} />}
            </div>
          )}

          {!viewer && <Repair plan={plan} />}

          {!plan.project && plan.tasks.length > 0 && (
            <>
              <h3 className="plan-section">Tasks</h3>
              <TaskGraph tasks={plan.tasks} colorOf={colorOf} hidden={hiddenFrom.length > 0} />
            </>
          )}

          <Conditions list={conditions} viewer={viewer} colorOf={colorOf} members={members} />

          {plan.observations.length > 0 && (
            <>
              <h3 className="plan-section">Outside changes</h3>
              <ul className="events">{plan.observations.map((o, i) => <li key={i}>{o}</li>)}</ul>
            </>
          )}

          {plan.bookings.length > 0 && (
            <>
              <h3 className="plan-section">Bookings <span className="section-sub">simulated, after commit only</span></h3>
              <ul className="bookings">{plan.bookings.map((b, i) => <li key={i}>{b}</li>)}</ul>
            </>
          )}

          {!viewer && plan.audit && plan.audit.length > 0 && (
            <>
              <h3 className="plan-section">Recent history</h3>
              <ol className="history">
                {plan.audit.slice(0, 8).map((a) => (
                  <li key={a.seq}>
                    <span className="when">{fmtTime(a.at)}</span>
                    <span className="what">
                      {a.who && <><Dot color={colorOf(a.who)} /><span className="sr-only">{a.who}: </span></>}
                      {a.detail}
                      {a.said && <q className="spoken">{a.said}</q>}
                    </span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </>
      )}
      {demoConsole}
    </section>
  );
}
