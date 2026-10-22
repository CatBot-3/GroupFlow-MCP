import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, HumanClient, type ActivityView, type AgentTurn, type ConditionView, type ConfirmationsView, type DemoStep, type Inbox, type Member, type PlanView, type ScenarioConfig, type ScopeStatus, type TranscriptLine } from '../api';
import { sleep, urlParam, type EchoControls } from '../App';
import { parseWhen } from './Timeline';
import './explainer.css';

/*
  The demo page, built for an explainer video: one story, played one step at a time, with the few things a viewer needs
  to see and nothing else. The left side is each person's own Echo, their only way into GroupFlow: what they said, what
  Alexa answered, what GroupFlow sent them, and the questions only they can answer, shown on the Echo's screen like a
  follow-up question docked under the conversation. When several people act at once their Echos stack in a column. The
  right side is the narrator's view of the plan: its timeline, the grid of answers, and what each person told only their
  own Alexa. Everything shown comes from the live server: the narrator panels from the demo inspector, the conversation
  from the model's real turns over MCP, the answers from each person's human-only session.
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

// What GroupFlow sends people that their Alexa reads out, kept in each Echo's history (answers about each stop are left out:
// sixteen lines of noise). member_joined is the only one not about a plan.
const HISTORY_KINDS = new Set(['plan', 'notice', 'confirm', 'carried', 'nudge', 'reminder', 'observation', 'degraded', 'committed', 'ready', 'conflict', 'repair', 'unblocked', 'member_joined']);
const KIND_LABEL: Record<string, string> = { plan: 'New plan', notice: 'Update', confirm: 'Please confirm', carried: 'Still counts', nudge: 'Reminder', reminder: 'Reminder',
  observation: 'Report', degraded: 'Plan broke', committed: 'Agreed', ready: 'Ready', conflict: 'Needs a change', repair: 'Update', unblocked: 'Update', member_joined: 'Group' };
// Telling everyone about a plan is what makes it shared; before that the grid of answers has nobody to ask.
const SHARING_KINDS = new Set(['notice', 'confirm']);

// One pace for every spoken line, and one for every reply, so each turn looks the same on camera.
const SPEECH_WORD_MS = 170;
const REPLY_WORD_MS = 85;
const LISTEN_MS = 380;
// Several people at once start a beat apart, so the eye can follow them.
const STAGGER_MS = 320;

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
/** The rest of a stop's line: "... on Fri, Oct 23, 18:15 to 18:30 at Central Hub. Your share: CAD 0.00. ..." ->
    "6:15–6:30 PM at Central Hub · your share CAD 0.00". */
const stopDetail = (text: string) => {
  const rest = / on (.+)$/.exec(text)?.[1] ?? '';
  const t = /(\d{2}):(\d{2})(?:-| to )(\d{2}):(\d{2})/.exec(rest);
  const where = / at ([^.]+)/.exec(rest)?.[1];
  const share = /Your share: ([A-Z]{3} [\d.]+)/.exec(rest)?.[1];
  if (!t) return rest;
  return `${clock(Number(t[1]) * 60 + Number(t[2]))}–${clock24(`${t[3]}:${t[4]}`)}${where ? ` at ${where}` : ''}${share ? ` · your share ${share}` : ''}`;
};
/** Server messages write 24-hour times ("18:00-18:15", "from 18:00 to 21:00"); the stage shows them the way people say them. */
const prettyTimes = (text: string) => text
  .replace(/(\d{2}):(\d{2})-(\d{2}):(\d{2})/g, (_m, a, b, c, d) => `${clock(Number(a) * 60 + Number(b))}–${clock24(`${c}:${d}`)}`)
  .replace(/\b(\d{2}):(\d{2})\b/g, (_m, a, b) => clock24(`${a}:${b}`));
const andList = (xs: string[]) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);

