import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { TaskView } from '../api';

interface Props {
  tasks: TaskView[];
  colorOf: (name: string) => string | undefined;
  hidden: boolean;
}

const STATUS: Record<TaskView['status'], string> = { todo: 'To do', in_progress: 'In progress', done: 'Done' };

interface Edge { d: string; live: boolean; key: string }

/** Tasks in columns by what they wait on; lines run from what has to happen first to what waits on it. */
export function TaskGraph({ tasks, colorOf, hidden }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const nodes = useRef(new Map<string, HTMLDivElement>());
  const [edges, setEdges] = useState<Edge[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });

  const columns = useMemo(() => {
    const byId = new Map(tasks.map((t) => [t.id, t]));
    const depth = new Map<string, number>();
    const visit = (t: TaskView, seen: Set<string>): number => {
      if (depth.has(t.id)) return depth.get(t.id)!;
      if (seen.has(t.id)) return 0;
      const ups = t.waits_on.map((id) => byId.get(id)).filter((u): u is TaskView => !!u);
      const d = ups.length ? 1 + Math.max(...ups.map((u) => visit(u, new Set([...seen, t.id])))) : 0;
      depth.set(t.id, d);
      return d;
    };
    tasks.forEach((t) => visit(t, new Set()));
    const max = Math.max(0, ...depth.values());
    const cols: TaskView[][] = Array.from({ length: Math.min(max + 1, 4) }, () => []);
    for (const t of tasks) cols[Math.min(depth.get(t.id) ?? 0, cols.length - 1)].push(t);
    const standalone = (t: TaskView) => t.waits_on.length === 0 && !tasks.some((x) => x.waits_on.includes(t.id));
    cols[0].sort((a, b) => Number(standalone(a)) - Number(standalone(b)));
    return cols;
  }, [tasks]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const origin = el.getBoundingClientRect();
      const next: Edge[] = [];
      for (const t of tasks) {
        const to = nodes.current.get(t.id);
        if (!to) continue;
        for (const dep of t.waits_on) {
          const from = nodes.current.get(dep);
          if (!from) continue;
          const a = from.getBoundingClientRect();
          const b = to.getBoundingClientRect();
          const x1 = a.right - origin.left;
          const y1 = a.top + a.height / 2 - origin.top;
          const x2 = b.left - origin.left;
          const y2 = b.top + Math.min(26, b.height / 2) - origin.top;
          const dx = Math.max(18, (x2 - x1) / 2);
          const upstream = tasks.find((x) => x.id === dep);
          next.push({ key: `${dep}>${t.id}`, live: upstream?.status !== 'done', d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}` });
        }
      }
      setEdges(next);
      setSize({ w: el.scrollWidth, h: el.scrollHeight });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [tasks, columns]);

  if (!tasks.length) return <p className="plan-empty">No tasks.</p>;
  const linked = tasks.some((t) => t.waits_on.length);

  return (
    <div className="graph" ref={box}>
      {linked && <p className="graph-hint">Lines run from what has to happen first to what waits on it.</p>}
      <svg className="graph-edges" width={size.w} height={size.h} aria-hidden="true">
        {edges.map((e) => <path key={e.key} d={e.d} className={e.live ? 'live' : 'met'} />)}
      </svg>
      <div className="graph-cols" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}>
        {columns.map((col, i) => (
          <div className="graph-col" key={i}>
            {col.map((t) => {
              const blocked = t.status !== 'done' && t.blocked_by.length > 0;
              return (
                <div key={t.id} ref={(el) => { if (el) nodes.current.set(t.id, el); else nodes.current.delete(t.id); }}
                  className={`task edge-${hidden ? 'hatch' : 'plain'} ${t.status} ${blocked ? 'blocked' : ''}`}>
                  <div className="task-title">{t.title}</div>
                  <div className="task-meta">
                    <span className="who"><span className="dot" style={{ background: colorOf(t.owner) }} aria-hidden="true" />{t.owner}</span>
                    <span className="task-status">{STATUS[t.status]}{t.due ? `, due ${t.due}` : ''}</span>
                  </div>
                  {t.decided_by === 'anyone' && <div className="task-open">Anyone can tick this off</div>}
                  {blocked && <div className="task-wait">{t.blocked_by[0]}</div>}
                  {t.pending.map((p, j) => <div key={j} className="task-flag">{p.by} {p.firm ? 'asked' : 'suggested'}: {p.value}</div>)}
                  {t.flags.length > 0 && <div className="task-flag">{t.flags[0].startsWith('Reopened') ? 'Reopened: what it relied on changed' : 'Check: something it relies on changed'}</div>}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
