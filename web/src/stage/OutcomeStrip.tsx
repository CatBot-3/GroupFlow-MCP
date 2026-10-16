import type { PlanView } from '../api';

export function OutcomeStrip({ plan, onMemory, memoryOpen }: { plan: PlanView | undefined; onMemory: () => void; memoryOpen: boolean }) {
  const c = plan?.confirmations;
  const summary = !plan?.working ? plan?.agreement_summary : null;
  const kept = c?.rows.reduce((sum, r) => sum + Object.values(r.items).filter((s) => s === 'carried_forward').length, 0) ?? summary?.carried_forward ?? 0;
  const total = c?.required ?? summary?.required ?? 0;
  const remaining = c ? c.required - c.satisfied : summary ? summary.required - summary.satisfied : null;
  const version = plan?.working ?? plan?.agreed;
  return <section className="outcome-strip" aria-label="Plan changes and confirmation effort">
    <div className="outcome-title"><span>Friday with friends</span><h1>Plans change. Keep everyone in agreement.</h1></div>
    <div className="outcome-stat"><strong>{plan ? summary?.changed_activity_ids.length ?? version?.changed_activity_ids?.length ?? 0 : '—'}</strong><span>activities changed</span></div>
    <div className="outcome-stat kept"><strong>{plan ? kept : '—'}</strong><span>confirmations kept</span></div>
    <div className="outcome-stat"><strong>{remaining ?? '—'}</strong><span>answers still needed</span></div>
    <button type="button" className="primary memory-toggle" onClick={onMemory} aria-expanded={memoryOpen}>{memoryOpen ? 'Back to the plan' : 'See how AI uses memory'}</button>
    {kept > 0 && total > 0 && <p className="outcome-comparison">Keep {kept} valid confirmations. Request {total - kept} new answers instead of all {total} ({Math.round(kept / total * 100)}% fewer requests). Same-plan policy comparison in this synthetic demo; not measured time savings.</p>}
  </section>;
}
