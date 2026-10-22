/* Types and fetchers for the simulator backend (/api) and the GroupFlow human-only API (/human). */

// ------------------------------------------------------------------ config and turns

export interface Member { id: string; name: string; color: string; groups: string[]; label?: string | null; role?: string; demoPasscode?: string }

export interface DemoStep {
  kind: 'say' | 'event' | 'phones' | 'verify' | 'skip';
  member?: string;
  text?: string;
  note: string;
  /** Starts a new day of the story ("Monday"). */
  chapter?: string;
  /** With chapter: the time of day it starts ("19:40"); the simulated Alexa's clock is set to it first. */
  clock?: string;
  /** For skip steps: how many quiet hours the server's worker should act as if had passed. */
  hours?: number;
  group?: string;
  place_id?: string;
  start?: string;
  end?: string;
  day?: number;
  members?: string[];
  decision?: 'approved' | 'rejected';
}

export interface PlanRef { id: string; title: string; template: string; templateName: string; defaultStage: string | null }
export interface PlaceRef { id: string; name: string; kind: string; plan: string; planId: string }

export interface ScenarioConfig {
  id: string;
  label: string;
  description: string | null;
  members: Member[];
  plans: PlanRef[];
  stages: string[];
  places: PlaceRef[];
  script: DemoStep[];
}

export interface ProviderInfo { id: string; label: string; model: string; ready: boolean; reason: string | null }
export interface TemplateInfo { id: string; name: string; description: string }
export interface Config { scenarios: ScenarioConfig[]; templates: TemplateInfo[]; provider: ProviderInfo; mcpUrl: string; serverBase: string; showcase?: { group: string; script: DemoStep[] }; chat?: boolean }

export interface ToolCallRecord { name: string; args: Record<string, unknown>; ok: boolean; result: unknown; ms: number; prefetched?: boolean; sent_to_model?: boolean; model_content?: unknown }
export interface LlmTurnStats { calls: { ms: number; inputTokens: number; outputTokens: number; toolCalls: string[] }[]; inputTokens: number; outputTokens: number; ms: number }
export interface AgentTurn { member: string; text: string; reply: string; provider: string; model: string; calls: ToolCallRecord[]; llm: LlmTurnStats; ms: number; at: string }

export interface TranscriptLine {
  role: 'user' | 'alexa';
  text: string;
  at: string;
  turn?: { calls: ToolCallRecord[]; llm: LlmTurnStats; ms: number; provider: string; model: string };
}

export interface TrafficEntry {
  seq: number; at: string; member: string; source: 'alexa' | 'panel'; direction: 'in' | 'out';
  kind: 'request' | 'response' | 'notification' | 'error'; method: string; id: number | string | null;
  summary: string; ms: number | null; sessionId: string | null; payload: unknown;
}

// ------------------------------------------------------------------ plans (the engine's views)

export type PlanStatus = 'ACTIVE' | 'DRAFT' | 'PROPOSED' | 'AWAITING_CONFIRMATIONS' | 'READY_TO_COMMIT' | 'COMMITTED' | 'DEGRADED' | 'REPAIRING' | 'BLOCKED' | 'NEEDS_ATTENTION' | 'CANCELLED';
export interface Suggestion { request: string | null; value: string; by: string; firm: boolean; said?: string | null }
export interface DetailView { id: string; label: string; value: string | null; owner: string; decided_by: string; status: 'confirmed' | 'disputed' | 'has_suggestions' | 'unset'; pending: Suggestion[]; flags: string[] }
export interface TaskView { id: string; title: string; type: string; owner: string; status: 'todo' | 'in_progress' | 'done'; due?: string | null; decided_by: string; blocked_by: string[]; waits_on: string[]; pending: Suggestion[]; flags: string[] }
export interface ActivityView { id: string; title: string; when: string; place: string | null; participants: string; cost_each?: string | null; your_cost?: string | null; status: 'planned' | 'dropped' }
export interface VersionView { label: string; kind: 'draft' | 'candidate' | 'committed'; activities: ActivityView[]; your_total?: string | null; changes: string[]; changed_activity_ids?: string[] }
export type ScopeStatus = 'approved' | 'pending' | 'rejected' | 'conditional_met' | 'conditional_unmet' | 'revoked' | 'expired' | 'carried_forward' | 'fallback_approved';
export interface ConfirmationsView { version: string; required: number; satisfied: number; complete: boolean; rows: { person: string; done: boolean; items: Record<string, ScopeStatus> }[] }
export interface ConditionView { id: string; plan: string; name: string; description: string; status: 'unverified' | 'active' | 'retired'; private: boolean; owner?: string; owner_id?: string }
export interface AlternativesResult {
  job: string; status: string; message: string; plan_status: PlanStatus;
  proof: { status: string; method: string; domain_size: number; explored: number; search_complete: boolean; runtime_ms: number; requested_method?: string | null; fallback_reason?: string | null } | null;
  alternatives: { candidate: string; label: string; alternate: boolean; changes: string[]; unchanged: string[]; cost_each_before: string; cost_each_after: string; reconfirm: string[] }[];
  conflict: string[];
}
export interface ExplainView { subject: string; message: string; changes: string[]; unchanged: string[]; triggers: string[]; options: { option: string; works: boolean; reason: string; would_also_change: string[] }[]; reconfirm: string[] }
export interface HistoryItem { seq: number; at: string; who: string | null; action: string; detail: string; said?: string | null }

