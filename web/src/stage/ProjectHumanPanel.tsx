import { useState } from 'react';
import type { ProjectHumanItem, ProjectHumanPlan } from '../api';

interface Props {
  items: ProjectHumanItem[]; projects: ProjectHumanPlan[]; busy: boolean;
  onAnswer: (id: string, decision: 'approve' | 'decline') => void;
  onRevoke: (id: string) => void;
  onCalendar: (plan: string, calendar: ProjectHumanPlan['calendar']) => void;
  onRole: (group: string, target: string, role: string, grant: boolean) => void;
}

function CalendarForm({ project, busy, onSave }: { project: ProjectHumanPlan; busy: boolean; onSave: Props['onCalendar'] }) {
  const [start, setStart] = useState(project.calendar.start);
  const [end, setEnd] = useState(project.calendar.end);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  return <details><summary>My availability · {project.title}</summary><form className="project-form" onSubmit={(e) => {
    e.preventDefault();
    onSave(project.id, { ...project.calendar, start, end, unavailable: [...project.calendar.unavailable, ...(from && to ? [[new Date(from).toISOString(), new Date(to).toISOString()] as [string,string]] : [])] });
  }}><label>Work starts<input type="time" required value={start} onChange={(e) => setStart(e.target.value)} /></label><label>Work ends<input type="time" required value={end} onChange={(e) => setEnd(e.target.value)} /></label>
    <label>Busy from<input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></label><label>Busy until<input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></label><p>Busy timestamps use your browser time zone ({Intl.DateTimeFormat().resolvedOptions().timeZone}); only your schedule availability is saved.</p>
    <button className="pbtn" disabled={busy || (Boolean(from) !== Boolean(to)) || (Boolean(from) && new Date(to) <= new Date(from))}>Save my availability</button>
    {project.calendar.unavailable.length > 0 && <button type="button" className="pbtn" disabled={busy} onClick={() => onSave(project.id, { ...project.calendar, start, end, unavailable: [] })}>Clear my busy windows</button>}
  </form></details>;
}

function Roles({ project, busy, onRole }: { project: ProjectHumanPlan; busy: boolean; onRole: Props['onRole'] }) {
  const [target, setTarget] = useState(project.members[0]?.id || '');
  const [role, setRole] = useState('approver');
  return <details><summary>Workspace roles · {project.workspace}</summary><div className="project-form"><label>Member<select value={target} onChange={(e) => setTarget(e.target.value)}>{project.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label><label>Role<select value={role} onChange={(e) => setRole(e.target.value)}><option value="approver">Stage approver</option><option value="lead">Project lead</option></select></label><button className="pbtn" disabled={busy} onClick={() => onRole(project.workspace, target, role, true)}>Grant role</button><button className="pbtn" disabled={busy} onClick={() => onRole(project.workspace, target, role, false)}>Revoke role</button></div></details>;
}

export function ProjectHumanPanel({ items, projects, busy, onAnswer, onRevoke, onCalendar, onRole }: Props) {
  if (!projects.length) return null;
  const current = items.filter((item) => item.current !== false);
  const previous = items.filter((item) => item.current === false);
  const card = (item: ProjectHumanItem) => {
      const project = projects.find((p) => p.id === item.plan_id);
      const zone = project?.timezone || 'UTC';
      const fmt = (stamp: string) => new Intl.DateTimeFormat('en-CA',{timeZone:zone,dateStyle:'medium',timeStyle:'short'}).format(new Date(stamp));
      const schedules = Array.isArray(item.payload.schedule) ? item.payload.schedule : [item.payload.schedule];
      return <article key={item.id} className={`phone-item ${item.can_answer && !['approved','carried_forward'].includes(item.status) ? 'pending' : ''}`}><p><strong>{item.kind === 'authorization' ? 'Authorize stage' : 'Commit to my work'}</strong> · {item.plan} / {item.payload.stage}</p>
        <ul className="project-human-schedule">{schedules.map((s) => <li key={s.item_id}><strong>{s.title}</strong><br />{fmt(s.start)} → {fmt(s.end)} ({zone}){Object.keys(s.resources).length ? ` · ${Object.keys(s.resources).join(', ')}` : ''}</li>)}</ul>
        {item.payload.cost_minor !== undefined && <p>Stage cost: {project?.currency || 'Project currency'} {(item.payload.cost_minor / 100).toFixed(2)}.</p>}
        <p className="phone-note">{item.status} · valid until {new Date(item.expires_at).toLocaleDateString()}</p>
        <div className="phone-row">{item.can_answer && !['approved','carried_forward'].includes(item.status) && <><button className="pbtn primary-p" disabled={busy} onClick={() => onAnswer(item.id, 'approve')}>{item.kind === 'authorization' ? 'Authorize stage' : 'Give my commitment'}</button><button className="pbtn" disabled={busy} onClick={() => onAnswer(item.id, 'decline')}>Decline</button></>}{(item.can_revoke || ['approved','carried_forward'].includes(item.status)) && <button className="pbtn" disabled={busy} onClick={() => onRevoke(item.id)}>Withdraw my answer</button>}</div>
      </article>;
  };
  return <section className="project-human"><h3 className="phone-h">Project commitments & authorization</h3><p className="phone-note">A work commitment covers your own assignment. Stage authorization covers the complete stage and budget. Read the exact schedule before answering.</p>
    {current.map(card)}
    {!current.length && <p className="phone-note">No current project schedule is waiting for your answer.</p>}
    {previous.length > 0 && <details><summary>Previously committed answers ({previous.length})</summary>{previous.map(card)}</details>}
    {projects.map((p) => <CalendarForm key={p.id} project={p} busy={busy} onSave={onCalendar} />)}
    {projects.filter((p, i, list) => p.admin && list.findIndex((x) => x.workspace === p.workspace) === i).map((p) => <Roles key={p.workspace} project={p} busy={busy} onRole={onRole} />)}
  </section>;
}
