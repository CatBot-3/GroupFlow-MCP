import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AuthError, chatApi, loadSession, openStream, saveSession,
  type ChatMe, type ChatMessage, type DemoPerson, type HumanInbox, type InvitePreview, type Room, type Session, type Updates,
} from './chatApi';
import './chat.css';

// Messages the server queued for this person (nobody was talking to Alexa when they were made).
const NOTICE_WORD: Record<string, string> = { notice: 'update', reminder: 'reminder', nudge: 'check-in', poll: 'vote', rsvp: 'coming', bring: 'bring list', costs: 'costs' };
const NOTICE_TITLE: Record<string, string> = {
  notice: 'Someone asked Alexa to let you know',
  reminder: 'A reminder someone set, sent when its time came',
  nudge: 'The plan has been quiet while waiting on you; GroupFlow checked in',
  poll: 'A vote in one of your plans: started, closed or decided',
  rsvp: 'Someone answered whether they are coming',
  bring: 'The bring list changed',
  costs: 'A shared cost or repayment that involves you',
};

/* The group chat: people talk in a room per group and privately with Alexa. Messages that start with @Alexa (or "Alexa,")
   ask the assistant, which acts as the person who wrote them. Answers that need a person's consent never come from a message:
   each person taps them on their own cards, which talk to their own GroupFlow page. */

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const initials = (name: string) => name.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();

function inviteFromUrl(): string | null {
  try {
    return new URLSearchParams(window.location.search).get('invite');
  } catch {
    return null;
  }
}

function clearInviteFromUrl() {
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete('invite');
    window.history.replaceState(null, '', url.toString());
  } catch {
    /* nothing to clear */
  }
}

// ------------------------------------------------------------------ sign in / sign up

function AuthScreen({ invite, preview, onSession }: { invite: string | null; preview: InvitePreview | null; onSession: (s: Session, joinedNow: boolean) => void }) {
  const [mode, setMode] = useState<'up' | 'in'>(invite ? 'up' : 'in');
  const [name, setName] = useState('');
  const [memberId, setMemberId] = useState('');
  const [passcode, setPasscode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState<DemoPerson[]>([]);

  useEffect(() => { void chatApi.demoPeople().then(setDemo); }, []);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="chat-auth">
      <div className="chat-auth-card">
        <p className="wordmark">GroupFlow</p>
        <h1>Plan with your people. Alexa keeps everyone in agreement.</h1>
        {preview && (
          <p className="chat-invite-note">
            <strong>{preview.invited_by}</strong> invited you to <strong>{preview.group.name}</strong> ({preview.members} {preview.members === 1 ? 'person' : 'people'}).
            {mode === 'up' ? ' Sign up to join.' : ' Sign in, then join from the banner.'}
          </p>
        )}
        {invite && !preview && <p className="chat-invite-note bad">This invite link is not valid any more. Ask for a new one.</p>}
        <div className="seg" role="tablist" aria-label="Account">
          <button type="button" role="tab" aria-selected={mode === 'up'} className={mode === 'up' ? 'on' : ''} onClick={() => setMode('up')}>Sign up</button>
          <button type="button" role="tab" aria-selected={mode === 'in'} className={mode === 'in' ? 'on' : ''} onClick={() => setMode('in')}>Sign in</button>
        </div>
        {mode === 'up' ? (
          <form className="chat-form" onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const s = await chatApi.signUp(name.trim(), passcode, preview ? invite ?? undefined : undefined);
              onSession({ token: s.token, csrf: s.csrf, member: s.member }, !!s.joined);
            });
          }}>
            <label>Your name<input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={40} required /></label>
            <label>Passcode (6+ characters)<input type="password" value={passcode} onChange={(e) => setPasscode(e.target.value)} autoComplete="new-password" minLength={6} required /></label>
            <button className="primary" type="submit" disabled={busy}>{preview ? `Sign up and join ${preview.group.name}` : 'Create my account'}</button>
            <p className="chat-fine">Your sign-in name is shown after sign-up. Your passcode is stored hashed and never reaches Alexa.</p>
          </form>
        ) : (
          <form className="chat-form" onSubmit={(e) => {
            e.preventDefault();
            void run(async () => { onSession(await chatApi.signIn(memberId.trim().toLowerCase(), passcode), false); });
          }}>
            <label>Sign-in name<input value={memberId} onChange={(e) => setMemberId(e.target.value)} autoComplete="username" required /></label>
            <label>Passcode<input type="password" value={passcode} onChange={(e) => setPasscode(e.target.value)} autoComplete="current-password" required /></label>
            <button className="primary" type="submit" disabled={busy}>Sign in</button>
          </form>
        )}
        {error && <p className="chat-error" role="alert">{error}</p>}
        {demo.length > 0 && (
          <div className="chat-demo">
            <p>Or try it as someone from the demo world (open another tab to be a second person):</p>
            <div className="chat-demo-people">
              {demo.map((p) => (
                <button key={p.id} type="button" className="ghost" disabled={busy} onClick={() => void run(async () => { onSession(await chatApi.signIn(p.id, p.passcode), false); })}>
                  <span className="speaker-dot" style={{ background: p.color }} />{p.name}
                </button>
              ))}
            </div>
          </div>
        )}
        <p className="chat-fine">Alexa here is a simulation of Alexa+ talking to the GroupFlow MCP server. <a href="/">Back to the demo</a></p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ messages

