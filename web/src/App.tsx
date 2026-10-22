import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type AgentTurn, type Config, type TrafficEntry, type TranscriptLine } from './api';
import { TrafficStrip } from './components/TrafficStrip';
import { ExplainerStage } from './stage/ExplainerStage';
import { GroupStage } from './stage/GroupStage';
import { canSpeak, speak } from './speech';

export interface EchoControls {
  busy: Record<string, boolean>;
  errors: Record<string, string | null>;
  drafts: Record<string, string>;
  setDraft: (member: string, value: string) => void;
  send: (member: string, text: string, plan?: string) => Promise<AgentTurn | null>;
}

export const prefersReducedMotion = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Type a line into an Echo so viewers can read it before it is sent. */
export async function typeInto(setDraft: (member: string, v: string) => void, member: string, text: string) {
  // A hidden tab runs its timers about once a second, which would turn this into a minute of typing: skip it there.
  if (prefersReducedMotion() || document.hidden) {
    setDraft(member, text);
    return;
  }
  for (let i = 1; i <= text.length; i += 2) {
    if (document.hidden) break;
    setDraft(member, text.slice(0, i));
    await sleep(14);
  }
  setDraft(member, text);
  await sleep(250);
}

const GROUP_KEY = 'groupflow.group';

/** Query parameters for links straight into a moment of the demo, e.g. ?group=family&view=linda&plan=party&pages=bob. */
export function urlParam(name: string): string | null {
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}

function initialGroup(): string {
  const fromUrl = urlParam('group');
  if (fromUrl) return fromUrl;
  try {
    return window.localStorage.getItem(GROUP_KEY) ?? 'friends';
  } catch {
    return 'friends';
  }
}

const SHORT: Record<string, string> = { family: 'Family', friends: 'Friends', cottage: 'Cottage trip', team: 'Team', study: 'Study group' };