export interface PlanView {
  id: string; title: string; kind: string; group: string; visible_to: string[]; organizer: string; you_organize: boolean; dates: string;
  status: PlanStatus; status_reason?: string | null; version: number; details: DetailView[]; tasks: TaskView[];
  agreed?: VersionView | null; working?: VersionView | null; confirmations?: ConfirmationsView | null; observations: string[]; bookings: string[];
  agreement_summary?: { version: string; required: number; satisfied: number; carried_forward: number; changes: string[]; changed_activity_ids: string[] } | null;
  item_types: string[]; condition_kinds: string[]; next_step: string;
  // Inspector-only extras
  template?: string; space?: string; audience?: string[]; organizer_id?: string; start_date?: string; days?: number;
  gate?: string[]; repair?: AlternativesResult | null; explain?: ExplainView | null; audit?: HistoryItem[]; conditions?: ConditionView[];
  lifecycle?: 'bounded' | 'ongoing'; planning_window_days?: number; preset?: string | null;
  project?: { stage: string; open_work: number; workstreams: string[]; revision: string | null; status: string; next: string; pending_by_person?: Record<string, number> } | null;
  polls?: PollView[]; rsvp?: RsvpView | null; bring_list?: BringItem[]; costs?: CostsView | null;
}

/** A vote (a suggestion to whoever decides) or a proposal settled unless someone objects by its deadline. */
export interface PollView {
  id: string; kind: 'vote' | 'unless someone objects'; question: string; status: 'open' | 'closed' | 'decided' | 'cancelled'; decides: string;
  votes: Record<string, string[]>; not_voted: string[]; options?: string[]; proposal?: string; about?: string; closes?: string; if_undecided?: string;
  your_vote?: string; result?: string; can_vote?: boolean; can_decide?: boolean;
}
export interface RsvpView {
  coming: string[]; maybe: string[]; not_coming: string[]; not_answered: string[]; headcount: number; headcount_with_maybes: number;
  your_answer?: string; dietary_needs?: string[]; dietary_note?: string;
}
export interface BringItem { id: string; thing: string; bringing: string | null }
export interface CostsView {
  currency: string; total: string; expenses: { id: string; what: string; amount: string; paid_by: string; split_among: string[]; status: string; your_share?: string }[];
  balances: { who: string; balance: string }[]; settle_up: string[]; your_balance?: string; repayments_waiting?: string[]; earlier_expenses?: number;
}
export interface TogetherPlan {
  plan_id: string; title: string; polls: PollView[]; rsvp: RsvpView | null; offer_rsvp: boolean; bring_list: BringItem[]; costs: CostsView | null;
  to_confirm: { id: string; text: string }[];
}

