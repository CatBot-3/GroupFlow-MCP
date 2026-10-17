import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, HumanClient, type BoardModel, type DecisionExtra, type Inbox, type ScenarioConfig, type TranscriptLine } from '../api';
import { sleep, typeInto, urlParam, type EchoControls } from '../App';
import { DemoBar } from '../components/DemoBar';
import { Echo, type EchoBadges } from '../components/Echo';
import { PhonePage } from './PhonePage';
import { PlanBoard, type PlanTab } from './PlanBoard';
import { parseWhen } from './Timeline';
import { MemoryExplorer } from './MemoryExplorer';
import { OutcomeStrip } from './OutcomeStrip';

interface Props {
  scenario: ScenarioConfig;
  transcripts: Record<string, TranscriptLine[]>;
  echo: EchoControls;
  tick: number;
  resetKey: number;
  onReset: (target?: string) => Promise<void>;
  onChanged: () => void;
  presentation?: boolean;
}

// A first line to try on each Echo, keyed by group and person, so it fits the plans that person has in that group.
const SUGGESTIONS: Record<string, string> = {
  'family.alice': "What's new?",
  'family.bob': 'What do I need to do?',
  'family.charlie': "What's holding up the invitations?",
  'family.linda': 'What are we doing this weekend?',
  'friends.alice': "What's new with Friday dinner?",
  'friends.bob': 'What do I need to confirm?',
  'friends.chen': 'Where are we having dinner on Friday?',
  'friends.dana': 'What do I owe for Friday?',
  'cottage.dana': 'Who still has to confirm the cottage weekend?',
  'cottage.chen': 'Where are we eating on Saturday night?',
  'cottage.omar': 'What are we doing on Saturday?',
  'cottage.priya': 'What time is the hike?',
  'team.priya': 'When is the design review?',
  'team.marcus': 'What do I need to bring to the design review?',
  'team.jun': 'Who is coming to the design review?',
  'team.sofia': 'What time does the design review start?',
  'study.chen': 'Who is preparing what for CS 341?',
  'study.sam': 'What am I preparing for the study group?',
  'study.noor': 'Where is the Wednesday session?',
  'study.jun': 'When are we studying this week?',
};

type Face = 'alexa' | 'page';
const DONE: Record<string, string> = { approved: 'Confirmed. Recorded with a one-time challenge.', rejected: 'Declined. The organizer will see it.', conditional: 'Confirmed with your condition.' };

