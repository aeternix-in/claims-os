import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { engine, rupees } from '../lib/api';
import { STAGE_LABEL, type CaseRow, type Stage } from '../lib/types';
import { StageBadge } from './Desk';
import { ClockText } from '../components/ClockText';
import BillPanel from '../components/BillPanel';
import DocumentsPanel from '../components/DocumentsPanel';
import ChecksCard from '../components/ChecksCard';
import QueryEditor, { type Query } from '../components/QueryEditor';
import NhcxPanel from '../components/NhcxPanel';

interface CaseDetail {
  case: CaseRow;
  events: { id: number; event: string; from_stage: Stage | null; to_stage: Stage; actor_role: string; note: string | null; created_at: string }[];
  clocks: { id: string; kind: string; due_at: string; stopped_at: string | null; breached: boolean }[];
  allowed_events: string[];
}

/** What each event asks the user for. Later steps replace these prompts with proper panels. */
const EVENT_INPUTS: Record<string, { label: string; fields: [string, string, 'number' | 'text'][] }> = {
  start_preauth: { label: 'Start pre-auth', fields: [] },
  submit_preauth: { label: 'Submit pre-auth', fields: [['estimate', 'Estimate (₹)', 'number'], ['icd10', 'ICD-10', 'text']] },
  preauth_query: { label: 'Log payer query', fields: [['query_text', 'Query from payer', 'text']] },
  answer_preauth_query: { label: 'Send reply', fields: [['reply_text', 'Reply', 'text']] },
  preauth_approved: { label: 'Record approval', fields: [['approved_amount', 'Approved (₹)', 'number']] },
  preauth_denied: { label: 'Record denial', fields: [['reason', 'Reason', 'text']] },
  enhancement_approved: { label: 'Record enhancement', fields: [['approved_amount', 'New approved total (₹)', 'number']] },
  advise_discharge: { label: 'Advise discharge', fields: [] },
  request_final_auth: { label: 'Request final approval', fields: [['billed_amount', 'Final bill (₹)', 'number']] },
  discharge_query: { label: 'Log payer query', fields: [['query_text', 'Query from payer', 'text']] },
  answer_discharge_query: { label: 'Send reply', fields: [['reply_text', 'Reply', 'text']] },
  final_auth_approved: { label: 'Record final approval', fields: [['approved_amount', 'Approved (₹)', 'number']] },
  prepare_claim: { label: 'Prepare claim file', fields: [] },
  submit_claim: { label: 'Submit claim file', fields: [] },
  claim_query: { label: 'Log claim query', fields: [['query_text', 'Query', 'text']] },
  answer_claim_query: { label: 'Send reply', fields: [['reply_text', 'Reply', 'text']] },
  settlement_letter: { label: 'Record settlement letter', fields: [['settled_amount', 'Settled (₹)', 'number'], ['payer_ref', "Payer's settlement reference", 'text']] },
  claim_rejected: { label: 'Record rejection', fields: [['reason', 'Reason', 'text']] },
  bank_credit_matched: { label: 'Match bank credit (or use Bank match)', fields: [['utr', 'UTR', 'text'], ['received_amount', 'Amount credited (₹)', 'number']] },
  close: { label: 'Close case', fields: [] },
  reopen_for_appeal: { label: 'Reopen for appeal', fields: [['reason', 'Grounds', 'text']] },
};

