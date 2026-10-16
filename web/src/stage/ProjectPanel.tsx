import { useCallback, useEffect, useState } from 'react';
import { api, type Member, type PlanView, type ProjectView, type ProjectWork } from '../api';

export function ProjectPanel({ plan, member, members }: { plan: PlanView; member?: string | null; members: Member[] }) {
  const actor = member || plan.organizer_id || members.find((m) => m.name === plan.organizer)?.id || '';
  const [view, setView] = useState<ProjectView | null>(null);
  const [stream, setStream] = useState('');
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [title, setTitle] = useState('');
  const [owner, setOwner] = useState(actor);
  const [duration, setDuration] = useState(60);
  const [due, setDue] = useState('');
  const [workstream, setWorkstream] = useState('Delivery');
  const [edit, setEdit] = useState<ProjectWork | null>(null);
  const [dependencies, setDependencies] = useState<string[]>([]);
  const [resourceDemand, setResourceDemand] = useState<Record<string, number>>({});
  const [stage, setStage] = useState('');
  const [budget, setBudget] = useState('');
  const load = useCallback(async () => {
    try { setView(await api.project(actor, plan.id, stream, offset)); setError(''); }
    catch (err) { setError((err as Error).message); }
  }, [actor, plan.id, stream, offset]);
  useEffect(() => { void load(); }, [load, plan.version, plan.status]);
  const act = async (tool: string, args: Record<string, unknown>) => {
    setBusy(true); setError(''); setMessage('');
    try { const r = await api.projectAction(actor, tool, { plan: plan.id, ...args }); setMessage([r.message || r.outcome, ...(r.problems || [])].join(' ')); await load(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  const local = (d: string) => new Intl.DateTimeFormat('en-CA', { timeZone: view?.timezone, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(d));
  if (!view) return <section className="project-panel"><h3>Project work</h3><p role="alert">{error || 'Loading project…'}</p></section>;
  const rev = view.revision;
  const schedule = new Map(rev?.schedule.map((s) => [s.item_id, s]) || []);
  const pick = (w: ProjectWork) => { setEdit(w); setTitle(w.title); setOwner(w.owner_id); setDuration(w.spec.duration_minutes); setDue(w.spec.due); setWorkstream(w.spec.workstream); setDependencies([...w.spec.depends_on]); setResourceDemand({...w.spec.resources}); };
  return <section className="project-panel" aria-label="Project workspace">
    <div className="project-top"><div><h3>{view.stage}</h3><p>{view.window_start} · {view.days}-day {plan.lifecycle || 'bounded'} window · {view.timezone}</p></div>
      <span className="status-chip">{rev ? `${rev.status}${rev.fresh ? '' : ' · stale'}` : 'Draft work'}</span></div>
    <p>{view.confirmation_totals.satisfied}/{view.confirmation_totals.required} commitments and authorizations satisfied · {view.total} open work units{rev ? ` · ${view.currency} ${(rev.cost_minor / 100).toFixed(2)}` : ''}</p>
    <div className="project-actions"><button disabled={busy} onClick={() => void act('repair_project', {})}>Prepare repair</button><button disabled={busy || !rev || !rev.fresh} onClick={() => void act('request_confirmations', {})}>Request commitments</button><button disabled={busy || rev?.status !== 'awaiting'} onClick={() => void act('commit_plan', {})}>Commit stage</button></div>
    {message && <p className="phone-ok" role="status">{message}</p>}{error && <p className="phone-error" role="alert">{error}</p>}
    {rev && <p className="proof">CP-SAT intervals · {rev.proof.optimal ? 'Lexicographic optimum proven' : 'Feasible; optimum not proven'} · {Math.round(rev.proof.ms)} ms{rev.proof.objective_order.length > 1 ? ` · ${rev.proof.objectives[0]} changed starts, ${rev.proof.objectives[1]} minutes moved` : ''}</p>}
    <label className="project-filter">Workstream <select value={stream} onChange={(e) => { setStream(e.target.value); setOffset(0); }}><option value="">All workstreams</option>{view.workstreams.map((s) => <option key={s}>{s}</option>)}</select></label>
    <div className="project-table-wrap"><table className="project-table"><thead><tr><th>Work / milestone</th><th>Owner</th><th>Due / effort</th><th>Schedule</th><th>Resources</th><th>State</th></tr></thead><tbody>
      {view.work.map((w) => { const s = schedule.get(w.id); return <tr key={w.id}><th scope="row"><button className="work-link" onClick={() => pick(w)}>{w.title}</button><small>{w.spec.workstream}{w.spec.milestone ? ` / ${w.spec.milestone}` : ''}{w.recurrence_id ? ` · occurrence ${w.recurrence_id}` : ''}</small></th><td>{w.owner}</td><td>{w.spec.due}<small>{w.spec.duration_minutes === 0 ? 'Milestone' : `${w.spec.duration_minutes} min`}</small></td><td>{s ? <>{local(s.start)}<small>→ {local(s.end)}</small></> : 'Unscheduled'}</td><td>{Object.entries(w.spec.resources).map(([r, n]) => `${r} × ${n}`).join(', ') || 'Owner only'}</td><td>{w.status}<button className="work-link" disabled={busy} onClick={() => void act('propose_change', { item: w.id, status: 'done' })}>Mark done</button></td></tr>; })}
      {!view.work.length && <tr><td colSpan={6}>Add work units, then prepare a schedule.</td></tr>}
    </tbody></table></div>
    <div className="project-actions"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button><span>{offset + 1}–{Math.min(offset + view.work.length, view.total)} / {view.total}</span><button disabled={view.next_offset === null} onClick={() => setOffset(view.next_offset || 0)}>Next</button></div>
    {rev && <details><summary>Commitments and stage authorization</summary><ul className="todo-list">{rev.confirmations.map((c) => <li key={c.scope_id}>{c.person} · {c.kind === 'authorization' ? 'Stage authorization' : view.work.find((w) => w.id === c.item)?.title || 'Work commitment'} · <strong>{c.status}</strong></li>)}</ul></details>}
    <details open={!!edit}><summary>{edit ? `Edit ${edit.title}` : 'Add or edit work'}</summary><form className="project-form" onSubmit={(e) => { e.preventDefault(); void act('upsert_work', { title, owner, expected_version: view.version, ...(edit ? { item: edit.id } : {}), spec: { duration_minutes: duration, ...(due ? { due } : {}), workstream, depends_on: dependencies, resources: resourceDemand } }); }}>
      <label>Title<input required value={title} onChange={(e) => setTitle(e.target.value)} /></label><label>Owner<select value={owner} onChange={(e) => setOwner(e.target.value)}>{members.filter((m) => plan.visible_to.includes(m.name)).map((m) => <option value={m.id} key={m.id}>{m.name}</option>)}</select></label>
      <label>Due date<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label><label>Minutes (0 milestone)<input type="number" min="0" max="480" step="15" value={duration} onChange={(e) => setDuration(Number(e.target.value))} /></label><label>Workstream<input required value={workstream} onChange={(e) => setWorkstream(e.target.value)} /></label>
      <label>Depends on (multiple)<select multiple value={dependencies} onChange={(e) => setDependencies(Array.from(e.target.selectedOptions).map((o) => o.value))}>{view.work.filter((w) => w.id !== edit?.id).map((w) => <option key={w.id} value={w.id}>{w.title}</option>)}{dependencies.filter((d) => !view.work.some((w) => w.id === d)).map((d, i) => <option key={d} value={d}>Existing dependency outside this page {i+1}</option>)}</select></label>
      {view.resources.map((r) => <label key={r.id}>{r.name} units (0–{r.capacity})<input type="number" min="0" max={r.capacity} value={resourceDemand[r.id] || 0} onChange={(e) => setResourceDemand((previous) => { const next={...previous}; const units=Number(e.target.value); if(units) next[r.id]=units; else delete next[r.id]; return next; })} /></label>)}<button disabled={busy || !title}>{edit ? 'Save draft changes' : 'Add draft work'}</button><button type="button" onClick={() => { setEdit(null); setTitle(''); setDependencies([]); setResourceDemand({}); }}>New work</button>
    </form></details>
    <details><summary>Stage policy and resources</summary><p>Approvers: {view.config.approvers.map((m) => members.find((p) => p.id === m)?.name || m).join(', ') || 'Configure an approver'} · confirmations valid for {view.config.approval_days} days.</p>
      <ul>{view.resources.map((r) => <li key={r.id}>{r.name}: capacity {r.capacity}</li>)}</ul>
      <form className="project-form" onSubmit={(e) => { e.preventDefault(); void act('configure_project', { changes: { ...(stage ? { stage } : {}), ...(budget ? { budget_minor: Math.round(Number(budget) * 100) } : {}) }, expected_version: view.version }); }}><label>Stage<input value={stage} placeholder={view.stage} onChange={(e) => setStage(e.target.value)} /></label><label>Budget ({view.currency})<input type="number" min="0" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="Unlimited" /></label><button disabled={busy || (!stage && !budget)}>Save stage policy</button></form>
    </details>
  </section>;
}
