import { useEffect, useState } from 'react';
import { engine, rupees, supabase } from '../lib/api';

interface Card {
  payer_id: string; payer: string; kind: string; claims: number; first_time_preauth_pct: number | null; query_rate: number | null;
  preauth_clock_breaches: number; discharge_clock_breaches: number; settled_vs_claimed_pct: number | null; deductions_after_discharge: number;
  short_payments: number; rejections: number; days_submit_to_settle: number | null; days_submit_to_bank: number | null;
  outstanding: number; ageing: Record<string, number>; hospital_leakage: number;
}
interface Owner { as_of: string; buckets: { id: string; label: string; amount: number; count: number }[]; in_house: number;
  leakage: { hospital_leakage: number; family_share: number; contestable: number }; top_leakage: { code: string; name: string; amount: number }[] }

async function download(path: string, name: string) {
  const { data } = await supabase.auth.getSession();
  const r = await fetch(`${import.meta.env.VITE_ENGINE_URL}${path}`, { headers: { Authorization: `Bearer ${data.session?.access_token ?? ''}` } });
  if (!r.ok) return;
  const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = name; a.click();
}
const dash = (v: number | null, unit = '') => (v == null ? '—' : `${v}${unit}`);

export function Scorecards() {
  const [cards, setCards] = useState<Card[] | null>(null);
  useEffect(() => { engine<{ scorecards: Card[] }>('/reports/payers').then((r) => setCards(r.scorecards)).catch(() => setCards([])); }, []);
  if (!cards) return <p className="text-ink-3">Loading scorecards…</p>;
  if (cards.length === 0) return <p className="text-ink-3">No claims yet.</p>;
  const ROWS: [string, (c: Card) => string][] = [
    ['Claims', (c) => String(c.claims)],
    ['Pre-auth approved first time', (c) => dash(c.first_time_preauth_pct, '%')],
    ['Queries per claim', (c) => dash(c.query_rate)],
    ['IRDAI clocks missed (1 h / 3 h)', (c) => `${c.preauth_clock_breaches} / ${c.discharge_clock_breaches}`],
    ['Settled vs claimed', (c) => dash(c.settled_vs_claimed_pct, '%')],
    ['Cut after discharge', (c) => rupees(c.deductions_after_discharge)],
    ['Short payments', (c) => rupees(c.short_payments)],
    ['Rejections', (c) => String(c.rejections)],
    ['Days, submission to settlement', (c) => dash(c.days_submit_to_settle)],
    ['Days, submission to bank', (c) => dash(c.days_submit_to_bank)],
    ['Outstanding', (c) => rupees(c.outstanding)],
    ['  of which over 60 days', (c) => rupees((c.ageing['61-90'] ?? 0) + (c.ageing['90+'] ?? 0))],
    ['Hospital leakage', (c) => rupees(c.hospital_leakage)],
  ];
  return (
    <div className="card overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-[13px]">
        <thead className="border-b border-line"><tr><th className="px-4 py-2" />{cards.map((c) => <th key={c.payer_id} className="px-4 py-2 text-right font-medium">{c.payer}<div className="text-[11px] font-normal capitalize text-ink-3">{c.kind}</div></th>)}</tr></thead>
        <tbody>{ROWS.map(([label, f]) => (
          <tr key={label} className="border-b border-line last:border-0"><td className="whitespace-pre px-4 py-1.5 text-ink-2">{label}</td>
            {cards.map((c) => <td key={c.payer_id} className="num px-4 py-1.5 text-right">{f(c)}</td>)}</tr>
        ))}</tbody>
      </table>
    </div>
  );
}

/** Owner pack and exports. */
export default function Reports() {
  const [o, setO] = useState<Owner | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => { engine<Owner>('/reports/owner').then(setO).catch((e) => setErr(e.message)); }, []);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="font-serif text-[28px] leading-tight">Reports</h1><p className="text-ink-3">The owner's pack, payer scorecards and exports</p></div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={() => download('/reports/claims.csv', 'claims-register.csv')}>Claim register (CSV)</button>
          <button className="btn-ghost" onClick={() => download('/reports/payers.csv', 'payer-scorecards.csv')}>Payer scorecards (CSV)</button>
        </div>
      </div>
      {err && <p className="rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}
      {o && (
        <section className="card p-5">
          <h2 className="mb-1 font-medium">Owner's pack</h2>
          <p className="mb-4 text-[13px] text-ink-3">As of {new Date(o.as_of).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}. Case numbers and amounts only.</p>
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <div className="label">Money by where it stands</div>
              {o.buckets.map((b) => <div key={b.id} className="flex justify-between py-1"><span><span className="num mr-2 text-ink-3">{b.id}</span>{b.label}</span><span className="num">{rupees(b.amount)} <span className="text-ink-3">· {b.count}</span></span></div>)}
              <div className="mt-2 flex justify-between border-t border-line pt-2"><span>Patients in house</span><span className="num">{o.in_house}</span></div>
            </div>
            <div>
              <div className="label">Leakage</div>
              <div className="flex justify-between py-1"><span>Hospital leakage</span><span className="num text-crit">{rupees(o.leakage.hospital_leakage)}</span></div>
              <div className="flex justify-between py-1"><span>Contestable now</span><span className="num">{rupees(o.leakage.contestable)}</span></div>
              <div className="flex justify-between py-1"><span>Family share</span><span className="num">{rupees(o.leakage.family_share)}</span></div>
              {o.top_leakage.length > 0 && <div className="mt-2 border-t border-line pt-2 text-[13px]">
                {o.top_leakage.map((t) => <div key={t.code} className="flex justify-between py-0.5"><span><span className="num mr-2 text-ink-3">{t.code}</span>{t.name}</span><span className="num">{rupees(t.amount)}</span></div>)}
              </div>}
            </div>
          </div>
        </section>
      )}
      <div><h2 className="mb-2 font-medium">Payer scorecards</h2><Scorecards /></div>
    </div>
  );
}
