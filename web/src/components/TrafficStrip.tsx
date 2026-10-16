import { useEffect, useRef, useState } from 'react';
import type { Member, TrafficEntry } from '../api';

interface Props {
  entries: TrafficEntry[];
  members: Member[];
  initiallyOpen?: boolean;
}

function hhmmss(iso: string) {
  return new Date(iso).toLocaleTimeString('en-GB', { hour12: false });
}

export function TrafficStrip({ entries, members, initiallyOpen = false }: Props) {
  const [open, setOpen] = useState(initiallyOpen);
  const [showPanel, setShowPanel] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const list = useRef<HTMLOListElement>(null);
  const stick = useRef(true);
  const programmatic = useRef(false);

  const shown = entries.filter((e) => showPanel || e.source === 'alexa');
  const calls = shown.filter((e) => e.kind === 'request' && e.method === 'tools/call').length;
  const chosen = shown.find((e) => e.seq === selected) ?? null;

  useEffect(() => {
    const el = list.current;
    if (!el || !stick.current) return;
    const bottom = el.scrollHeight - el.clientHeight;
    if (Math.abs(el.scrollTop - bottom) > 1) {
      // Our own scroll must not be read as the user scrolling away from the bottom.
      programmatic.current = true;
      el.scrollTop = bottom;
    }
  }, [shown.length, open]);

  return (
    <section className={`traffic ${open ? 'open' : ''}`} aria-label="MCP traffic">
      <header className="traffic-head">
        <h2>MCP traffic</h2>
        <p className="traffic-sum">{calls} tool call{calls === 1 ? '' : 's'}, every message as sent between the Echos and the GroupFlow server</p>
        <label className="toggle">
          <input type="checkbox" checked={showPanel} onChange={(e) => setShowPanel(e.target.checked)} />
          Include plan panel reads
        </label>
        <button type="button" className="ghost" onClick={() => setOpen((o) => !o)} aria-expanded={open}>{open ? 'Hide' : 'Show'}</button>
      </header>
      {open && (
        <div className={`traffic-body ${chosen ? 'with-detail' : ''}`}>
          <ol
            className="traffic-list"
            ref={list}
            onScroll={(e) => {
              if (programmatic.current) {
                programmatic.current = false;
                return;
              }
              const el = e.currentTarget;
              stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
            }}
          >
            {shown.length === 0 && <li className="traffic-empty">Talk to any Echo and the JSON-RPC messages appear here.</li>}
            {shown.map((e) => {
              const m = members.find((x) => x.id === e.member);
              return (
                <li key={e.seq}>
                  <button type="button" className={`tline ${e.kind} ${e.seq === selected ? 'sel' : ''}`} onClick={() => setSelected(e.seq === selected ? null : e.seq)}>
                    <span className="t-time">{hhmmss(e.at)}</span>
                    <span className="t-who" style={{ color: m?.color }}>{m?.name ?? e.member}</span>
                    <span className="t-dir" aria-label={e.direction === 'out' ? 'to server' : 'from server'}>{e.direction === 'out' ? '→' : '←'}</span>
                    <span className="t-method">{e.method}</span>
                    <span className="t-sum">{e.summary}</span>
                    <span className="t-ms">{e.ms !== null ? `${e.ms.toFixed(0)} ms` : ''}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          {chosen && (
            <div className="traffic-detail">
              <p className="detail-head">
                {chosen.direction === 'out' ? 'Request' : 'Response'} {chosen.id !== null ? `#${chosen.id}` : ''} on session {chosen.sessionId?.slice(0, 8) ?? 'not yet assigned'}
              </p>
              <pre>{JSON.stringify(chosen.payload, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
