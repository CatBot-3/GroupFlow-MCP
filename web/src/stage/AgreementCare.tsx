import { useEffect, useState } from 'react';
import './AgreementCare.css';
import { HumanClient, type InboxPlan, type AgreementReview, type AgreementTerms, type FallbackList, type FallbackChallenge, type AnalysisResult } from '../api';

const amount = (n:number,currency='CAD') => new Intl.NumberFormat(undefined,{style:'currency',currency}).format(n/100);
const searchStatus = (s:string) => ({optimal_within_domain:'Best in the tested choices',best_found:'Feasible repair found',unknown:'No answer before the time limit',infeasible_within_domain:'No tested repair fits'}[s] ?? s);
function terms(t:AgreementTerms|null) { return t ? t.cancelled ? 'Cancelled' : `${t.date}, ${t.time}${t.place ? ` at ${t.place}` : ''}; your share ${amount(t.cost_share_minor,t.currency)}.` : 'No participation terms'; }

export function AgreementCare({client,plans,onChanged}:{client:HumanClient;plans:InboxPlan[];onChanged:()=>Promise<void>}) {
  const [selected,setSelected] = useState('');
  const [review,setReview] = useState<AgreementReview|null>(null);
  const [fallbacks,setFallbacks] = useState<FallbackList|null>(null);
  const [fairness,setFairness] = useState(false);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [message,setMessage] = useState('');
  const [reason,setReason] = useState<Record<string,string>>({});
  const [fallbackActivity,setFallbackActivity] = useState('');
  const [venues,setVenues] = useState<string[]>([]);
  const [expiry,setExpiry] = useState('');
  const [maxCost,setMaxCost] = useState('');
  const [latestEnd,setLatestEnd] = useState('');
  const [challenge,setChallenge] = useState<FallbackChallenge|null>(null);
  const [scenarioKind,setScenarioKind] = useState('place_unavailable');
  const [scenarioPlace,setScenarioPlace] = useState('');
  const [scenarioMember,setScenarioMember] = useState('');
  const [scenarioPrice,setScenarioPrice] = useState('');
  const [analysis,setAnalysis] = useState<AnalysisResult|null>(null);
  const [privateReview,setPrivateReview] = useState<{conditions:{id:string;description:string}[];message:string}|null>(null);
  const plan = plans.some(p=>p.id===selected) ? selected : plans[0]?.id ?? '';
  const incomingVersion = plans.find(p=>p.id===plan)?.agreement_version;
  async function reload(id=plan) {
    const [r,f] = await Promise.all([client.agreementReview(id),client.fallbacks(id)]);
    setReview(r);setFallbacks(f);
    if (r.group_id) setFairness((await client.fairnessStatus(r.group_id)).enabled);
  }
  useEffect(()=>{
    let current=true;
    setReview(null);setFallbacks(null);setError('');setMessage('');setChallenge(null);setAnalysis(null);setPrivateReview(null);
    setFallbackActivity('');setVenues([]);setScenarioPlace('');
    if (plan) void Promise.all([client.agreementReview(plan),client.fallbacks(plan)]).then(async([r,f])=>{
      const pref = r.group_id ? await client.fairnessStatus(r.group_id) : {enabled:false};
      if(current){setReview(r);setFallbacks(f);setFairness(pref.enabled);}
    }).catch(e=>{if(current)setError((e as Error).message);});
    return ()=>{current=false;};
  },[client,plan,incomingVersion]);
  async function run(fn:()=>Promise<unknown>,refresh=true) {
    setBusy(true);setError('');setMessage('');
    try { const result=await fn() as {message?:string}|undefined;setMessage(result?.message ?? 'Saved.');
      if(refresh){await reload();await onChanged();} }
    catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  if(!plans.length)return null;
  const activities=review?.activities??[];
  const ownActivities=activities.filter(a=>a.participants.includes(client.memberId));
  const fallbackTarget=ownActivities.find(a=>a.id===(fallbackActivity||ownActivities[0]?.id));
  const publicVenues=Array.from(new Map(activities.flatMap(a=>a.venues).map(v=>[v.id,v])).values());
  return <section className="agreement-care" aria-label="Your agreements">
    <h4>Your agreements when plans change</h4>
    <label>Review plan<select aria-label="Agreement plan" value={plan} disabled={busy} onChange={e=>setSelected(e.target.value)}>{plans.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
    {error&&<p className="phone-error" role="alert">{error}</p>}{message&&<p className="phone-ok" role="status">{message}</p>}
    {!review&&<p>Loading your terms…</p>}
    {review?.items.map(item=><article className="decision-card" key={item.activity_id} aria-label={`Your agreement: ${item.title}`}>
      <h5>{item.title}</h5><p><strong>Before:</strong> {terms(item.before)}</p><p><strong>Now:</strong> {terms(item.after)}</p><p>{item.why}</p>
      {item.contest&&<p>Your private objection: {item.contest.reason}</p>}
      {item.can_answer&&item.scope_id&&['pending','revoked','rejected','expired','conditional_unmet'].includes(item.status)&&<div className="care-preview"><p>{item.confirmation_text}</p>
        <button className="pbtn primary-p" disabled={busy} onClick={()=>void run(()=>client.decide(item.scope_id!,'approved'))}>Approve my current terms</button>
        <button className="pbtn" disabled={busy} onClick={()=>void run(()=>client.decide(item.scope_id!,'rejected'))}>Decline my current terms</button>
      </div>}
      {item.contest&&!item.can_answer&&<p>The organizer needs to repair or reopen the current proposal before you can answer again.</p>}
      {item.can_contest&&<details><summary>Request a new review of my commitment</summary><p>{item.confirmation_mode==='whole_plan'?'You use whole-plan confirmation. This withdraws your whole-plan commitment.':'This withdraws only your commitment to this activity.'} Your reason stays private.</p>
        <form onSubmit={e=>{e.preventDefault();setChallenge(null);void run(()=>client.contestAgreement(plan,item.activity_id,reason[item.activity_id]??'',review.version_token));}}>
          <label>My private reason<textarea required maxLength={1000} value={reason[item.activity_id]??''} onChange={e=>setReason({...reason,[item.activity_id]:e.target.value})}/></label>
          <button className="pbtn" disabled={busy}>Withdraw and request review</button>
        </form></details>}
    </article>)}
    {review&&!review.items.length&&<p>You have no activity commitments to review on this plan.</p>}
    <button className="pbtn" disabled={busy} onClick={()=>void run(async()=>{setPrivateReview(await client.privateReview(plan));return{message:'Only your personal limits are shown.'};},false)}>Review my private limits</button>
    {privateReview&&<div className="decision-card"><p>{privateReview.message}</p>{privateReview.conditions.map(c=><p key={c.id}>{c.description}</p>)}{!privateReview.conditions.length&&<p>No personal limits are recorded for you here.</p>}</div>}
    {!!ownActivities.length&&<details className="decision-card"><summary>Approve specific fallback choices</summary><p>Choose up to three named venues for this one activity. Times, participants and shown prices stay exact. This gives no booking or payment permission.</p>
      <form onSubmit={e=>{e.preventDefault();const entered=new FormData(e.currentTarget);const enteredExpiry=String(entered.get('expires_at')??'');const enteredEnd=String(entered.get('latest_end')??'');void run(async()=>{
        const c=await client.fallbackChallenge(plan,fallbackTarget?.id??'',venues,new Date(enteredExpiry).toISOString(),maxCost?Math.round(Number(maxCost)*100):undefined,enteredEnd||undefined);
        setChallenge(c);return c;
      },false);}}>
        <label>My activity<select value={fallbackTarget?.id??''} onChange={e=>{setFallbackActivity(e.target.value);setVenues([]);setChallenge(null);}}>{ownActivities.map(a=><option key={a.id} value={a.id}>{a.title}</option>)}</select></label>
        <fieldset><legend>Exact venue choices</legend>{fallbackTarget?.venues.map(v=><label className="care-check" key={v.id}><input type="checkbox" checked={venues.includes(v.id)} disabled={busy||(!venues.includes(v.id)&&venues.length>=3)} onChange={e=>{setVenues(e.target.checked?[...venues,v.id]:venues.filter(x=>x!==v.id));setChallenge(null);}}/>{v.name} — {amount(v.price_minor,review?.currency)}</label>)}</fieldset>
        <label>Permission expires<input name="expires_at" type="datetime-local" required value={expiry} onInput={e=>{setExpiry(e.currentTarget.value);setChallenge(null);}} onChange={e=>{setExpiry(e.target.value);setChallenge(null);}}/></label>
        <label>Maximum personal total (optional, {review?.currency??'CAD'})<input type="number" min="0" step="0.01" value={maxCost} onChange={e=>{setMaxCost(e.target.value);setChallenge(null);}}/></label>
        <label>Latest daily end (optional)<input name="latest_end" type="time" value={latestEnd} onInput={e=>{setLatestEnd(e.currentTarget.value);setChallenge(null);}} onChange={e=>{setLatestEnd(e.target.value);setChallenge(null);}}/></label>
        <button className="pbtn" disabled={busy||!venues.length}>Review fallback permission</button>
      </form>
      {challenge&&<div className="care-preview" aria-label="Fallback permission preview"><h5>Review before giving permission</h5>{challenge.branches.map(b=><p key={b.terms.place_id}>{b.text}</p>)}<p>Valid until {new Date(challenge.expires_at).toLocaleString()}.</p>
        {challenge.bounds.max_total_minor!==undefined&&<p>Your total cannot exceed {amount(challenge.bounds.max_total_minor,review?.currency)}.</p>}
        {challenge.bounds.latest_end&&<p>Your activities must end by {challenge.bounds.latest_end} each day.</p>}<p>{challenge.message}</p>
        <button className="pbtn primary-p" disabled={busy} onClick={()=>void run(async()=>{const c=challenge;setChallenge(null);return client.fallbackDecision(c,'approve');})}>Approve these exact fallback choices</button>
        <button className="pbtn" disabled={busy} onClick={()=>void run(async()=>{const c=challenge;setChallenge(null);return client.fallbackDecision(c,'decline');})}>Decline fallback permission</button>
      </div>}
      {fallbacks?.authorizations.map(f=><div className="decision-action" key={f.id}><p>{f.branches.map(b=>b.terms.place_name).join(' or ')} · {f.status} · until {new Date(f.expires_at).toLocaleString()}</p>{f.status!=='revoked'&&<button className="pbtn" disabled={busy} onClick={()=>void run(()=>client.revokeFallback(f.id))}>Withdraw this fallback permission</button>}</div>)}
    </details>}
    {!!activities.length&&<details className="decision-card"><summary>Test possible future disruptions</summary><p>Compare the current plan and available alternatives under an explicit scenario. This changes no plan or permission and predicts no failure probability.</p>
      <form onSubmit={e=>{e.preventDefault();void run(async()=>{const s:Record<string,unknown>={kind:scenarioKind};if(scenarioKind==='participant_unavailable')s.member_id=scenarioMember||review?.people?.[0]?.id;else s.place_id=scenarioPlace||publicVenues[0]?.id;if(scenarioKind==='price_changed')s.price_minor=Math.round(Number(scenarioPrice)*100);const r=await client.analyze(plan,'resilience',[s]);setAnalysis(r);return r;},false);}}>
        <label>Possible change<select value={scenarioKind} onChange={e=>setScenarioKind(e.target.value)}><option value="place_unavailable">Venue unavailable that day</option><option value="price_changed">Venue price changes</option><option value="participant_unavailable">Person unavailable that day</option></select></label>
        {scenarioKind==='participant_unavailable'?<label>Participant<select value={scenarioMember||review?.people?.[0]?.id||''} onChange={e=>setScenarioMember(e.target.value)}>{review?.people?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>:<label>Scenario venue<select value={scenarioPlace||publicVenues[0]?.id||''} onChange={e=>setScenarioPlace(e.target.value)}>{publicVenues.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label>}
        {scenarioKind==='price_changed'&&<label>Hypothetical price ({review?.currency??'CAD'})<input type="number" min="0" step="0.01" required value={scenarioPrice} onChange={e=>setScenarioPrice(e.target.value)}/></label>}
        <button className="pbtn" disabled={busy}>Compare this future scenario</button>
      </form>
    </details>}
    {review?.group_id&&<details className="decision-card"><summary>Share accommodation history in this group</summary><p>Optional history helps compare who repeatedly adjusts. Only opted-in people and plans visible to everyone hearing the result count. Hard limits and personal consent always apply.</p>
      <p>Your history sharing is {fairness?'on':'off'}.</p><div className="phone-row"><button className="pbtn" disabled={busy} onClick={()=>void run(()=>client.fairnessPreference(review.group_id!,!fairness))}>{fairness?'Stop sharing and delete my history':'Opt in for future repairs'}</button>
      <button className="pbtn" disabled={busy} onClick={()=>void run(()=>client.fairnessPreference(review.group_id!,fairness,true))}>Reset my accommodation history</button>
      <button className="pbtn" disabled={busy} onClick={()=>void run(async()=>{const r=await client.analyze(plan,'fairness');setAnalysis(r);return r;},false)}>Compare history across feasible choices</button></div>
    </details>}
    {analysis&&<article className="decision-card" aria-label="Planning comparison"><h5>Planning comparison</h5><p>{analysis.message}</p>
      {analysis.scenarios?.map(s=><div key={s.id}><p>Declared scenario: {s.input.kind.replaceAll('_',' ')} — {s.input.place_id?publicVenues.find(v=>v.id===s.input.place_id)?.name??'Selected venue':review?.people?.find(p=>p.id===s.input.member_id)?.name??'Selected participant'}{s.input.price_minor!==undefined?` at ${amount(s.input.price_minor,review?.currency)}`:''}.</p>
        {s.comparisons.map(c=><div className="decision-action" key={c.candidate}><strong>{c.label}: {c.currently_feasible_in_scenario?'still meets hard limits in this scenario':'needs a repair in this scenario'}</strong><p>Repair search: {searchStatus(c.status)}.</p>
          {c.repairs.map((r,index)=><div key={index}><p>{r.changes.length?r.changes.join('; '):'No activity change needed.'}</p><p>{r.reconfirmation_count} people would review the changed terms.</p></div>)}{!c.repairs.length&&<p>No validated repair was found in the tested choices.</p>}</div>)}
      </div>)}
      {analysis.alternatives?.map(a=><div key={a.candidate}><p>{a.label}{a.candidate===analysis.least_change?' · least change':''}{a.candidate===analysis.fairer?' · best tested history balance':''}; largest consented accommodation total: {a.maximum_total} points.</p><p>{a.changes.join('; ')}</p>{review?.can_manage&&<button className="pbtn" disabled={busy} onClick={()=>void run(()=>client.planning(plan,a.candidate))}>Select {a.label} for personal confirmation</button>}</div>)}
      {analysis.budget_remaining!==undefined&&<p>Private diagnostic comparisons remaining in this ten-minute window: {analysis.budget_remaining}.</p>}
      {analysis.privacy_scope&&<p>Repeated private diagnostics are limited. Shared plan outcomes can still reveal information.</p>}
    </article>}
  </section>;
}
