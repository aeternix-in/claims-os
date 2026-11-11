import { useEffect, useState } from 'react';
import { engine, rupees } from '../lib/api';

interface Msg { id: number; workflow: string; action: string; direction: 'outbound' | 'inbound'; workflow_id: string | null; status: string; error: string | null; created_at: string }
interface View {
  enabled: boolean; payer_on_nhcx: boolean; demo_mode: boolean; stage: string;
  last_decision: { decision: string; outcome: string; disposition: string | null; preauth_ref: string | null; submitted: number | null; eligible: number | null } | null;
  utr: string | null; paid_amount: number | null;
  ready: Record<'preauth' | 'claim', { ok: boolean; problems: string[]; total: number | null }>;
  actions: { workflow: 'preauth' | 'claim'; kind: 'send' | 'resubmit' | 'resend' }[];
  open_query: { id: string; query_text: string } | null; messages: Msg[]; can_simulate_payment: boolean;
}
interface Detail extends Msg { wire: { payload: unknown }; plain: { headers: Record<string, string>; fhir: unknown }; sync_response: unknown; encrypted: boolean; interpretation: Record<string, unknown> | null; resources: string[] }
interface Details { patient_dob: string | null; patient_sex: string | null; patient_abha: string | null; treating_doctor: string | null; doctor_hpr_id: string | null;
  icd10: string | null; provisional_dx: string | null; product_code: string | null; member_id: string | null; relationship: string | null }

const MSG_LABEL: Record<string, string> = {
  'preauth:submit': 'Pre-auth sent', 'preauth:on_submit': "Insurer's pre-auth decision", 'claim:submit': 'Claim sent',
  'claim:on_submit': "Insurer's claim decision", 'communication:request': 'Insurer query', 'communication:on_request': 'Our reply to the query',
  'paymentnotice:request': 'Payment notice', 'paymentnotice:on_request': 'Our acknowledgement',
};
const WF_CODE: Record<string, string> = { '12': 'new pre-auth', '121': 'revised pre-auth', '15': 'new claim', '19': 'query reply (pre-auth)', '161': 'query reply (claim)', '24': 'pre-auth query', '27': 'claim query' };

/** Admitted → Pre-auth → Claim → Paid, from our stage plus what NHCX said. */
function Journey({ v }: { v: View }) {
  const s = v.stage;
  const pre = ['in_treatment', 'discharge', 'discharge_pending', 'discharge_query', 'discharged', 'claim', 'submitted', 'claim_query', 'settled', 'paid', 'closed', 'rejected'].includes(s) ? 'done'
    : s === 'denied' ? 'bad' : s === 'preauth_query' ? 'query' : s === 'preauth_pending' ? 'wait' : 'todo';
  const clm = ['settled', 'paid', 'closed'].includes(s) ? 'done' : s === 'rejected' ? 'bad' : s === 'claim_query' ? 'query' : s === 'submitted' ? 'wait' : 'todo';
  const paid = ['paid', 'closed'].includes(s) ? 'done' : v.utr ? 'wait' : 'todo';
  const steps: [string, string, string][] = [
    ['Admitted', 'done', ''], ['Pre-auth', pre, pre === 'query' ? 'Needs info' : pre === 'wait' ? 'With insurer' : pre === 'bad' ? 'Rejected' : ''],
    ['Claim', clm, clm === 'query' ? 'Needs info' : clm === 'wait' ? 'With insurer' : clm === 'bad' ? 'Rejected' : ''],
    ['Paid', paid, paid === 'wait' ? 'Insurer paid; awaiting bank credit' : ''],
  ];
  const dot: Record<string, string> = { done: 'bg-good border-good', wait: 'bg-accent-soft border-accent', query: 'bg-warn-soft border-[var(--ring-warn)]', bad: 'bg-crit border-crit', todo: 'bg-surface border-line-strong' };
  return (
    <ol className="flex items-start" aria-label="Claim journey">
      {steps.map(([label, st, note], i) => (
        <li key={label} className="flex flex-1 flex-col items-center text-center">
          <div className="flex w-full items-center">
            <div className={`h-0.5 flex-1 ${i === 0 ? 'invisible' : steps[i - 1][1] === 'done' ? 'bg-good' : 'bg-line'}`} />
            <span className={`h-4 w-4 shrink-0 rounded-full border-2 ${dot[st]}`} />
            <div className={`h-0.5 flex-1 ${i === steps.length - 1 ? 'invisible' : st === 'done' ? 'bg-good' : 'bg-line'}`} />
          </div>
          <div className="mt-1 text-[13px] font-medium">{label}</div>
          {note && <div className="text-[11px] text-ink-3">{note}</div>}
        </li>
      ))}
    </ol>
  );
}