export interface ProjectWork {
  id: string; title: string; owner_id: string; owner: string; status: string; recurrence_id?: string | null;
  spec: { not_before: string; due: string; duration_minutes: number; workstream: string; milestone?: string | null; resources: Record<string, number>; depends_on: string[]; cost_minor: number };
}
export interface ProjectSchedule { item_id: string; title: string; owner_id: string; start: string; end: string; resources: Record<string, number> }
export interface ProjectView {
  plan: string; title: string; version: number; stage: string; window_start: string; days: number; timezone: string; currency: string;
  config: { stage: string; approvers: string[]; budget_minor: number | null; approval_days: number; calendar: { start: string; end: string; weekdays: number[] } };
  resources: { id: string; name: string; capacity: number }[]; work: ProjectWork[]; total: number; next_offset: number | null; workstreams: string[];
  confirmation_totals: { required: number; satisfied: number };
  revision: { id: string; status: string; fresh: boolean; cost_minor: number; schedule: ProjectSchedule[];
    proof: { optimal: boolean; ms: number; objectives: number[]; objective_order: string[]; backend: string };
    confirmations: { scope_id: string; person: string; kind: string; item: string | null; status: string }[] } | null;
}

export interface RequestView { request: string; plan: string; item: string | null; what: string; current: string | null; proposed: string; by: string; firm: boolean; said?: string | null }
export interface TodoView { kind: string; plan: string; text: string; where: string }
export interface ContextView {
  you: string; today: string; headline: string; decisions_for_you: RequestView[]; updates: string[]; your_todos: TodoView[];
  affected: { item: string; label: string; plan: string; message: string }[]; your_conditions: ConditionView[]; plans: PlanView[]; other_plans: string[];
}

export interface MemoryNode { id: string; kind: 'person' | 'group' | 'plan' | 'item' | 'version' | 'confirmation' | 'condition'; label: string; data: unknown; source: string }
export interface MemorySnapshot {
  source: 'mcp'; member: string; at: string; context: ContextView;
  graph: { nodes: MemoryNode[]; edges: { from: string; to: string; relation: string }[]; pagination: { next_offset: number | null; truncated: Record<string, number> }; description: string };
  last_turn: AgentTurn | null; trace_note: string; plan_not_visible?: string | null;
}

export interface InspectorView {
  scope: string;
  group: { id: string; name: string; members: { id: string; name: string; label: string | null; role: string; color: string }[]; plans: { id: string; title: string; template: string; status: string }[] } | null;
  plans: PlanView[];
  observations: { id: string; kind: string; verification: string; payload: Record<string, unknown>; received_at: string }[];
}

/** What the board shows: either the narrator's inspector or exactly what one person's Echo receives over MCP. */
export interface BoardModel {
  source: 'inspector' | 'mcp';
  member: string | null;
  plans: PlanView[];
  context: ContextView | null;
  /** Set when get_context refused the plan asked about (the member cannot see it). */
  refused?: string | null;
  /** The plan a member view was asked about. */
  focus?: string | null;
}

// ------------------------------------------------------------------ fetchers

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (body as { error?: string | { message?: string; code?: string } }).error;
    throw Object.assign(new Error(typeof err === 'string' ? err : err?.message ?? `Request failed (${res.status})`),
      { code: typeof err === 'object' ? err?.code : undefined });
  }
  return body as T;
}

const post = (url: string, body?: unknown) =>
  fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) });

