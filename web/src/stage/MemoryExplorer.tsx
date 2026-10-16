import { useEffect, useState } from 'react';
import { api, type MemoryNode, type MemorySnapshot } from '../api';

const GROUPS: { kind: MemoryNode['kind']; title: string; x: number; y: number }[] = [
  { kind: 'person', title: 'Speaker', x: 10, y: 85 },
  { kind: 'group', title: 'Your groups', x: 220, y: 85 },
  { kind: 'plan', title: 'Visible plans', x: 440, y: 85 },
  { kind: 'item', title: 'Items & activities', x: 650, y: 10 },
  { kind: 'version', title: 'Plan versions', x: 650, y: 165 },
  { kind: 'confirmation', title: 'Human confirmations', x: 865, y: 165 },
  { kind: 'condition', title: 'Your private limits', x: 220, y: 245 },
];

export function MemoryExplorer({ member, group, plan, tick }: { member: string; group: string; plan: string; tick: number }) {
  const [data, setData] = useState<MemorySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<MemoryNode['kind']>('plan');
  const [nodeId, setNodeId] = useState<string | null>(null);
  const [tab, setTab] = useState<'memory' | 'turn'>('memory');
  const [callIndex, setCallIndex] = useState(0);
  useEffect(() => {
    let active = true;
    setData(null); setError(null); setNodeId(null); setCallIndex(0);
    api.memory(member, group, plan).then((value) => { if (active) setData(value); }).catch((e) => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [member, group, plan, tick]);
  if (error) return <section className="memory-explorer" role="alert">Authorized memory could not be read: {error}</section>;
  if (!data) return <section className="memory-explorer" aria-busy="true">Reading this speaker's memory through MCP…</section>;
  const nodes = data.graph.nodes;
  const listed = nodes.filter((n) => n.kind === kind);
  const chosen = listed.find((n) => n.id === nodeId) ?? listed[0];
  const recordLabel = (n: MemoryNode) => {
    const parent = nodes.find((p) => p.id === data.graph.edges.find((e) => e.to === n.id)?.from);
    return n.kind === 'item' && parent?.kind === 'version' ? `${n.label} (${parent.label})` : n.label;
  };
  const turn = data.last_turn;
  const calls = turn?.calls ?? [];
  const call = calls[callIndex];
  const relations = [...new Set(data.graph.edges.map((e) => {
    const a = nodes.find((n) => n.id === e.from)?.kind;
    const b = nodes.find((n) => n.id === e.to)?.kind;
    return a && b ? `${a}:${b}` : '';
  }).filter(Boolean))];
  return (
    <section className="memory-explorer" aria-label="How AI uses MCP memory">
      <header className="memory-heading">
        <div><h2>How AI uses the shared memory</h2><p>One identity → authorized data → tool calls → checked results</p></div>
        <div className="seg" aria-label="Evidence view">
          <button className={tab === 'memory' ? 'on' : ''} onClick={() => setTab('memory')}>Visible memory</button>
          <button className={tab === 'turn' ? 'on' : ''} onClick={() => setTab('turn')}>This speaker's last turn</button>
        </div>
      </header>
      {tab === 'memory' ? <>
        <p className="memory-note">Fresh MCP snapshot · {data.context.you} · {new Date(data.at).toLocaleTimeString()}. Select a category, then a record. This is a map of returned information, not the raw database.</p>
        <div className="memory-canvas"><svg className="memory-graph" viewBox="0 0 1055 335" role="group" aria-label="Authorized memory relationships">
          <defs><marker id={`arrow-${member}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
          {relations.map((edge) => {
            const [a, b] = edge.split(':');
            const from = GROUPS.find((g) => g.kind === a)!; const to = GROUPS.find((g) => g.kind === b)!;
            const x1 = from.x + 180, y1 = from.y + 37, x2 = to.x, y2 = to.y + 37;
            return <path key={edge} d={`M ${x1} ${y1} C ${x1 + 35} ${y1}, ${x2 - 35} ${y2}, ${x2} ${y2}`} className="memory-edge" markerEnd={`url(#arrow-${member})`} />;
          })}
          {GROUPS.map((g) => {
            const count = nodes.filter((n) => n.kind === g.kind).length;
            return <g key={g.kind} transform={`translate(${g.x} ${g.y})`} role="button" tabIndex={0} aria-label={`${g.title}: ${count} returned records`} aria-pressed={kind === g.kind}
              onClick={() => { setKind(g.kind); setNodeId(null); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setKind(g.kind); setNodeId(null); } }} className={`memory-bucket ${kind === g.kind ? 'selected' : ''}`}>
              <rect width="180" height="74" rx="12" /><text x="14" y="26">{g.title}</text><text x="14" y="52" className="memory-count">{count ? `${count} returned record${count === 1 ? '' : 's'}` : 'Not returned in this snapshot'}</text>
            </g>;
          })}
        </svg></div>
        <div className="memory-records">
          <div className="memory-record-list" aria-label="Memory records">{listed.length ? listed.map((n) => <button key={n.id} className={n.id === chosen?.id ? 'selected' : ''} onClick={() => setNodeId(n.id)}>{recordLabel(n)}</button>) : <p>This category was not returned. That does not prove no such records exist.</p>}</div>
          <div className="memory-record-detail">{chosen && <><strong>{chosen.label}</strong><p>Source: {chosen.source}</p><p>{data.graph.edges.filter((e) => e.from === chosen.id || e.to === chosen.id).map((e) => `${nodes.find((n) => n.id === e.from)?.label} → ${e.relation} → ${nodes.find((n) => n.id === e.to)?.label}`).join(' · ')}</p><details><summary>Returned fields</summary><pre>{JSON.stringify(chosen.data, null, 2)}</pre></details></>}</div>
        </div>
        <p className="memory-note">{data.graph.description} Pages and summaries can omit records; zero here means zero returned. No panel data is added to the model's conversation.</p>
      </> : <>
        {!turn ? <p>No recorded turn for this speaker in this scope. Ask a question to see the real reads and results.</p> : <>
          <blockquote>{turn.text}</blockquote><p className="memory-note">Recorded turn: {new Date(turn.at).toLocaleTimeString()} · {turn.provider} / {turn.model} · {(turn.ms / 1000).toFixed(2)} s</p>
          <div className="evidence-flow" aria-label="Actual MCP calls">{calls.map((c, i) => <button key={i} className={`${i === callIndex ? 'selected' : ''} ${c.ok ? '' : 'refused'}`} onClick={() => setCallIndex(i)}><span>{i + 1}. {c.prefetched ? 'Host briefing' : 'Model request'}</span><strong>{c.name}</strong><small>{c.ok ? 'Server returned a result' : 'Server refused / error'} · {c.sent_to_model ? 'sent to model' : 'not sent to another model call'}</small></button>)}</div>
          {call && <div className="evidence-result"><strong>{call.name}</strong><p>{call.ok ? 'The outcome below comes from the server. A successful tool response can still describe a blocked action.' : 'The model received a refusal or error; this is not a successful action.'}</p><details><summary>{call.sent_to_model ? 'Exact tool content sent to model' : 'Returned result (not delivered to another model call)'}</summary><pre>{JSON.stringify(call.sent_to_model ? call.model_content : call.result, null, 2)}</pre></details></div>}
          <p className="evidence-reply"><strong>Reply:</strong> {turn.reply}</p>
        </>}
        <p className="memory-note">{data.trace_note}</p>
      </>}
    </section>
  );
}