function DetailsForm({ caseId, onSaved }: { caseId: string; onSaved: () => void }) {
  const [d, setD] = useState<Details | null>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const [err, setErr] = useState('');
  useEffect(() => { engine<Details>(`/cases/${caseId}/details`).then((x) => { setD(x); setF(Object.fromEntries(Object.entries(x).map(([k, v]) => [k, v ?? '']))); }); }, [caseId]);
  if (!d) return null;
  const FIELDS: [keyof Details, string, string?][] = [
    ['patient_dob', 'Patient date of birth', 'date'], ['patient_sex', 'Sex (M / F / O)'], ['patient_abha', 'ABHA number'],
    ['treating_doctor', "Treating doctor"], ['doctor_hpr_id', "Doctor's HPR ID"], ['icd10', 'ICD-10'], ['provisional_dx', 'Diagnosis'],
    ['product_code', 'Policy product code'], ['member_id', 'Member ID'],
  ];
  async function save() {
    setErr('');
    const changed = Object.fromEntries(Object.entries(f).filter(([k, v]) => (d![k as keyof Details] ?? '') !== v).map(([k, v]) => [k, v || null]));
    try { await engine(`/cases/${caseId}/details`, { method: 'PATCH', body: JSON.stringify(changed) }); onSaved(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        {FIELDS.map(([k, label, type]) => (
          <div key={k}><label className="label" htmlFor={`d-${k}`}>{label}</label>
            <input id={`d-${k}`} className="input py-1" type={type ?? 'text'} value={f[k] ?? ''} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
        ))}
      </div>
      <button className="btn-primary py-1" onClick={save}>Save details</button>
      {err && <p className="text-[13px] text-crit">{err}</p>}
    </div>
  );
}

function Wire({ payload }: { payload: unknown }) {
  if (typeof payload !== 'string') return <pre className="max-h-72 overflow-auto rounded bg-surface-2 p-2 text-[11px]">{JSON.stringify(payload, null, 2)}</pre>;
  const parts = payload.split('.');
  const col = ['text-accent', 'text-warn', 'text-good', 'text-ink', 'text-crit'];
  const names = ['protected header', 'encrypted key', 'IV', 'ciphertext', 'tag'];
  return (
    <div>
      <div className="mb-1 flex flex-wrap gap-3 text-[11px]">{names.map((n, i) => <span key={n} className={col[i]}>■ {n}</span>)}</div>
      <p className="num max-h-40 overflow-auto break-all rounded bg-surface-2 p-2 text-[11px]">{parts.map((p, i) => <span key={i} className={col[i]}>{p.length > 400 ? p.slice(0, 400) + '…' : p}{i < parts.length - 1 ? '.' : ''}</span>)}</p>
    </div>
  );
}

/** "Under the hood": every message exchanged with NHCX for this case. */
function UnderTheHood({ caseId, messages }: { caseId: string; messages: Msg[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const [d, setD] = useState<Detail | null>(null);
  const [tab, setTab] = useState<'readable' | 'wire' | 'fhir'>('readable');
  useEffect(() => { if (open != null) engine<Detail>(`/cases/${caseId}/nhcx/messages/${open}`).then(setD); else setD(null); }, [open, caseId]);
  if (messages.length === 0) return <p className="text-ink-3">Nothing exchanged with NHCX yet.</p>;
  return (
    <ul className="divide-y divide-line">
      {messages.map((m) => (
        <li key={m.id} className="py-2">
          <button className="flex w-full items-center justify-between gap-2 text-left" onClick={() => { setOpen(open === m.id ? null : m.id); setTab('readable'); }}>
            <span><span className={m.direction === 'outbound' ? 'text-accent' : 'text-good'}>{m.direction === 'outbound' ? '→' : '←'}</span> {MSG_LABEL[`${m.workflow}:${m.action}`] ?? `${m.workflow}/${m.action}`}
              {m.workflow_id && <span className="ml-2 text-[12px] text-ink-3">workflow {m.workflow_id}{WF_CODE[m.workflow_id] ? ` · ${WF_CODE[m.workflow_id]}` : ''}</span>}</span>
            <span className={`text-[12px] ${m.status === 'failed' ? 'text-crit' : 'text-ink-3'}`}>{m.status} · {new Date(m.created_at).toLocaleTimeString('en-IN', { timeStyle: 'short' })}</span>
          </button>
          {m.error && <p className="text-[12px] text-crit">{m.error}</p>}
          {open === m.id && d && (
            <div className="mt-2 space-y-2 rounded-md border border-line p-3">
              <div className="flex gap-1 text-[12px]">{(['readable', 'wire', 'fhir'] as const).map((t) => (
                <button key={t} onClick={() => setTab(t)} className={`rounded px-2 py-0.5 ${tab === t ? 'bg-accent-soft text-accent' : 'text-ink-3 hover:text-ink'}`}>
                  {t === 'readable' ? 'What it says' : t === 'wire' ? (d.encrypted ? 'As it travelled (encrypted)' : 'As it travelled') : 'FHIR document'}</button>))}</div>
              {tab === 'readable' && (
                <div className="space-y-2 text-[13px]">
                  <div><span className="label">FHIR resources</span> {d.resources.join(', ') || '—'}</div>
                  {d.interpretation && <div><span className="label">How we read the insurer's answer</span>
                    {Object.entries(d.interpretation).map(([k, v]) => <div key={k} className="flex justify-between"><span className="text-ink-2">{k}</span><span className="num">{String(v ?? '—')}</span></div>)}</div>}
                  <div><span className="label">Protocol headers</span>
                    {Object.entries(d.plain.headers).map(([k, v]) => <div key={k} className="num flex justify-between gap-2 text-[12px]"><span className="text-ink-3">{k}</span><span className="truncate">{v}</span></div>)}</div>
                  {d.sync_response != null && <div><span className="label">Exchange's reply</span><pre className="overflow-auto rounded bg-surface-2 p-2 text-[11px]">{JSON.stringify(d.sync_response, null, 2)}</pre></div>}
                </div>
              )}
              {tab === 'wire' && <Wire payload={d.wire?.payload} />}
              {tab === 'fhir' && <pre className="max-h-96 overflow-auto rounded bg-surface-2 p-2 text-[11px]">{JSON.stringify(d.plain.fhir, null, 2)}</pre>}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function NhcxPanel({ caseId, onChange }: { caseId: string; onChange: () => void }) {
  const [v, setV] = useState<View | null>(null);
  const [scenario, setScenario] = useState('approve');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean; list?: string[] } | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  const load = () => engine<View>(`/cases/${caseId}/nhcx`).then(setV).catch((e) => setMsg({ text: e.message, bad: true }));
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [caseId]);

  async function send(workflow: string) {
    setBusy(true); setMsg(null);
    try {
      const r = await engine<{ status: string; error: string | null; workflow_id: string }>(`/cases/${caseId}/nhcx/send/${workflow}`, {
        method: 'POST', body: JSON.stringify(v?.demo_mode ? { scenario } : {}) });
      setMsg(r.status === 'failed' ? { text: `Not delivered: ${r.error}. You can resend.`, bad: true } : { text: `Sent to NHCX (workflow ${r.workflow_id}).` });
    } catch (e) {
      const err = e as Error & { status?: number };
      setMsg({ text: err.message, bad: true });
    }
    setBusy(false); load(); onChange();
  }
  async function pay() { setBusy(true); try { await engine(`/cases/${caseId}/nhcx/demo-pay`, { method: 'POST' }); } catch (e) { setMsg({ text: (e as Error).message, bad: true }); } setBusy(false); load(); onChange(); }

  if (!v) return <p className="text-ink-3">{msg?.text ?? 'Loading…'}</p>;
  if (!v.enabled) return <p className="card p-4 text-ink-3">NHCX is not switched on for this deployment. Claims go by the payer's portal; everything else works the same.</p>;
  if (!v.payer_on_nhcx) return <p className="card p-4 text-ink-3">This payer has no NHCX participant code. Add it on the Payers screen to send claims over NHCX.</p>;
  const wf: 'preauth' | 'claim' = ['claim', 'submitted', 'claim_query', 'settled', 'paid', 'closed', 'rejected', 'discharged'].includes(v.stage) ? 'claim' : 'preauth';
  const ready = v.ready[wf];
  const d = v.last_decision;

  return (
    <div className="space-y-4">
      <div className="card p-4"><Journey v={v} /></div>

      <div className="card space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-medium">National Health Claims Exchange</h3>
          {v.demo_mode && <span className="rounded bg-warn-soft px-2 py-0.5 text-[11px] text-warn">Demo mode: mock insurer</span>}
        </div>
        {!ready.ok && (
          <div className="rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
            <div className="font-medium">To fill in before the {wf === 'preauth' ? 'pre-auth' : 'claim'} can go over NHCX:</div>
            <ul className="ml-4 list-disc">{ready.problems.map((p) => <li key={p}>{p}</li>)}</ul>
            <button className="mt-1 underline" onClick={() => setShowDetails(true)}>Fill in the details</button>
          </div>
        )}
        {ready.ok && ready.total != null && <p className="text-[13px] text-ink-2">Ready: {rupees(ready.total)} in {wf === 'preauth' ? 'the pre-auth' : 'the claim'}, built to NHA's ClaimBundle.</p>}
        {v.open_query && <p className="rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">Insurer asks: “{v.open_query.query_text}” Answer it on the Queries tab, or send a revised pre-auth.</p>}
        <div className="flex flex-wrap items-center gap-2">
          {v.actions.map((a) => (
            <button key={a.workflow + a.kind} className="btn-primary" disabled={busy || (a.kind !== 'resend' && !v.ready[a.workflow].ok)} onClick={() => send(a.workflow)}>
              {a.kind === 'resend' ? `Resend ${a.workflow === 'preauth' ? 'pre-auth' : 'claim'}` : a.kind === 'resubmit' ? 'Send revised pre-auth' : `Send ${a.workflow === 'preauth' ? 'pre-auth' : 'claim'} over NHCX`}
            </button>
          ))}
          {v.demo_mode && v.actions.length > 0 && (
            <label className="flex items-center gap-2 text-[13px] text-ink-2">Insurer will…
              <select className="input w-auto py-1" value={scenario} onChange={(e) => setScenario(e.target.value)}>
                <option value="approve">approve</option><option value="partial">approve part</option><option value="reject">reject</option>
                <option value="query">ask for more info</option><option value="timeout">not respond</option>
              </select></label>
          )}
          {v.can_simulate_payment && <button className="btn-ghost" disabled={busy} onClick={pay}>Simulate insurer payment</button>}
          <button className="btn-ghost" onClick={() => setShowDetails(!showDetails)}>{showDetails ? 'Hide details' : 'Details for NHCX'}</button>
        </div>
        {msg && <p className={`rounded-md px-3 py-2 text-[13px] ${msg.bad ? 'bg-crit-soft text-crit' : 'bg-good-soft text-good'}`}>{msg.text}</p>}
        {showDetails && <div className="border-t border-line pt-3"><DetailsForm caseId={caseId} onSaved={() => { load(); setShowDetails(false); }} /></div>}
      </div>

      {(d || v.utr) && (
        <div className="card grid gap-4 p-4 sm:grid-cols-2">
          {d && <div><div className="label">Insurer's latest decision</div>
            <div className="font-medium capitalize">{d.decision.replace('_', ' ')}</div>
            {d.disposition && <div className="text-[13px] text-ink-2">{d.disposition}</div>}
            <div className="num text-[13px]">{d.submitted != null && `Asked ${rupees(d.submitted)}`}{d.eligible != null && ` · Eligible ${rupees(d.eligible)}`}{d.preauth_ref && ` · Ref ${d.preauth_ref}`}</div></div>}
          {v.utr && <div><div className="label">Payment notice</div>
            <div className="num">{rupees(v.paid_amount)} · UTR {v.utr}</div>
            <div className="text-[13px] text-ink-3">{['paid', 'closed'].includes(v.stage) ? 'Matched to the bank credit' : 'Closes when this UTR is matched on Bank match'}</div></div>}
        </div>
      )}

      <div className="card p-4"><h3 className="mb-2 font-medium">Under the hood</h3><UnderTheHood caseId={caseId} messages={v.messages} /></div>
    </div>
  );
}