export const api = {
  memory: (member: string, group: string, plan: string) => fetch(`/api/memory/${encodeURIComponent(member)}?group=${encodeURIComponent(group)}&plan=${encodeURIComponent(plan)}`).then((r) => json<MemorySnapshot>(r)),
  project: (member: string, plan: string, workstream = '', offset = 0) => fetch(`/api/project/${encodeURIComponent(member)}?plan=${encodeURIComponent(plan)}&offset=${offset}${workstream ? `&workstream=${encodeURIComponent(workstream)}` : ''}`).then((r) => json<ProjectView>(r)),
  projectAction: (member: string, tool: string, arguments_: Record<string, unknown>) => post(`/api/project/${encodeURIComponent(member)}/tools`, { tool, arguments: arguments_ }).then((r) => json<{ outcome: string; message?: string; problems?: string[] }>(r)),
  config: () => fetch('/api/config').then((r) => json<Config>(r)),
  health: () => fetch('/api/health').then((r) => json<{ mcp: { protocol: string } | null; error?: string }>(r)),
  transcripts: () => fetch('/api/transcripts').then((r) => json<Record<string, TranscriptLine[]>>(r)),
  traffic: (since = 0) => fetch(`/api/traffic?since=${since}`).then((r) => json<TrafficEntry[]>(r)),
  say: (member: string, text: string, scenario: string, plan?: string) => post('/api/say', { member, text, scenario, plan }).then((r) => json<AgentTurn>(r)),
  reset: (scenario: string | null) => post('/api/reset', { scenario }).then((r) => json<{ ok: boolean }>(r)),
  forget: (member: string | null) => post('/api/forget', { member }).then((r) => json<{ ok: boolean }>(r)),
  notify: (data: Record<string, unknown>) => post('/api/notify', data).catch(() => undefined),
  demoEvent: (group: string, place_id: string, start = '00:00', end = '23:59', day?: number) =>
    post('/api/demo/event', { group, place_id, start, end, day }).then((r) => json<{ outcome: string; affected_plans?: string[] }>(r)),
  demoClock: (chapter: string | null, time?: string) => post('/api/demo/clock', { chapter, time }).then((r) => json<{ today: string }>(r)),
  skipAhead: (hours: number, group: string) => post('/api/demo/skip-ahead', { hours, group }).then((r) => json<SkipAhead>(r)),

  inspector: async (group: string): Promise<BoardModel> => {
    const r = await json<InspectorView>(await fetch(`/api/inspector?scenario=${encodeURIComponent(group)}`));
    return { source: 'inspector', member: null, plans: r.plans, context: null };
  },
  view: async (member: string, plan?: string): Promise<BoardModel> => {
    const q = plan ? `?plan=${encodeURIComponent(plan)}` : '';
    const r = await json<{ context: ContextView | null; error?: string | { code?: string; message: string } | null }>(await fetch(`/api/view/${member}${q}`));
    if (!r.context) {
      const msg = typeof r.error === 'string' ? r.error : r.error?.message ?? 'No view';
      if (plan) return { source: 'mcp', member, plans: [], context: null, refused: msg, focus: plan };
      throw new Error(msg);
    }
    return { source: 'mcp', member, plans: r.context.plans, context: r.context, refused: null, focus: plan ?? null };
  },
};

/** The demo console's "skip ahead": what the server's worker did as if that many quiet hours had passed. */
export interface SkipAhead {
  hours: number; reminders_sent: number; reviewer: string;
  checkins: { plan: string; quiet_for: string; reviewer: string; reviewed?: string; reason?: string | null; sent: { name: string; text: string; source: string }[] }[];
}

// ------------------------------------------------------------------ human-only API (each person's own page)

