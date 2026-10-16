/* The group chat's client. Two kinds of calls, kept apart on purpose:
   - /api/chat/... is the chat host (rooms, messages, asking Alexa), authenticated with the person's own GroupFlow session;
   - /human/... is the person's own page on the GroupFlow server (sign-in, confirmations). Alexa never makes these calls. */

export interface ChatMember { id: string; name: string; role: string; label?: string | null; color?: string | null }
export interface ToolNote { name: string; ok: boolean; outcome: string | null }
export interface ChatMessage {
  id: number; room: string; author: string; author_name: string; text: string; at: string;
  meta: {
    for?: string; in_reply_to?: number; calls?: ToolNote[]; ms?: number; error?: string;
    followup?: { id: string; question: string; options: string[] }; notice?: boolean; from?: string; plan?: string;
  };
}
export interface Room { id: string; title: string; kind: 'group' | 'dm'; members: ChatMember[]; your_role?: string; last: ChatMessage | null }
export interface ChatMe { member: { id: string; name: string; color: string | null }; linked: boolean; links: { id: string; host: string }[]; assistant: string; rooms: Room[] }
export interface Session { token: string; csrf: string; member: { id: string; name: string } }
export interface InvitePreview { group: { id: string; name: string }; invited_by: string; members: number; expires_at: string }
export interface Updates { linked: boolean; headline: string | null; updates: string[]; todos: { kind: string; plan: string; text: string; where: string }[]; decisions: { plan: string; what: string; proposed: string; by: string }[] }
export interface PendingAnswer { scope_id: string; plan: string; plan_id: string; revision: string; activity_id: string | null; text: string; status: string }
export interface DraftCondition { id: string; plan: string; description: string; status: string; verify_digest: string }
export interface HumanInbox { pending: PendingAnswer[]; conditions: DraftCondition[] }
export interface DemoPerson { id: string; name: string; color: string; passcode: string; groups: string[] }

const SESSION_KEY = 'groupflow.chat.session';

export function loadSession(): Session | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null) {
  try {
    if (s) window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable: the session lasts until the tab reloads */
  }
}

export class AuthError extends Error {}

async function body<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (data as { error?: string | { message?: string } }).error;
    const message = typeof err === 'string' ? err : err?.message ?? `Request failed (${res.status})`;
    if (res.status === 401) throw new AuthError(message);
    throw new Error(message);
  }
  return data as T;
}

async function call<T>(session: Session | null, method: 'GET' | 'POST', path: string, payload?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (session) headers.authorization = `Bearer ${session.token}`;
  if (method === 'POST') {
    headers['content-type'] = 'application/json';
    if (session) headers['x-gf-csrf'] = session.csrf;
  }
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: method === 'POST' ? JSON.stringify(payload ?? {}) : undefined });
  } catch {
    throw new Error('The chat server is not reachable. Is "python -m groupflow demo" running?');
  }
  return body<T>(res);
}

export const chatApi = {
  signUp: (name: string, passcode: string, invite?: string) => call<Session & { joined?: { id: string; name: string } | null }>(null, 'POST', '/api/chat/signup', { name, passcode, ...(invite ? { invite } : {}) }),
  signIn: (memberId: string, passcode: string) => call<Session>(null, 'POST', '/human/login', { member_id: memberId, passcode }),
  me: (s: Session, refresh = false) => call<ChatMe>(s, 'GET', `/api/chat/me${refresh ? '?refresh=1' : ''}`),
  link: (s: Session) => call<{ linked: boolean; host: string; message?: string }>(s, 'POST', '/api/chat/link'),
  createGroup: (s: Session, name: string) => call<{ group: { id: string; name: string } }>(s, 'POST', '/api/chat/groups', { name }),
  invite: (s: Session, group: string) => call<{ code: string; join_url?: string; expires_at: string }>(s, 'POST', '/api/chat/invites', { group }),
  preview: (code: string) => call<InvitePreview>(null, 'GET', `/api/chat/invites/${encodeURIComponent(code)}`),
  join: (s: Session, code: string) => call<{ outcome: string; group: { id: string; name: string } }>(s, 'POST', '/api/chat/join', { code }),
  leave: (s: Session, group: string) => call<{ outcome: string }>(s, 'POST', '/api/chat/leave', { group }),
  messages: (s: Session, room: string, after = 0) => call<{ messages: ChatMessage[] }>(s, 'GET', `/api/chat/rooms/${encodeURIComponent(room)}/messages${after ? `?after=${after}` : ''}`),
  send: (s: Session, room: string, text: string) => call<{ message: ChatMessage; assistant: boolean }>(s, 'POST', `/api/chat/rooms/${encodeURIComponent(room)}/messages`, { text }),
  updates: (s: Session) => call<Updates>(s, 'GET', '/api/chat/updates'),
  // The person's own page on the GroupFlow server: what only they can do.
  inbox: (s: Session) => call<HumanInbox>(s, 'GET', '/human/inbox'),
  answer: async (s: Session, scopeId: string, decision: 'approved' | 'rejected') => {
    const ch = await call<{ challenge_id: string; nonce: string }>(s, 'POST', '/human/challenges', { scope_id: scopeId });
    return call<{ outcome: string }>(s, 'POST', '/human/decisions', { challenge_id: ch.challenge_id, nonce: ch.nonce, decision });
  },
  verify: (s: Session, conditionId: string, digest: string) => call<{ outcome: string }>(s, 'POST', '/human/conditions/verify', { condition_id: conditionId, verify_digest: digest }),
  demoPeople: async (): Promise<DemoPerson[]> => {
    try {
      const cfg = await call<{ scenarios: { members: { id: string; name: string; color: string; demoPasscode?: string; groups: string[] }[] }[] }>(null, 'GET', '/api/config');
      const seen = new Map<string, DemoPerson>();
      for (const sc of cfg.scenarios) for (const m of sc.members) if (m.demoPasscode && !seen.has(m.id)) seen.set(m.id, { id: m.id, name: m.name, color: m.color, passcode: m.demoPasscode, groups: m.groups });
      return [...seen.values()];
    } catch {
      return [];
    }
  },
};

/** Live room events (Server-Sent Events read with fetch, so the session stays in a header, never in a URL). */
export function openStream(s: Session, onEvent: (event: string, data: ChatMessage & { for?: string }) => void, onEnd: (authFailed: boolean) => void): () => void {
  const ctrl = new AbortController();
  (async () => {
    let authFailed = false;
    try {
      const res = await fetch('/api/chat/stream', { headers: { authorization: `Bearer ${s.token}` }, signal: ctrl.signal });
      if (res.status === 401) authFailed = true;
      if (!res.ok || !res.body) throw new Error('stream closed');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let cut: number;
        while ((cut = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          let event = 'message';
          let data = '';
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            else if (line.startsWith('data:')) data += line.slice(5).trim();
          }
          if (data) {
            try {
              onEvent(event, JSON.parse(data));
            } catch {
              /* a malformed frame is skipped */
            }
          }
        }
      }
    } catch {
      /* aborted or dropped: the caller reconnects */
    }
    if (!ctrl.signal.aborted) onEnd(authFailed);
  })();
  return () => ctrl.abort();
}
