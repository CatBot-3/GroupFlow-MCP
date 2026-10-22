import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, HumanClient, type ActivityView, type AgentTurn, type ConditionView, type ConfirmationsView, type DemoStep, type Inbox, type Member, type PlanView, type ScenarioConfig, type ScopeStatus, type TranscriptLine } from '../api';
import { sleep, typeInto, urlParam, type EchoControls } from '../App';
import { parseWhen } from './Timeline';
import './explainer.css';

/*
  The demo page, built for an explainer video: one story, played one step at a time, with the few things a viewer needs
  to see and nothing else. The left side is the person speaking (their Echo, or their own GroupFlow page when they answer);
  the right side is the narrator's view of the plan: its timeline, the grid of answers, and what each person told only
  their own Alexa. Everything shown comes from the live server: the narrator panels from the demo inspector, the Echo from
  the model's real turns over MCP, the own pages from each person's human-only session.
*/

interface Props {
  scenario: ScenarioConfig;
  transcripts: Record<string, TranscriptLine[]>;
  echo: EchoControls;
  tick: number;
  resetKey: number;
  onChanged: () => void;
  /** Header controls owned by the app (speak replies, reset, test lab). */
  controls: ReactNode;
  live: { mcp: boolean; model: boolean; modelNote: string | null };
}

// Brighter versions of each friend's colour, for the dark stage.
const TONE: Record<string, string> = { alice: '#f0ac5f', bob: '#58c4dd', chen: '#83c167', dana: '#e58be0' };
const tone = (m: Member | undefined) => (m ? TONE[m.id] ?? m.color : '#a5b0c0');

const STATUS: Record<string, [string, string]> = {
  DRAFT: ['Draft', 'nobody has agreed yet'],
  PROPOSED: ['New version', 'ready to send'],
  AWAITING_CONFIRMATIONS: ['Sent', 'waiting for answers'],
  READY_TO_COMMIT: ['Everyone answered', 'ready to lock in'],
  COMMITTED: ['Agreed', 'locked in and booked'],
  DEGRADED: ['No longer works', 'something it relied on changed'],
  BLOCKED: ['Stuck', 'no version fits everyone'],
  REPAIRING: ['Searching', 'looking for a fix'],
  NEEDS_ATTENTION: ['Needs attention', ''],
};
const TONE_OF_STATUS: Record<string, string> = { DRAFT: 'quiet', PROPOSED: 'blue', AWAITING_CONFIRMATIONS: 'blue', READY_TO_COMMIT: 'green', COMMITTED: 'green', DEGRADED: 'red', BLOCKED: 'red' };

// What the server sends people that the viewer should see arrive (answers about each stop are left out: sixteen lines of noise).
const FEED_KINDS = new Set(['plan', 'confirm', 'carried', 'nudge', 'observation', 'degraded', 'committed', 'ready']);
const FEED_LABEL: Record<string, string> = { plan: 'New plan', confirm: 'Please confirm', carried: 'Still counts', nudge: 'Check-in', observation: 'Report', degraded: 'Plan broke', committed: 'Agreed', ready: 'Ready' };

const clock = (min: number) => {
  const h = Math.floor(min / 60) % 24;
  return `${h % 12 || 12}:${String(min % 60).padStart(2, '0')}`;
};
const clock24 = (hhmm: string) => {
  const [h, m] = hhmm.split(':').map(Number);
  return `${clock(h * 60 + m)} ${h >= 12 ? 'PM' : 'AM'}`;
};

/** "Earliest time they can start: 18:15" -> "Can't arrive before 6:15 PM". */
function limitLabel(c: Pick<ConditionView, 'name' | 'description'>): string {
  const value = c.description.split(': ').slice(1).join(': ');
  const t = /^(\d{2}:\d{2})$/.exec(value.trim());
  if (c.name === 'available_from' && t) return `Can't arrive before ${clock24(t[1])}`;
  if (c.name === 'home_by' && t) return `Home by ${clock24(t[1])}`;
  if (c.name === 'diet') return value.charAt(0).toUpperCase() + value.slice(1);
  if (c.name === 'budget') return `Budget ${value}`;
  return c.description;
}