export interface InboxItem { scope_id: string; plan: string; plan_id: string; revision: string; activity_id: string | null; text: string; status: ScopeStatus; via: string | null; scope_digest: string }
export interface Receipt { receipt_id: string; plan: string; revision: string; decision: 'approved' | 'rejected' | 'conditional'; status: string; condition: Record<string, unknown> | null; text: string; created_at: string; current?: boolean }
export interface InboxPlan { id: string; group_id?: string; title: string; status: PlanStatus; planning?: boolean; you_organize?: boolean; agreement_version?: string; conditions: { name: string; label: string; check: string; hint: string; tags: string[] }[] }
export interface Inbox {
  member: { id: string; name: string }; plans: InboxPlan[]; pending: InboxItem[]; answered: InboxItem[]; receipts: Receipt[];
  conditions: (ConditionView & { plan_id: string | null; value: Record<string, unknown>; verify_digest: string })[]; notifications: { id: string; kind?: string; text: string; created_at: string; plan_id: string | null }[];
  project_items?: ProjectHumanItem[];
  project_plans?: ProjectHumanPlan[];
  together?: TogetherPlan[];
  decision_inbox?: { cards: DecisionCard[]; unresolved: number };
  action_receipts?: ActionReceipt[];
  calendar_links?: { id: string; created_at: string }[];
  groups?: { id: string; name: string }[];
}
export interface ActionReceipt { id: string; actor: string; subjects: string[]; operation: string; plans: string[]; outcome: string; message: string; reversible: boolean; created_at: string }
export interface DecisionCard { plan: string; title: string; version_token: string; version: number; update_count: number; updates: string[]; changed_since: string | null; preferences: { muted?: number; digest?: number; quiet_start?: number | null; quiet_end?: number | null; timezone?: string }; actions: { kind: string; id: string; text: string; why: string; effect: string; options?: string[] }[] }
export interface PlanningChoices { message: string; status?: string; outcome?: string; alternatives?: { candidate: string; label: string; changes: string[]; unchanged: string[]; cost_each_after: string }[]; suggestions?: { changes: string[]; hypothetical: boolean }[]; facts?: { id: string; kind: string; place_id?: string; source: string; freshness: string; verified_at: string | null; valid_until: string | null; value: unknown }[] }
export interface AgreementTerms { title: string; date: string; time: string; place: string | null; cost_share_minor: number; currency: string; participants: string[]; cancelled: boolean }
export interface AgreementReview { plan: string; title: string; version_token: string; revision: string | null; group_id?: string; can_manage?: boolean; start_date?: string; currency?: string; message: string;
  items: { activity_id: string; title: string; before: AgreementTerms | null; after: AgreementTerms | null; status: ScopeStatus; why: string; can_contest: boolean; can_answer: boolean; confirmation_text: string | null; scope_id: string | null; confirmation_mode: string; contest?: {reason: string} | null }[];
  activities?: { id: string; title: string; kind: string; place_id: string | null; participants: string[]; venues: {id: string; name: string; price_minor: number}[] }[];
  people?: {id:string;name:string}[];
}
export interface FallbackBranch { terms: {place_id:string;place_name:string}; text:string }
export interface FallbackChallenge { challenge_id:string;nonce:string;branches:FallbackBranch[];expires_at:string;bounds:{max_total_minor?:number;latest_end?:string};message:string }
export interface FallbackList { authorizations:{id:string;activity_id:string;status:string;branches:FallbackBranch[];expires_at:string}[];message:string }
export interface AnalysisResult { outcome:string;message:string;budget_remaining?:number;privacy_scope?:string;least_change?:string;fairer?:string;
  scenarios?:{id:number;input:{kind:string;place_id?:string;price_minor?:number;member_id?:string};comparisons:{candidate:string;label:string;status:string;currently_feasible_in_scenario:boolean;repairs:{reconfirmation_count:number;changed_activities:string[];changes:string[]}[]}[]}[];
  alternatives?:{candidate:string;label:string;changes:string[];maximum_total:number;current_burden:Record<string,number>;totals:Record<string,number>}[];
}
export interface PendingAction { key: string; tool: string; arguments: Record<string, unknown>; expected?: string; createdAt: string }
export interface ActionResult { action_receipt?: ActionReceipt; plan?: { id: string }; outcome: string; message?: string }
export interface ActionPreview { outcome: string; expected: string; changes: { item: string; before: unknown; after: unknown }[]; affected_participants: string[]; approval_impact: { carried: {member:string;activity:string}[]; needs_confirmation: {member:string;activity:string}[] }; message: string }
export interface ProjectHumanItem {
  id: string; plan: string; plan_id: string; kind: 'commitment' | 'authorization'; status: string; expires_at: string; can_answer: boolean; can_revoke?: boolean; current?: boolean;
  payload: { stage: string; cost_minor?: number; recurrence_id?: string; schedule: ProjectSchedule | ProjectSchedule[]; spec?: ProjectWork['spec'] };
}
export interface ProjectHumanPlan { id: string; title: string; workspace: string; timezone?: string; currency?: string; admin: boolean; calendar: { weekdays: number[]; start: string; end: string; unavailable: [string, string][] }; members: { id: string; name: string }[] }
export interface DecisionExtra { condition?: Record<string, unknown>; reason?: string; new_limit?: { kind: string; value: Record<string, unknown> } }

/** A person's own session on the GroupFlow server. It never goes through the agent. */
export class HumanClient {
  token: string | null = null;
  private csrf: string | null = null;
  constructor(readonly base: string, readonly memberId: string) {}

  get signedIn() { return !!this.token; }

  private get pendingKey() { return `groupflow.pending.v1:${this.base}:${this.memberId}`; }
  pendingActions(): PendingAction[] {
    const raw = localStorage.getItem(this.pendingKey);
    if (!raw) return [];
    try { const items: unknown = JSON.parse(raw); if (Array.isArray(items) && items.every(p => p && typeof p.key === 'string' && typeof p.tool === 'string' && typeof p.arguments === 'object' && p.arguments)) return items as PendingAction[]; } catch { /* Preserve unreadable evidence; do not issue a fresh write. */ }
    throw new Error('Saved pending actions could not be read. Keep this browser data and reload before making another change.');
  }
  private savePending(items: PendingAction[]) { localStorage.setItem(this.pendingKey, JSON.stringify(items)); }
  private clearPending(key: string) { this.savePending(this.pendingActions().filter(p => p.key !== key)); }

