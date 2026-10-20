import { useEffect, useState } from 'react';
import { HumanClient, type Inbox, type PlanningChoices, type DecisionCard, type PendingAction, type ActionPreview } from '../api';
import { AgreementCare } from './AgreementCare';

export function DecisionHub({ client, inbox, planIds, onRefresh }: { client: HumanClient; inbox: Inbox; planIds: Set<string>; onRefresh?: (inbox: Inbox) => void }) {
  const [state, setState] = useState(inbox);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [feed, setFeed] = useState('');
  const [choices, setChoices] = useState<Record<string, PlanningChoices>>({});
  const [title, setTitle] = useState('');
  const [day, setDay] = useState('');
  const [group, setGroup] = useState('');
  const [draft, setDraft] = useState('');
  const [activity, setActivity] = useState('');
  const [place, setPlace] = useState('');
  const [start, setStart] = useState('18:00');
  const [end, setEnd] = useState('20:00');
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [preview, setPreview] = useState<{tool:string;args:Record<string,unknown>;data:ActionPreview} | null>(null);
  const [facts, setFacts] = useState<Record<string,PlanningChoices>>({});
  const [hypotheses, setHypotheses] = useState<Record<string,PlanningChoices>>({});
  const [startFold,setStartFold] = useState('');
  const [endFold,setEndFold] = useState('');
  useEffect(() => { try { setPending(client.pendingActions()); } catch (e) { setError((e as Error).message); } setPreview(null); }, [client]);
  useEffect(() => setState(inbox), [inbox]);
  async function run(fn: () => Promise<unknown>, success: string) {
    setBusy(true); setError(''); setMessage('');
    try { const result = await fn(); const fresh = await client.inbox(); setState(fresh); onRefresh?.(fresh);
      const outcome = result as { action_receipt?: { message?: string }; message?: string } | undefined;
      setMessage(outcome?.action_receipt?.message ?? outcome?.message ?? success); }
    catch (e) { setError(e instanceof Error ? e.message : 'Please try again.'); }
    finally { setBusy(false); try { setPending(client.pendingActions()); } catch (e) { setError((e as Error).message); } }
  }
  async function act(tool: string, args: Record<string, unknown>, card?: DecisionCard) {
    const original = client.pendingActions().find(p => JSON.stringify([p.tool,p.arguments]) === JSON.stringify([tool,args]));
    if (original) return client.recoverAction(original);
    const version = card?.version_token ?? (typeof args.plan === 'string' ? (await client.resolvePlan(args.plan)).version : undefined);
    if (version && ['propose_change','decide_change','link_items','bring_list','costs','rsvp'].includes(tool)) {
      const data = await client.previewAction(tool,args,version);
      setPreview({tool,args,data});
      return {outcome:'preview',message:'Review the changes below, then choose Save this change.'};
    }
    return client.act(tool,args,crypto.randomUUID(),version);
  }
  const cards = state.decision_inbox?.cards.filter(c => planIds.has(c.plan) || c.plan === draft) ?? [];
  const defaultGroup = state.plans.find(p => planIds.has(p.id))?.group_id ?? state.groups?.[0]?.id;
  return <section className="decision-hub" aria-label="Your decision inbox">
    <h3 className="phone-h">Your next steps</h3>
    {error && <p className="phone-error" role="alert">{error}</p>}
    {message && <p className="phone-ok" role="status">{message}</p>}
    <AgreementCare client={client} plans={state.plans.filter(p=>planIds.has(p.id)&&p.planning)} onChanged={async()=>{const fresh=await client.inbox();setState(fresh);onRefresh?.(fresh);}} />
    {pending.length > 0 && <article className="decision-card" aria-label="Pending actions"><h4>Check whether your changes were saved</h4><p>A connection ended before the result was known. Your original request is kept on this device.</p>
      {pending.map(op => <div key={op.key} className="decision-action"><small>Started {new Date(op.createdAt).toLocaleString()}</small><div className="phone-row">
        <button className="pbtn" disabled={busy} onClick={() => void run(() => client.recoverAction(op),'Saved result checked.')}>Check saved result</button>
        <button className="pbtn" disabled={busy} onClick={() => void run(() => client.recoverAction(op,true),'Original request recovered.')}>Recover original request</button>
      </div></div>)}</article>}
    {preview && <article className="decision-card" aria-label="Change preview"><h4>Review this change</h4>
      {preview.data.changes.map(c => <div className="decision-action" key={c.item}><p>Before: {publicChange(c.before)}</p><p>After: {publicChange(c.after)}</p></div>)}
      {!preview.data.changes.length && <p>The preview found no activity or task changes. Review the server outcome when saving.</p>}
      <p>Affected people: {preview.data.affected_participants.join(', ') || 'See the current record'}</p>
      <p>Keep valid confirmations: {preview.data.approval_impact.carried.map(a => `${a.member} · ${a.activity}`).join('; ') || 'None'}</p>
      <p>Need an answer: {preview.data.approval_impact.needs_confirmation.map(a => `${a.member} · ${a.activity}`).join('; ') || 'None'}</p>
      <div className="phone-row"><button className="pbtn" disabled={busy} onClick={() => void run(async () => { const current=preview; setPreview(null); return client.act(current.tool,current.args,crypto.randomUUID(),current.data.expected); },'Change saved.')}>Save this change</button>
      <button className="pbtn" disabled={busy} onClick={() => setPreview(null)}>Cancel preview</button></div>
    </article>}
    {!cards.length && <p className="phone-empty">Nothing is waiting for you.</p>}
    {cards.map(card => <article className="decision-card" key={card.plan}>
      <h4>{card.title}</h4>
      {card.changed_since && <small>Updated {new Date(card.changed_since).toLocaleString()}</small>}
      {card.actions.map(a => <div className="decision-action" key={`${a.kind}:${a.id}`}>
        <strong>{a.text}</strong><p>{a.why} {a.effect}</p>
        {a.kind === 'confirmation' && <a href={`#decision-${a.id}`}>Review your confirmation</a>}
        {a.kind === 'task' && <button className="pbtn" disabled={busy} onClick={() => void run(() => act('propose_change', { plan: card.plan, item: a.id, status: 'done' }, card), 'Completion recorded. Review the server outcome below.')}>Mark done</button>}
        {a.kind === 'request' && <div className="phone-row">{['accept', 'reject'].map(decision => <button className="pbtn" key={decision} disabled={busy} onClick={() => void run(() => act('decide_change', { request: a.id, decision }, card), 'Decision recorded.')}>{decision === 'accept' ? 'Accept' : 'Decline'}</button>)}</div>}
        {a.kind === 'question' && <div className="phone-row">{a.options?.map(option => <button className="pbtn" disabled={busy} key={option} onClick={() => void run(() => act('answer_followup', { followup: a.id, choice: option }, card), 'Your answer was recorded.')}>{option}</button>)}</div>}
      </div>)}
      {card.updates.map(text => <p className="decision-update" key={text}>{text}</p>)}
      {card.update_count > card.updates.length && <small>{card.update_count} updates grouped here.</small>}
      <details><summary>Notification preferences</summary><Preferences card={card} busy={busy} save={values => void run(() => client.preferences(card.plan, values), 'Preferences saved. Your decisions stay visible.')} /></details>
    </article>)}
    <details className="decision-card"><summary>Plan something new</summary>
      <form onSubmit={e => { e.preventDefault(); const entered=new FormData(e.currentTarget); void run(async () => {
        const r = await act('create_plan', { template: 'general_event', title:String(entered.get('title')??''), start_date:String(entered.get('start_date')??''), group: String(entered.get('group') || defaultGroup || '') });
        if (r.plan?.id) setDraft(r.plan.id);
      }, 'Draft created. Add what people will do, then look for feasible choices.'); }}>
        <label>Plan name<input name="title" required value={title} onChange={e => setTitle(e.target.value)} /></label>
        <label>Date<input name="start_date" type="date" required value={day} onInput={e => setDay(e.currentTarget.value)} onChange={e => setDay(e.target.value)} /></label>
        <label>Group<select name="group" value={group || defaultGroup || ''} onChange={e => setGroup(e.target.value)}>{state.groups?.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
        <button className="pbtn" disabled={busy}>Create draft</button>
      </form>
      {draft && <form onSubmit={e => { e.preventDefault(); const entered=new FormData(e.currentTarget); const sf=entered.get('start_fold'),ef=entered.get('end_fold'); void run(() => act('propose_change', { plan: draft, new_type: 'activity', title:String(entered.get('activity')??''), start:String(entered.get('start')??''), end:String(entered.get('end')??''), place:String(entered.get('place')??''), participants: ['everyone'], ...(sf!==''&&sf!==null?{start_fold:Number(sf)}:{}), ...(ef!==''&&ef!==null?{end_fold:Number(ef)}:{}) }), 'Activity added to the draft. Each person still needs to confirm.'); }}>
        <label>Activity<input name="activity" required value={activity} onChange={e => setActivity(e.target.value)} /></label>
        <label>Place<input name="place" required value={place} onChange={e => setPlace(e.target.value)} /></label>
        <label>Starts<input name="start" type="time" required value={start} onInput={e => setStart(e.currentTarget.value)} onChange={e => setStart(e.target.value)} /></label>
        <label>Ends<input name="end" type="time" required value={end} onInput={e => setEnd(e.currentTarget.value)} onChange={e => setEnd(e.target.value)} /></label>
        <details><summary>Repeated time when clocks move back</summary><label>Start occurrence<select name="start_fold" value={startFold} onChange={e => setStartFold(e.target.value)}><option value="">Ask if ambiguous</option><option value="0">First occurrence</option><option value="1">Second occurrence</option></select></label><label>End occurrence<select name="end_fold" value={endFold} onChange={e => setEndFold(e.target.value)}><option value="">Ask if ambiguous</option><option value="0">First occurrence</option><option value="1">Second occurrence</option></select></label></details>
        <p>For a place you enter yourself, opening hours and prices need checking.</p><button className="pbtn" disabled={busy}>Add activity</button>
      </form>}
    </details>
    <details className="decision-card"><summary>Choose a feasible plan</summary>
      <p>A vote can guide the organizer. Selecting an option prepares it for personal confirmation.</p>
      {state.plans.filter(p => p.group_id === defaultGroup && (p as typeof p & { planning?: boolean; you_organize?: boolean }).planning && (p as typeof p & { you_organize?: boolean }).you_organize).map(p => <div key={p.id}>
        <strong>{p.title}</strong><button className="pbtn" disabled={busy} onClick={() => void run(async () => { const loaded = await client.planning(p.id); setChoices(c => ({ ...c, [p.id]: loaded })); }, 'Feasible options loaded. No confirmations were sent.')}>Find options</button>
        {choices[p.id]?.message && <p>{choices[p.id].message.replace(': .', ':').replace('send it with request_confirmations.', 'ask each person to confirm below.')}</p>}
        {choices[p.id]?.alternatives?.map(c => <div className="decision-action" key={c.candidate}><strong>{c.label} · {c.cost_each_after}</strong><p>{c.changes.join('; ') || 'Keep the current arrangement'}</p><button className="pbtn" disabled={busy} onClick={() => void run(() => client.planning(p.id, c.candidate), 'Option selected. Ask the organizer to send confirmation requests next.')}>Select this option</button></div>)}
        <div className="phone-row"><button className="pbtn" disabled={busy} onClick={() => void run(async () => {const r=await client.planning(p.id,undefined,'relaxations');setHypotheses(h=>({...h,[p.id]:r}));return r;},'Ideas checked.')}>Explore possible changes</button>
        <button className="pbtn" disabled={busy} onClick={() => void run(async () => {const r=await client.planning(p.id,undefined,'facts');setFacts(f=>({...f,[p.id]:r}));return r;},'Sources loaded.')}>Check fact sources</button></div>
        {hypotheses[p.id] && <div><p>{hypotheses[p.id].message}</p>{hypotheses[p.id].suggestions?.map((h,i)=><p key={i}>Hypothetical: {h.changes.join('; ')}</p>)}</div>}
        {facts[p.id]?.facts?.map(f=><div className="decision-action" key={f.id}><strong>{f.place_id?.replaceAll('_',' ')} · {f.source}</strong><p>{factValue(f.value)} · {f.freshness}</p><small>Verified: {f.verified_at?new Date(f.verified_at).toLocaleString():'Needs checking'} · Expires: {f.valid_until?new Date(f.valid_until).toLocaleString():'Not supplied'}</small></div>)}
        <p>{p.status === 'COMMITTED' ? 'The agreed version is committed.' : 'Each person answers on their own page before the organizer can commit.'}</p>
        <div className="phone-row">
          <button className="pbtn" disabled={busy} onClick={() => void run(() => act('request_confirmations', { plan: p.id }), 'Confirmation requests sent. Each person answers for themselves.')}>Ask each person to confirm</button>
          <button className="pbtn" disabled={busy} onClick={() => void run(() => act('commit_plan', { plan: p.id }), 'Review the server outcome below.')}>Commit agreed plan</button>
        </div>
      </div>)}
    </details>
    <details className="decision-card"><summary>Your recent actions</summary>
      {state.action_receipts?.slice(0, 8).map(r => <div className="decision-action" key={r.id}><strong>{r.actor} · {r.subjects.join(', ')}</strong><p>{r.message}</p><small>{new Date(r.created_at).toLocaleString()}</small>{r.reversible && <button className="pbtn" disabled={busy} onClick={() => void run(() => client.correctAction(r.id), 'Correction recorded. Later changes are protected.')}>Correct this action</button>}</div>)}
    </details>
    <details className="decision-card"><summary>Your calendar</summary><p>Subscribe to your agreed activities. Changes update the same events; drafts stay out.</p>
      <button className="pbtn" disabled={busy} onClick={() => void run(async () => { const r = await client.calendarLink(); setFeed(new URL(r.url, location.origin).href); }, 'Private calendar link created. Keep it private.')}>Create subscription link</button>
      {feed && <label>Private subscription link<input readOnly value={feed} onFocus={e => e.target.select()} /></label>}
      {state.calendar_links?.map(l => <div key={l.id}><small>Created {new Date(l.created_at).toLocaleDateString()}</small><button className="pbtn" disabled={busy} onClick={() => void run(async () => { await client.revokeCalendar(l.id); setFeed(''); }, 'Subscription revoked.')}>Revoke link</button></div>)}
    </details>
  </section>;
}

function publicChange(value: unknown): string {
  if (!value || typeof value!=='object') return 'None';
  const v=value as Record<string,unknown>;
  const clock=(n:unknown)=>typeof n==='number'?`${String(Math.floor(n/60)%24).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:'';
  return [v.title??v.label,v.value,v.status,v.start!==undefined?`${clock(v.start)}–${clock(v.end)} ${v.timezone??''}`:'',v.place,v.owner,v.bringing,v.payer,typeof v.amount_minor==='number'?`${(v.amount_minor/100).toFixed(2)} ${v.currency??''}`:'',v.answer,Array.isArray(v.participants)?v.participants.join(', '):''].filter(Boolean).join(' · ');
}

function factValue(value: unknown): string {
  if (!value || typeof value!=='object') return 'Needs checking';
  const v=value as Record<string,unknown>;
  const clock=(n:unknown)=>typeof n==='number'?`${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:'';
  return [typeof v.price_minor==='number'?`Estimated price ${(v.price_minor/100).toFixed(2)} per person`:'',typeof v.capacity==='number'?`Capacity ${v.capacity}`:'',v.open_min!==undefined?`Hours ${clock(v.open_min)}–${clock(v.close_min)}`:'',typeof v.summary==='string'?v.summary:''].filter(Boolean).join(' · ') || 'Source details need checking';
}

function Preferences({ card, busy, save }: { card: DecisionCard; busy: boolean; save: (v: Record<string, unknown>) => void }) {
  const [muted, setMuted] = useState(!!card.preferences.muted);
  const [digest, setDigest] = useState(!!card.preferences.digest);
  const [start, setStart] = useState(card.preferences.quiet_start?.toString() ?? '');
  const [end, setEnd] = useState(card.preferences.quiet_end?.toString() ?? '');
  return <form onSubmit={e => { e.preventDefault(); save({ muted, digest, quiet_start: start === '' ? null : Number(start), quiet_end: end === '' ? null : Number(end), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }); }}>
    <label><input type="checkbox" checked={muted} onChange={e => setMuted(e.target.checked)} />Mute routine updates</label>
    <label><input type="checkbox" checked={digest} onChange={e => setDigest(e.target.checked)} />Group updates into a digest</label>
    <label>Quiet from (hour)<input type="number" min="0" max="23" value={start} onChange={e => setStart(e.target.value)} /></label>
    <label>Until (hour)<input type="number" min="0" max="23" value={end} onChange={e => setEnd(e.target.value)} /></label>
    <button className="pbtn" disabled={busy}>Save preferences</button>
  </form>;
}