export default function CaseView() {
  const { id } = useParams();
  const [d, setD] = useState<CaseDetail | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [err, setErr] = useState('');
  const [tab, setTab] = useState<'timeline' | 'bill' | 'docs' | 'queries' | 'nhcx'>('docs');
  const [queries, setQueries] = useState<Query[]>([]);
  const loadQ = () => engine<{ queries: Query[] }>(`/cases/${id}/queries`).then((r) => setQueries(r.queries)).catch(() => {});
  const [checkKey, setCheckKey] = useState(0);
  const recheck = () => setCheckKey((k) => k + 1);

  const load = () => engine<CaseDetail>(`/cases/${id}`).then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); loadQ(); }, [id]);

  async function fire(event: string) {
    setErr('');
    const spec = EVENT_INPUTS[event];
    const payload = Object.fromEntries(spec.fields.map(([k, , t]) => [k, t === 'number' ? Number(vals[k]) : vals[k]]));
    try {
      await engine(`/cases/${id}/events`, { method: 'POST', body: JSON.stringify({ event, payload, expected_version: d?.case.version }) });
      setActive(null); setVals({}); load(); loadQ(); recheck();
    } catch (e) { setErr((e as Error).message); recheck(); }
  }

  if (!d) return <p className="text-ink-3">{err || 'Loading…'}</p>;
  const c = d.case;
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <div>
        <div className="mb-1 num text-ink-3">{c.case_number}</div>
        <h1 className="mb-2 font-serif text-[28px]">{c.provisional_dx ?? 'Admission'}</h1>
        <StageBadge stage={c.stage} />
        <div className="card mt-5 grid grid-cols-3 divide-x divide-line">
          {[['Estimate', c.estimate], ['Approved', c.approved_amount], ['Billed', c.billed_amount]].map(([k, v]) => (
            <div key={k as string} className="p-4"><div className="label">{k}</div><div className="num text-lg">{rupees(v as number)}</div></div>
          ))}
        </div>
        <div className="mb-3 mt-6 flex gap-1 border-b border-line" role="tablist">
          {([['docs', 'Documents'], ['bill', 'Bill and audit'], ['queries', `Queries${queries.some((q) => q.status !== 'sent') ? ' •' : ''}`], ['nhcx', 'NHCX'], ['timeline', 'Timeline']] as const).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
              className={`-mb-px border-b-2 px-3 py-2 ${tab === k ? 'border-accent font-medium text-accent' : 'border-transparent text-ink-3 hover:text-ink'}`}>{l}</button>
          ))}
        </div>
        {tab === 'nhcx' ? <NhcxPanel caseId={c.id} onChange={() => { load(); loadQ(); recheck(); }} /> : tab === 'queries' ? (
          <div className="space-y-4">{queries.length === 0 ? <p className="text-ink-3">No payer queries on this case.</p> :
            queries.map((q) => <div key={q.id} className="card p-4"><QueryEditor q={q} onChange={() => { loadQ(); load(); }} /></div>)}</div>
        ) : tab === 'docs' ? <DocumentsPanel caseId={c.id} onChange={recheck} /> : tab === 'bill' ? <BillPanel caseId={c.id} onChange={() => { load(); recheck(); }} /> : (
        <ol className="card divide-y divide-line">
          {d.events.map((e) => (
            <li key={e.id} className="flex justify-between gap-4 px-4 py-2">
              <span>{STAGE_LABEL[e.to_stage]} <span className="text-ink-3">· {e.event.replace(/_/g, ' ')} · {e.actor_role}</span></span>
              <span className="num shrink-0 text-ink-3">{new Date(e.created_at).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' })}</span>
            </li>
          ))}
        </ol>
        )}
      </div>
      <aside className="space-y-4">
        <div className="card p-4">
          <div className="label">Next step</div>
          {d.allowed_events.length === 0 && <p className="text-ink-3">Nothing for your role at this stage. Admissions, pre-auth and discharge steps are done by the insurance desk; settlement and bank steps by accounts.</p>}
          <div className="flex flex-col gap-2">
            {d.allowed_events.map((ev) => {
              const spec = EVENT_INPUTS[ev] ?? { label: ev, fields: [] };
              return active === ev ? (
                <div key={ev} className="space-y-2 rounded-md border border-accent-line bg-accent-soft p-3">
                  {spec.fields.map(([k, label, t]) => (
                    <div key={k}><label className="label">{label}</label>
                      <input className="input" type={t} value={vals[k] ?? ''} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></div>
                  ))}
                  <div className="flex gap-2"><button className="btn-primary" onClick={() => fire(ev)}>{spec.label}</button>
                    <button className="btn-ghost" onClick={() => setActive(null)}>Cancel</button></div>
                </div>
              ) : (
                <button key={ev} className="btn-ghost justify-start"
                  onClick={() => (ev.startsWith('answer_') ? setTab('queries') : spec.fields.length ? setActive(ev) : fire(ev))}>
                  {ev.startsWith('answer_') ? 'Answer on the Queries tab' : spec.label}</button>
              );
            })}
          </div>
          {err && <p className="mt-3 rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}
        </div>
        {!['closed', 'paid', 'denied', 'rejected', 'settled'].includes(c.stage) && <ChecksCard caseId={c.id} refreshKey={checkKey} />}
        {d.clocks.length > 0 && (
          <div className="card p-4">
            <div className="label">IRDAI clocks</div>
            {d.clocks.map((k) => (
              <div key={k.id} className="flex justify-between py-1"><span className="capitalize">{k.kind}</span><ClockText due={k.due_at} stopped={k.stopped_at} /></div>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}
