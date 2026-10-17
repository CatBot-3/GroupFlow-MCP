import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Member, TranscriptLine } from '../api';
import { canListen, listen } from '../speech';

export interface EchoBadges { decisions: number; changes: number }

interface Props {
  member: Member;
  lines: TranscriptLine[];
  busy: boolean;
  error: string | null;
  draft: string;
  onDraft: (value: string) => void;
  onSend: (text: string) => void;
  badges: EchoBadges;
  cued: boolean;
  suggestion: string;
  /** Dinner: each card has a second face, the member's own GroupFlow page. */
  face?: 'alexa' | 'page';
  onFace?: (face: 'alexa' | 'page') => void;
  pageBadge?: number;
  page?: ReactNode;
  showDiagnostics?: boolean;
}

const TOOL_WORDS: Record<string, string> = {
  get_context: 'read plans',
  search_plan: 'searched',
  create_plan: 'made plan',
  propose_change: 'changed',
  decide_change: 'decided',
  link_items: 'linked',
  set_my_conditions: 'drafted condition',
  report_observation: 'reported',
  request_confirmations: 'asked to confirm',
  find_alternatives: 'alternatives',
  get_alternatives: 'alternatives',
  commit_plan: 'commit',
  explain: 'explained',
  get_history: 'history',
  create_group: 'started group',
  invite_to_group: 'made invite',
  notify_people: 'told people',
  ask_followup: 'asked first',
  answer_followup: 'acted on answer',
  set_reminder: 'set reminder',
  review_plan: 'checked progress',
};

const QUIET = new Set(['get_context', 'search_plan', 'explain', 'get_history']);

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function outcomeOf(result: unknown): string | null {
  const r = result as { outcome?: string; code?: string; status?: string; proof?: { status?: string } } | null;
  if (!r) return null;
  return r.outcome ?? r.proof?.status ?? (r.code ? r.code.toLowerCase() : null);
}

export function Echo({ member, lines, busy, error, draft, onDraft, onSend, badges, cued, suggestion, face = 'alexa', onFace, pageBadge = 0, page, showDiagnostics = true }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length, busy, draft, face]);

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (text && !busy) onSend(text);
  };

  const toggleMic = () => {
    if (listening) {
      stopRef.current?.();
      return;
    }
    setVoiceError(null);
    setListening(true);
    stopRef.current = listen(
      (text) => {
        onDraft(text);
        onSend(text);
      },
      (err) => {
        setListening(false);
        if (err) setVoiceError(err);
      },
    );
  };

  const color = { ['--member' as string]: member.color };
  const onPage = face === 'page' && page;

  return (
    <article className={`echo ${busy ? 'busy' : ''} ${listening ? 'listening' : ''} ${cued ? 'cued' : ''} ${onPage ? 'on-page' : ''}`} style={color} aria-label={`${member.name}'s devices`}>
      <div className="lightbar" aria-hidden="true" />
      <header className={`echo-head ${onFace ? 'with-faces' : ''}`}>
        <div>
          <h2 className="echo-name" title={member.role}>{member.name}</h2>
          {!onFace && <p className="echo-role">{member.role}</p>}
        </div>
        {onFace ? (
          <div className="faces" role="tablist" aria-label={`${member.name}: Alexa or own page`}>
            <button type="button" role="tab" aria-selected={face === 'alexa'} className={face === 'alexa' ? 'on' : ''} onClick={() => onFace('alexa')}>Alexa</button>
            <button type="button" role="tab" aria-selected={face === 'page'} className={face === 'page' ? 'on' : ''} onClick={() => onFace('page')}
              aria-label={`${member.name}'s own GroupFlow page${pageBadge ? `, ${pageBadge} to confirm` : ''}`}>
              Own page{pageBadge > 0 && <span className="face-count">{pageBadge}</span>}
            </button>
          </div>
        ) : (
          <div className="echo-badges">
            {badges.decisions > 0 && <span className="badge decision">{badges.decisions} to decide</span>}
            {badges.changes > 0 && <span className="badge change">{badges.changes} changed</span>}
          </div>
        )}
      </header>

      {onPage ? (
        <div className="page-face">{page}</div>
      ) : (
        <>
          <div className="echo-screen" ref={scroller} aria-live="polite">
            {lines.length === 0 && !busy && (
              <p className="echo-empty">
                {member.name}'s Echo is listening. Try <button type="button" className="suggest" onClick={() => onSend(suggestion)}>“{suggestion}”</button>
              </p>
            )}
            {lines.map((l, i) => (
              l.role === 'user' ? (
                <p key={i} className="said spoken">{l.text}</p>
              ) : (
                <div key={i} className="reply">
                  <p>{l.text}</p>
                  {l.turn && l.turn.llm && showDiagnostics && (
                    <p className="turn-stats">
                      {l.turn.llm.calls.length} model call{l.turn.llm.calls.length === 1 ? '' : 's'}, {formatTokens(l.turn.llm.inputTokens + l.turn.llm.outputTokens)} tokens, {(l.turn.ms / 1000).toFixed(1)} s
                    </p>
                  )}
                  {l.turn && l.turn.calls.length > 0 && showDiagnostics && (
                    <ul className="calls" aria-label="Tools Alexa called">
                      {l.turn.calls.map((c, j) => {
                        const out = outcomeOf(c.result);
                        return (
                          <li key={j} className={`${c.ok ? '' : 'failed'} ${c.prefetched ? 'pre' : ''}`}
                            title={c.prefetched ? `${c.name}: read by the simulator at the start of the turn` : `${c.name} ${JSON.stringify(c.args)}`}>
                            {TOOL_WORDS[c.name] ?? c.name}{out && !QUIET.has(c.name) ? `: ${out.replace(/_/g, ' ')}` : ''}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              )
            ))}
            {busy && <p className="thinking" aria-label="Alexa is working">Alexa is thinking and checking with GroupFlow</p>}
            {(error || voiceError) && <p className="echo-error" role="alert">{error ?? voiceError}</p>}
          </div>

          <form className="echo-input" onSubmit={submit}>
            <label className="sr-only" htmlFor={`say-${member.id}`}>Say something to Alexa as {member.name}</label>
            <input
              id={`say-${member.id}`}
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              placeholder={`Talk to Alexa as ${member.name}`}
              autoComplete="off"
              disabled={busy}
            />
            {canListen && (
              <button type="button" className={`mic ${listening ? 'on' : ''}`} onClick={toggleMic} aria-pressed={listening} aria-label={listening ? 'Stop listening' : `Speak as ${member.name}`} disabled={busy}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" /></svg>
              </button>
            )}
            <button type="submit" className="send" disabled={busy || !draft.trim()}>Send</button>
          </form>
        </>
      )}
    </article>
  );
}