function describe(s: DemoStep, members: Member[]): ReactNode {
  const who = (id?: string) => members.find((m) => m.id === id);
  const name = (id?: string) => <b style={{ color: tone(who(id)) }}>{who(id)?.name}</b>;
  const list = (ids: string[]) => ids.map((id, i) => <span key={id}>{i ? (i === ids.length - 1 ? ' and ' : ', ') : ''}{name(id)}</span>);
  if (s.kind === 'say') return <>{name(s.member)} says <q>{(s.text ?? '').replace(/^Alexa,\s*/i, '')}</q></>;
  if (s.kind === 'join') return <>{list(s.members ?? [])} ask their own Alexa to join with Alice's code</>;
  if (s.kind === 'deliver') return <>GroupFlow's message reaches {list(s.members ?? [])}</>;
  if (s.kind === 'verify') return <>{name(s.member)}'s Echo asks whether the limit it heard is right</>;
  if (s.kind === 'confirm') return <>{list(s.members ?? [])} answer each stop on their own Echo</>;
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

function AnswerGrid({ plan, people, memory, shared }: { plan: PlanView | null; people: Member[]; memory: Record<string, ConfirmationsView>; shared: boolean }) {
  const version = plan?.working ?? plan?.agreed ?? null;
  const acts = (version?.activities ?? []).filter((a) => a.status !== 'dropped');
  const committed = plan?.status === 'COMMITTED';
  const summary = plan?.agreement_summary ?? null;
  // Between rounds (agreed, or broken and not yet re-sent) the grid shows the answers that made the last agreed version.
  // After a reload the page has not seen the rounds, but an agreed version means every answer was in.
  const synthetic: ConfirmationsView | null = summary ? { version: summary.version, required: summary.required, satisfied: summary.satisfied, complete: true,
    rows: people.map((m) => ({ person: m.name, done: true, items: Object.fromEntries(acts.map((a) => [a.title, 'approved' as ScopeStatus])) })) } : null;
  const remembered = !plan?.confirmations && summary ? memory[summary.version] ?? synthetic : null;
  const conf: ConfirmationsView | null = plan?.confirmations ?? remembered;
  const changedTitles = new Set(acts.filter((a) => (version?.changed_activity_ids ?? []).includes(a.id)).map((a) => a.title));
  const carried = conf ? conf.rows.reduce((n, r) => n + Object.values(r.items).filter((s) => s === 'carried_forward').length, 0) : 0;
  const cell = (person: string, title: string): ScopeStatus | 'none' => {
    const s = conf?.rows.find((r) => r.person === person)?.items[title];
    if (!s) return 'none';
    return remembered && s === 'pending' ? 'approved' : s;
  };
  // The table has a row for each person and a column for each stop, so it waits until both are known: the stops are the
  // organizer's to choose, and nobody is asked anything before the plan is shared with them.
  const showTable = !!plan && (shared || !!conf);
  let headline = 'No plan yet';
  let sub = '';
  if (plan && !showTable) { headline = 'Not shared yet'; sub = `${plan.organizer} is still shaping the draft`; }
  else if (plan && !conf) { headline = 'Nobody asked to confirm yet'; sub = 'everyone has been told about the plan'; }
  if (conf) {
    const required = remembered && summary ? summary.required : conf.required;
    const satisfied = remembered && summary ? summary.satisfied : conf.satisfied;
    headline = remembered ? `All ${required} answers in for ${summary?.version}` : `${satisfied} of ${required} answered`;
    sub = remembered && !committed ? `${summary?.version} no longer works` : carried > 0 && !remembered ? `${carried} still count from before · ${required - carried} asked again`
      : committed ? 'every answer is for this exact version' : 'each person answers on their own Echo';
  }
  return (
    <section className="x-card x-answers" aria-label="Who has confirmed which stop">
      <header><h3>Answers</h3><p><strong>{headline}</strong>{sub && <span> · {sub}</span>}</p></header>
      {!showTable ? <p className="x-empty">{plan ? 'The table appears once the plan is shared: one row for each person, one column for each stop.' : 'Nothing to answer yet.'}</p> : (
        <div className="x-grid-table x-pop-in" style={{ gridTemplateColumns: `minmax(64px, auto) repeat(${acts.length}, 1fr)` }}>
          <span />
          {acts.map((a) => <span key={a.id} className={`x-col ${changedTitles.has(a.title) && carried > 0 ? 'again' : ''}`}>{a.title}</span>)}
          {people.map((m) => (
            <div key={m.id} className="x-row" style={{ display: 'contents' }}>
              <span className="x-person" style={{ color: tone(m) }}>{m.name}</span>
              {acts.map((a) => {
                const s = cell(m.name, a.title);
                const again = s === 'pending' && carried > 0 && changedTitles.has(a.title);
                const cls = s === 'approved' || s === 'conditional_met' ? 'yes' : s === 'carried_forward' ? 'kept' : s === 'pending' ? (again ? 'again' : 'wait') : s === 'rejected' ? 'no' : 'none';
                return <span key={a.id} className={`x-cell ${cls}`}>{cls === 'yes' ? '✓' : cls === 'kept' ? <>✓<small>still counts</small></> : cls === 'again' ? '?' : cls === 'no' ? '✕' : ''}</span>;
              })}
            </div>
          ))}
        </div>
      )}
      {showTable && <footer className="x-legend"><span className="x-cell yes">✓</span> confirmed <span className="x-cell kept">✓</span> still counts <span className="x-cell wait" /> waiting <span className="x-cell again">?</span> asked again</footer>}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ private limits

function PrivateLimits({ plan, people }: { plan: PlanView | null; people: Member[] }) {
  const conds = (plan?.conditions ?? []).filter((c) => c.status !== 'retired');
  return (
    <section className="x-card x-limits" aria-label="What each person told only their own Alexa">
      <header><h3>Told only to their own Alexa</h3><p>Narrator's view. GroupFlow checks every version against these and never says whose they are.</p></header>
      <ul>
        {people.map((m) => {
          const mine = conds.filter((c) => c.owner_id === m.id);
          return (
            <li key={m.id} className="x-pop-in">
              <span className="x-person" style={{ color: tone(m) }}>{m.name}</span>
              {mine.length ? (
                <span className="x-locks">
                  {mine.map((c) => (
                    <span key={c.id} className={`x-lock ${c.status === 'active' ? 'on' : 'draft'}`} title={c.status === 'active' ? `${m.name} confirmed this on their own Echo` : `Waiting for ${m.name} to confirm it on their own Echo`}>
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

// ------------------------------------------------------------------------------------------------ a person's Echo

/** One line in a person's Echo history: what they said, Alexa's reply, a message GroupFlow sent that Alexa read out, or the
    answer they gave on the Echo's screen. `shown` orders what is on screen; a message that arrived while the person's Echo
    was off camera is held until it is shown, the way an Echo lights up when the person comes back to it. */
interface LogEntry { key: string; kind: 'said' | 'reply' | 'gf' | 'receipt'; text: string; gfKind?: string; held?: boolean; shown: number; turn?: TranscriptLine['turn'] }
interface Speech { words: string[]; shown: number; sent: boolean; since: number }

/** A question on the Echo's screen that only its owner can answer, answered page by page. The tap goes to GroupFlow's
    human-only API with the person's own session, never through the model. */
interface PromptEntry {
  id: string; title: string; detail: string;
  state: 'open' | 'pressing' | 'editing' | 'yes' | 'no';
  pick?: number; result?: string;
  // a drafted limit
  digest?: string; kind?: string; value?: Record<string, unknown>; planId?: string | null;
  // a stop of a version
  revision?: string;
}
type PromptMode = 'join' | 'verify' | 'confirm';
interface Prompt { member: string; mode: PromptMode; question: string; options: string[]; entries: PromptEntry[]; page: number; note?: string; closing?: boolean; receipt: (done: PromptEntry[]) => string }
type Answer = { choice: 'yes' | 'no' | 'other'; time?: string; amount?: number };

function OtherEditor({ entry, mode, onDone }: { entry: PromptEntry; mode: PromptMode; onDone: (a: Answer | null) => void }) {
  const time = typeof entry.value?.time === 'string' ? entry.value.time : null;
  const [v, setV] = useState(mode === 'verify' ? time ?? '' : '');
  if (mode === 'verify' && !time) {
    return <p className="x-other-note">Tell your Alexa what is different; this screen can only set a time. <button type="button" className="x-btn" onClick={() => onDone(null)}>Back</button></p>;
  }
  return (
    <form className="x-other" onSubmit={(e) => { e.preventDefault(); onDone(mode === 'verify' ? { choice: 'other', time: v } : { choice: 'other', amount: Number(v) }); }}>
      <label>{mode === 'verify' ? 'Set it yourself' : 'Yes, if my total stays under $'}
        <input type={mode === 'verify' ? 'time' : 'number'} min={mode === 'verify' ? undefined : 1} value={v} onChange={(e) => setV(e.target.value)} />
      </label>
      <button type="submit" className="x-btn yes" disabled={!v}>Save</button>
      <button type="button" className="x-btn" onClick={() => onDone(null)}>Back</button>
    </form>
  );
}

function PromptSheet({ person, prompt, compact, onPick, onEdit }: { person: Member; prompt: Prompt; compact: boolean; onPick: (e: PromptEntry, a: Answer) => void; onEdit: (e: PromptEntry, on: boolean) => void }) {
  const entry = prompt.entries[Math.min(prompt.page, prompt.entries.length - 1)];
  const many = prompt.entries.length > 1;
  return (
    <section className={`x-sheet ${compact ? 'compact' : ''} ${prompt.closing ? 'closing' : ''}`} data-mode={prompt.mode} aria-label={`A question only ${person.name} can answer`}>
      <header>
        <span className="x-sheet-tag">GroupFlow check</span>
        <span className="x-sheet-who">only {person.name} can answer this{many ? ` · ${Math.min(prompt.page + 1, prompt.entries.length)} of ${prompt.entries.length}` : ''}</span>
      </header>
      <p className="x-sheet-q">{prompt.question}</p>
      {prompt.mode === 'confirm' && many && (
        <div className="x-chips">
          {prompt.entries.map((e, i) => <span key={e.id} className={`x-chip ${e.state === 'yes' ? 'yes' : e.state === 'no' ? 'no' : i === prompt.page ? 'now' : ''}`}>{e.state === 'yes' ? '✓ ' : e.state === 'no' ? '✕ ' : ''}{e.title}</span>)}
        </div>
      )}
      {entry && (
        <div key={entry.id} className="x-sheet-item">
          <b>{entry.title}</b>
          {entry.detail && <span>{entry.detail}</span>}
        </div>
      )}
      {entry && (entry.state === 'editing' ? <OtherEditor entry={entry} mode={prompt.mode} onDone={(a) => (a ? onPick(entry, a) : onEdit(entry, false))} /> : (
        <ol className="x-options">
          {prompt.options.map((label, i) => {
            const picked = entry.pick === i;
            const cls = picked ? (entry.state === 'pressing' ? 'pressing' : entry.state === 'yes' ? 'chosen yes' : entry.state === 'no' ? 'chosen no' : '') : '';
            return (
              <li key={label}>
                <button type="button" className={cls} disabled={entry.state === 'yes' || entry.state === 'no'}
                  onClick={() => (i === 2 ? onEdit(entry, true) : onPick(entry, { choice: i === 0 ? 'yes' : 'no' }))}>
                  <kbd>{i + 1}</kbd><span>{label}</span>{picked && (entry.state === 'yes' || entry.state === 'no') && <i aria-hidden="true">{entry.state === 'yes' ? '✓' : '✕'}</i>}
                </button>
              </li>
            );
          })}
        </ol>
      ))}
      {prompt.note && <p className="x-sheet-note">{prompt.note}</p>}
    </section>
  );
}

/** Consecutive "X joined Friends." messages read as one line, the way an Echo would group them. */
function grouped(entries: LogEntry[]): LogEntry[] {
  const out: LogEntry[] = [];
  for (const e of entries) {
    const prev = out[out.length - 1];
    const a = e.gfKind === 'member_joined' ? /^(.+?) joined (.+)\.$/.exec(e.text) : null;
    const b = prev?.gfKind === 'member_joined' ? /^(.+?) joined (.+)\.$/.exec(prev.text) : null;
    if (a && b && a[2] === b[2]) {
      const names = [...b[1].split(/, | and /), a[1]];
      out[out.length - 1] = { ...prev, key: `${prev.key}+${e.key}`, text: `${andList(names)} joined ${a[2]}.`, shown: Math.max(prev.shown, e.shown) };
    } else out.push(e);
  }
  return out;
}

interface EchoProps {
  person: Member; entries: LogEntry[]; speech?: Speech; reveal?: { text: string; shown: number }; busy: boolean; since: number;
  prompt?: Prompt; compact: boolean; notify: boolean; input?: ReactNode;
  onPick: (e: PromptEntry, a: Answer) => void; onEdit: (e: PromptEntry, on: boolean) => void;
}

function EchoView({ person, entries, speech, reveal, busy, since, prompt, compact, notify, input, onPick, onEdit }: EchoProps) {
  const lines = grouped(entries).slice(compact ? -6 : -16);
  // The spoken line in progress is drawn on its own until the transcript has it; then Alexa's reply is revealed word by word.
  const answered = !!speech && lines.some((l) => l.kind === 'said' && l.shown > speech.since && l.text === speech.words.join(' '));
  const shownText = (l: LogEntry): string | null => {
    if (l.kind !== 'reply' || !speech || l.shown <= speech.since) return l.text;
    if (reveal && reveal.text === l.text) return l.text.split(/\s+/).slice(0, reveal.shown).join(' ');
    return null;
  };
  const inProgress = !!speech && !answered;
  // Which GroupFlow tools Alexa's last reply used, while that reply is still the newest thing on the Echo.
  const lastLine = lines[lines.length - 1];
  const tools = lastLine?.kind === 'reply' ? [...new Set((lastLine.turn?.calls ?? []).filter((c) => !c.prefetched).map((c) => c.name))] : [];
  return (
    <article className={`x-card x-echo ${compact ? 'mini' : ''} ${busy ? 'busy' : ''} ${inProgress && !speech?.sent ? 'listening' : ''} ${notify ? 'notify' : ''}`} style={{ ['--tone' as string]: tone(person) }}>
      <header><span className="x-ring" /> <h3>{person.name}'s Echo</h3><span className="x-sub">only {person.name} hears this</span></header>
      <div className="x-talk">
        {lines.length === 0 && !inProgress && <p className="x-empty">{person.name}'s Echo is listening.</p>}
        {lines.map((l) => {
          const text = shownText(l);
          if (text === null) return null;
          const old = l.shown <= since ? 'old' : 'new';
          if (l.kind === 'said') return <p key={l.key} className={`x-said ${old}`}>{l.text}</p>;
          if (l.kind === 'reply') return <p key={l.key} className={`x-reply ${old}`}>{text}</p>;
          if (l.kind === 'receipt') return <p key={l.key} className={`x-receipt ${old}`}><span className="x-tick">✓</span><span>{l.text}<small>answered on {person.name}'s screen</small></span></p>;
          return <p key={l.key} className={`x-gf k-${l.gfKind} ${old}`}><span className="x-gf-tag">From GroupFlow · {KIND_LABEL[l.gfKind ?? ''] ?? 'Update'}</span>{prettyTimes(l.text)}</p>;
        })}
        {inProgress && speech && <p className="x-said new">{speech.words.slice(0, speech.shown).join(' ')}{!speech.sent && <span className="x-caret" />}</p>}
        {busy && <p className="x-thinking">Alexa is checking with GroupFlow…</p>}
      </div>
      {!compact && tools.length > 0 && !busy && !inProgress && !reveal && !prompt && <p className="x-tools">GroupFlow tools used: {tools.join(', ')}</p>}
      {prompt ? <PromptSheet person={person} prompt={prompt} compact={compact} onPick={onPick} onEdit={onEdit} /> : input}
    </article>
  );
}

// ------------------------------------------------------------------------------------------------ the stage

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

/** "Friday dinner: the draft no longer fits everyone. Meet up would start at 18:00, before ... Want me to ..." -> the middle. */
const conflictProblem = (text: string) => /fits everyone\. (.+?)(?: Want me|$)/.exec(text)?.[1] ?? null;

export function ExplainerStage({ scenario, transcripts, echo, tick, resetKey, onChanged, controls, live }: Props) {
  const members = scenario.members;
  const script = scenario.script;
  const organizerId = members[0]?.id ?? '';
  const [step, setStep] = useState(savedStep);
  useEffect(() => { try { window.sessionStorage.setItem(STEP_KEY, String(step)); } catch { /* storage unavailable */ } }, [step]);
  const mounted = useRef(false);
  const [playing, setPlaying] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Whose Echo is on screen: one person, or several stacked in a column when they act at once.
  const [view, setView] = useState<string[]>([organizerId]);
  const [plans, setPlans] = useState<PlanView[]>([]);
  const [problems, setProblems] = useState<{ why: string; items: string[] }>({ why: '', items: [] });
  const [speech, setSpeech] = useState<Record<string, Speech>>({});
  const [reveal, setReveal] = useState<Record<string, { text: string; shown: number }>>({});
  const [prompts, setPrompts] = useState<Record<string, Prompt>>({});
  const promptsRef = useRef(prompts);
  promptsRef.current = prompts;
  const [notify, setNotify] = useState<Record<string, boolean>>({});
  const [, setLogTick] = useState(0);
  // Who is in the group on the server (null: the group does not exist yet, before Alice creates it).
  const [inGroup, setInGroup] = useState<Set<string> | null>(null);
  const invite = useRef<{ code: string; group: string } | null>(null);
  const clients = useRef<Record<string, HumanClient>>({});
  const memory = useRef<Record<string, ConfirmationsView>>({});
  const logs = useRef<Record<string, LogEntry[]>>({});
  const seq = useRef(0);
  const stepSince = useRef(0);
  const linesSeen = useRef<Record<string, number>>({});
  const notesSeen = useRef<Set<string>>(new Set());
  const visible = useRef<Set<string>>(new Set([organizerId]));
  const speaking = useRef<Set<string>>(new Set());
  const shared = useRef<Set<string>>(new Set());
  // Where a person's receipt goes in their history: where it stood when they gave their last answer, so a message that
  // arrives while the sheet is closing lands after it.
  const receiptAt = useRef<Record<string, number>>({});

  const client = useCallback((id: string) => (clients.current[id] ??= new HumanClient('', id)), []);
  const plan = plans.find((p) => p.status !== 'CANCELLED') ?? null;
  const planRef = useRef<string | undefined>(undefined);
  planRef.current = plan?.id;
  const nameOf = useCallback((id: string) => members.find((m) => m.id === id)?.name ?? id, [members]);

  const signIn = useCallback(async (id: string) => {
    const c = client(id);
    if (!c.signedIn) await c.login(members.find((m) => m.id === id)?.demoPasscode ?? '');
    return c;
  }, [client, members]);

  const push = useCallback((member: string, e: Omit<LogEntry, 'shown'>) => {
    (logs.current[member] ??= []).push({ ...e, shown: e.held ? 0 : ++seq.current });
  }, []);

  /** Messages GroupFlow sent a person, added to their Echo history once. Held while they are talking (so a message their own
      turn caused lands after the reply) and while their Echo is off screen. */
  const ingest = useCallback((member: string, inbox: Inbox | null, planIds: Set<string>, organizer: string | null) => {
    if (!inbox || speaking.current.has(member)) return false;
    let changed = false;
    for (const n of inbox.notifications) {
      const kind = n.kind ?? '';
      if (!HISTORY_KINDS.has(kind)) continue;
      if (n.plan_id ? !planIds.has(n.plan_id) : kind !== 'member_joined') continue;
      const key = `${member}|${n.id}`;
      if (notesSeen.current.has(key)) continue;
      notesSeen.current.add(key);
      push(member, { key, kind: 'gf', gfKind: kind, text: n.text, held: !visible.current.has(member) });
      if (n.plan_id && SHARING_KINDS.has(kind) && member !== organizer) shared.current.add(n.plan_id);
      if (kind === 'conflict') {
        const p = conflictProblem(n.text);
        if (p) setProblems({ why: 'The draft no longer fits', items: [p] });
      }
      changed = true;
    }
    return changed;
  }, [push]);

  const refreshing = useRef<Promise<void> | null>(null);
  const again = useRef(false);
  const refresh = useCallback(async (): Promise<void> => {
    // Overlapping calls (a tap, a server event, a step) collapse into one more run after the current one.
    if (refreshing.current) { again.current = true; return refreshing.current; }
    const run = async () => {
      do {
        again.current = false;
        try {
          const insp = await api.inspector(scenario.id);
          setPlans(insp.plans);
          setInGroup(insp.group ? new Set(insp.group.members.map((m) => m.id)) : null);
          const planIds = new Set(insp.plans.map((p) => p.id));
          const organizer = insp.plans[0]?.organizer_id ?? organizerId;
          let changed = false;
          await Promise.all(members.map(async (m) => {
            const c = clients.current[m.id];
            try { if (c?.signedIn && ingest(m.id, await c.inbox(), planIds, organizer)) changed = true; } catch { /* shown when a step needs it */ }
          }));
          if (changed) setLogTick((t) => t + 1);
        } catch (err) {
          setError((err as Error).message);
        }
      } while (again.current);
    };
    refreshing.current = run().finally(() => { refreshing.current = null; });
    return refreshing.current;
  }, [scenario.id, members, ingest, organizerId]);

  /** Bring held messages on screen for these people, newest last. */
  const reveal_ = useCallback((ids: string[]) => {
    let any = false;
    for (const id of ids) {
      for (const e of logs.current[id] ?? []) if (e.held) { e.held = false; e.shown = ++seq.current; any = true; }
    }
    if (any) setLogTick((t) => t + 1);
    return any;
  }, []);

  /** Put these people's Echos on screen (one, or several in a column). */
  const show = useCallback((ids: string[], opts: { reveal?: boolean } = {}) => {
    visible.current = new Set(ids);
    setView(ids);
    if (opts.reveal !== false) reveal_(ids);
  }, [reveal_]);

  const flash = useCallback((id: string) => {
    setNotify((n) => ({ ...n, [id]: true }));
    setTimeout(() => setNotify((n) => ({ ...n, [id]: false })), 2400);
  }, []);

  // Each person's Echo is signed in to their own GroupFlow session in the background (the screen side of the device), so
  // the messages GroupFlow sends them show up as they arrive.
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
    setNote(null); setError(null); setProblems({ why: '', items: [] });
    setSpeech({}); setReveal({}); setPrompts({}); setNotify({});
    invite.current = null;
    memory.current = {};
    logs.current = {};
    linesSeen.current = {};
    notesSeen.current = new Set();
    shared.current = new Set();
    speaking.current = new Set();
    receiptAt.current = {};
    stepSince.current = seq.current;
    show([organizerId]);
    setLogTick((t) => t + 1);
    return () => { cancelled = true; };
  }, [resetKey, members, signIn, refresh, show, organizerId]);

  // What people said and what Alexa answered, in the order it happened.
  useEffect(() => {
    let changed = false;
    for (const m of members) {
      const lines = transcripts[m.id] ?? [];
      let seen = linesSeen.current[m.id] ?? 0;
      if (lines.length < seen) {
        logs.current[m.id] = (logs.current[m.id] ?? []).filter((e) => e.kind !== 'said' && e.kind !== 'reply');
        seen = 0;
        changed = true;
      }
      for (let i = seen; i < lines.length; i++) {
        push(m.id, { key: `t-${m.id}-${seq.current + 1}`, kind: lines[i].role === 'user' ? 'said' : 'reply', text: lines[i].text, turn: lines[i].turn });
        changed = true;
      }
      linesSeen.current[m.id] = lines.length;
    }
    if (changed) setLogTick((t) => t + 1);
  }, [transcripts, members, push]);

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

  const readTurn = useCallback((turn: AgentTurn) => {
    for (const c of turn.calls) {
      if (c.prefetched || !c.ok) continue;
      const r = c.result as { outcome?: string; problems?: string[]; code?: string; group?: { name?: string } } | null;
      if (c.name === 'invite_to_group' && r?.code) invite.current = { code: r.code, group: r.group?.name ?? 'the group' };
      if (c.name === 'request_confirmations' || c.name === 'send_for_confirmation') setProblems(r?.outcome === 'infeasible' ? { why: 'Not sent', items: r.problems ?? [] } : { why: '', items: [] });
      if (c.name === 'find_alternatives' || c.name === 'find_options') setProblems({ why: '', items: [] });
    }
  }, []);

  // --------------------------------------------------------------------------------------------- talking to Alexa

  /** A spoken line appears at one pace on the person's Echo. */
  const speak = useCallback(async (member: string, text: string) => {
    speaking.current.add(member);
    const words = text.split(/\s+/).filter(Boolean);
    const since = seq.current;
    setSpeech((s) => ({ ...s, [member]: { words, shown: 0, sent: false, since } }));
    await sleep(LISTEN_MS);
    for (let i = 1; i <= words.length; i++) {
      setSpeech((s) => ({ ...s, [member]: { words, shown: i, sent: false, since } }));
      await sleep(SPEECH_WORD_MS);
    }
    setSpeech((s) => ({ ...s, [member]: { words, shown: words.length, sent: true, since } }));
  }, []);

  const ask = useCallback(async (member: string, text: string) => {
    const turn = await echo.send(member, text, planRef.current);
    if (!turn) {
      speaking.current.delete(member);
      setSpeech((s) => { const n = { ...s }; delete n[member]; return n; });
      throw new Error('This step did not complete. Press Play to try it again.');
    }
    readTurn(turn);
    return turn;
  }, [echo, readTurn]);

  /** Alexa's reply at another pace; then anything GroupFlow sent the person during that turn lands after it. */
  const answerOut = useCallback(async (member: string, turn: AgentTurn) => {
    const words = turn.reply.split(/\s+/).filter(Boolean);
    for (let i = 1; i <= words.length; i++) {
      setReveal((r) => ({ ...r, [member]: { text: turn.reply, shown: i } }));
      await sleep(REPLY_WORD_MS);
    }
    setReveal((r) => { const n = { ...r }; delete n[member]; return n; });
    setSpeech((s) => { const n = { ...s }; delete n[member]; return n; });
    speaking.current.delete(member);
    await refresh();
  }, [refresh]);

  const speakAndAnswer = useCallback(async (member: string, text: string) => {
    show([member]);
    await speak(member, text);
    const turn = await ask(member, text);
    await answerOut(member, turn);
    return turn;
  }, [show, speak, ask, answerOut]);

  // --------------------------------------------------------------------------------------------- answering on the screen

  const setPrompt = useCallback((member: string, patch: Partial<Prompt> | null) => {
    setPrompts((p) => {
      if (patch === null) { const n = { ...p }; delete n[member]; return n; }
      return p[member] ? { ...p, [member]: { ...p[member], ...patch } } : p;
    });
  }, []);
  const setEntry = useCallback((member: string, id: string, patch: Partial<PromptEntry>) => {
    setPrompts((p) => (p[member] ? { ...p, [member]: { ...p[member], entries: p[member].entries.map((e) => (e.id === id ? { ...e, ...patch } : e)) } } : p));
  }, []);

  /** One answer on the person's own screen, whether the story taps it or someone clicks it: the human-only API, never Alexa. */
  const submit = useCallback(async (member: string, mode: PromptMode, e: PromptEntry, a: Answer) => {
    const c = await signIn(member);
    const pick = a.choice === 'yes' ? 0 : a.choice === 'no' ? 1 : 2;
    const rest = (promptsRef.current[member]?.entries ?? []).filter((x) => x.id !== e.id && x.state !== 'yes' && x.state !== 'no');
    if (!rest.length) receiptAt.current[member] = (logs.current[member] ?? []).length;
    if (mode === 'join') {
      const r = await c.decideJoin(e.id, a.choice === 'yes');
      setEntry(member, e.id, { state: a.choice === 'yes' ? 'yes' : 'no', pick, result: r.message });
    } else if (mode === 'verify') {
      if (a.choice === 'yes') {
        await c.verify(e.id, e.digest ?? '');
        setEntry(member, e.id, { state: 'yes', pick, result: e.title });
      } else if (a.choice === 'no') {
        setEntry(member, e.id, { state: 'no', pick, result: `${e.title} (not counted)` });
      } else if (a.time && e.kind && e.planId) {
        await c.setCondition(e.planId, e.kind, { ...(e.value ?? {}), time: a.time });
        setEntry(member, e.id, { state: 'yes', pick, result: `${e.title.replace(/\d{1,2}:\d{2} [AP]M/, clock24(a.time))}` });
      }
    } else {
      if (a.choice === 'yes') {
        await c.decide(e.id, 'approved');
        setEntry(member, e.id, { state: 'yes', pick });
      } else if (a.choice === 'no') {
        await c.decide(e.id, 'rejected');
        setEntry(member, e.id, { state: 'no', pick });
      } else if (a.amount && a.amount > 0) {
        await c.decide(e.id, 'conditional', { condition: { max_total_minor: Math.round(a.amount * 100) } });
        setEntry(member, e.id, { state: 'yes', pick, result: `under $${a.amount}` });
      }
    }
    await api.notify({ member });
    void refresh();
    onChanged();
  }, [signIn, setEntry, refresh, onChanged]);

  /** The sheet closes and what the person answered stays in their history. */
  const finish = useCallback(async (member: string) => {
    const done = promptsRef.current[member];
    if (!done) return;
    setPrompt(member, { closing: true });
    await sleep(320);
    setPrompt(member, null);
    const log = (logs.current[member] ??= []);
    const at = Math.min(receiptAt.current[member] ?? log.length, log.length);
    delete receiptAt.current[member];
    log.splice(at, 0, { key: `r-${member}-${seq.current + 1}`, kind: 'receipt', text: done.receipt(done.entries), shown: ++seq.current });
    setLogTick((t) => t + 1);
  }, [setPrompt]);

  /** What is waiting for this person on their screen, as one sheet. */
  const openPrompt = useCallback(async (member: string, mode: PromptMode): Promise<Prompt> => {
    const c = await signIn(member);
    const inbox = await c.inbox();
    const name = nameOf(member);
    if (mode === 'join') {
      const [j] = inbox.joins ?? [];
      if (!j) throw new Error(`${name} has no join waiting. This step has not advanced.`);
      return { member, mode, question: `Join ${j.group.name}?`, options: [`Yes, join ${j.group.name}`, 'No, not now'], page: 0,
        entries: [{ id: j.id, title: `${j.invited_by} invited you to ${j.group.name}`, detail: 'You will see the plans shared with the group.', state: 'open' }],
        receipt: (es) => (es[0]?.state === 'yes' ? `Joined ${j.group.name}` : `Did not join ${j.group.name}`) };
    }
    const insp = await api.inspector(scenario.id);
    const planIds = new Set(insp.plans.map((p) => p.id));
    const title = insp.plans[0]?.title ?? 'the plan';
    if (mode === 'verify') {
      const drafts = inbox.conditions.filter((x) => x.status === 'unverified' && (!x.plan_id || planIds.has(x.plan_id)));
      if (!drafts.length) throw new Error(`${name} has no limit waiting to be confirmed. This step has not advanced.`);
      return { member, mode, question: `Should GroupFlow count this for ${title}?`, options: ['Yes, count it', 'No', 'Other: set it myself'], page: 0,
        entries: drafts.map((d) => ({ id: d.id, title: limitLabel(d), detail: 'Your Alexa drafted this from what you said. It counts only if you say yes here.', state: 'open',
          digest: d.verify_digest, kind: d.name, value: d.value, planId: d.plan_id })),
        note: 'Kept private: the others only ever hear that "a member" has a limit.',
        receipt: (es) => { const yes = es.filter((e) => e.state === 'yes'); return yes.length ? `Counted: ${yes.map((e) => e.result ?? e.title).join('; ')}` : 'Nothing counted'; } };
    }
    const items = inbox.pending.filter((it) => planIds.has(it.plan_id));
    if (!items.length) throw new Error(`Nothing was waiting for ${name}. This step has not advanced.`);
    const rev = items[0].revision;
    const kept = inbox.answered.filter((it) => planIds.has(it.plan_id) && it.revision === rev && it.status === 'carried_forward').length;
    return { member, mode, question: `${title}, version ${rev}: does this stop work for you?`, options: ['Yes', 'No', 'Other: yes, if my total stays under…'], page: 0,
      entries: items.map((it) => ({ id: it.scope_id, title: stopOf(it.text), detail: stopDetail(it.text), state: 'open', revision: it.revision })),
      note: kept ? `${kept} other ${kept === 1 ? 'stop' : 'stops'} did not change, so ${kept === 1 ? 'that answer still counts' : 'those answers still count'}.` : undefined,
      receipt: (es) => { const yes = es.filter((e) => e.state === 'yes').map((e) => e.title); return `Yes to ${andList(yes)} for version ${rev}${kept ? ` (${kept} still counted from before)` : ''}`; } };
  }, [signIn, nameOf, scenario.id]);

  /** The story's own taps: open the sheet on the person's Echo and say yes to each page in turn. */
  const answerOnScreen = useCallback(async (member: string, mode: PromptMode) => {
    const p = await openPrompt(member, mode);
    setPrompts((x) => ({ ...x, [member]: p }));
    await sleep(mode === 'confirm' ? 700 : 1000);
    for (let i = 0; i < p.entries.length; i++) {
      const e = p.entries[i];
      setPrompt(member, { page: i });
      if (i) await sleep(260);
      setEntry(member, e.id, { state: 'pressing', pick: 0 });
      await sleep(mode === 'confirm' ? 340 : 480);
      await submit(member, mode, e, { choice: 'yes' });
      await sleep(mode === 'confirm' ? 300 : 520);
    }
    await sleep(350);
    await finish(member);
  }, [openPrompt, setPrompt, setEntry, submit, finish]);

  /** Someone watching clicks an option themselves. */
  const clickPick = useCallback(async (member: string, e: PromptEntry, a: Answer) => {
    const p = prompts[member];
    if (!p || playing) return;
    try {
      await submit(member, p.mode, e, a);
      const next = p.entries.findIndex((x) => x.id !== e.id && (x.state === 'open' || x.state === 'editing'));
      if (next >= 0) { await sleep(300); setPrompt(member, { page: next }); } else { await sleep(500); await finish(member); }
    } catch (err) {
      setError((err as Error).message);
    }
  }, [prompts, playing, submit, setPrompt, finish]);

  // --------------------------------------------------------------------------------------------- the steps

  /** Several people ask their own Alexa to join with the code Alice's Alexa read out, side by side, then each taps Yes. */
  const joinStep = useCallback(async (ids: string[], template: string) => {
    const inv = invite.current ?? (() => {
      for (const l of [...(transcripts[organizerId] ?? [])].reverse()) {
        const call = l.turn?.calls.find((c) => c.name === 'invite_to_group' && c.ok);
        const r = call?.result as { code?: string; group?: { name?: string } } | undefined;
        if (r?.code) return { code: r.code, group: r.group?.name ?? 'the group' };
      }
      return null;
    })();
    if (!inv) throw new Error("There is no invite from Alice's Alexa yet. This step has not advanced.");
    const text = template.replace('{invite}', inv.code);
    show(ids);
    // Everyone speaks a beat apart; the replies are revealed together once every Alexa has answered, so no one's reply
    // happens off camera while the others are still thinking.
    const turns = await Promise.all(ids.map(async (id, i) => {
      await sleep(i * STAGGER_MS);
      await speak(id, text);
      return ask(id, text);
    }));
    await Promise.all(ids.map(async (id, i) => { await sleep(i * STAGGER_MS); await answerOut(id, turns[i]); }));
    await sleep(300);
    await Promise.all(ids.map(async (id, i) => { await sleep(i * STAGGER_MS * 2); await answerOnScreen(id, 'join'); }));
    await refresh();
    onChanged();
    await sleep(500);
  }, [transcripts, organizerId, show, speak, ask, answerOut, answerOnScreen, refresh, onChanged]);

  /** What GroupFlow just sent these people arrives on their Echos, a beat apart. Nothing is called: it is already theirs. */
  const deliverStep = useCallback(async (ids: string[]) => {
    await refresh();
    const missing = ids.filter((id) => !(logs.current[id] ?? []).some((e) => e.held));
    if (missing.length === ids.length) throw new Error(`GroupFlow has not sent ${andList(ids.map(nameOf))} anything new. This step has not advanced.`);
    show(ids, { reveal: false });
    await sleep(600);
    for (const id of ids) {
      reveal_([id]);
      flash(id);
      await sleep(550);
    }
    await sleep(900);
  }, [refresh, show, reveal_, flash, nameOf]);

  /** People answer what is waiting on their own screens; several at once side by side. */
  const screenStep = useCallback(async (ids: string[], mode: PromptMode) => {
    await refresh();
    show(ids);
    await sleep(500);
    await Promise.all(ids.map(async (id, i) => { await sleep(i * STAGGER_MS); await answerOnScreen(id, mode); }));
    await refresh();
    await sleep(400);
  }, [refresh, show, answerOnScreen]);

  const playNext = useCallback(async () => {
    if (playing || step >= script.length) return;
    const s = script[step];
    setPlaying(true);
    setError(null);
    stepSince.current = seq.current;
    try {
      if (s.chapter && s.clock) await api.demoClock(s.chapter, s.clock);
      if (s.kind === 'say' && s.member && s.text) {
        await speakAndAnswer(s.member, s.text);
      } else if (s.kind === 'join') {
        await joinStep(s.members ?? [], s.text ?? 'Alexa, join the group. The code is {invite}.');
      } else if (s.kind === 'deliver') {
        await deliverStep(s.members ?? []);
      } else if (s.kind === 'verify' && s.member) {
        await screenStep([s.member], 'verify');
      } else if (s.kind === 'confirm') {
        await screenStep(s.members ?? [], 'confirm');
      } else if (s.kind === 'skip') {
        const r = await api.skipAhead(s.hours ?? 48, scenario.id);
        const reached = r.checkins.flatMap((c) => c.sent.map((x) => x.name));
        if (!reached.length) throw new Error('Nobody was checked in with: the plan is not waiting on anyone. This step has not advanced.');
        const ids = members.filter((m) => reached.includes(m.name)).map((m) => m.id);
        // Alexa starts this conversation: the reminder arrives on the Echo of whoever has not answered.
        visible.current = new Set();
        await refresh();
        show(ids, { reveal: false });
        await sleep(600);
        for (const id of ids) { reveal_([id]); flash(id); await sleep(550); }
        await sleep(600);
      } else if (s.kind === 'event' && s.place_id) {
        visible.current = new Set();
        await api.demoEvent(s.group ?? scenario.id, s.place_id, s.start, s.end, s.day);
        if (s.members?.length) await deliverStep(s.members);
      }
      setNote(s.note);
      setStep((n) => n + 1);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPlaying(false);
    }
  }, [playing, step, script, speakAndAnswer, joinStep, deliverStep, screenStep, scenario.id, members, refresh, show, reveal_, flash, onChanged]);

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
  // Only people in the group appear; before Alice creates it, only Alice. The rest pop in as they join.
  const people = members.filter((m) => (inGroup ? inGroup.has(m.id) : m.id === organizerId));
  const onScreen = view.map((id) => members.find((m) => m.id === id)).filter(Boolean) as Member[];
  const compact = onScreen.length > 1;
  const [status, statusSub] = plan ? STATUS[plan.status] ?? [plan.status, ''] : inGroup ? ['No plan yet', 'Alice is about to start one'] : ['No group yet', 'Alice is about to start one'];
  const proof = plan?.working?.kind === 'candidate' && plan.repair?.proof ? plan.repair.proof : null;
  // A report matters while the plan still uses that place; once the plan has moved on, it is history.
  const inUse = new Set([...(plan?.agreed?.activities ?? []), ...(plan?.working?.activities ?? [])].map((a) => a.place));
  const reports = (plan?.observations ?? []).filter((o) => inUse.has(/^(.+?) became unavailable/.exec(o)?.[1] ?? /reported that (.+?) is unavailable/.exec(o)?.[1] ?? ''));
  const bookings = plan?.status === 'COMMITTED' ? (plan.bookings ?? []).filter((b) => b.includes('cancelled') || b.includes(`SIM-${plan.agreed?.label}-`)).slice(-3) : [];
  const isShared = !!plan && (shared.current.has(plan.id) || plan.status !== 'DRAFT');

  const inputFor = (person: Member) => {
    const draft = echo.drafts[person.id] ?? '';
    const busy = !!echo.busy[person.id];
    return (
      <form className="x-input" onSubmit={(e) => {
        e.preventDefault();
        const t = draft.trim();
        if (!t || busy || playing) return;
        echo.setDraft(person.id, '');
        void (async () => {
          try {
            stepSince.current = seq.current;
            const words = t.split(/\s+/);
            speaking.current.add(person.id);
            setSpeech((s) => ({ ...s, [person.id]: { words, shown: words.length, sent: true, since: seq.current } }));
            const turn = await ask(person.id, t);
            await answerOut(person.id, turn);
          } catch (err) { setError((err as Error).message); }
        })();
      }}>
        <input value={draft} placeholder={`Talk to Alexa as ${person.name}`} onChange={(e) => echo.setDraft(person.id, e.target.value)} disabled={busy} aria-label={`Talk to Alexa as ${person.name}`} />
      </form>
    );
  };

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
          <p className="x-note">{note ?? (step > 0 ? script[step - 1]?.note : null) ?? 'Four friends plan dinner for Friday. Each one talks only to their own Alexa; GroupFlow keeps the plan they share.'}</p>
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
          <nav className="x-people" aria-label="The people in the group">
            {people.map((m) => {
              const held = (logs.current[m.id] ?? []).filter((e) => e.held).length;
              return (
                <button key={m.id} type="button" className={`${view.includes(m.id) ? 'on' : ''}`} style={{ ['--tone' as string]: tone(m) }}
                  onClick={() => { if (!playing) { stepSince.current = seq.current; show([m.id]); } }}>
                  <span className="x-avatar">{m.name[0]}</span>
                  <span>{m.name}</span>
                  {held > 0 && <small className="x-badge" title="New from GroupFlow, not heard yet">{held}</small>}
                </button>
              );
            })}
            {!inGroup && <span className="x-people-note">No group yet</span>}
          </nav>

          <div className={`x-echos ${compact ? 'stacked' : ''}`} style={{ ['--rows' as string]: String(onScreen.length) }}>
            {onScreen.map((person) => (
              <EchoView key={person.id} person={person} compact={compact}
                entries={(logs.current[person.id] ?? []).filter((e) => !e.held)}
                speech={speech[person.id]} reveal={reveal[person.id]} busy={!!echo.busy[person.id]} since={stepSince.current}
                prompt={prompts[person.id]} notify={!!notify[person.id]}
                input={compact ? undefined : inputFor(person)}
                onPick={(e, a) => void clickPick(person.id, e, a)}
                onEdit={(e, on) => setEntry(person.id, e.id, { state: on ? 'editing' : 'open', pick: on ? 2 : undefined })} />
            ))}
          </div>
        </section>

        <section className="x-right">
          <section className="x-card x-plan" aria-label="The plan">
            <header>
              <h2>{plan?.title ?? 'Friday dinner'}</h2>
              <span className={`x-status ${plan ? TONE_OF_STATUS[plan.status] ?? 'quiet' : 'quiet'}`}><b>{status}</b>{statusSub && <> · {statusSub}</>}</span>
              {plan && <span className="x-sub">{plan.dates} · organized by {plan.organizer}</span>}
            </header>
            {plan ? <Timeline plan={plan} problems={problems.items} /> : <p className="x-empty big">{inGroup ? 'No plan yet. Alice is about to ask Alexa to start one.' : 'No group yet. Alice is about to ask Alexa to start one and invite her friends.'}</p>}
            <div className="x-facts">
              {problems.items.map((p) => <p key={p} className="x-fact bad">{problems.why}: {p.replace(/(\d{2}):(\d{2})/g, (_m, h, mm) => clock24(`${h}:${mm}`))}</p>)}
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
            <AnswerGrid plan={plan} people={people} memory={memory.current} shared={isShared} />
            <PrivateLimits plan={plan} people={people} />
          </div>
        </section>
      </main>
    </div>
  );
}
