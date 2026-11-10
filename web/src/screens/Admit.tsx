import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { engine, rupees, supabase } from '../lib/api';

interface Payer { id: string; name: string; kind: string }
interface Check {
  room_advice: { cap: number | null; advice: string; best: string | null };
  room_rule_label: string; rooms: Record<string, number>;
  chosen_room?: { category: string; rate: number; payer_share_of_room_linked: number };
}
const ROOMS: Record<string, string> = { general: 'General ward', twin: 'Twin sharing', single: 'Single room', deluxe: 'Deluxe room', icu: 'ICU' };

const PATIENT: [string, string, string?][] = [
  ['patient_name', 'Patient name'], ['uhid', 'UHID'], ['dob', 'Date of birth', 'date'], ['abha_id', 'ABHA number'],
  ['treating_doctor', 'Treating doctor'], ['doctor_hpr_id', "Doctor's HPR ID"],
  ['provisional_dx', 'Provisional diagnosis'], ['icd10', 'ICD-10'], ['procedure_id', 'Procedure code (for package rates)'],
];
const POLICY: [string, string, string?][] = [
  ['policy_number', 'Policy number'], ['sum_insured', 'Sum insured (₹)', 'number'], ['balance', 'Balance left (₹)', 'number'],
  ['copay_pct', 'Co-pay %', 'number'], ['sub_limit', 'Sub-limit for this treatment (₹)', 'number'],
  ['member_id', 'Member ID'], ['product_code', 'Policy product code (NHCX)'],
];