export function GroupStage({ scenario, transcripts, echo, tick, resetKey, onReset, onChanged, presentation = false }: Props) {
  const members = scenario.members;
  const script = scenario.script;
  const [inspector, setInspector] = useState<BoardModel | null>(null);
  const [memberView, setMemberView] = useState<BoardModel | null>(null);
  // A link may open this group on a given view, plan and set of own pages (only for the group it names).
  const linked = urlParam('group') === scenario.id;
  const [viewAs, setViewAs] = useState((linked && urlParam('view') && (!presentation || members.some((m) => m.id === urlParam('view'))) && urlParam('view')) || (presentation ? members[0].id : 'all'));
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [planId, setPlanId] = useState((linked && urlParam('plan')) || scenario.plans[0]?.id || '');
  const [faces, setFaces] = useState<Record<string, Face>>({});
  const [inboxes, setInboxes] = useState<Record<string, Inbox | null>>({});
  const [phoneBusy, setPhoneBusy] = useState<Record<string, boolean>>({});
  const [phoneErr, setPhoneErr] = useState<Record<string, string | null>>({});
  const [phoneMsg, setPhoneMsg] = useState<Record<string, string | null>>({});
  const [, setSignedTick] = useState(0);
  const [step, setStep] = useState(() => (linked ? Math.max(0, Number(urlParam('step')) || 0) : 0));
  const [playing, setPlaying] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [place, setPlace] = useState(scenario.places[0]?.id ?? '');
  const [closeDay, setCloseDay] = useState<number | null>(null);
  const [stage, setStage] = useState(scenario.stages[0] ?? '');
  const [skipNote, setSkipNote] = useState<string | null>(null);
  const viewAsRef = useRef(viewAs);
  viewAsRef.current = viewAs;
  const planRef = useRef(planId);
  planRef.current = planId;

  const sendFocused = useCallback(async (member: string, text: string) => {
    const turn = await echo.send(member, text, planRef.current || undefined);
    const created = turn?.calls.find((c) => c.name === 'create_plan' && c.ok)?.result as { plan?: { id?: string } } | undefined;
    if (created?.plan?.id) setPlanId(created.plan.id);
  }, [echo]);

  // One human session per person against the GroupFlow server's human-only API, reached through the simulator's same-origin
  // pass-through (/human), which forwards the person's own headers unchanged; the agent never sees it.
  const clients = useRef<Record<string, HumanClient>>({});
  const client = useCallback((id: string) => (clients.current[id] ??= new HumanClient('', id)), []);

  // Several refreshes can overlap (a sign-in, a phone answer and the event stream all trigger one); only the newest may
  // write, otherwise a slow earlier call that started before someone signed in would blank their page again.
  const inboxSeq = useRef(0);
  const loadInboxes = useCallback(async () => {
    const seq = ++inboxSeq.current;
    const entries = await Promise.all(members.map(async (m) => {
      const c = clients.current[m.id];
      if (!c?.signedIn) return [m.id, null] as const;
      try {
        return [m.id, await c.inbox()] as const;
      } catch (err) {
        setPhoneErr((e) => ({ ...e, [m.id]: (err as Error).message }));
        return [m.id, null] as const;
      }
    }));
    if (seq === inboxSeq.current) setInboxes(Object.fromEntries(entries));
  }, [members]);

  const refresh = useCallback(async () => {
    try {
      const target = viewAsRef.current;
      const [insp, mine] = await Promise.all([presentation ? Promise.resolve(null) : api.inspector(scenario.id), target === 'all' ? Promise.resolve(null) : api.view(target, planRef.current)]);
      // Presentation uses only the selected identity's MCP view. The admin
      // inspector remains available in the test lab.
      setInspector(presentation ? mine : insp);
      setMemberView(mine);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
    await loadInboxes();
  }, [scenario.id, loadInboxes, presentation]);

  useEffect(() => {
    void refresh();
  }, [tick, viewAs, refresh]);

  // A member's view is focused on the chosen plan, so switching plans asks their session again.
  useEffect(() => {
    if (viewAsRef.current !== 'all') void refresh();
  }, [planId, refresh]);

  useEffect(() => {
    if (inspector?.plans.some((p) => p.status === 'REPAIRING')) {
      const t = setTimeout(() => void refresh(), 700);
      return () => clearTimeout(t);
    }
  }, [inspector, refresh]);

  const mounted = useRef(false);
  useEffect(() => {
    // Not on the first render: a link may have opened the page at a later step of the story.
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    setStep(0);
    setNote(null);
    setFaces({});
    for (const c of Object.values(clients.current)) c.logout();
    setInboxes({});
    setPhoneErr({});
    setPhoneMsg({});
  }, [resetKey]);

  // ---------------------------------------------------------------- own-page actions

  const phone = useCallback(async (id: string, fn: (c: HumanClient) => Promise<unknown>, done?: string) => {
    setPhoneBusy((b) => ({ ...b, [id]: true }));
    setPhoneErr((e) => ({ ...e, [id]: null }));
    setPhoneMsg((e) => ({ ...e, [id]: null }));
    try {
      await fn(client(id));
      // Refresh this person's result immediately. Other pages refresh from
      // the event stream; do not hold one answer behind every person's inbox.
      ++inboxSeq.current;
      const own = client(id);
      const fresh = own.signedIn ? await own.inbox() : null;
      setInboxes((x) => ({ ...x, [id]: fresh }));
      if (done) setPhoneMsg((e) => ({ ...e, [id]: done }));
      setSignedTick((t) => t + 1);
      await api.notify({ member: id });
      onChanged();
      return true;
    } catch (err) {
      setPhoneErr((e) => ({ ...e, [id]: (err as Error).message }));
      setSignedTick((t) => t + 1);
      return false;
    } finally {
      setPhoneBusy((b) => ({ ...b, [id]: false }));
    }
  }, [client, onChanged]);

  const signIn = useCallback(async (id: string) => {
    const m = members.find((x) => x.id === id);
    const c = client(id);
    if (!c.signedIn) await phone(id, (cl) => cl.login(m?.demoPasscode ?? ''));
    return c;
  }, [members, client, phone]);

  useEffect(() => {
    const pages = linked ? (urlParam('pages') ?? '').split(',').filter((id) => members.some((m) => m.id === id)) : [];
    if (!pages.length) return;
    void (async () => {
      setFaces((f) => ({ ...f, ...Object.fromEntries(pages.map((id) => [id, 'page' as Face])) }));
      await Promise.all(pages.map((id) => signIn(id)));
      await loadInboxes();
    })();
    // Only once, when the page opens from a link.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------- guided demo

  const playNext = useCallback(async () => {
    if (playing || step >= script.length) return;
    const s = script[step];
    setPlaying(true);
    setNote(null);
    try {
      if (s.kind === 'say' && s.member && s.text) {
        if (presentation) setViewAs(s.member);
        setFaces((f) => ({ ...f, [s.member!]: 'alexa' }));
        await typeInto(echo.setDraft, s.member, s.text);
        const turn = await echo.send(s.member, s.text, planRef.current || undefined);
        if (!turn) throw new Error('This step did not complete. Retry it before continuing.');
      } else if (s.kind === 'event' && s.place_id) {
        await api.demoEvent(s.group ?? scenario.id, s.place_id, s.start, s.end, s.day);
        onChanged();
      } else if (s.kind === 'phones' || s.kind === 'verify') {
        const ids = s.kind === 'phones' ? s.members ?? [] : s.member ? [s.member] : [];
        const planIds = new Set(scenario.plans.map((p) => p.id));
        for (const id of ids) {
          if (presentation) setViewAs(id);
          setFaces((f) => ({ ...f, [id]: 'page' }));
          const c = await signIn(id);
          if (!c.signedIn) throw new Error('Sign-in did not complete. This step has not advanced.');
          await sleep(350);
          const inbox = c.signedIn ? await c.inbox() : null;
          setInboxes((x) => ({ ...x, [id]: inbox }));
          await sleep(450);
          if (s.kind === 'phones') {
            for (const item of (inbox?.pending ?? []).filter((it) => planIds.has(it.plan_id))) {
              if (!await phone(id, (cl) => cl.decide(item.scope_id, s.decision ?? 'approved'), DONE[s.decision ?? 'approved'])) throw new Error('A human answer was not recorded. Retry this step.');
              await sleep(250);
            }
            for (const item of (inbox?.project_items ?? []).filter((it) => planIds.has(it.plan_id) && it.can_answer && !['approved', 'carried_forward'].includes(it.status))) {
              if (!await phone(id, (cl) => cl.decideProject(item.id, s.decision === 'rejected' ? 'decline' : 'approve'), 'Your project answer was recorded.')) throw new Error('A project answer was not recorded. Retry this step.');
              await sleep(150);
            }
          } else {
            for (const cond of (inbox?.conditions ?? []).filter((x) => x.status === 'unverified')) {
              if (!await phone(id, (cl) => cl.verify(cond.id, cond.verify_digest), 'It counts now.')) throw new Error('A condition was not verified. Retry this step.');
              await sleep(250);
            }
          }
        }
      }
      setNote(s.note);
      setStep((n) => n + 1);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPlaying(false);
    }
  }, [playing, step, script, echo, scenario, signIn, phone, onChanged, presentation]);

  // ---------------------------------------------------------------- derived

  const groupPlanIds = useMemo(() => new Set((inspector?.plans ?? []).map((p) => p.id)), [inspector]);

  const pendingFor = useMemo(() => {
    const out: Record<string, number> = {};
    for (const p of inspector?.plans ?? []) {
      for (const [id, count] of Object.entries(p.project?.pending_by_person || {})) out[id] = (out[id] || 0) + count;
      for (const r of p.confirmations?.rows ?? []) {
        const m = members.find((x) => x.name === r.person);
        if (m) out[m.id] = (out[m.id] ?? 0) + Object.values(r.items).filter((v) => v === 'pending').length;
      }
    }
    return out;
  }, [inspector, members]);

  const badges = useMemo(() => {
    const out: Record<string, EchoBadges> = {};
    for (const m of members) out[m.id] = { decisions: 0, changes: 0 };
    for (const p of inspector?.plans ?? []) {
      for (const d of p.details) {
        const owner = members.find((m) => m.name === d.owner);
        if (owner) out[owner.id].decisions += d.pending.filter((x) => x.request).length;
        if (owner) out[owner.id].changes += d.flags.length;
      }
      for (const t of p.tasks) {
        const owner = members.find((m) => m.name === t.owner);
        if (owner) out[owner.id].decisions += t.pending.filter((x) => x.request).length;
        if (owner) out[owner.id].changes += t.flags.length;
      }
    }
    return out;
  }, [inspector, members]);

  const next = step < script.length ? script[step] : null;
  const cuedIds = new Set(next ? (next.kind === 'say' || next.kind === 'verify' ? [next.member] : next.kind === 'phones' ? next.members ?? [] : []) : []);
  const board = viewAs === 'all' ? inspector : memberView?.member === viewAs && memberView.focus === planId ? memberView : null;

  // Plans made on the spot show up in the inspector; keep the picker in step with them.
  const planTabs = useMemo<PlanTab[]>(() => {
    const seen = new Map<string, string>();
    for (const p of scenario.plans) seen.set(p.id, p.title);
    for (const p of inspector?.plans ?? []) if (!seen.has(p.id)) seen.set(p.id, p.title);
    return [...seen.entries()]
      .filter(([id]) => !inspector || inspector.plans.some((p) => p.id === id))
      .map(([id, title]) => {
        const view = inspector?.plans.find((p) => p.id === id);
        return { id, title: view?.title ?? title, hiddenFrom: view ? members.filter((m) => !view.visible_to.includes(m.name)).map((m) => m.name) : [] };
      });
  }, [scenario.plans, inspector, members]);
  const shownPlan = planTabs.some((p) => p.id === planId) ? planId : planTabs[0]?.id ?? planId;

  // Plans over several days: the console closes a place on one day, by default the day the plan uses it.
  const placeRef = scenario.places.find((x) => x.id === place);
  const placePlan = inspector?.plans.find((p) => p.id === placeRef?.planId);
  const dayOptions = useMemo(() => {
    if (!placePlan?.start_date || (placePlan.days ?? 1) < 2) return [];
    return Array.from({ length: placePlan.days ?? 1 }, (_, i) => {
      const d = new Date(`${placePlan.start_date}T12:00:00`);
      d.setDate(d.getDate() + i);
      return { i, label: d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) };
    });
  }, [placePlan?.start_date, placePlan?.days]);
  const usedDay = useMemo(() => {
    const acts = [...(placePlan?.agreed?.activities ?? []), ...(placePlan?.working?.activities ?? [])];
    const hit = acts.find((a) => a.place === placeRef?.name);
    const label = hit ? parseWhen(hit.when).day : '';
    const idx = label ? dayOptions.findIndex((o) => o.label.startsWith(label)) : -1;
    return idx >= 0 ? idx : 0;
  }, [placePlan, placeRef, dayOptions]);
  const day = closeDay ?? usedDay;

  const demoConsole = (
    <details className="console">
      <summary>Demo console <span className="section-sub">plays a trusted outside source; Alexa cannot do these</span></summary>
      {scenario.places.length > 0 && (
        <form className="console-row" onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api.demoEvent(scenario.id, place, '00:00', '23:59', dayOptions.length ? day : undefined);
            onChanged();
          } catch (err) {
            setError((err as Error).message);
          }
        }}>
          <label htmlFor={`place-${scenario.id}`}>Place</label>
          <select id={`place-${scenario.id}`} value={place} onChange={(e) => { setPlace(e.target.value); setCloseDay(null); }}>
            {scenario.places.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {dayOptions.length > 0 && (
            <>
              <label htmlFor={`day-${scenario.id}`}>on</label>
              <select id={`day-${scenario.id}`} value={day} onChange={(e) => setCloseDay(Number(e.target.value))}>
                {dayOptions.map((o) => <option key={o.i} value={o.i}>{o.label}</option>)}
              </select>
            </>
          )}
          <button type="submit" className="ghost">Report closed all day (verified)</button>
        </form>
      )}
      <div className="console-row">
        {scenario.stages.length > 0 && (
          <>
            <label htmlFor={`stage-${scenario.id}`}>Start from</label>
            <select id={`stage-${scenario.id}`} value={stage} onChange={(e) => setStage(e.target.value)}>
              {scenario.stages.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button type="button" className="ghost" onClick={() => void onReset(`${scenario.id}:${stage}`)}>Reset to this stage</button>
          </>
        )}
        <button type="button" className="ghost" onClick={async () => { await api.forget(null); onChanged(); }}
          title="Wipes Alexa's chat history for everyone. The plans stay, because they live on the GroupFlow server.">Clear Alexa's chat memory</button>
      </div>
      <div className="console-row">
        <button type="button" className="ghost" title="Runs what the server's worker does after two days with no progress: reminders due by then go out, and plans stuck waiting on someone get a check-in. The clock itself does not move."
          onClick={async () => {
            try {
              const r = await api.skipAhead(48, scenario.id);
              const rounds = r.checkins.map((c) => {
                const people = c.sent.map((x) => x.name);
                return c.reviewed ? `${c.plan}: quiet, but nothing stuck` : `${c.plan}: checked in with ${people.join(', ') || 'nobody'}`;
              });
              const reminders = r.reminders_sent ? `${r.reminders_sent} reminder${r.reminders_sent === 1 ? '' : 's'} sent. ` : '';
              setSkipNote(`${reminders}${rounds.length ? rounds.join('; ') + ` (${r.reviewer}).` : 'No plan is stuck waiting on anyone.'}`);
              onChanged();
            } catch (err) {
              setError((err as Error).message);
            }
          }}>Skip ahead two quiet days</button>
        <span className="console-note" aria-live="polite">{skipNote ?? 'Reminders and automatic check-ins, as the server would send them.'}</span>
      </div>
    </details>
  );

  return (
    <>
      <DemoBar script={script} step={step} playing={playing} note={note} members={members} onPlay={playNext} places={scenario.places} />
      {presentation && <OutcomeStrip plan={board?.plans.find((p) => p.id === shownPlan)} memoryOpen={memoryOpen} onMemory={() => setMemoryOpen((v) => !v)} />}
      {!presentation && <div className="lab-memory-control"><button type="button" className="ghost" disabled={viewAs === 'all'} onClick={() => setMemoryOpen((v) => !v)} aria-expanded={memoryOpen && viewAs !== 'all'}>{memoryOpen && viewAs !== 'all' ? 'Hide authorized memory' : 'Explore authorized MCP memory'}</button><span>{viewAs === 'all' ? 'Choose a person in the plan view first. The admin inspector is never used for this graph.' : 'The graph uses the selected person’s MCP session.'}</span></div>}
      {error && <div className="banner" role="alert">{error}</div>}
      {memoryOpen && viewAs !== 'all' && <MemoryExplorer key={`${viewAs}:${shownPlan}`} member={viewAs} group={scenario.id} plan={shownPlan} planTitle={planTabs.find((p) => p.id === shownPlan)?.title} tick={tick} />}
      <main className={`main group ${presentation ? 'focused-stage' : ''}`}>
        <section className="echoes" aria-label={`${scenario.label}: Echo devices and each person's own GroupFlow page`}>
          {presentation && <nav className="speaker-picker" aria-label="Demo speaker">{members.map((m) => <button type="button" key={m.id} className={viewAs === m.id ? 'selected' : ''} onClick={() => setViewAs(m.id)}><span className="speaker-dot" style={{ background: m.color }} />{m.name}</button>)}</nav>}
          {(presentation ? members.filter((m) => m.id === viewAs) : members).map((m) => (
            <Echo
              key={m.id}
              member={m}
              showDiagnostics={!presentation}
              lines={transcripts[m.id] ?? []}
              busy={!!echo.busy[m.id]}
              error={echo.errors[m.id] ?? null}
              draft={echo.drafts[m.id] ?? ''}
              onDraft={(v) => echo.setDraft(m.id, v)}
              onSend={(text) => void sendFocused(m.id, text)}
              badges={badges[m.id] ?? { decisions: 0, changes: 0 }}
              cued={cuedIds.has(m.id) && !playing}
              suggestion={SUGGESTIONS[`${scenario.id}.${m.id}`] ?? "What's new?"}
              face={faces[m.id] ?? 'alexa'}
              onFace={(f) => setFaces((x) => ({ ...x, [m.id]: f }))}
              pageBadge={pendingFor[m.id] ?? 0}
              page={
                <PhonePage
                  member={m}
                  simple={presentation}
                  signedIn={!!clients.current[m.id]?.signedIn}
                  inbox={inboxes[m.id] ?? null}
                  groupPlanIds={groupPlanIds}
                  busy={!!phoneBusy[m.id]}
                  error={phoneErr[m.id] ?? null}
                  message={phoneMsg[m.id] ?? null}
                  onLogin={(p) => void phone(m.id, (c) => c.login(p))}
                  onLogout={() => { client(m.id).logout(); setInboxes((x) => ({ ...x, [m.id]: null })); setSignedTick((t) => t + 1); }}
                  onDecide={(scope, d, extra?: DecisionExtra) => void phone(m.id, (c) => c.decide(scope, d, extra), DONE[d])}
                  onVerify={(cid, dg) => void phone(m.id, (c) => c.verify(cid, dg), 'It counts now.')}
                  onRevoke={(rid) => void phone(m.id, (c) => c.revoke(rid), 'Withdrawn. The organizer will see it.')}
                  onSetCondition={(pid, k, v) => void phone(m.id, (c) => c.setCondition(pid, k, v), 'Saved. It counts from now on.')}
                  onProjectAnswer={(sid, d) => void phone(m.id, (c) => c.decideProject(sid, d), d === 'approve' ? 'Your scoped answer was recorded.' : 'Declined. The lead can prepare a repair.')}
                  onProjectRevoke={(sid) => void phone(m.id, (c) => c.revokeProject(sid), 'Your project answer was withdrawn.')}
                  onProjectCalendar={(pid, cal) => void phone(m.id, (c) => c.projectCalendar(pid, cal), 'Your availability was saved. The schedule needs review.')}
                  onWorkspaceRole={(gid, target, role, grant) => void phone(m.id, (c) => c.workspaceRole(gid, target, role, grant), 'Workspace role updated.')}
                />
              }
            />
          ))}
        </section>
        <PlanBoard model={board} planId={shownPlan} plans={planTabs} onPlan={setPlanId} viewAs={viewAs} onViewAs={setViewAs} members={members} console={presentation ? undefined : demoConsole} memberOnly={presentation} />
      </main>
    </>
  );
}
