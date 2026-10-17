import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ActivityView, VersionView } from '../api';

/** "18:30-19:30" or "Sat 18:30-20:00" -> day label, start and end minutes. */
export function parseWhen(when: string): { day: string; start: number; end: number } {
  const m = /^(?:(\w{3}) )?(\d{2}):(\d{2})-(\d{2}):(\d{2})$/.exec(when.trim());
  if (!m) return { day: '', start: 0, end: 60 };
  const start = Number(m[2]) * 60 + Number(m[3]);
  let end = Number(m[4]) * 60 + Number(m[5]);
  if (end < start) end += 24 * 60;
  return { day: m[1] ?? '', start, end };
}

const fmt = (min: number) => `${String(Math.floor(min / 60) % 24).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;

interface LaneProps {
  label: string;
  sub: string;
  acts: ActivityView[];
  range: [number, number];
  changed: Set<string>;
  broken?: Set<string>;
  tone: 'agreed' | 'proposed';
}

function Lane({ label, sub, acts, range, changed, broken, tone }: LaneProps) {
  const [lo, hi] = range;
  const span = Math.max(30, hi - lo);
  return (
    <div className={`lane ${tone}`}>
      <div className="lane-label"><strong>{label}</strong><span>{sub}</span></div>
      <div className="lane-track">
        {acts.map((a) => {
          const w = parseWhen(a.when);
          const left = ((w.start - lo) / span) * 100;
          const width = Math.max(4, ((w.end - w.start) / span) * 100);
          const title = `${a.title}, ${a.when}${a.place ? ` at ${a.place}` : ''}${a.cost_each ? `, ${a.cost_each} each` : ''}${a.participants !== 'everyone' ? `, with ${a.participants}` : ''}${a.status === 'dropped' ? ', dropped' : ''}${broken?.has(a.id) ? ', no longer available' : ''}`;
          return (
            <div key={a.id} className={`block ${changed.has(a.id) ? 'changed' : ''} ${a.status === 'dropped' || broken?.has(a.id) ? 'broken' : ''}`} style={{ left: `${left}%`, width: `${width}%` }} title={title}>
              <span className="block-title">{a.title}</span>
              <span className="block-venue">{a.place}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface Props {
  agreed: VersionView | null | undefined;
  working: VersionView | null | undefined;
  /** Agreed activities a verified outside fact has broken. */
  broken?: Set<string>;
}

// A time label is about 40 px wide; ticks closer than this overlap ("18:0018:30" on a phone).
const TICK_PX = 56;
// Below this width blocks are too thin to read ("M", "De..."), so the schedule becomes an agenda list.
const AGENDA_PX = 520;

function Agenda({ versions, day, changed, broken }: { versions: VersionView[]; day: string; changed: Set<string>; broken?: Set<string> }) {
  const latest = versions[versions.length - 1];
  const earlier = versions.length > 1 ? versions[0] : null;
  const before = new Map((earlier?.activities ?? []).map((a) => [a.id, a]));
  const rows = latest.activities.filter((a) => parseWhen(a.when).day === day).sort((a, b) => parseWhen(a.when).start - parseWhen(b.when).start);
  return (
    <ul className="agenda">
      {rows.map((a) => {
        const was = before.get(a.id);
        const isChanged = changed.has(a.id);
        // "Broken" marks the agreed version's activity that an outside fact hit. With a newer version shown, it describes what this
        // row replaced, not the row itself.
        const isBroken = a.status === 'dropped' || (!earlier && !!broken?.has(a.id));
        const wasBroken = !!earlier && !!broken?.has(a.id);
        return (
          <li key={a.id} className={`${isChanged ? 'changed' : ''} ${isBroken ? 'broken' : ''}`}>
            <span className="agenda-when">{a.when.replace(/^\w{3} /, '')}</span>
            <span className="agenda-what"><strong>{a.title}</strong>{a.place ? ` · ${a.place}` : ''}
              {isChanged && was && (was.place !== a.place || was.when !== a.when) &&
                <small> was {was.place !== a.place ? was.place : was.when}{wasBroken ? ', no longer available' : ''}</small>}
              {isBroken && <small> no longer available</small>}
            </span>
          </li>
        );
      })}
      {earlier && latest !== earlier && <li className="agenda-key"><span>{latest.label}</span> {latest.kind === 'draft' ? 'not shared yet' : 'not agreed yet'}; changes are marked.</li>}
    </ul>
  );
}

/** The agreed version above the one not agreed yet, one block per day, so a changed activity lines up with what it replaces. */
export function Timeline({ agreed, working, broken }: Props) {
  const versions = [agreed, working].filter((v): v is VersionView => !!v);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const changed = useMemo(() => {
    const out = new Set<string>();
    if (!agreed || !working) return out;
    const before = new Map(agreed.activities.map((a) => [a.id, a]));
    for (const a of working.activities) {
      const b = before.get(a.id);
      if (!b || b.when !== a.when || b.place !== a.place || b.participants !== a.participants || b.status !== a.status) out.add(a.id);
    }
    return out;
  }, [agreed, working]);
  const days = useMemo(() => {
    const order: string[] = [];
    for (const v of versions) for (const a of v.activities) {
      const d = parseWhen(a.when).day;
      if (!order.includes(d)) order.push(d);
    }
    return order;
  }, [versions]);

  if (!versions.length) return null;
  if (width < AGENDA_PX) {
    return (
      <div className="timelines" ref={box}>
        {days.map((day) => (
          <div key={day || 'one'} className="timeline">
            {day && <p className="timeline-day">{day}</p>}
            <Agenda versions={versions} day={day} changed={changed} broken={broken} />
          </div>
        ))}
      </div>
    );
  }
  const track = Math.max(120, width - 82);
  return (
    <div className="timelines" ref={box}>
      {days.map((day) => {
        const inDay = (v: VersionView) => v.activities.filter((a) => parseWhen(a.when).day === day);
        const times = versions.flatMap((v) => inDay(v).flatMap((a) => { const w = parseWhen(a.when); return [w.start, w.end]; }));
        const range: [number, number] = times.length ? [Math.floor(Math.min(...times) / 30) * 30, Math.ceil(Math.max(...times) / 30) * 30] : [18 * 60, 21 * 60];
        const span = Math.max(30, range[1] - range[0]);
        const room = Math.max(2, Math.floor(track / TICK_PX));
        const stepMin = [30, 60, 120, 180, 240, 360].find((s) => span / s + 1 <= room) ?? 360;
        const ticks: number[] = [];
        for (let t = range[0]; t <= range[1]; t += stepMin) ticks.push(t);
        return (
          <div key={day || 'one'} className="timeline" role="img"
            aria-label={`${day ? `${day}: ` : ''}${versions.map((v) => `${v.label}: ${inDay(v).map((a) => `${a.title} ${a.when}${a.place ? ` at ${a.place}` : ''}`).join(', ')}`).join('. ')}`}>
            {day && <p className="timeline-day">{day}</p>}
            {agreed && <Lane label={agreed.label} sub="agreed" acts={inDay(agreed)} range={range} changed={new Set()} broken={broken} tone="agreed" />}
            {working && <Lane label={working.label} sub={working.kind === 'draft' ? 'not shared yet' : 'not agreed yet'} acts={inDay(working)} range={range} changed={changed} tone="proposed" />}
            <div className="lane ticks" aria-hidden="true">
              <div className="lane-label" />
              <div className="lane-track">
                {ticks.map((t) => <span key={t} className="tick-mark" style={{ left: `${((t - range[0]) / Math.max(30, range[1] - range[0])) * 100}%` }}>{fmt(t)}</span>)}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