/** "Friday dinner: Meet up on Fri, Oct 23, ..." -> "Meet up". */
const stopOf = (text: string) => /^[^:]+: (.+?) on /.exec(text)?.[1] ?? text;

function describe(s: DemoStep, members: Member[]): ReactNode {
  const who = (id?: string) => members.find((m) => m.id === id);
  if (s.kind === 'say') {
    const m = who(s.member);
    return <><b style={{ color: tone(m) }}>{m?.name}</b> says <q>{(s.text ?? '').replace(/^Alexa,\s*/i, '')}</q></>;
  }
  if (s.kind === 'verify') return <><b style={{ color: tone(who(s.member)) }}>{who(s.member)?.name}</b> confirms their private limit on their own page</>;
  if (s.kind === 'phones') return <>{(s.members ?? []).map((id, i) => <span key={id}>{i ? ', ' : ''}<b style={{ color: tone(who(id)) }}>{who(id)?.name}</b></span>)} answer on their own pages</>;
  if (s.kind === 'skip') return <>{s.hours} hours pass and nobody says anything</>;
  return <>The restaurant's own feed reports it closed tonight</>;
}

// ------------------------------------------------------------------------------------------------ the plan's timeline

function Timeline({ plan, problems }: { plan: PlanView; problems: string[] }) {
  const agreed = plan.agreed;
  const working = plan.working;
  const verifiedPlaces = plan.observations.filter((o) => o.includes('(verified)')).map((o) => /^(.+?) became unavailable/.exec(o)?.[1]).filter(Boolean) as string[];
  const lanes: { label: string; sub: string; acts: ActivityView[]; changed: Set<string>; broken: Set<string>; tone: string }[] = [];
  if (agreed) {
    const broken = new Set(agreed.activities.filter((a) => a.place && verifiedPlaces.includes(a.place)).map((a) => a.id));
    lanes.push({ label: agreed.label, sub: working ? 'agreed before' : 'agreed', acts: agreed.activities, changed: new Set(), broken, tone: working ? 'agreed' : 'current' });
  }
  // A first proposal replaces the draft, which is not kept as a version; rebuild it from the listed changes so the
  // viewer sees what moved ("Meet up: 18:00-18:15 -> 18:15-18:30", "Dinner: Restaurant A -> Restaurant C").
  const changedTitles = new Set<string>();
  let before: ActivityView[] | null = null;
  if (working && !agreed && working.kind === 'candidate' && working.changes.length) {
    before = working.activities.map((a) => ({ ...a }));
    for (const c of working.changes) {
      const m = /^(.+?): (.+?) -> (.+)$/.exec(c);
      if (!m || m[2].startsWith('cost')) continue;
      const act = before.find((a) => a.title === m[1]);
      if (!act) continue;
      changedTitles.add(m[1]);
      if (/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(m[2])) act.when = m[2];
      else act.place = m[2];
    }
    lanes.push({ label: 'Draft', sub: 'before', acts: before, changed: new Set(), broken: new Set(), tone: 'agreed' });
  }
  if (working) {
    const flagged = new Set(working.activities.filter((a) => problems.some((p) => p.startsWith(`${a.title} `))).map((a) => a.id));
    const changed = new Set([...(agreed ? working.changed_activity_ids ?? [] : []), ...working.activities.filter((a) => changedTitles.has(a.title)).map((a) => a.id)]);
    lanes.push({ label: working.label, sub: working.kind === 'draft' ? 'draft' : agreed || before ? 'proposed change' : 'proposed', acts: working.activities,
                 changed, broken: flagged, tone: 'working' });
  }
  const all = lanes.flatMap((l) => l.acts.map((a) => parseWhen(a.when)));
  const lo = Math.min(18 * 60, ...all.map((w) => w.start)) - 15;
  const hi = Math.max(21 * 60 + 30, ...all.map((w) => w.end)) + 15;
  const span = hi - lo;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / 30) * 30; t <= hi; t += 30) ticks.push(t);
  const x = (min: number) => `${((min - lo) / span) * 100}%`;
  return (
    <div className="x-timeline">
      <div className="x-axis">
        {ticks.map((t) => <span key={t} className={t % 60 === 0 ? 'hour' : ''} style={{ left: x(t) }}>{t % 60 === 0 ? `${clock(t)} PM` : clock(t)}</span>)}
      </div>
      {lanes.map((lane) => (
        <div key={lane.label + lane.sub} className={`x-lane ${lane.tone}`}>
          <div className="x-lane-label"><strong>{lane.label}</strong><span>{lane.sub}</span></div>
          <div className="x-track">
            {ticks.map((t) => <i key={t} className="x-grid" style={{ left: x(t) }} />)}
            {lane.acts.map((a) => {
              const w = parseWhen(a.when);
              const cls = lane.broken.has(a.id) ? 'broken' : lane.changed.has(a.id) ? 'changed' : '';
              return (
                <div key={a.id} className={`x-block ${cls}`} style={{ left: x(w.start), width: `calc(${((w.end - w.start) / span) * 100}% - 4px)` }}
                  title={`${a.title}, ${a.when}${a.place ? ` at ${a.place}` : ''}`}>
                  <b>{a.title}</b>
                  <span>{a.place?.replace('Shared ride home', 'Shared ride')}</span>
                  <em>{clock(w.start)}–{clock(w.end)}</em>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ the grid of answers

function AnswerGrid({ plan, members, memory }: { plan: PlanView | null; members: Member[]; memory: Record<string, ConfirmationsView> }) {
  const version = plan?.working ?? plan?.agreed ?? null;
  const acts = (version?.activities ?? []).filter((a) => a.status !== 'dropped');
  const committed = plan?.status === 'COMMITTED';
  const summary = plan?.agreement_summary ?? null;
  // Between rounds (agreed, or broken and not yet re-sent) the grid shows the answers that made the last agreed version.
  // After a reload the page has not seen the rounds, but an agreed version means every answer was in.
  const synthetic: ConfirmationsView | null = summary ? { version: summary.version, required: summary.required, satisfied: summary.satisfied, complete: true,
    rows: members.map((m) => ({ person: m.name, done: true, items: Object.fromEntries(acts.map((a) => [a.title, 'approved' as ScopeStatus])) })) } : null;
  const remembered = !plan?.confirmations && summary ? memory[summary.version] ?? synthetic : null;
  const conf: ConfirmationsView | null = plan?.confirmations ?? remembered;
  const changedTitles = new Set(acts.filter((a) => (version?.changed_activity_ids ?? []).includes(a.id)).map((a) => a.title));
  const carried = conf ? conf.rows.reduce((n, r) => n + Object.values(r.items).filter((s) => s === 'carried_forward').length, 0) : 0;
  const cell = (person: string, title: string): ScopeStatus | 'none' => {
    const s = conf?.rows.find((r) => r.person === person)?.items[title];
    if (!s) return 'none';
    return remembered && s === 'pending' ? 'approved' : s;
  };
  let headline = 'No plan yet';
  let sub = '';
  if (plan && !conf) { headline = 'Not sent yet'; sub = 'Nobody has been asked'; }
  if (conf) {
    const required = remembered && summary ? summary.required : conf.required;
    const satisfied = remembered && summary ? summary.satisfied : conf.satisfied;
    headline = remembered ? `All ${required} answers in for ${summary?.version}` : `${satisfied} of ${required} answered`;
    sub = remembered && !committed ? `${summary?.version} no longer works` : carried > 0 && !remembered ? `${carried} still count from before · ${required - carried} asked again`
      : committed ? 'every answer is for this exact version' : 'each person answers on their own page';
  }
  return (
    <section className="x-card x-answers" aria-label="Who has confirmed which stop">
      <header><h3>Answers</h3><p><strong>{headline}</strong>{sub && <span> · {sub}</span>}</p></header>
      <div className="x-grid-table" style={{ gridTemplateColumns: `minmax(64px, auto) repeat(${Math.max(acts.length, 4)}, 1fr)` }}>
        <span />
        {acts.length ? acts.map((a) => <span key={a.id} className={`x-col ${changedTitles.has(a.title) && carried > 0 ? 'again' : ''}`}>{a.title}</span>)
          : ['Stop', 'Stop', 'Stop', 'Stop'].map((t, i) => <span key={i} className="x-col dim">{t}</span>)}
        {members.map((m) => (
          <div key={m.id} className="x-row" style={{ display: 'contents' }}>
            <span className="x-person" style={{ color: tone(m) }}>{m.name}</span>
            {(acts.length ? acts : [0, 1, 2, 3].map(() => null)).map((a, i) => {
              const s = a ? cell(m.name, a.title) : 'none';
              const again = a && s === 'pending' && carried > 0 && changedTitles.has(a.title);
              const cls = s === 'approved' || s === 'conditional_met' ? 'yes' : s === 'carried_forward' ? 'kept' : s === 'pending' ? (again ? 'again' : 'wait') : s === 'rejected' ? 'no' : 'none';
              return <span key={a?.id ?? i} className={`x-cell ${cls}`}>{cls === 'yes' ? '✓' : cls === 'kept' ? <>✓<small>still counts</small></> : cls === 'again' ? '?' : cls === 'no' ? '✕' : ''}</span>;
            })}
          </div>
        ))}
      </div>
      <footer className="x-legend"><span className="x-cell yes">✓</span> confirmed <span className="x-cell kept">✓</span> still counts <span className="x-cell wait" /> waiting <span className="x-cell again">?</span> asked again</footer>
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ private limits

function PrivateLimits({ plan, members }: { plan: PlanView | null; members: Member[] }) {
  const conds = (plan?.conditions ?? []).filter((c) => c.status !== 'retired');
  return (
    <section className="x-card x-limits" aria-label="What each person told only their own Alexa">
      <header><h3>Told only to their own Alexa</h3><p>Narrator's view. GroupFlow checks every version against these and never says whose they are.</p></header>
      <ul>
        {members.map((m) => {
          const mine = conds.filter((c) => c.owner_id === m.id);
          return (
            <li key={m.id}>
              <span className="x-person" style={{ color: tone(m) }}>{m.name}</span>
              {mine.length ? (
                <span className="x-locks">
                  {mine.map((c) => (
                    <span key={c.id} className={`x-lock ${c.status === 'active' ? 'on' : 'draft'}`} title={c.status === 'active' ? `${m.name} confirmed this on their own page` : `Waiting for ${m.name} to confirm it on their own page`}>
                      <i aria-hidden="true">🔒</i>{limitLabel(c)}{c.status !== 'active' && <small>not confirmed yet</small>}
                    </span>
                  ))}
                </span>
              ) : <span className="x-none">nothing private</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ the stage

type Face = 'echo' | 'page';
interface FeedEntry { key: string; kind: string; text: string; at: string; to: string[] }

const STEP_KEY = 'groupflow.story.step';

/** Where the story stands survives a reload (a recording take should not restart because the page did); ?step=N jumps there. */
function savedStep(): number {
  const fromUrl = Number(urlParam('step'));
  if (fromUrl > 0) return fromUrl;
  try { return Number(window.sessionStorage.getItem(STEP_KEY)) || 0; } catch { return 0; }
}

function clockLabel(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}

export function ExplainerStage({ scenario, transcripts, echo, tick, resetKey, onChanged, controls, live }: Props) {
  const members = scenario.members;
  const script = scenario.script;
  const [step, setStep] = useState(savedStep);
  useEffect(() => { try { window.sessionStorage.setItem(STEP_KEY, String(step)); } catch { /* storage unavailable */ } }, [step]);
  const mounted = useRef(false);
  const [playing, setPlaying] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ member: string; face: Face }>({ member: members[0]?.id ?? '', face: 'echo' });
  const [plans, setPlans] = useState<PlanView[]>([]);
  const [inboxes, setInboxes] = useState<Record<string, Inbox | null>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [pageLog, setPageLog] = useState<Record<string, string[]>>({});
  const [feedTick, setFeedTick] = useState(0);
  const clients = useRef<Record<string, HumanClient>>({});
  const memory = useRef<Record<string, ConfirmationsView>>({});
  const feedSeen = useRef<Map<string, FeedEntry>>(new Map());

  const client = useCallback((id: string) => (clients.current[id] ??= new HumanClient('', id)), []);
  const plan = plans.find((p) => p.status !== 'CANCELLED') ?? null;
  const planRef = useRef<string | undefined>(undefined);
  planRef.current = plan?.id;

  const signIn = useCallback(async (id: string) => {
    const c = client(id);
    if (!c.signedIn) await c.login(members.find((m) => m.id === id)?.demoPasscode ?? '');
    return c;
  }, [client, members]);

  const refresh = useCallback(async () => {
    try {
      const insp = await api.inspector(scenario.id);
      setPlans(insp.plans);
      const entries = await Promise.all(members.map(async (m) => {
        const c = clients.current[m.id];
        try { return [m.id, c?.signedIn ? await c.inbox() : null] as const; } catch { return [m.id, null] as const; }
      }));
      setInboxes(Object.fromEntries(entries));
    } catch (err) {
      setError((err as Error).message);
    }
  }, [scenario.id, members]);

  // Everyone's own page is open in the background, so the messages GroupFlow sends them show up as they arrive.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      for (const c of Object.values(clients.current)) c.logout();
      for (const m of members) {
        try { await signIn(m.id); } catch { /* the page shows the error when a step needs it */ }
      }
      if (!cancelled) void refresh();
    })();
    // A reset starts the story over; the first run keeps the step a reload or link restored.
    if (mounted.current) setStep(0);
    mounted.current = true;
    setNote(null); setError(null); setProblems([]); setPageLog({});
    setFocus({ member: members[0]?.id ?? '', face: 'echo' });
    memory.current = {};
    feedSeen.current = new Map();
    setFeedTick((t) => t + 1);
    return () => { cancelled = true; };
  }, [resetKey, members, signIn, refresh]);

  useEffect(() => { void refresh(); }, [tick, refresh]);
  useEffect(() => {
    if (plan?.status === 'REPAIRING') {
      const t = setTimeout(() => void refresh(), 600);
      return () => clearTimeout(t);
    }
  }, [plan?.status, refresh]);

  // The grid keeps the last answers it saw, because a locked-in plan no longer lists them one by one.
  useEffect(() => {
    if (plan?.confirmations) memory.current[plan.confirmations.version] = plan.confirmations;
  }, [plan?.confirmations]);

  // Messages are unread notifications on each person's page; once Alexa reads them out they leave the page, so the feed
  // remembers what it has seen.
  useEffect(() => {
    const ids = new Set(plans.map((p) => p.id));
    let changed = false;
    for (const m of members) {
      for (const n of inboxes[m.id]?.notifications ?? []) {
        if (!n.plan_id || !ids.has(n.plan_id) || !FEED_KINDS.has(n.kind ?? '')) continue;
        const key = `${n.kind}|${n.text}`;
        const e = feedSeen.current.get(key) ?? { key, kind: n.kind ?? '', text: n.text, at: n.created_at, to: [] };
        if (!e.to.includes(m.name)) { e.to = [...e.to, m.name]; changed = true; }
        if (!feedSeen.current.has(key)) changed = true;
        feedSeen.current.set(key, e);
      }
    }
    if (changed) setFeedTick((t) => t + 1);
  }, [inboxes, plans, members]);
  const feed = useMemo(() => [...feedSeen.current.values()].sort((a, b) => b.at.localeCompare(a.at)).slice(0, 5),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feedTick]);

  const readTurn = useCallback((turn: AgentTurn) => {
    for (const c of turn.calls) {
      if (c.prefetched || !c.ok) continue;
      const r = c.result as { outcome?: string; problems?: string[] } | null;
      if (c.name === 'request_confirmations' || c.name === 'send_for_confirmation') setProblems(r?.outcome === 'infeasible' ? r.problems ?? [] : []);
      if (c.name === 'find_alternatives' || c.name === 'find_options') setProblems([]);
    }
  }, []);

  const answerPage = useCallback(async (id: string, what: 'verify' | 'confirm', decision: 'approved' | 'rejected' = 'approved') => {
    const name = members.find((m) => m.id === id)?.name ?? id;
    setFocus({ member: id, face: 'page' });
    setPageLog((x) => ({ ...x, [id]: [] }));
    const c = await signIn(id);
    await sleep(500);
    const inbox = await c.inbox();
    const planIds = new Set((await api.inspector(scenario.id)).plans.map((p) => p.id));
    const log = (line: string) => setPageLog((x) => ({ ...x, [id]: [...(x[id] ?? []), line] }));
    if (what === 'verify') {
      const drafts = inbox.conditions.filter((x) => x.status === 'unverified' && (!x.plan_id || planIds.has(x.plan_id)));
      if (!drafts.length) throw new Error(`${name} has no limit waiting to be confirmed. This step has not advanced.`);
      for (const d of drafts) {
        await c.verify(d.id, d.verify_digest);
        log(`Confirmed my limit: ${limitLabel(d)}`);
        await sleep(450);
      }
    } else {
      const items = inbox.pending.filter((it) => planIds.has(it.plan_id));
      if (!items.length) throw new Error(`Nothing was waiting for ${name}. This step has not advanced.`);
      for (const it of items) {
        await c.decide(it.scope_id, decision);
        log(`${decision === 'approved' ? 'Confirmed' : 'Declined'} ${stopOf(it.text)} (${it.revision})`);
        await sleep(320);
      }
    }
    const fresh = await c.inbox();
    setInboxes((x) => ({ ...x, [id]: fresh }));
    await api.notify({ member: id });
    onChanged();
    await sleep(500);
  }, [members, signIn, scenario.id, onChanged]);

  const playNext = useCallback(async () => {
    if (playing || step >= script.length) return;
    const s = script[step];
    setPlaying(true);
    setError(null);
    try {
      if (s.chapter && s.clock) await api.demoClock(s.chapter, s.clock);
      if (s.kind === 'say' && s.member && s.text) {
        setFocus({ member: s.member, face: 'echo' });
        await typeInto(echo.setDraft, s.member, s.text);
        const turn = await echo.send(s.member, s.text, planRef.current);
        if (!turn) throw new Error('This step did not complete. Press Play to try it again.');
        readTurn(turn);
      } else if (s.kind === 'verify' && s.member) {
        await answerPage(s.member, 'verify');
      } else if (s.kind === 'phones') {
        for (const id of s.members ?? []) await answerPage(id, 'confirm', s.decision ?? 'approved');
      } else if (s.kind === 'skip') {
        const r = await api.skipAhead(s.hours ?? 48, scenario.id);
        const reached = r.checkins.flatMap((c) => c.sent.map((x) => x.name));
        if (!reached.length) throw new Error('Nobody was checked in with: the plan is not waiting on anyone. This step has not advanced.');
        const first = members.find((m) => m.name === reached[0]);
        if (first) setFocus({ member: first.id, face: 'echo' });
      } else if (s.kind === 'event' && s.place_id) {
        await api.demoEvent(s.group ?? scenario.id, s.place_id, s.start, s.end, s.day);
      }
      setNote(s.note);
      setStep((n) => n + 1);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPlaying(false);
    }
  }, [playing, step, script, echo, readTurn, answerPage, scenario.id, members, onChanged]);

  // N plays the next step (handy while recording), unless someone is typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.key.toLowerCase() !== 'n' || e.metaKey || e.ctrlKey || e.altKey) return;
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable) return;
      e.preventDefault();
      void playNext();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [playNext]);

  const day = [...script.slice(0, step)].reverse().find((s) => s.chapter) ?? script[0];
  const chapter = day?.chapter ?? '';
  const next = step < script.length ? script[step] : null;
  const person = members.find((m) => m.id === focus.member) ?? members[0];
  const lines = (transcripts[person?.id ?? ''] ?? []).slice(-4);
  const lastAlexa = [...lines].reverse().find((l) => l.role === 'alexa');
  const tools = [...new Set((lastAlexa?.turn?.calls ?? []).filter((c) => !c.prefetched).map((c) => c.name))];
  const draft = echo.drafts[person?.id ?? ''] ?? '';
  const busy = !!echo.busy[person?.id ?? ''];
  const unread = (id: string) => (inboxes[id]?.notifications ?? []).filter((n) => FEED_KINDS.has(n.kind ?? '') && plans.some((p) => p.id === n.plan_id));
  const newest = person ? unread(person.id).slice(-1)[0] : undefined;
  const [status, statusSub] = plan ? STATUS[plan.status] ?? [plan.status, ''] : ['No plan yet', 'Alice is about to start one'];
  const proof = plan?.working?.kind === 'candidate' && plan.repair?.proof ? plan.repair.proof : null;
  // A report matters while the plan still uses that place; once the plan has moved on, it is history.
  const inUse = new Set([...(plan?.agreed?.activities ?? []), ...(plan?.working?.activities ?? [])].map((a) => a.place));
  const reports = (plan?.observations ?? []).filter((o) => inUse.has(/^(.+?) became unavailable/.exec(o)?.[1] ?? /reported that (.+?) is unavailable/.exec(o)?.[1] ?? ''));
  const bookings = plan?.status === 'COMMITTED' ? (plan.bookings ?? []).filter((b) => b.includes('cancelled') || b.includes(`SIM-${plan.agreed?.label}-`)).slice(-3) : [];

  return (
    <div className="x-app">
      <header className="x-top">
        <div className="x-brand">
          <span className="x-wordmark">GroupFlow</span>
          <span className="x-title">Friday dinner, Monday to Friday</span>
        </div>
        <div className="x-pills">
          <span className={`x-pill ${live.model ? 'ok' : 'bad'}`} title={live.modelNote ?? undefined}>Simulated Alexa+</span>
          <span className={`x-pill ${live.mcp ? 'ok' : 'bad'}`}>{live.mcp ? 'Live MCP server' : 'MCP server not reachable'}</span>
          {controls}
        </div>
      </header>

      <section className="x-story" aria-live="polite">
        <div className="x-chapter">{chapter}{day?.clock && <small>{clockLabel(day.clock)}</small>}</div>
        <div className="x-caption">
          <p className="x-note">{note ?? (step > 0 ? script[step - 1]?.note : null) ?? 'Four friends plan dinner for Friday. Each one talks to their own Alexa; GroupFlow keeps the plan they share.'}</p>
          <p className="x-next">{next ? <><span>Next:</span> {describe(next, members)}</> : <span>The story is over. Reset to play it again.</span>}</p>
        </div>
        <div className="x-play">
          <div className="x-dots" aria-label={`Step ${Math.min(step + 1, script.length)} of ${script.length}`}>
            {script.map((s, i) => <i key={i} className={`${i < step ? 'done' : i === step ? 'now' : ''} ${s.chapter ? 'chapter' : ''}`} />)}
          </div>
          <button type="button" className="x-play-button" onClick={() => void playNext()} disabled={playing || !next} aria-keyshortcuts="N">
            {playing ? 'Playing…' : next ? `Play step ${step + 1}` : 'Done'}
          </button>
        </div>
        {error && <div className="x-error" role="alert">{error}</div>}
      </section>

      <main className="x-main">
        <section className="x-left">
          <nav className="x-people" aria-label="The four friends">
            {members.map((m) => {
              const count = unread(m.id).length;
              return (
                <button key={m.id} type="button" className={m.id === person?.id ? 'on' : ''} style={{ ['--tone' as string]: tone(m) }} onClick={() => setFocus({ member: m.id, face: 'echo' })}>
                  <span className="x-avatar">{m.name[0]}</span>
                  <span>{m.name}</span>
                  {count > 0 && <small className="x-badge" title="New messages from GroupFlow">{count}</small>}
                </button>
              );
            })}
          </nav>

          {person && focus.face === 'echo' && (
            <article className={`x-card x-echo ${busy ? 'busy' : ''}`} style={{ ['--tone' as string]: tone(person) }}>
              <header><span className="x-ring" /> <h3>{person.name}'s Echo</h3><span className="x-sub">only {person.name} hears this</span></header>
              {newest && !busy && <p className="x-incoming"><b>New from GroupFlow:</b> {newest.text}</p>}
              <div className="x-talk">
                {lines.length === 0 && !draft && <p className="x-empty">{person.name}'s Echo is listening.</p>}
                {lines.map((l, i) => (
                  <p key={i} className={`${l.role === 'user' ? 'x-said' : 'x-reply'} ${i < lines.length - 2 ? 'old' : ''}`}>{l.text}</p>
                ))}
                {busy && draft && <p className="x-said">{draft}</p>}
                {busy && <p className="x-thinking">Alexa is checking with GroupFlow…</p>}
                {!busy && draft && <p className="x-said typing">{draft}</p>}
              </div>
              {tools.length > 0 && !busy && <p className="x-tools">GroupFlow tools used: {tools.join(', ')}</p>}
              <form className="x-input" onSubmit={(e) => { e.preventDefault(); const t = draft.trim(); if (t && !busy) void echo.send(person.id, t, planRef.current).then((turn) => turn && readTurn(turn)); }}>
                <input value={draft} placeholder={`Talk to Alexa as ${person.name}`} onChange={(e) => echo.setDraft(person.id, e.target.value)} disabled={busy} aria-label={`Talk to Alexa as ${person.name}`} />
              </form>
            </article>
          )}

          {person && focus.face === 'page' && (
            <article className="x-card x-page" style={{ ['--tone' as string]: tone(person) }}>
              <header><h3>{person.name}'s own GroupFlow page</h3><span className="x-sub">signed in as {person.name}; Alexa cannot reach this page</span></header>
              <ul>
                {(pageLog[person.id] ?? []).map((line, i) => <li key={i}><span className="x-tick">✓</span>{line}</li>)}
                {!(pageLog[person.id] ?? []).length && <li className="x-empty">Opening {person.name}'s page…</li>}
              </ul>
            </article>
          )}

          <section className="x-card x-feed" aria-label="Messages GroupFlow sent">
            <header><h3>Messages GroupFlow sent</h3></header>
            {feed.length === 0 ? <p className="x-empty">None yet.</p> : (
              <ul>
                {feed.map((f) => (
                  <li key={f.key} className={`k-${f.kind}`}>
                    <span className="x-kind">{FEED_LABEL[f.kind] ?? f.kind}</span>
                    <span className="x-to">to {f.to.join(', ')}</span>
                    <span className="x-text">{f.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </section>

        <section className="x-right">
          <section className="x-card x-plan" aria-label="The plan">
            <header>
              <h2>{plan?.title ?? 'Friday dinner'}</h2>
              <span className={`x-status ${plan ? TONE_OF_STATUS[plan.status] ?? 'quiet' : 'quiet'}`}><b>{status}</b>{statusSub && <> · {statusSub}</>}</span>
              {plan && <span className="x-sub">{plan.dates} · organized by {plan.organizer}</span>}
            </header>
            {plan ? <Timeline plan={plan} problems={problems} /> : <p className="x-empty big">No plan yet. Alice is about to ask Alexa to start one.</p>}
            <div className="x-facts">
              {problems.map((p) => <p key={p} className="x-fact bad">Not sent: {p.replace(/(\d{2}):(\d{2})/g, (_m, h, mm) => clock24(`${h}:${mm}`))}</p>)}
              {proof && <p className="x-fact">GroupFlow checked {proof.explored} of {proof.domain_size} combinations of places and times in {Math.max(1, Math.round(proof.runtime_ms))} ms.</p>}
              {reports.map((o) => {
                const unverified = o.includes('not verified');
                const text = unverified ? o.replace(/ \(not verified\)( \(unverified\))?$/, '') : `The restaurant's own notice: ${o.replace(/ \(verified\)$/, '').replace(/ on \d{4}-\d{2}-\d{2}/, ' tonight').replace(/(\d{2}):(\d{2})/g, (_m, h, mm) => clock24(`${h}:${mm}`))}`;
                return <p key={o} className={`x-fact ${unverified ? 'rumour' : 'bad'}`}>{unverified ? 'Unconfirmed report, plan unchanged: ' : ''}{text}</p>;
              })}
              {bookings.map((b) => <p key={b} className="x-fact good">{b.includes('cancelled') ? 'Cancelled' : 'Booked'} {/^book (.+?) for (\d+) at (\d{2}:\d{2})/.exec(b)?.slice(1).reduce((s, v, i) => s + (i === 0 ? v : i === 1 ? ` for ${v}` : ` at ${clock24(v)}`), '') ?? b} (simulated)</p>)}
            </div>
          </section>
          <div className="x-bottom">
            <AnswerGrid plan={plan} members={members} memory={memory.current} />
            <PrivateLimits plan={plan} members={members} />
          </div>
        </section>
      </main>
    </div>
  );
}