export function App() {
  const [mode, setMode] = useState<'demo' | 'lab'>(() => urlParam('mode') === 'lab' || (urlParam('group') && urlParam('group') !== 'friends') ? 'lab' : 'demo');
  const [config, setConfig] = useState<Config | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [protocol, setProtocol] = useState<string | null>(null);
  const [group, setGroup] = useState<string>(initialGroup);
  const [transcripts, setTranscripts] = useState<Record<string, TranscriptLine[]>>({});
  const [traffic, setTraffic] = useState<TrafficEntry[]>([]);
  const [tick, setTick] = useState(0);
  const [resetKey, setResetKey] = useState(0);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [speakReplies, setSpeakReplies] = useState(false);
  const speakRef = useRef(speakReplies);
  speakRef.current = speakReplies;
  const groupRef = useRef(group);
  groupRef.current = group;

  const bump = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    try {
      window.localStorage.setItem(GROUP_KEY, group);
    } catch {
      /* storage unavailable */
    }
  }, [group]);

  const loadTranscripts = useCallback(async () => {
    try {
      setTranscripts(await api.transcripts());
      setFatal(null);
    } catch (err) {
      setFatal((err as Error).message);
    }
  }, []);

  useEffect(() => {
    api.config().then((cfg) => { setConfig(cfg); if (!cfg.scenarios.some((s) => s.id === 'friends')) setMode('lab'); }).catch((e) => setFatal((e as Error).message));
    api.health().then((h) => setProtocol(h.mcp?.protocol ?? null)).catch(() => setProtocol(null));
    api.traffic().then(setTraffic).catch(() => undefined);
    void loadTranscripts();
    const es = new EventSource('/api/stream');
    es.addEventListener('traffic', (e) => {
      const entry = JSON.parse((e as MessageEvent).data) as TrafficEntry;
      setTraffic((prev) => (prev.some((p) => p.seq === entry.seq) ? prev : [...prev, entry].slice(-500)));
    });
    for (const name of ['turn', 'event', 'human', 'forget', 'project']) {
      es.addEventListener(name, () => {
        void loadTranscripts();
        bump();
      });
    }
    es.addEventListener('reset', () => {
      setTraffic([]);
      void loadTranscripts();
      setResetKey((k) => k + 1);
      bump();
    });
    return () => es.close();
  }, [loadTranscripts, bump]);

  const setDraft = useCallback((member: string, value: string) => setDrafts((d) => ({ ...d, [member]: value })), []);

  const send = useCallback(async (member: string, text: string, plan?: string): Promise<AgentTurn | null> => {
    setBusy((b) => ({ ...b, [member]: true }));
    setErrors((e) => ({ ...e, [member]: null }));
    try {
      const turn = await api.say(member, text, groupRef.current, plan);
      setDrafts((d) => ({ ...d, [member]: '' }));
      if (speakRef.current) speak(turn.reply);
      await loadTranscripts();
      bump();
      return turn;
    } catch (err) {
      setErrors((e) => ({ ...e, [member]: (err as Error).message }));
      return null;
    } finally {
      setBusy((b) => ({ ...b, [member]: false }));
    }
  }, [loadTranscripts, bump]);

  const echo: EchoControls = useMemo(() => ({ busy, errors, drafts, setDraft, send }), [busy, errors, drafts, setDraft, send]);

  const reset = useCallback(async (target?: string) => {
    try {
      await api.reset(target ?? groupRef.current);
      setDrafts({});
      setErrors({});
      setResetKey((k) => k + 1);
      await loadTranscripts();
      bump();
    } catch (err) {
      setFatal((err as Error).message);
    }
  }, [loadTranscripts, bump]);

  const selected = mode === 'demo' && config?.scenarios.some((s) => s.id === 'friends') ? 'friends' : group;
  groupRef.current = selected;
  const current = config?.scenarios.find((s) => s.id === selected) ?? config?.scenarios[0] ?? null;
  const memberIds = useMemo(() => new Set(current?.members.map((m) => m.id) ?? []), [current]);
  const groupTraffic = useMemo(() => traffic.filter((e) => memberIds.has(e.member)), [traffic, memberIds]);

  if (!config || !current) {
    return (
      <div className="boot">
        <p className="wordmark">GroupFlow</p>
        <p>{fatal ? `The simulator backend is not reachable: ${fatal}. Start it with "python -m groupflow demo" from the GroupFlow folder.` : 'Connecting to the simulator…'}</p>
      </div>
    );
  }

  const story = mode === 'demo' ? config.scenarios.find((sc) => sc.id === 'friends') : undefined;
  if (story) {
    const controls = (
      <>
        {canSpeak && <label className="x-ctl"><input type="checkbox" checked={speakReplies} onChange={(e) => setSpeakReplies(e.target.checked)} />Speak replies</label>}
        <button type="button" className="x-ctl" onClick={() => void reset('friends')}>Reset the story</button>
        <button type="button" className="x-ctl" onClick={() => setMode('lab')}>Test lab</button>
      </>
    );
    return (
      <>
        {fatal && <div className="banner" role="alert">{fatal}</div>}
        <ExplainerStage scenario={{ ...story, script: config.showcase?.script ?? story.script }} transcripts={transcripts} echo={echo} tick={tick} resetKey={resetKey}
          onChanged={bump} controls={controls} live={{ mcp: !!protocol, model: config.provider.ready, modelNote: config.provider.ready ? null : config.provider.reason }} />
      </>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="wordmark">GroupFlow</span>
          <div className="seg" aria-label="Experience">
            {config.scenarios.some((s) => s.id === 'friends') && <button type="button" onClick={() => setMode('demo')}>Story demo</button>}
            <button type="button" className="on">Test lab</button>
          </div>
          <div className="seg scenario-switch" role="radiogroup" aria-label="Group">
            {config.scenarios.map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={s.id === current.id} className={s.id === current.id ? 'on' : ''} onClick={() => setGroup(s.id)}
                title={`${s.label}: ${s.plans.map((p) => p.title).join(', ')}`}>
                {SHORT[s.id] ?? s.label}
              </button>
            ))}
          </div>
        </div>
        <div className="status">
          <span className={`pill ${protocol ? 'ok' : 'bad'}`} title={config.mcpUrl}>
            {protocol ? `MCP ${protocol} over Streamable HTTP` : 'MCP server not reachable'}
          </span>
          <span className={`pill ${config.provider.ready ? 'ok' : 'bad'}`} title={config.provider.ready ? 'The LLM decides which MCP tools to call; the GroupFlow server applies the rules' : config.provider.reason ?? ''}>
            {config.provider.ready ? `Alexa brain: ${config.provider.label} ${config.provider.model}` : `Alexa brain: no ${config.provider.label} key`}
          </span>
          {canSpeak && (
            <label className="toggle">
              <input type="checkbox" checked={speakReplies} onChange={(e) => setSpeakReplies(e.target.checked)} />
              Speak replies
            </label>
          )}
          {config.chat && <a className="ghost chat-link" href="/chat" target="_blank" rel="noreferrer">Open the group chat</a>}
          <button type="button" className="ghost" onClick={() => void reset()}>Reset this group</button>
        </div>
      </header>

      {fatal && <div className="banner" role="alert">{fatal}</div>}
      {!config.provider.ready && (
        <div className="banner" role="alert">Alexa needs an LLM to understand people. {config.provider.reason}</div>
      )}

      <GroupStage key={current.id} scenario={current} transcripts={transcripts} echo={echo} tick={tick} resetKey={resetKey} onReset={reset} onChanged={bump} />

      <TrafficStrip entries={groupTraffic} members={current.members} />
    </div>
  );
}
