import { useEffect } from 'react';
import type { DemoStep, Member, PlaceRef } from '../api';

interface Props {
  script: DemoStep[];
  step: number;
  playing: boolean;
  note: string | null;
  members: Member[];
  onPlay: () => void;
  places?: PlaceRef[];
}

function displayLine(text: string): string {
  const t = text.replace(/^Alexa,\s*/i, '').replace(/^heads up:\s*/i, '');
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function describe(s: DemoStep, members: Member[], places: PlaceRef[]) {
  if (s.kind === 'event') {
    const place = places.find((v) => v.id === s.place_id)?.name ?? s.place_id?.replace(/_/g, ' ');
    const when = s.start && s.end && !(s.start === '00:00' && s.end === '23:59') ? ` from ${s.start} to ${s.end}` : '';
    return <><span className="speaker sys">Demo console</span> reports <strong>{place}</strong> unavailable{when}, as a trusted outside source would</>;
  }
  if (s.kind === 'verify') {
    const who = members.find((m) => m.id === s.member);
    return <><span className="speaker" style={{ color: who?.color }}>{who?.name ?? s.member}</span> checks the condition Alexa drafted, on their own page</>;
  }
  if (s.kind === 'phones') {
    const names = (s.members ?? []).map((id) => members.find((m) => m.id === id)?.name ?? id);
    return <><span className="speaker sys">{names.join(', ')}</span> answer on their own GroupFlow pages</>;
  }
  const who = members.find((m) => m.id === s.member);
  return <><span className="speaker" style={{ color: who?.color }}>{who?.name ?? s.member}</span> says <q className="spoken">{displayLine(s.text ?? '')}</q></>;
}

export function DemoBar({ script, step, playing, note, members, onPlay, places = [] }: Props) {
  const done = step >= script.length;
  const next = done ? null : script[step];

  // "N" plays the next step when focus is not in a text field (handy while recording).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.key.toLowerCase() !== 'n' || e.metaKey || e.ctrlKey || e.altKey) return;
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable) return;
      e.preventDefault();
      onPlay();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onPlay]);

  return (
    <div className="demobar">
      <div className="demobar-progress" aria-hidden="true">
        {script.map((s, i) => (
          <span key={i} className={`tick ${s.kind !== 'say' ? 'square' : ''} ${i < step ? 'done' : i === step ? 'now' : ''}`}
            style={{ ['--c' as string]: s.kind === 'say' ? members.find((m) => m.id === s.member)?.color : 'var(--ink)' }} />
        ))}
      </div>
      <div className="demobar-text">
        {next ? (
          <p><span className="demobar-step">Guided demo, step {step + 1} of {script.length}.</span> {describe(next, members, places)}</p>
        ) : (
          <p><span className="demobar-step">Guided demo finished.</span> Reset to play it again, or talk to any Echo yourself.</p>
        )}
        {note && <p className="demobar-note">{note}</p>}
      </div>
      <button type="button" className="primary" onClick={onPlay} disabled={done || playing} aria-keyshortcuts="N">
        {playing ? 'Playing…' : done ? 'Done' : `Play step ${step + 1}`}
      </button>
    </div>
  );
}