function Message({ m, mine, colors, onQuick }: {
  m: ChatMessage; mine: boolean; colors: Record<string, string>; onQuick?: (answer: string) => void;
}) {
  if (m.author === 'system') return <li className="chat-msg system"><span>{m.text}</span></li>;
  const alexa = m.author === 'assistant';
  const calls = (m.meta.calls ?? []).filter((c) => c.name !== 'get_context');
  return (
    <li className={`chat-msg ${alexa ? 'alexa' : mine ? 'mine' : 'theirs'}`}>
      {!mine && (
        <span className="chat-avatar" style={alexa ? undefined : { background: colors[m.author] ?? 'var(--ink-3)' }} aria-hidden="true">
          {alexa ? 'A' : initials(m.author_name)}
        </span>
      )}
      <div className="chat-bubble">
        <p className="chat-meta">
          <strong>{alexa ? 'Alexa' : mine ? 'You' : m.author_name}</strong>
          {alexa && m.meta.for && <span> answering {colors[`name:${m.meta.for}`] ?? 'someone'}</span>}
          {alexa && m.meta.notice && <span className="chat-notice-tag" title={NOTICE_TITLE[m.meta.kind ?? 'notice']}>{NOTICE_WORD[m.meta.kind ?? 'notice'] ?? 'update'}{m.meta.plan ? ` · ${m.meta.plan}` : ''}</span>}
          <time dateTime={m.at}>{time(m.at)}</time>
        </p>
        <p className={alexa ? 'spoken' : undefined}>{m.text}</p>
        {onQuick && m.meta.followup && (
          <p className="chat-quick" role="group" aria-label="Quick answers (the server runs what you pick)">
            {m.meta.followup.options.map((o) => <button key={o} type="button" className="ghost" onClick={() => onQuick(o)}>{o}</button>)}
          </p>
        )}
        {alexa && calls.length > 0 && (
          <p className="chat-tools" title="MCP tools Alexa called for this answer, and what the GroupFlow server decided">
            {calls.map((c, i) => <span key={i} className={c.ok ? '' : 'refused'}>{c.name.replace(/_/g, ' ')}{c.outcome ? ` · ${c.outcome.replace(/_/g, ' ')}` : ''}</span>)}
          </p>
        )}
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ the person's own answers (never through Alexa)

function OwnPanel({ inbox, updates, busy, onAnswer, onVerify }: {
  inbox: HumanInbox | null; updates: Updates | null; busy: boolean;
  onAnswer: (scopeId: string, decision: 'approved' | 'rejected') => void; onVerify: (id: string, digest: string) => void;
}) {
  const drafts = (inbox?.conditions ?? []).filter((c) => c.status === 'unverified');
  return (
    <aside className="chat-own" aria-label="Your answers">
      <h2>Needs your answer</h2>
      <p className="chat-fine">Only you can answer these. Alexa can ask, never answer for you.</p>
      {!inbox?.pending.length && !drafts.length && <p className="chat-empty-note">Nothing waiting for you.</p>}
      {inbox?.pending.map((p) => (
        <div key={p.scope_id} className="chat-card">
          <p className="chat-card-plan">{p.plan} · {p.revision}</p>
          <p>{p.text}</p>
          <div className="chat-card-actions">
            <button type="button" className="primary" disabled={busy} onClick={() => onAnswer(p.scope_id, 'approved')}>Confirm</button>
            <button type="button" className="ghost" disabled={busy} onClick={() => onAnswer(p.scope_id, 'rejected')}>Decline</button>
          </div>
        </div>
      ))}
      {drafts.map((c) => (
        <div key={c.id} className="chat-card">
          <p className="chat-card-plan">{c.plan} · private condition</p>
          <p>{c.description}</p>
          <div className="chat-card-actions">
            <button type="button" className="primary" disabled={busy} onClick={() => onVerify(c.id, c.verify_digest)}>That's right</button>
          </div>
        </div>
      ))}
      {updates && (updates.updates.length > 0 || updates.decisions.length > 0) && (
        <>
          <h2>Updates for you</h2>
          <ul className="chat-updates">
            {updates.decisions.map((d, i) => <li key={`d${i}`}><strong>{d.by}</strong> suggests {d.what}: {d.proposed} ({d.plan}). Tell Alexa your answer.</li>)}
            {updates.updates.map((u, i) => <li key={`u${i}`}>{u}</li>)}
          </ul>
          <p className="chat-fine">Ask Alexa in your private thread to hear them; that marks them heard.</p>
        </>
      )}
    </aside>
  );
}

// ------------------------------------------------------------------ the app

export function ChatApp() {
  const [session, setSession] = useState<Session | null>(loadSession);
  const [me, setMe] = useState<ChatMe | null>(null);
  const [roomId, setRoomId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, ChatMessage[]>>({});
  const [unread, setUnread] = useState<Record<string, number>>({});
  const [typing, setTyping] = useState<Record<string, string | null>>({});
  const [draft, setDraft] = useState('');
  const [inbox, setInbox] = useState<HumanInbox | null>(null);
  const [updates, setUpdates] = useState<Updates | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<string | null>(inviteFromUrl);
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [newGroup, setNewGroup] = useState<string | null>(null);
  const [shareCode, setShareCode] = useState<{ code: string; url?: string } | null>(null);
  const [showOwn, setShowOwn] = useState(false);
  const listRef = useRef<HTMLOListElement>(null);
  const roomRef = useRef(roomId);
  roomRef.current = roomId;

  const signOut = useCallback((message?: string) => {
    saveSession(null);
    setSession(null);
    setMe(null);
    setMessages({});
    setInbox(null);
    setUpdates(null);
    if (message) setError(message);
  }, []);

  const guard = useCallback(async (fn: () => Promise<void>) => {
    try {
      await fn();
    } catch (err) {
      if (err instanceof AuthError) signOut('Your session ended. Sign in again.');
      else setError((err as Error).message);
    }
  }, [signOut]);

  useEffect(() => {
    if (!invite) return;
    chatApi.preview(invite).then(setPreview).catch(() => setPreview(null));
  }, [invite]);

  const refreshMe = useCallback(async (s: Session, refresh = false) => {
    const data = await chatApi.me(s, refresh);
    setMe(data);
    setRoomId((cur) => (cur && data.rooms.some((r) => r.id === cur) ? cur : data.rooms[0]?.id ?? null));
    return data;
  }, []);

  const refreshOwn = useCallback(async (s: Session) => {
    const [ib, up] = await Promise.all([chatApi.inbox(s).catch(() => null), chatApi.updates(s).catch(() => null)]);
    setInbox(ib);
    setUpdates(up);
  }, []);

  useEffect(() => {
    if (!session) return;
    void guard(async () => {
      await refreshMe(session, true);
      await refreshOwn(session);
    });
  }, [session, guard, refreshMe, refreshOwn]);

  // Live events for every room this person is in.
  useEffect(() => {
    if (!session) return;
    let stop: (() => void) | null = null;
    let timer: number | undefined;
    const connect = () => {
      stop = openStream(session, (event, data) => {
        if (event === 'typing') {
          setTyping((t) => ({ ...t, [data.room]: (data as unknown as { for: string }).for }));
          return;
        }
        if (event !== 'message') return;
        setMessages((all) => {
          const cur = all[data.room] ?? [];
          return cur.some((m) => m.id === data.id) ? all : { ...all, [data.room]: [...cur, data] };
        });
        if (data.author === 'assistant') {
          setTyping((t) => ({ ...t, [data.room]: null }));
          void refreshOwn(session);
        }
        if (data.room !== roomRef.current) setUnread((u) => ({ ...u, [data.room]: (u[data.room] ?? 0) + 1 }));
        if (data.author === 'system') void refreshMe(session, true).catch(() => undefined);
      }, (authFailed) => {
        if (authFailed) signOut('Your session ended. Sign in again.');
        else timer = window.setTimeout(connect, 2000);
      });
    };
    connect();
    return () => { stop?.(); window.clearTimeout(timer); };
  }, [session, refreshMe, refreshOwn, signOut]);

  // Load a room's history when it is opened.
  useEffect(() => {
    if (!session || !roomId) return;
    setUnread((u) => ({ ...u, [roomId]: 0 }));
    void guard(async () => {
      const { messages: list } = await chatApi.messages(session, roomId);
      setMessages((all) => ({ ...all, [roomId]: list }));
    });
  }, [session, roomId, guard]);

  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: 'end' });
  }, [messages, roomId, typing]);

  const onSession = (s: Session, joinedNow: boolean) => {
    saveSession(s);
    setError(null);
    setSession(s);
    if (joinedNow) {
      setInvite(null);
      clearInviteFromUrl();
    }
  };

  const room: Room | null = me?.rooms.find((r) => r.id === roomId) ?? null;
  const colors = useMemo(() => {
    const out: Record<string, string> = {};
    for (const r of me?.rooms ?? []) for (const m of r.members) {
      out[m.id] = m.color ?? 'var(--ink-3)';
      out[`name:${m.id}`] = m.name;
    }
    if (me) out[`name:${me.member.id}`] = me.member.name;
    return out;
  }, [me]);

  if (!session) return <AuthScreen invite={invite} preview={preview} onSession={onSession} />;
  if (!me) {
    return (
      <div className="boot">
        <p className="wordmark">GroupFlow</p>
        <p>{error ?? 'Opening your chats…'}</p>
        {error && <button type="button" className="ghost" onClick={() => signOut()}>Sign in again</button>}
      </div>
    );
  }

  const act = (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    void guard(fn).finally(() => setBusy(false));
  };

  const send = (spoken?: string) => {
    const text = (spoken ?? draft).trim();
    if (!text || !room) return;
    act(async () => {
      const r = await chatApi.send(session, room.id, text);
      if (spoken === undefined) setDraft('');
      setMessages((all) => {
        const cur = all[room.id] ?? [];
        return cur.some((m) => m.id === r.message.id) ? all : { ...all, [room.id]: [...cur, r.message] };
      });
      if (r.assistant) setTyping((t) => ({ ...t, [room.id]: me.member.name }));
    });
  };

  const pending = (inbox?.pending.length ?? 0) + (inbox?.conditions.filter((c) => c.status === 'unverified').length ?? 0);
  const organizer = room?.kind === 'group' && room.your_role === 'organizer';
  const roomMessages = room ? messages[room.id] ?? [] : [];
  // Quick answers only on the newest message, and only for the person Alexa asked; tapping one simply says it.
  const lastMessage = roomMessages[roomMessages.length - 1];
  const quickFor = lastMessage?.author === 'assistant' && lastMessage.meta.followup && lastMessage.meta.for === me.member.id ? lastMessage.id : null;
  const quick = (answer: string) => send(room?.kind === 'group' ? `@Alexa ${answer}` : answer);

  return (
    <div className="chat-app">
      <header className="chat-top">
        <span className="wordmark">GroupFlow</span>
        <span className="pill ok" title="Alexa's answers come from an LLM calling the GroupFlow MCP server, as Alexa+ would">Alexa+ simulation</span>
        <span className="chat-top-me"><span className="speaker-dot" style={{ background: me.member.color ?? 'var(--ink-3)' }} />{me.member.name} <small>({me.member.id})</small></span>
        <button type="button" className="ghost chat-own-toggle" onClick={() => setShowOwn((v) => !v)}>Your answers{pending ? ` (${pending})` : ''}</button>
        <button type="button" className="ghost" onClick={() => signOut()}>Sign out</button>
      </header>

      {!me.linked && (
        <div className="chat-banner">
          <p><strong>Let Alexa act for you in this chat?</strong> Alexa will read and change your plans only when you ask it, as you. It can never confirm, decline or withdraw for you, and you can revoke this on your GroupFlow page at any time.</p>
          <button type="button" className="primary" disabled={busy} onClick={() => act(async () => { await chatApi.link(session); await refreshMe(session, true); })}>Link Alexa</button>
        </div>
      )}
      {invite && preview && !me.rooms.some((r) => r.id === preview.group.id) && (
        <div className="chat-banner">
          <p><strong>{preview.invited_by}</strong> invited you to <strong>{preview.group.name}</strong>. Joining shows you the plans shared with the whole group.</p>
          <button type="button" className="primary" disabled={busy} onClick={() => act(async () => {
            const r = await chatApi.join(session, invite);
            setInvite(null);
            clearInviteFromUrl();
            await refreshMe(session, true);
            setRoomId(r.group.id);
          })}>Join {preview.group.name}</button>
          <button type="button" className="ghost" onClick={() => { setInvite(null); clearInviteFromUrl(); }}>Not now</button>
        </div>
      )}
      {error && <div className="chat-banner bad" role="alert"><p>{error}</p><button type="button" className="ghost" onClick={() => setError(null)}>Dismiss</button></div>}

      <div className={`chat-body ${showOwn ? 'own-open' : ''}`}>
        <nav className="chat-rooms" aria-label="Conversations">
          <ul>
            {me.rooms.map((r) => (
              <li key={r.id}>
                <button type="button" className={r.id === roomId ? 'on' : ''} onClick={() => { setRoomId(r.id); setShareCode(null); }}>
                  <span className={`chat-room-icon ${r.kind}`} aria-hidden="true">{r.kind === 'dm' ? 'A' : initials(r.title)}</span>
                  <span className="chat-room-text">
                    <strong>{r.title}</strong>
                    <small>{r.kind === 'group' ? `${r.members.length} ${r.members.length === 1 ? 'person' : 'people'}` : 'Only you and Alexa'}</small>
                  </span>
                  {(unread[r.id] ?? 0) > 0 && <span className="chat-unread">{unread[r.id]}</span>}
                </button>
              </li>
            ))}
          </ul>
          {newGroup === null ? (
            <button type="button" className="ghost chat-new" onClick={() => setNewGroup('')}>+ New group</button>
          ) : (
            <form className="chat-form compact" onSubmit={(e) => {
              e.preventDefault();
              const name = newGroup.trim();
              if (!name) return;
              act(async () => {
                const g = await chatApi.createGroup(session, name);
                setNewGroup(null);
                await refreshMe(session, true);
                setRoomId(g.group.id);
              });
            }}>
              <label>Group name<input autoFocus value={newGroup} onChange={(e) => setNewGroup(e.target.value)} maxLength={60} /></label>
              <div className="chat-card-actions">
                <button type="submit" className="primary" disabled={busy || !newGroup.trim()}>Start</button>
                <button type="button" className="ghost" onClick={() => setNewGroup(null)}>Cancel</button>
              </div>
            </form>
          )}
        </nav>

        <main className="chat-main">
          {room && (
            <>
              <div className="chat-room-head">
                <div>
                  <h1>{room.title}</h1>
                  <p className="chat-fine">
                    {room.kind === 'group'
                      ? <>{room.members.map((m) => m.name).join(', ')}. Everyone here reads Alexa's answers, so Alexa leaves out anything private or hidden from someone here.</>
                      : <>Only you read this thread. Ask about your own limits, private notes or plans hidden from someone.</>}
                  </p>
                </div>
                {room.kind === 'group' && (
                  <div className="chat-room-actions">
                    {organizer && <button type="button" className="ghost" disabled={busy} onClick={() => act(async () => {
                      const r = await chatApi.invite(session, room.id);
                      setShareCode({ code: r.code, url: r.join_url });
                    })}>Invite people</button>}
                    <button type="button" className="ghost" disabled={busy} onClick={() => {
                      if (!window.confirm(`Leave ${room.title}? Its shared plans stop being visible to you.`)) return;
                      act(async () => { await chatApi.leave(session, room.id); await refreshMe(session, true); });
                    }}>Leave</button>
                  </div>
                )}
              </div>
              {shareCode && (
                <div className="chat-share">
                  <p>Share this with the people you want in <strong>{room.title}</strong>. Each person joins on their own; nobody can be added for them.</p>
                  <p className="chat-code">{shareCode.code}</p>
                  {shareCode.url && (
                    <p className="chat-share-link">
                      <input readOnly value={shareCode.url} onFocus={(e) => e.currentTarget.select()} aria-label="Invite link" />
                      <button type="button" className="ghost" onClick={() => { void navigator.clipboard?.writeText(shareCode.url ?? '').catch(() => undefined); }}>Copy</button>
                    </p>
                  )}
                  <button type="button" className="ghost" onClick={() => setShareCode(null)}>Done</button>
                </div>
              )}
              <ol className="chat-list" ref={listRef} aria-live="polite">
                {roomMessages.length === 0 && (
                  <li className="chat-msg system"><span>{room.kind === 'group' ? 'Say hello. Start a message with @Alexa to ask the assistant, for example "@Alexa start a plan for Saturday".' : 'Ask Alexa anything about your plans.'}</span></li>
                )}
                {roomMessages.map((m) => <Message key={m.id} m={m} mine={m.author === me.member.id} colors={colors}
                  onQuick={m.id === quickFor && !busy ? quick : undefined} />)}
                {typing[room.id] && <li className="chat-msg alexa typing"><span className="chat-avatar" aria-hidden="true">A</span><div className="chat-bubble"><p>Alexa is working on {typing[room.id] === me.member.name ? 'your' : `${typing[room.id]}'s`} request…</p></div></li>}
              </ol>
              <form className="chat-compose" onSubmit={(e) => { e.preventDefault(); send(); }}>
                {room.kind === 'group' && <button type="button" className="ghost chat-at" onClick={() => setDraft((d) => (d.trim().toLowerCase().startsWith('@alexa') ? d : `@Alexa ${d}`))} title="Address Alexa">@Alexa</button>}
                <label className="sr-only" htmlFor="chat-input">Message</label>
                <input id="chat-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={2000} autoComplete="off"
                  placeholder={room.kind === 'group' ? `Message ${room.title}. Start with @Alexa to ask the assistant.` : 'Message Alexa'} />
                <button type="submit" className="primary" disabled={busy || !draft.trim()}>Send</button>
              </form>
            </>
          )}
        </main>

        <OwnPanel inbox={inbox} updates={updates} busy={busy}
          onAnswer={(scopeId, decision) => act(async () => { await chatApi.answer(session, scopeId, decision); await refreshOwn(session); })}
          onVerify={(id, digest) => act(async () => { await chatApi.verify(session, id, digest); await refreshOwn(session); })} />
      </div>
    </div>
  );
}