export default function Admit() {
  const nav = useNavigate();
  const [payers, setPayers] = useState<Payer[]>([]);
  const [f, setF] = useState<Record<string, string>>({ copay_pct: '0', rule: 'none', roomPct: '1', icuPct: '2', max: 'twin', room_category: 'general' });
  const [check, setCheck] = useState<Check | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { supabase.from('payers').select('id,name,kind').order('name').then(({ data }) => setPayers(data ?? [])); }, []);

  const roomRule = () => f.rule === 'pctSI' ? { kind: 'pctSI', roomPct: Number(f.roomPct), icuPct: Number(f.icuPct) }
    : f.rule === 'category' ? { kind: 'category', max: f.max } : { kind: 'none' };
  const num = (k: string) => (f[k] ? Number(f[k]) : undefined);
  const input = (k: string, label: string, type = 'text', required = false) => (
    <div key={k}><label className="label" htmlFor={k}>{label}</label>
      <input id={k} type={type} min={type === 'number' ? 0 : undefined} className="input" required={required}
        value={f[k] ?? ''} onChange={(e) => { setF({ ...f, [k]: e.target.value }); setCheck(null); }} /></div>
  );

  // policy check runs whenever the policy is complete enough
  useEffect(() => {
    if (!f.payer_id || !f.sum_insured) return;
    const t = setTimeout(() => {
      engine<Check>('/policy-check', { method: 'POST', body: JSON.stringify({
        payer_id: f.payer_id, sum_insured: Number(f.sum_insured), balance: num('balance'), copay_pct: Number(f.copay_pct || 0),
        sub_limit: num('sub_limit'), room_rule: roomRule(), room_category: f.room_category }) }).then(setCheck).catch(() => setCheck(null));
    }, 300);
    return () => clearTimeout(t);
  }, [f.payer_id, f.sum_insured, f.balance, f.copay_pct, f.sub_limit, f.rule, f.roomPct, f.icuPct, f.max, f.room_category]);

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr('');
    const rate = check?.rooms[f.room_category];
    const body: Record<string, unknown> = {
      patient_name: f.patient_name, uhid: f.uhid || undefined, treating_doctor: f.treating_doctor || undefined,
      provisional_dx: f.provisional_dx || undefined, procedure_id: f.procedure_id || undefined,
      dob: f.dob || undefined, sex: f.sex || undefined, abha_id: f.abha_id || undefined, doctor_hpr_id: f.doctor_hpr_id || undefined,
      icd10: f.icd10 || undefined, member_id: f.member_id || undefined, product_code: f.product_code || undefined,
      payer_id: f.payer_id, policy_number: f.policy_number, sum_insured: num('sum_insured'), balance: num('balance'),
      copay_pct: Number(f.copay_pct || 0), sub_limit: num('sub_limit'), room_rule: roomRule(),
      room_category: f.room_category, ward_category: ROOMS[f.room_category], room_rent_per_day: rate,
    };
    try {
      const c = await engine<{ id: string }>('/cases', { method: 'POST', body: JSON.stringify(body) });
      nav(`/case/${c.id}`);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  }

  const share = check?.chosen_room?.payer_share_of_room_linked ?? 1;

  return (
    <form onSubmit={submit} className="grid max-w-5xl gap-6 lg:grid-cols-[1fr_320px]">
      <div className="space-y-5">
        <h1 className="font-serif text-[28px]">New admission</h1>
        <section className="card grid gap-4 p-5 sm:grid-cols-2">
          <h2 className="font-medium sm:col-span-2">Patient</h2>
          {PATIENT.map(([k, l, t]) => input(k, l, t ?? 'text', k === 'patient_name'))}
          <div><label className="label" htmlFor="sex">Sex</label>
            <select id="sex" className="input" value={f.sex ?? ''} onChange={(e) => setF({ ...f, sex: e.target.value })}>
              <option value="">—</option><option value="M">Male</option><option value="F">Female</option><option value="O">Other</option></select></div>
        </section>
        <section className="card grid gap-4 p-5 sm:grid-cols-2">
          <h2 className="font-medium sm:col-span-2">Policy</h2>
          <div className="sm:col-span-2"><label className="label" htmlFor="payer">Payer</label>
            <select id="payer" required className="input" value={f.payer_id ?? ''} onChange={(e) => setF({ ...f, payer_id: e.target.value })}>
              <option value="">Choose insurer, TPA or scheme</option>
              {payers.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.kind})</option>)}
            </select></div>
          {POLICY.map(([k, l, t]) => input(k, l, t, k === 'policy_number' || k === 'sum_insured'))}
          <div><label className="label" htmlFor="rule">Room limit</label>
            <select id="rule" className="input" value={f.rule} onChange={(e) => setF({ ...f, rule: e.target.value })}>
              <option value="none">No room limit</option><option value="pctSI">% of sum insured a day</option><option value="category">Up to a room category</option>
            </select></div>
          {f.rule === 'pctSI' && <>{input('roomPct', 'Room % of SI a day', 'number')}{input('icuPct', 'ICU % of SI a day', 'number')}</>}
          {f.rule === 'category' && <div><label className="label" htmlFor="max">Up to</label>
            <select id="max" className="input" value={f.max} onChange={(e) => setF({ ...f, max: e.target.value })}>
              {['general', 'twin', 'single', 'deluxe'].map((r) => <option key={r} value={r}>{ROOMS[r]}</option>)}</select></div>}
        </section>
        <section className="card p-5">
          <h2 className="mb-3 font-medium">Room</h2>
          <div className="grid gap-2 sm:grid-cols-5">
            {Object.keys(ROOMS).map((r) => {
              const rate = check?.rooms[r]; const over = check?.room_advice.cap != null && rate != null && r !== 'icu' && rate > check.room_advice.cap;
              return (
                <button type="button" key={r} onClick={() => setF({ ...f, room_category: r })}
                  className={`rounded-md border p-3 text-left ${f.room_category === r ? 'border-accent bg-accent-soft' : 'border-line bg-surface hover:bg-surface-2'}`}>
                  <div className="text-[13px] font-medium">{ROOMS[r]}</div>
                  <div className="num text-[12px] text-ink-3">{rate ? rupees(rate) : '—'}</div>
                  {over && <div className="mt-1 text-[11px] text-warn">Above limit</div>}
                </button>
              );
            })}
          </div>
        </section>
        {err && <p className="rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}
        <button className="btn-primary" disabled={busy}>{busy ? 'Creating…' : 'Create admission'}</button>
      </div>

      <aside className="lg:pt-14">
        <div className="card sticky top-6 p-4">
          <div className="label">Policy check</div>
          {!check && <p className="text-ink-3">Choose the payer and enter the sum insured to see the room advice and the family's exposure.</p>}
          {check && <>
            <p className="mb-2">{check.room_rule_label}.</p>
            <p className="mb-3 text-ink-2">{check.room_advice.advice}</p>
            {share < 1 ? (
              <div className="rounded-md bg-warn-soft px-3 py-2 text-warn">
                In a {ROOMS[f.room_category].toLowerCase()} the payer pays only <b>{Math.round(share * 100)}%</b> of room-linked charges
                (surgeon, OT, anaesthesia, visits, consumables). The family pays the rest. Tell them now, not at discharge.
              </div>
            ) : <div className="rounded-md bg-good-soft px-3 py-2 text-good">No proportionate cut in this room.</div>}
          </>}
        </div>
      </aside>
    </form>
  );
}