  private async call<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    if (this.token) headers.authorization = `Bearer ${this.token}`;
    if (method === 'POST') {
      headers['content-type'] = 'application/json';
      if (this.csrf) headers['x-gf-csrf'] = this.csrf;
    }
    let res: Response;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      res = await fetch(`${this.base}${path}`, { method, headers, signal: controller.signal, body: method === 'POST' ? JSON.stringify(body ?? {}) : undefined });
      if (res.status === 401 && path !== '/human/login') this.token = null;
      try { return await json<T>(res); }
      catch (e) { if (e instanceof Error && res.headers.get('x-request-id')) e.message += ` (Reference ${res.headers.get('x-request-id')})`; throw e; }
    } catch (e) {
      if (e instanceof Error && (e as Error & {code?:string}).code) throw e;
      throw new Error(e instanceof Error && e.message.includes('Reference') ? e.message : 'The connection ended before the result was known. Check pending actions to see whether it was saved.');
    } finally {
      clearTimeout(timer);
    }
  }

  async login(passcode: string) {
    const r = await this.call<{ token: string; csrf: string; member: { id: string; name: string } }>('POST', '/human/login', { member_id: this.memberId, passcode });
    this.token = r.token;
    this.csrf = r.csrf;
    return r;
  }
  logout() { this.token = null; this.csrf = null; }
  async inbox() {
    return this.call<Inbox>('GET', '/human/inbox');
  }
  async act(tool: string, args: Record<string, unknown>, requestKey: string, expected?: string): Promise<ActionResult> {
    const pending = this.pendingActions();
    const signature = JSON.stringify([tool,args]);
    const original = pending.find(p => JSON.stringify([p.tool,p.arguments]) === signature);
    const op = original ?? { key: requestKey, tool, arguments: args, expected, createdAt: new Date().toISOString() };
    if (!original) this.savePending([...pending, op]); // Persist before starting the request.
    try {
      const result = await this.call<ActionResult>('POST', '/human/actions/apply', { tool:op.tool,arguments:op.arguments,request_key:op.key,expected:op.expected });
      this.clearPending(op.key);
      return result;
    } catch (e) {
      const code = (e as {code?:string})?.code;
      if (code && !['INTERNAL','UNAUTHENTICATED','RATE_LIMITED'].includes(code)) this.clearPending(op.key);
      throw e;
    }
  }
  actionStatus(requestKey: string) { return this.call<{ outcome: string; result?: ActionResult; message: string }>('GET', '/human/actions/status?request_key='+encodeURIComponent(requestKey)); }
  async recoverAction(op: PendingAction, retry = false): Promise<ActionResult> {
    const saved = await this.actionStatus(op.key);
    if (saved.outcome === 'saved' && saved.result) { this.clearPending(op.key); return saved.result; }
    if (retry) return this.act(op.tool,op.arguments,op.key,op.expected);
    return {outcome:'unknown',message:'The saved result is still unknown. You can retry the original request safely.'};
  }
  previewAction(tool: string, args: Record<string, unknown>, expected: string) { return this.call<ActionPreview>('POST', '/human/actions/preview', {tool,arguments:args,expected}); }
  correctAction(receipt: string) { return this.call('POST', '/human/actions/correct', { receipt }); }
  resolvePlan(plan: string) { return this.call<{ version: string }>('GET', '/human/resolve?plan=' + encodeURIComponent(plan)); }
  preferences(plan: string, values: Record<string, unknown>) { return this.call('POST', '/human/preferences', { plan, ...values }); }
  calendarLink() { return this.call<{ id: string; url: string }>('POST', '/human/calendar'); }
  revokeCalendar(id: string) { return this.call('POST', '/human/calendar/revoke', { id }); }
  planning(plan: string, candidate?: string, mode='options') { return this.call<PlanningChoices>('POST', '/human/planning', { plan, candidate,mode }); }
  agreementReview(plan:string) { return this.call<AgreementReview>('GET','/human/agreements?plan='+encodeURIComponent(plan)); }
  contestAgreement(plan:string,activity:string,reason:string,expected:string) { return this.call<{message:string}>('POST','/human/agreements/contest',{plan,activity,reason,expected}); }
  fallbacks(plan:string) { return this.call<FallbackList>('GET','/human/fallbacks?plan='+encodeURIComponent(plan)); }
  fallbackChallenge(plan:string,activity:string,places:string[],expires_at:string,max_total_minor?:number,latest_end?:string) { return this.call<FallbackChallenge>('POST','/human/fallbacks/challenges',{plan,activity,places,expires_at,max_total_minor,latest_end}); }
  fallbackDecision(challenge:FallbackChallenge,decision:'approve'|'decline') { return this.call<{message:string}>('POST','/human/fallbacks/decisions',{challenge_id:challenge.challenge_id,nonce:challenge.nonce,decision}); }
  revokeFallback(id:string) { return this.call<{message:string}>('POST','/human/fallbacks/revoke',{id}); }
  fairnessPreference(group:string,enabled:boolean,reset=false) { return this.call<{message:string}>('POST','/human/fairness/preferences',{group,enabled,reset}); }
  fairnessStatus(group:string) { return this.call<{enabled:boolean}>('GET','/human/fairness/preferences?group='+encodeURIComponent(group)); }
  analyze(plan:string,mode:'resilience'|'fairness',scenarios?:Record<string,unknown>[]) { return this.call<AnalysisResult>('POST','/human/planning',{plan,mode,scenarios}); }
  privateReview(plan:string) { return this.call<{conditions:{id:string;description:string}[];message:string}>('GET','/human/planning/privacy?plan='+encodeURIComponent(plan)); }
  async decideProject(scopeId: string, decision: 'approve' | 'decline') {
    const ch = await this.call<{ challenge_id: string; nonce: string }>('POST', '/human/projects/challenges', { scope_id: scopeId });
    return this.call<{ outcome: string }>('POST', '/human/projects/decisions', { challenge_id: ch.challenge_id, nonce: ch.nonce, decision });
  }
  revokeProject(scopeId: string) { return this.call('POST', '/human/projects/revoke', { scope_id: scopeId }); }
  projectCalendar(plan: string, calendar: ProjectHumanPlan['calendar']) { return this.call('POST', '/human/projects/calendar', { plan, calendar }); }
  workspaceRole(group: string, target: string, role: string, grant: boolean) { return this.call('POST', '/human/workspace/roles', { group, target, role, grant }); }
  async decide(scopeId: string, decision: 'approved' | 'rejected' | 'conditional', extra: DecisionExtra = {}) {
    const ch = await this.call<{ challenge_id: string; nonce: string }>('POST', '/human/challenges', { scope_id: scopeId });
    return this.call<{ outcome: string; receipt_id: string; plan_status: string }>('POST', '/human/decisions', { challenge_id: ch.challenge_id, nonce: ch.nonce, decision, ...extra });
  }
  verify(conditionId: string, verifyDigest: string) { return this.call<{ outcome: string }>('POST', '/human/conditions/verify', { condition_id: conditionId, verify_digest: verifyDigest }); }
  setCondition(planId: string, kind: string, value: Record<string, unknown>) { return this.call<{ outcome: string }>('POST', '/human/conditions', { kind, value, plan_id: planId }); }
  revoke(receiptId: string) { return this.call<{ outcome: string }>('POST', '/human/revoke', { receipt_id: receiptId }); }
  vote(poll: string, choice: string, opts: { reason?: string; decide?: boolean } = {}) { return this.call<{ outcome: string; message: string }>('POST', '/human/polls/vote', { poll, choice, ...opts }); }
  rsvp(plan: string, answer: 'yes' | 'no' | 'maybe', plusOnes?: number) { return this.call<{ outcome: string; message: string }>('POST', '/human/rsvp', { plan, answer, plus_ones: plusOnes ?? null }); }
  bring(plan: string, action: 'add' | 'bring' | 'unclaim' | 'remove', things: string[]) { return this.call<{ outcome: string; message: string }>('POST', '/human/bring', { plan, action, things }); }
  confirmCost(plan: string, id: string, answer: 'yes' | 'no') { return this.call<{ outcome: string; message: string }>('POST', '/human/costs/confirm', { plan, id, answer }); }
}
